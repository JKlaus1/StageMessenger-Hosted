"""SQLite persistence: admin password, leaders, gigs.

Live state (who is connected, message logs) is NOT here — see live.py.
One connection per call, WAL mode; traffic is tiny (a few writes per gig).
"""
import os
import secrets
import sqlite3
import threading
import time
from contextlib import contextmanager

from werkzeug.security import check_password_hash, generate_password_hash

DB_PATH = os.environ.get('SM_DB', os.path.join(os.environ.get('SM_DATA', './data'), 'stage-messenger.sqlite'))

GIG_IDLE_EXPIRE_S = int(os.environ.get('SM_GIG_IDLE_HOURS', '48')) * 3600
ENDED_RETAIN_S    = 30 * 86400          # ended gigs stay listed for 30 days, then purged
DEFAULT_MAX_GIGS  = 5

_lock = threading.Lock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS leaders (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  username      TEXT NOT NULL UNIQUE COLLATE NOCASE,
  display_name  TEXT NOT NULL,
  pw_hash       TEXT NOT NULL,
  pw_version    INTEGER NOT NULL DEFAULT 1,
  active        INTEGER NOT NULL DEFAULT 1,
  max_gigs      INTEGER NOT NULL DEFAULT 5,
  created_at    INTEGER NOT NULL,
  last_login    INTEGER
);
CREATE TABLE IF NOT EXISTS gigs (
  id             TEXT PRIMARY KEY,
  leader_id      INTEGER NOT NULL REFERENCES leaders(id),
  name           TEXT NOT NULL,
  gig_date       TEXT NOT NULL DEFAULT '',
  join_token     TEXT NOT NULL UNIQUE,
  created_at     INTEGER NOT NULL,
  last_activity  INTEGER NOT NULL,
  ended_at       INTEGER,
  end_reason     TEXT
);
CREATE INDEX IF NOT EXISTS gigs_leader ON gigs(leader_id, ended_at);
"""


def _connect():
    os.makedirs(os.path.dirname(os.path.abspath(DB_PATH)), exist_ok=True)
    c = sqlite3.connect(DB_PATH, timeout=10, isolation_level=None)
    c.row_factory = sqlite3.Row
    c.execute('PRAGMA journal_mode=WAL')
    c.execute('PRAGMA foreign_keys=ON')
    return c


@contextmanager
def _db():
    c = _connect()
    try:
        yield c
    finally:
        c.close()


def init():
    with _lock, _db() as c:
        c.executescript(SCHEMA)


def now():
    return int(time.time())


def new_token():
    """128-bit URL-safe random token (22 chars)."""
    return secrets.token_urlsafe(16)


# ── Admin ────────────────────────────────────────────────────────────────────

def admin_password_set():
    with _db() as c:
        return c.execute("SELECT 1 FROM meta WHERE key='admin_pw'").fetchone() is not None


def set_admin_password(pw):
    h = generate_password_hash(pw)
    with _lock, _db() as c:
        c.execute("INSERT INTO meta(key,value) VALUES('admin_pw',?) "
                  "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (h,))
        # Bump the admin session version so existing admin sessions are logged out.
        c.execute("INSERT INTO meta(key,value) VALUES('admin_ver','1') "
                  "ON CONFLICT(key) DO UPDATE SET value=CAST(value AS INTEGER)+1")


def check_admin_password(pw):
    with _db() as c:
        r = c.execute("SELECT value FROM meta WHERE key='admin_pw'").fetchone()
    return bool(r) and check_password_hash(r['value'], pw)


def admin_version():
    with _db() as c:
        r = c.execute("SELECT value FROM meta WHERE key='admin_ver'").fetchone()
    return int(r['value']) if r else 0


# ── Leaders ──────────────────────────────────────────────────────────────────

def _leader_dict(r):
    if not r:
        return None
    return {k: r[k] for k in r.keys() if k != 'pw_hash'}


def create_leader(username, display_name, password, max_gigs=DEFAULT_MAX_GIGS):
    with _lock, _db() as c:
        try:
            cur = c.execute(
                "INSERT INTO leaders(username,display_name,pw_hash,max_gigs,created_at) VALUES(?,?,?,?,?)",
                (username, display_name, generate_password_hash(password), max_gigs, now()))
        except sqlite3.IntegrityError:
            return None
        return get_leader(cur.lastrowid, c)


def get_leader(leader_id, c=None):
    own = c is None
    c = c or _connect()
    try:
        return _leader_dict(c.execute("SELECT * FROM leaders WHERE id=?", (leader_id,)).fetchone())
    finally:
        if own:
            c.close()


def list_leaders():
    with _db() as c:
        rows = c.execute(
            "SELECT l.*, (SELECT COUNT(*) FROM gigs g WHERE g.leader_id=l.id AND g.ended_at IS NULL) AS active_gigs "
            "FROM leaders l ORDER BY l.username COLLATE NOCASE").fetchall()
    return [_leader_dict(r) for r in rows]


def authenticate_leader(username, password):
    """Returns the leader dict on success, None otherwise. Inactive leaders cannot log in."""
    with _db() as c:
        r = c.execute("SELECT * FROM leaders WHERE username=?", (username,)).fetchone()
        if not r or not r['active'] or not check_password_hash(r['pw_hash'], password):
            return None
        c.execute("UPDATE leaders SET last_login=? WHERE id=?", (now(), r['id']))
        return _leader_dict(r)


def update_leader(leader_id, *, display_name=None, password=None, active=None, max_gigs=None):
    sets, args = [], []
    if display_name is not None:
        sets.append('display_name=?'); args.append(display_name)
    if password is not None:
        sets += ['pw_hash=?', 'pw_version=pw_version+1']; args.append(generate_password_hash(password))
    if active is not None:
        sets.append('active=?'); args.append(1 if active else 0)
        if not active:
            sets.append('pw_version=pw_version+1')   # disabling also kills live sessions
    if max_gigs is not None:
        sets.append('max_gigs=?'); args.append(int(max_gigs))
    if not sets:
        return get_leader(leader_id)
    args.append(leader_id)
    with _lock, _db() as c:
        c.execute(f"UPDATE leaders SET {', '.join(sets)} WHERE id=?", args)
        return get_leader(leader_id, c)


def verify_leader_password(leader_id, password):
    with _db() as c:
        r = c.execute("SELECT pw_hash FROM leaders WHERE id=?", (leader_id,)).fetchone()
    return bool(r) and check_password_hash(r['pw_hash'], password)


# ── Gigs ─────────────────────────────────────────────────────────────────────

def _gig_dict(r):
    return {k: r[k] for k in r.keys()} if r else None


def count_active_gigs(leader_id):
    with _db() as c:
        return c.execute("SELECT COUNT(*) FROM gigs WHERE leader_id=? AND ended_at IS NULL",
                         (leader_id,)).fetchone()[0]


def create_gig(leader_id, name, gig_date=''):
    gid, tok, t = secrets.token_urlsafe(8), new_token(), now()
    with _lock, _db() as c:
        c.execute("INSERT INTO gigs(id,leader_id,name,gig_date,join_token,created_at,last_activity) "
                  "VALUES(?,?,?,?,?,?,?)", (gid, leader_id, name, gig_date, tok, t, t))
        return _gig_dict(c.execute("SELECT * FROM gigs WHERE id=?", (gid,)).fetchone())


def get_gig(gig_id):
    with _db() as c:
        return _gig_dict(c.execute("SELECT * FROM gigs WHERE id=?", (gig_id,)).fetchone())


def gig_by_token(token):
    """Active gig for a join token, or None. Constant shape regardless of why it failed."""
    if not token or len(token) > 64:
        return None
    with _db() as c:
        return _gig_dict(c.execute("SELECT * FROM gigs WHERE join_token=? AND ended_at IS NULL",
                                   (token,)).fetchone())


def leader_gigs(leader_id):
    with _db() as c:
        rows = c.execute("SELECT * FROM gigs WHERE leader_id=? "
                         "ORDER BY (ended_at IS NOT NULL), created_at DESC", (leader_id,)).fetchall()
    return [_gig_dict(r) for r in rows]


def all_active_gigs():
    with _db() as c:
        rows = c.execute("SELECT g.*, l.username AS leader_username FROM gigs g "
                         "JOIN leaders l ON l.id=g.leader_id WHERE g.ended_at IS NULL "
                         "ORDER BY g.created_at DESC").fetchall()
    return [_gig_dict(r) for r in rows]


def rotate_join_token(gig_id):
    tok = new_token()
    with _lock, _db() as c:
        c.execute("UPDATE gigs SET join_token=? WHERE id=? AND ended_at IS NULL", (tok, gig_id))
        return _gig_dict(c.execute("SELECT * FROM gigs WHERE id=?", (gig_id,)).fetchone())


def end_gig(gig_id, reason='ended'):
    with _lock, _db() as c:
        cur = c.execute("UPDATE gigs SET ended_at=?, end_reason=? WHERE id=? AND ended_at IS NULL",
                        (now(), reason, gig_id))
        return cur.rowcount > 0


def touch_gig(gig_id):
    with _db() as c:
        c.execute("UPDATE gigs SET last_activity=? WHERE id=?", (now(), gig_id))


def expire_idle_gigs():
    """Ends gigs idle past the limit and purges long-ended ones. Returns ids just ended."""
    t = now()
    with _lock, _db() as c:
        ids = [r['id'] for r in c.execute(
            "SELECT id FROM gigs WHERE ended_at IS NULL AND last_activity < ?",
            (t - GIG_IDLE_EXPIRE_S,)).fetchall()]
        if ids:
            c.executemany("UPDATE gigs SET ended_at=?, end_reason='expired' WHERE id=?",
                          [(t, i) for i in ids])
        c.execute("DELETE FROM gigs WHERE ended_at IS NOT NULL AND ended_at < ?", (t - ENDED_RETAIN_S,))
    return ids
