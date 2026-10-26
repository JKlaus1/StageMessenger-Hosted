"""Stage Messenger — hosted, multi-gig build (stage-messenger.com).

Roles
  admin   /admin            password set from the server CLI (manage.py); Cloudflare Access in front
  leader  /login → /dashboard   created by the admin; creates gigs, gets join links, runs Control
  member  /g/<join token>       anyone holding the link; registers a name for that gig only
  display /g/<join token>/display?monitor=N&name=STAGE+L

Isolation
  Every Socket.IO room is namespaced g:<gigId>:<room>. The gig a socket belongs to is fixed at
  connect time from its join token (or device token, or leader session for Control) and is the
  only gig its events can ever reach. Client-supplied targets are validated and re-prefixed.

Run (container): gunicorn -w 1 --threads 100 -b 0.0.0.0:8000 server:app
One worker only: live state is in this process's memory.
"""
import os
import re
import time
from urllib.parse import urlsplit, parse_qs

from flask import Flask, abort, jsonify, redirect, request, send_from_directory, session
from flask_socketio import ConnectionRefusedError, SocketIO, emit, join_room

import live
import store
from live import LOCK, limiter, room

# ── Config ───────────────────────────────────────────────────────────────────

DEV        = os.environ.get('SM_DEV') == '1'
PUBLIC_URL = os.environ.get('PUBLIC_URL', '').rstrip('/')
SECRET_KEY = os.environ.get('SECRET_KEY', '')
if not SECRET_KEY:
    if not DEV:
        raise SystemExit('SECRET_KEY is not set — refusing to start (set SM_DEV=1 for local dev).')
    SECRET_KEY = 'dev-only-not-secret'

ADMIN_SESSION_S = 12 * 3600
PUBLIC_DIR      = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'public')

app = Flask(__name__, static_folder=None)
app.config.update(
    SECRET_KEY=SECRET_KEY,
    SESSION_COOKIE_NAME='sm_session',
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE='Lax',
    SESSION_COOKIE_SECURE=not DEV,
    PERMANENT_SESSION_LIFETIME=30 * 86400,
    MAX_CONTENT_LENGTH=64 * 1024,
)


def _allowed_origins():
    extra = [o.strip().rstrip('/') for o in os.environ.get('ALLOWED_ORIGINS', '').split(',') if o.strip()]
    origins = ([PUBLIC_URL] if PUBLIC_URL else []) + extra
    if DEV:
        return '*' if not origins else origins + ['http://localhost:8000', 'http://127.0.0.1:8000']
    return origins or None          # None = python-engineio same-origin check


socketio = SocketIO(app, async_mode='threading', cors_allowed_origins=_allowed_origins(),
                    max_http_buffer_size=64 * 1024)

store.init()

# ── Validation ───────────────────────────────────────────────────────────────

NAME_RE     = re.compile(r'^[A-Z0-9 _-]{1,16}$')
USERNAME_RE = re.compile(r'^[A-Za-z0-9_.-]{3,32}$')
DATE_RE     = re.compile(r'^\d{4}-\d{2}-\d{2}$')
COLOR_RE    = re.compile(r'^#[0-9a-fA-F]{6}$')
TARGET_RE   = re.compile(r'^(mon-\d{1,3}|personal-[A-Z0-9 _-]{1,16})$')
MIN_PW      = 8


def client_ip():
    return (request.headers.get('CF-Connecting-IP')
            or (request.headers.get('X-Forwarded-For') or '').split(',')[0].strip()
            or request.remote_addr or '?')


def jbody():
    d = request.get_json(silent=True)
    return d if isinstance(d, dict) else {}


def sstr(v, n):
    return (v if isinstance(v, str) else '').strip()[:n]


def err(msg, code=400):
    return jsonify({'error': msg}), code


# ── Auth helpers ─────────────────────────────────────────────────────────────

def current_leader():
    lid, ver = session.get('lid'), session.get('lv')
    if not lid:
        return None
    ld = store.get_leader(lid)
    if not ld or not ld['active'] or ld['pw_version'] != ver:
        return None
    return ld


def is_admin():
    v = session.get('adm')
    return (v is not None and v == store.admin_version()
            and session.get('adm_t', 0) > time.time() - ADMIN_SESSION_S)


def owned_gig(gid, leader):
    g = store.get_gig(gid)
    if not g or not leader or g['leader_id'] != leader['id']:
        return None
    return g


@app.before_request
def csrf_guard():
    """State-changing API calls must be JSON from our own origin."""
    if request.method in ('POST', 'PUT', 'PATCH', 'DELETE') and (
            request.path.startswith('/api/') or request.path.startswith('/admin/api/')):
        if not request.is_json:
            return err('JSON required', 415)
        origin = request.headers.get('Origin')
        if origin and urlsplit(origin).netloc != request.host:
            return err('Bad origin', 403)


@app.after_request
def headers(resp):
    resp.headers['Referrer-Policy'] = 'no-referrer'          # never leak a join token via Referer
    resp.headers['X-Content-Type-Options'] = 'nosniff'
    resp.headers['X-Frame-Options'] = 'DENY'
    if resp.mimetype == 'text/html':
        resp.headers['Cache-Control'] = 'no-cache'
        resp.headers['X-Robots-Tag'] = 'noindex, nofollow'
    if request.path.startswith('/api/') or request.path.startswith('/admin/api/'):
        resp.headers['Cache-Control'] = 'no-store'
    return resp


# ── Pages ────────────────────────────────────────────────────────────────────

def page(name):
    return send_from_directory(PUBLIC_DIR, name)


@app.route('/healthz')
def healthz():
    return jsonify({'ok': True, 'gigs': len(live.gigs)})


@app.route('/')
def landing():
    return page('landing.html')


@app.route('/login')
def login_page():
    if current_leader():
        return redirect('/dashboard')
    return page('login.html')


@app.route('/dashboard')
def dashboard_page():
    if not current_leader():
        return redirect('/login')
    return page('dashboard.html')


@app.route('/c/<gid>')
def control_page(gid):
    ld = current_leader()
    if not ld:
        return redirect('/login')
    g = owned_gig(gid, ld)
    if not g or g['ended_at']:
        return redirect('/dashboard')
    return page('control.html')


@app.route('/g/<token>')
def member_page(token):
    # Always served: the socket does the auth, and a reloaded page whose link was
    # rotated can still rejoin with its device token.
    return page('messenger.html')


@app.route('/g/<token>/display')
def display_page(token):
    return page('display.html')


@app.route('/admin')
def admin_page():
    return page('admin.html')


# Static assets (JS, icons). HTML pages are only reachable via the routes above.
@app.route('/<path:fname>')
def static_file(fname):
    if fname.endswith('.html'):
        abort(404)
    return send_from_directory(PUBLIC_DIR, fname)


# ── Installable app manifest ─────────────────────────────────────────────────

def _pwa_for(path):
    if path.startswith('/c/'):
        return 'Stage Messenger Control', 'SM Control', 'control'
    if path.startswith('/g/') and path.endswith('/display'):
        return 'Stage Monitor', 'Stage Mon', 'messenger'
    if path.startswith('/dashboard'):
        return 'Stage Messenger Leader', 'SM Leader', 'control'
    return 'Stage Messenger', 'Messenger', 'messenger'


@app.route('/app.webmanifest')
def app_manifest():
    import json
    start = request.args.get('start', '/')
    u = urlsplit(start)
    if u.scheme or u.netloc or not u.path.startswith('/') or start.startswith('//'):
        start, u = '/', urlsplit('/')
    name, short, icon = _pwa_for(u.path)
    who = (parse_qs(u.query).get('name') or [''])[0].strip().upper()[:16]
    if u.path.startswith('/g/') and not u.path.endswith('/display') and who:
        name, short = f'Stage Messenger — {who}', who.title()[:12]
    m = {
        'id': start, 'name': name, 'short_name': short, 'start_url': start, 'scope': '/',
        'display': 'fullscreen', 'display_override': ['fullscreen', 'standalone'],
        'orientation': 'any', 'background_color': '#0d0d1a', 'theme_color': '#0d0d1a',
        'icons': [{'src': f'/pwa/{icon}-{px}.png', 'sizes': f'{px}x{px}', 'type': 'image/png',
                   'purpose': p} for px in (192, 512) for p in ('any', 'maskable')],
    }
    resp = app.response_class(json.dumps(m), mimetype='application/manifest+json')
    resp.headers['Cache-Control'] = 'no-cache'
    return resp


# ── Leader API ───────────────────────────────────────────────────────────────

def gig_json(g):
    out = {'id': g['id'], 'name': g['name'], 'date': g['gig_date'],
           'created_at': g['created_at'], 'last_activity': g['last_activity'],
           'ended_at': g['ended_at'], 'end_reason': g['end_reason'],
           'devices': live.device_count(g['id'])}
    if not g['ended_at']:
        out['join_token'] = g['join_token']
    return out


def leader_json(ld):
    return {'id': ld['id'], 'username': ld['username'], 'display_name': ld['display_name'],
            'max_gigs': ld['max_gigs']}


@app.post('/api/login')
def api_login():
    d = jbody()
    user, pw = sstr(d.get('username'), 64), d.get('password') if isinstance(d.get('password'), str) else ''
    ip_key, user_key = 'login-ip:' + client_ip(), 'login-user:' + user.lower()
    if limiter.blocked(ip_key, 10, 900) or limiter.blocked(user_key, 10, 900):
        return err('Too many attempts — try again in 15 minutes.', 429)
    ld = store.authenticate_leader(user, pw[:256])
    if not ld:
        limiter.record(ip_key)
        limiter.record(user_key)
        return err('Wrong username or password.', 401)
    limiter.reset(user_key)
    for k in ('lid', 'lv'):
        session.pop(k, None)
    session['lid'], session['lv'] = ld['id'], ld['pw_version']
    session.permanent = True
    return jsonify({'ok': True, 'leader': leader_json(ld)})


@app.post('/api/logout')
def api_logout():
    session.pop('lid', None)
    session.pop('lv', None)
    return jsonify({'ok': True})


@app.get('/api/me')
def api_me():
    ld = current_leader()
    if not ld:
        return err('Not signed in', 401)
    return jsonify({'leader': leader_json(ld), 'public_url': PUBLIC_URL})


@app.get('/api/gigs')
def api_gigs():
    ld = current_leader()
    if not ld:
        return err('Not signed in', 401)
    return jsonify({'gigs': [gig_json(g) for g in store.leader_gigs(ld['id'])],
                    'max_gigs': ld['max_gigs']})


@app.post('/api/gigs')
def api_gig_create():
    ld = current_leader()
    if not ld:
        return err('Not signed in', 401)
    d = jbody()
    name, date = sstr(d.get('name'), 60), sstr(d.get('date'), 10)
    if not name:
        return err('Give the gig a name.')
    if date and not DATE_RE.match(date):
        return err('Date must be YYYY-MM-DD.')
    if not limiter.hit(f'gig-create:{ld["id"]}', 30, 3600):
        return err('Too many gigs created this hour.', 429)
    if store.count_active_gigs(ld['id']) >= ld['max_gigs']:
        return err(f'You already have {ld["max_gigs"]} active gigs — end one first.', 409)
    g = store.create_gig(ld['id'], name, date)
    return jsonify({'gig': gig_json(g)}), 201


@app.post('/api/gigs/<gid>/rotate')
def api_gig_rotate(gid):
    ld = current_leader()
    g = owned_gig(gid, ld)
    if not g or g['ended_at']:
        return err('Gig not found', 404)
    g = store.rotate_join_token(gid)
    socketio.emit('link-rotated', {'join_token': g['join_token']}, to=room(gid, 'control'))
    return jsonify({'gig': gig_json(g)})


@app.post('/api/gigs/<gid>/end')
def api_gig_end(gid):
    ld = current_leader()
    g = owned_gig(gid, ld)
    if not g or g['ended_at']:
        return err('Gig not found', 404)
    store.end_gig(gid, 'ended')
    teardown(gid, 'ended')
    return jsonify({'ok': True})


@app.post('/api/account/password')
def api_password():
    ld = current_leader()
    if not ld:
        return err('Not signed in', 401)
    d = jbody()
    cur, new = d.get('current'), d.get('new')
    if not isinstance(cur, str) or not isinstance(new, str):
        return err('Missing fields')
    if not limiter.hit(f'pwchange:{ld["id"]}', 10, 900):
        return err('Too many attempts.', 429)
    if not store.verify_leader_password(ld['id'], cur[:256]):
        return err('Current password is wrong.', 401)
    if len(new) < MIN_PW or len(new) > 256:
        return err(f'New password must be at least {MIN_PW} characters.')
    ld = store.update_leader(ld['id'], password=new)
    session['lv'] = ld['pw_version']          # keep this session, log out all others
    return jsonify({'ok': True})


# ── Admin API ────────────────────────────────────────────────────────────────

@app.post('/admin/api/login')
def admin_login():
    if not store.admin_password_set():
        return err('Admin password not set. On the server run: '
                   'docker compose exec stage-messenger python manage.py set-admin-password', 409)
    key = 'admin-ip:' + client_ip()
    if limiter.blocked(key, 5, 900):
        return err('Too many attempts — try again in 15 minutes.', 429)
    pw = jbody().get('password')
    if not isinstance(pw, str) or not store.check_admin_password(pw[:256]):
        limiter.record(key)
        return err('Wrong password.', 401)
    session['adm'], session['adm_t'] = store.admin_version(), time.time()
    return jsonify({'ok': True})


@app.post('/admin/api/logout')
def admin_logout():
    session.pop('adm', None)
    session.pop('adm_t', None)
    return jsonify({'ok': True})


@app.get('/admin/api/state')
def admin_state():
    if not is_admin():
        return err('Not signed in', 401)
    gigs = []
    for g in store.all_active_gigs():
        gigs.append({'id': g['id'], 'name': g['name'], 'date': g['gig_date'],
                     'leader': g['leader_username'], 'created_at': g['created_at'],
                     'last_activity': g['last_activity'], 'devices': live.device_count(g['id'])})
    return jsonify({'leaders': store.list_leaders(), 'gigs': gigs})


@app.post('/admin/api/leaders')
def admin_leader_create():
    if not is_admin():
        return err('Not signed in', 401)
    d = jbody()
    user = sstr(d.get('username'), 32)
    disp = sstr(d.get('display_name'), 40) or user
    pw = d.get('password') if isinstance(d.get('password'), str) else ''
    try:
        max_gigs = int(d.get('max_gigs') or store.DEFAULT_MAX_GIGS)
    except (TypeError, ValueError):
        return err('max_gigs must be a number')
    if not USERNAME_RE.match(user):
        return err('Username: 3–32 letters, numbers, dot, dash or underscore.')
    if len(pw) < MIN_PW or len(pw) > 256:
        return err(f'Password must be at least {MIN_PW} characters.')
    if not 1 <= max_gigs <= 50:
        return err('max_gigs must be 1–50')
    ld = store.create_leader(user, disp, pw, max_gigs)
    if not ld:
        return err('That username is taken.', 409)
    return jsonify({'leader': ld}), 201


@app.post('/admin/api/leaders/<int:lid>')
def admin_leader_update(lid):
    if not is_admin():
        return err('Not signed in', 401)
    if not store.get_leader(lid):
        return err('Leader not found', 404)
    d = jbody()
    kw = {}
    if 'display_name' in d:
        kw['display_name'] = sstr(d['display_name'], 40) or None
    if 'password' in d:
        pw = d['password'] if isinstance(d['password'], str) else ''
        if len(pw) < MIN_PW or len(pw) > 256:
            return err(f'Password must be at least {MIN_PW} characters.')
        kw['password'] = pw
    if 'active' in d:
        kw['active'] = bool(d['active'])
    if 'max_gigs' in d:
        try:
            mg = int(d['max_gigs'])
        except (TypeError, ValueError):
            return err('max_gigs must be a number')
        if not 1 <= mg <= 50:
            return err('max_gigs must be 1–50')
        kw['max_gigs'] = mg
    ld = store.update_leader(lid, **kw)
    if kw.get('active') is False or 'password' in kw:
        drop_leader_controls(lid)
    return jsonify({'leader': ld})


@app.post('/admin/api/gigs/<gid>/end')
def admin_gig_end(gid):
    if not is_admin():
        return err('Not signed in', 401)
    if not store.end_gig(gid, 'ended-by-admin'):
        return err('Gig not found or already ended', 404)
    teardown(gid, 'ended-by-admin')
    return jsonify({'ok': True})


# ── Live teardown ────────────────────────────────────────────────────────────

def teardown(gid, reason):
    """Tell everyone in the gig it is over, disconnect them, forget the live state."""
    socketio.emit('gig-ended', {'reason': reason}, to=room(gid, 'sockets'))
    with LOCK:
        owned = live.drop(gid)
    for s in owned:
        try:
            socketio.server.disconnect(s, namespace='/')
        except Exception:
            pass


def drop_leader_controls(lid):
    with LOCK:
        victims = [s for s, c in live.sids.items() if c['kind'] == 'control' and c.get('leader_id') == lid]
    for s in victims:
        socketio.emit('signed-out', {}, to=s)
        try:
            socketio.server.disconnect(s, namespace='/')
        except Exception:
            pass


def expiry_loop():
    while True:
        socketio.sleep(600)
        try:
            for gid in store.expire_idle_gigs():
                teardown(gid, 'expired')
            limiter.gc()
        except Exception as e:          # keep the loop alive
            print('[expiry]', type(e).__name__, e)


def touch(g):
    t = time.time()
    if t - g.last_touch > 60:
        g.last_touch = t
        store.touch_gig(g.gid)


# ── Socket events ────────────────────────────────────────────────────────────

def ctx():
    return live.sids.get(request.sid)


def control_ctx():
    """The caller's context if it is a Control socket whose leader is still valid."""
    c = ctx()
    if not c or c['kind'] != 'control':
        return None
    ld = store.get_leader(c['leader_id'])
    if not ld or not ld['active'] or ld['pw_version'] != c['lv']:
        return None
    return c


@socketio.on('connect')
def on_connect(auth):
    auth = auth if isinstance(auth, dict) else {}
    kind, sid, ip = auth.get('kind'), request.sid, client_ip()

    if kind == 'control':
        ld = current_leader()
        gid = auth.get('gig') if isinstance(auth.get('gig'), str) else ''
        g = owned_gig(gid, ld) if ld else None
        if not g or g['ended_at']:
            raise ConnectionRefusedError('not-authorized')
        with LOCK:
            gl = live.get(gid, create=True)
            gl.controls.add(sid)
            live.sids[sid] = {'gid': gid, 'kind': 'control', 'leader_id': ld['id'], 'lv': ld['pw_version']}
        join_room(room(gid, 'sockets'))
        join_room(room(gid, 'control'))
        emit('gig-info', {'name': g['name'], 'date': g['gig_date'], 'join_token': g['join_token']})
        return

    if kind != 'member':
        raise ConnectionRefusedError('invalid-link')

    fail_key = 'join-fail:' + ip
    if limiter.blocked(fail_key, 20, 600):
        raise ConnectionRefusedError('rate-limited')

    dev = auth.get('device') if isinstance(auth.get('device'), str) else None
    gid = None
    with LOCK:
        if dev and dev in live.token_index:
            gid = live.token_index[dev]
    if gid:
        g = store.get_gig(gid)
        if not g or g['ended_at']:
            g, gid = None, None
    if not gid:
        dev = None
        g = store.gig_by_token(auth.get('token') if isinstance(auth.get('token'), str) else '')
        if not g:
            limiter.record(fail_key)
            raise ConnectionRefusedError('invalid-link')
        gid = g['id']

    with LOCK:
        live.get(gid, create=True)
        live.sids[sid] = {'gid': gid, 'kind': 'member', 'token': dev}
    join_room(room(gid, 'sockets'))
    emit('gig-info', {'name': g['name'], 'date': g['gig_date']})


@socketio.on('disconnect')
def on_disconnect(*_):
    sid = request.sid
    with LOCK:
        c = live.sids.pop(sid, None)
        if not c:
            return
        g = live.get(c['gid'])
        if not g:
            return
        g.controls.discard(sid)
        d = g.devices.pop(sid, None)
        if d and g.names.get(d['name']) == sid:
            del g.names[d['name']]
        devices = g.device_list() if d else None
    if devices is not None:
        socketio.emit('devices-updated', devices, to=room(c['gid'], 'sockets'))


@socketio.on('register-control')
def on_register_control(*_):
    c = control_ctx()
    if not c:
        return
    with LOCK:
        g = live.get(c['gid'])
        if not g:
            return
        devices, log = g.device_list(), list(g.log)
    emit('devices-updated', devices)
    emit('log-history', log)


@socketio.on('register')
def on_register(data):
    c = ctx()
    if not c or c['kind'] != 'member':
        return
    data = data if isinstance(data, dict) else {}
    sid, gid = request.sid, c['gid']
    name  = sstr(data.get('name'), 32).upper()
    dtype = 'display' if data.get('type') == 'display' else 'personal'
    role  = 'sender' if data.get('role') == 'sender' else 'receiver'
    can_send = role == 'sender' or bool(data.get('canSend'))
    if dtype == 'display':
        role, can_send = 'receiver', False

    if not name:
        return emit('register-rejected', {'reason': 'Please enter a name.'})
    if len(name) > 16:
        return emit('register-rejected', {'reason': 'Name must be 16 characters or fewer.'})
    if not NAME_RE.match(name):
        return emit('register-rejected', {'reason': 'Letters, numbers, spaces and hyphens only.'})
    if dtype == 'display':
        try:
            mon = int(data.get('monitorId', 0))
        except (TypeError, ValueError):
            mon = 0
        if not 0 <= mon <= 999:
            return emit('register-rejected', {'reason': 'Monitor number must be 0–999.'})
        client_room = f'mon-{mon}'
    else:
        client_room = f'personal-{name}'

    stale = None
    with LOCK:
        g = live.get(gid)
        if not g:
            return
        tok = c.get('token')
        holder = g.names.get(name)
        if holder and holder != sid:
            hd = g.devices.get(holder)
            if tok and hd and hd.get('token') == tok:
                stale = holder                      # same device reconnecting: take the name back
            else:
                return emit('register-rejected',
                            {'reason': f'"{name}" is already taken — choose a different name.'})
        if stale:
            g.devices.pop(stale, None)
            live.sids.pop(stale, None)
        old = g.devices.get(sid)
        if old and g.names.get(old['name']) == sid:
            del g.names[old['name']]
        if not tok:
            tok = store.new_token()
            live.token_index[tok] = gid
            c['token'] = tok
        g.device_tokens[tok] = name
        g.devices[sid] = {'name': name, 'type': dtype, 'role': role, 'room': client_room,
                          'canSend': can_send, 'token': tok}
        g.names[name] = sid
        touch(g)
        devices = g.device_list()

    if stale:
        try:
            socketio.server.disconnect(stale, namespace='/')
        except Exception:
            pass
    join_room(room(gid, 'devices'))
    join_room(room(gid, client_room))
    emit('register-accepted', {'name': name, 'room': client_room, 'role': role, 'device': tok})
    socketio.emit('devices-updated', devices, to=room(gid, 'sockets'))


def sender_ctx():
    """(ctx, sender name or 'Operator', is_control) if the caller may send, else None."""
    c = control_ctx()
    if c:
        return c, 'Operator', True
    c = ctx()
    if not c or c['kind'] != 'member':
        return None
    with LOCK:
        g = live.get(c['gid'])
        d = g.devices.get(request.sid) if g else None
    if not d or not d['canSend']:
        return None
    return c, d['name'], False


def map_targets(gid, targets):
    """Client targets → namespaced rooms. ['all'] → every registered device in this gig."""
    if not isinstance(targets, list) or not targets:
        targets = ['all']
    targets = [t for t in targets[:50] if isinstance(t, str)]
    if 'all' in targets:
        return ['all'], [room(gid, 'devices')]
    clean = [t for t in targets if TARGET_RE.match(t)]
    return clean, [room(gid, t) for t in clean]


@socketio.on('send-message')
def on_send_message(data):
    s = sender_ctx()
    if not s:
        return
    c, frm, is_control = s
    if not limiter.hit('msg:' + request.sid, 30, 10):
        return emit('send-rejected', {'reason': 'Slow down — too many messages.'})
    data = data if isinstance(data, dict) else {}
    message = sstr(data.get('message'), 500)
    if not message:
        return
    color = data.get('color') if isinstance(data.get('color'), str) and COLOR_RE.match(data['color']) else '#cc0000'
    try:
        duration = max(1000, min(86400000, int(data.get('duration', 30000))))
    except (TypeError, ValueError):
        duration = 30000
    targets, rooms = map_targets(c['gid'], data.get('targets'))
    if not rooms:
        return
    payload = {'message': message, 'subtitle': sstr(data.get('subtitle'), 200),
               'color': color, 'duration': duration, 'from': frm}
    for r in rooms:
        socketio.emit('message', payload, to=r)
    ctl = room(c['gid'], 'control')
    socketio.emit('message', payload, to=ctl)
    with LOCK:
        g = live.get(c['gid'])
        if not g:
            return
        entry = g.append_log('sent' if is_control else 'incoming', frm, message, targets, color)
        touch(g)
    socketio.emit('log-entry', entry, to=ctl)


@socketio.on('clear')
def on_clear(data):
    s = sender_ctx()
    if not s:
        return
    c, frm, _ = s
    data = data if isinstance(data, dict) else {}
    targets, rooms = map_targets(c['gid'], data.get('targets'))
    for r in rooms:
        socketio.emit('clear', to=r)
    label = 'All Devices' if targets == ['all'] else ', '.join(targets)
    with LOCK:
        g = live.get(c['gid'])
        if not g:
            return
        entry = g.append_log('system', frm, '— CLEAR: ' + label + ' —', targets, '#555')
    socketio.emit('log-entry', entry, to=room(c['gid'], 'control'))


@socketio.on('clear-log')
def on_clear_log(*_):
    s = sender_ctx()
    if not s:
        return
    c = s[0]
    with LOCK:
        g = live.get(c['gid'])
        if not g:
            return
        g.log.clear()
    socketio.emit('log-cleared', to=room(c['gid'], 'control'))


@socketio.on('kick')
def on_kick(data):
    """Control only. Removes a device and revokes its rejoin token (it can only come back
    with a valid join link — rotate the link too if it leaked)."""
    c = control_ctx()
    if not c:
        return
    name = sstr((data or {}).get('name') if isinstance(data, dict) else '', 16).upper()
    with LOCK:
        g = live.get(c['gid'])
        if not g:
            return
        victim = g.names.pop(name, None)
        d = g.devices.pop(victim, None) if victim else None
        for t in [t for t, n in g.device_tokens.items() if n == name]:
            del g.device_tokens[t]
            live.token_index.pop(t, None)
        if victim:
            live.sids.pop(victim, None)
        devices = g.device_list()
        entry = g.append_log('system', 'Operator', f'— KICKED: {name} —', [], '#555') if d else None
    if victim:
        socketio.emit('kicked', {}, to=victim)
        try:
            socketio.server.disconnect(victim, namespace='/')
        except Exception:
            pass
    socketio.emit('devices-updated', devices, to=room(c['gid'], 'sockets'))
    if entry:
        socketio.emit('log-entry', entry, to=room(c['gid'], 'control'))


# ── Startup ──────────────────────────────────────────────────────────────────

if os.environ.get('SM_NO_BACKGROUND') != '1':
    socketio.start_background_task(expiry_loop)

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8000))
    print(f'Stage Messenger (hosted) — http://127.0.0.1:{port}')
    socketio.run(app, host='0.0.0.0', port=port, debug=False, allow_unsafe_werkzeug=True)
