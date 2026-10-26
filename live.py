"""In-memory live state for each active gig: connected devices, names, message log.

Everything here is lost on restart by design (same as the Pi build): a restart
drops connections, clients reconnect, and devices re-register by name.
All access goes through LOCK.
"""
import threading
import time
from collections import defaultdict, deque

LOCK    = threading.RLock()
LOG_CAP = 500


def room(gid, suffix):
    """Every Socket.IO room is namespaced by gig id. Clients never see or send these."""
    return f'g:{gid}:{suffix}'


class GigLive:
    __slots__ = ('gid', 'devices', 'names', 'controls', 'log', 'seq',
                 'device_tokens', 'last_touch')

    def __init__(self, gid):
        self.gid           = gid
        self.devices       = {}      # sid -> {name, type, role, room, canSend, token}
        self.names         = {}      # NAME -> sid
        self.controls      = set()   # control sids
        self.log           = []
        self.seq           = 0
        self.device_tokens = {}      # device token -> NAME (rejoin credential, revoked on kick)
        self.last_touch    = 0.0

    def append_log(self, kind, frm, message, targets, color):
        self.seq += 1
        entry = {'id': self.seq, 'ts': int(time.time() * 1000), 'kind': kind, 'from': frm,
                 'message': message, 'targets': targets, 'color': color}
        self.log.append(entry)
        if len(self.log) > LOG_CAP:
            del self.log[:len(self.log) - LOG_CAP]
        return entry

    def device_list(self):
        return [{'name': d['name'], 'type': d['type'], 'role': d['role'],
                 'room': d['room'], 'canSend': d['canSend']} for d in self.devices.values()]


gigs        = {}   # gid -> GigLive
sids        = {}   # sid -> {'gid', 'kind': 'member'|'control', 'token'?, 'leader_id'?, 'lv'?}
token_index = {}   # device token -> gid


def get(gid, create=False):
    g = gigs.get(gid)
    if g is None and create:
        g = gigs[gid] = GigLive(gid)
    return g


def device_count(gid):
    g = gigs.get(gid)
    return len(g.devices) if g else 0


def drop(gid):
    """Removes a gig's live state. Returns the sids that belonged to it."""
    g = gigs.pop(gid, None)
    if g:
        for t in g.device_tokens:
            token_index.pop(t, None)
    owned = [s for s, c in sids.items() if c['gid'] == gid]
    for s in owned:
        sids.pop(s, None)
    return owned


class RateLimiter:
    """Sliding-window counter keyed by arbitrary strings (IP, username, sid)."""

    def __init__(self):
        self._hits = defaultdict(deque)
        self._lock = threading.Lock()

    def _trim(self, q, window, t):
        while q and q[0] <= t - window:
            q.popleft()

    def blocked(self, key, limit, window):
        t = time.monotonic()
        with self._lock:
            q = self._hits.get(key)
            if not q:
                return False
            self._trim(q, window, t)
            return len(q) >= limit

    def record(self, key):
        with self._lock:
            self._hits[key].append(time.monotonic())

    def hit(self, key, limit, window):
        """Records a hit and returns True if still within the limit."""
        t = time.monotonic()
        with self._lock:
            q = self._hits[key]
            self._trim(q, window, t)
            if len(q) >= limit:
                return False
            q.append(t)
            return True

    def reset(self, key):
        with self._lock:
            self._hits.pop(key, None)

    def gc(self, max_window=3600):
        t = time.monotonic()
        with self._lock:
            for k in [k for k, q in self._hits.items() if not q or q[-1] <= t - max_window]:
                del self._hits[k]


limiter = RateLimiter()
