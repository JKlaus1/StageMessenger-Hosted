"""Two-gig isolation and auth tests (no network; Flask + Flask-SocketIO test clients).

    python3 tests/test_isolation.py
"""
import os
import sys
import tempfile

TMP = tempfile.mkdtemp()
os.environ.update(SM_DEV='1', SM_NO_BACKGROUND='1', SM_DATA=TMP, LEGACY_HOST='joseph.stage-messenger.com')
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

import live    # noqa: E402
import server  # noqa: E402
import store   # noqa: E402

app, sio = server.app, server.socketio
PASSED = 0


def check(cond, label):
    global PASSED
    if not cond:
        print('FAIL', label)
        raise SystemExit(1)
    PASSED += 1
    print('ok  ', label)


def events(c, name=None):
    got = c.get_received()
    return [e for e in got if name is None or e['name'] == name]


def arg0(e):
    # The test client delivers the reserved 'message' event's payload unwrapped.
    a = e['args']
    return a[0] if isinstance(a, list) else a


def msgs(c):
    return [arg0(e)['message'] for e in events(c, 'message')]


def post(cl, url, body=None, **kw):
    return cl.post(url, json=body or {}, **kw)


def member(token=None, device=None):
    auth = {'kind': 'member'}
    if token:
        auth['token'] = token
    if device:
        auth['device'] = device
    return sio.test_client(app, auth=auth)


def register(c, name, role='receiver', **extra):
    c.emit('register', dict(name=name, type='personal', role=role, **extra))
    ev = events(c)
    acc = [e for e in ev if e['name'] == 'register-accepted']
    rej = [e for e in ev if e['name'] == 'register-rejected']
    return (acc[0]['args'][0] if acc else None), (rej[0]['args'][0]['reason'] if rej else None)


# ── Admin + leaders ──────────────────────────────────────────────────────────
admin = app.test_client()
r = post(admin, '/admin/api/login', {'password': 'x'})
check(r.status_code == 409, 'admin login refused until password set from CLI')
store.set_admin_password('admin-pass-123')
check(post(admin, '/admin/api/login', {'password': 'nope'}).status_code == 401, 'admin wrong password 401')
check(post(admin, '/admin/api/login', {'password': 'admin-pass-123'}).status_code == 200, 'admin login')
check(app.test_client().get('/admin/api/state').status_code == 401, 'admin state needs admin session')
r = post(admin, '/admin/api/leaders', {'username': 'alice', 'display_name': 'Alice', 'password': 'alicepass1', 'max_gigs': 2})
check(r.status_code == 201, 'admin creates leader alice')
alice_id = r.get_json()['leader']['id']
check(post(admin, '/admin/api/leaders', {'username': 'bob', 'password': 'bobpass12'}).status_code == 201, 'admin creates bob')
check(post(admin, '/admin/api/leaders', {'username': 'BOB', 'password': 'bobpass12'}).status_code == 409, 'username unique (case-insensitive)')
check(post(admin, '/admin/api/leaders', {'username': 'x', 'password': 'short'}).status_code == 400, 'leader validation')

# ── CSRF guard ───────────────────────────────────────────────────────────────
anon = app.test_client()
check(anon.post('/api/login', data='username=alice').status_code == 415, 'form POST rejected (JSON only)')
check(anon.post('/api/login', json={}, headers={'Origin': 'https://evil.example'}).status_code == 403, 'foreign Origin rejected')

# ── Leader login + gigs ──────────────────────────────────────────────────────
A, B = app.test_client(), app.test_client()
check(post(A, '/api/login', {'username': 'alice', 'password': 'wrong'}).status_code == 401, 'leader wrong password')
check(post(A, '/api/login', {'username': 'ALICE', 'password': 'alicepass1'}).status_code == 200, 'alice login (case-insensitive user)')
check(post(B, '/api/login', {'username': 'bob', 'password': 'bobpass12'}).status_code == 200, 'bob login')
check(anon.get('/dashboard').status_code == 302, 'dashboard redirects when signed out')

ga = post(A, '/api/gigs', {'name': 'Alice Wedding', 'date': '2026-11-01'}).get_json()['gig']
gb = post(B, '/api/gigs', {'name': 'Bob Bar Gig'}).get_json()['gig']
check(len(ga['join_token']) >= 22 and ga['join_token'] != gb['join_token'], '128-bit distinct join tokens')
check(post(A, '/api/gigs', {'name': 'x', 'date': 'nope'}).status_code == 400, 'date validation')
post(A, '/api/gigs', {'name': 'Alice 2'})
check(post(A, '/api/gigs', {'name': 'Alice 3'}).status_code == 409, 'max_gigs cap enforced (2)')
ids_b = [g['id'] for g in B.get('/api/gigs').get_json()['gigs']]
check(ga['id'] not in ids_b, "bob's gig list excludes alice's gigs")
check(post(B, f"/api/gigs/{ga['id']}/rotate").status_code == 404, "bob cannot rotate alice's gig")
check(post(B, f"/api/gigs/{ga['id']}/end").status_code == 404, "bob cannot end alice's gig")
check(B.get(f"/c/{ga['id']}").status_code == 302, "bob cannot open alice's control page")

# ── Sockets: connect auth ────────────────────────────────────────────────────
bad = member('not-a-real-token')
check(not bad.is_connected(), 'invalid join token refused')
nok = sio.test_client(app, auth={})
check(not nok.is_connected(), 'connect without auth refused')
ctl_a = sio.test_client(app, auth={'kind': 'control', 'gig': ga['id']}, flask_test_client=A)
check(ctl_a.is_connected(), 'alice control socket connects to her gig')
ctl_bad = sio.test_client(app, auth={'kind': 'control', 'gig': ga['id']}, flask_test_client=B)
check(not ctl_bad.is_connected(), "bob's session cannot open control for alice's gig")
ctl_anon = sio.test_client(app, auth={'kind': 'control', 'gig': ga['id']})
check(not ctl_anon.is_connected(), 'control needs a leader session')
ctl_b = sio.test_client(app, auth={'kind': 'control', 'gig': gb['id']}, flask_test_client=B)
ctl_a.emit('register-control'); ctl_b.emit('register-control')

# ── Members in both gigs, same names ─────────────────────────────────────────
a_bass, a_keys, a_mon = member(ga['join_token']), member(ga['join_token']), member(ga['join_token'])
b_bass, b_drums = member(gb['join_token']), member(gb['join_token'])
acc, _ = register(a_bass, 'bass', role='sender')
check(acc and acc['name'] == 'BASS' and acc['room'] == 'personal-BASS' and acc['device'], 'A: BASS registers as sender, gets device token')
a_bass_dev = acc['device']
register(a_keys, 'KEYS')
a_mon.emit('register', {'name': 'STAGE L', 'type': 'display', 'monitorId': 1}); a_mon.get_received()
acc, rej = register(b_bass, 'BASS', role='sender')
check(acc and not rej, 'B: same name BASS allowed in a different gig')
_, rej = register(member(ga['join_token']), 'BASS')
check(rej and 'taken' in rej, 'A: duplicate BASS rejected within the same gig')
register(b_drums, 'DRUMS')
for c in (a_bass, a_keys, a_mon, b_bass, b_drums, ctl_a, ctl_b):
    c.get_received()

# ── Messaging isolation ──────────────────────────────────────────────────────
a_bass.emit('send-message', {'message': 'A to all', 'targets': ['all']})
check('A to all' in msgs(a_keys) and 'A to all' in msgs(a_mon), 'A message reaches A devices')
check('A to all' in msgs(ctl_a), 'A message reaches A control')
check(not msgs(b_bass) and not msgs(b_drums) and not msgs(ctl_b), 'A message never reaches gig B')
a_bass.get_received()

a_bass.emit('send-message', {'message': 'inject', 'targets': [f"g:{gb['id']}:devices", 'all-devices', 'control-clients', 'personal-BASS']})
check(msgs(a_bass) == ['inject'], 'injected room names dropped; personal-BASS stays inside gig A')
check(not msgs(b_bass) and not msgs(b_drums) and not msgs(ctl_b), 'target injection cannot reach gig B')
for c in (a_keys, a_mon, ctl_a):
    c.get_received()

a_bass.emit('send-message', {'message': 'mon1', 'targets': ['mon-1']})
check(msgs(a_mon) == ['mon1'] and not msgs(a_keys), 'mon-1 targeting inside gig')
ctl_b.emit('send-message', {'message': 'B op', 'targets': ['all']})
check('B op' in msgs(b_bass) and not msgs(a_keys) and not msgs(a_bass), 'B operator message stays in B')

a_keys.emit('send-message', {'message': 'receiver tries', 'targets': ['all']})
check(not msgs(a_mon), 'receiver-only device cannot send')

r = events(ctl_b, 'log-entry')
ctl_b2 = sio.test_client(app, auth={'kind': 'control', 'gig': gb['id']}, flask_test_client=B)
ctl_b2.emit('register-control')
got = ctl_b2.get_received()
hist = [e for e in got if e['name'] == 'log-history'][0]['args'][0]
check(hist and all('A ' not in h['message'] and h['message'] != 'inject' for h in hist), 'B log history has no A entries')
devs = [e for e in got if e['name'] == 'devices-updated'][0]['args'][0]
check(sorted(d['name'] for d in devs) == ['BASS', 'DRUMS'], 'B device list only shows B devices')

payload = {'message': 'x' * 900, 'color': 'javascript:alert(1)', 'duration': 10 ** 12, 'targets': ['all']}
a_bass.emit('send-message', payload)
m = arg0(events(a_keys, 'message')[0])
check(len(m['message']) == 500 and m['color'] == '#cc0000' and m['duration'] == 86400000, 'payload sanitised (length, color, duration)')

# ── Rotation + device-token rejoin ───────────────────────────────────────────
old = ga['join_token']
ga = post(A, f"/api/gigs/{ga['id']}/rotate").get_json()['gig']
check(ga['join_token'] != old, 'rotate issues a new join token')
check(any(e['name'] == 'link-rotated' for e in ctl_a.get_received()), 'control told about the new link')
check(not member(old).is_connected(), 'old join link refused after rotation')
check(member(ga['join_token']).is_connected(), 'new join link works')
again = member(old, device=a_bass_dev)
check(again.is_connected(), 'existing device rejoins with its device token after rotation')
acc, rej = register(again, 'BASS', role='sender')
check(acc and not rej, 'same device reclaims its name (stale socket replaced)')
check(not a_bass.is_connected(), 'stale BASS socket disconnected')
a_bass = again

# ── Kick ─────────────────────────────────────────────────────────────────────
ctl_a.emit('kick', {'name': 'BASS'})
check(not a_bass.is_connected(), 'kicked device disconnected')
check(not member(old, device=a_bass_dev).is_connected(), 'kicked device token revoked')
ctl_b.emit('kick', {'name': 'KEYS'})
check(a_keys.is_connected(), "gig B's control cannot kick a gig A device")

# ── Disable leader ───────────────────────────────────────────────────────────
post(admin, f'/admin/api/leaders/{alice_id}', {'active': False})
check(not ctl_a.is_connected(), "disabling alice drops her control sockets")
check(A.get('/api/gigs').status_code == 401, 'disabled leader session rejected')
check(post(app.test_client(), '/api/login', {'username': 'alice', 'password': 'alicepass1'}).status_code == 401, 'disabled leader cannot log in')
post(admin, f'/admin/api/leaders/{alice_id}', {'active': True})
check(post(A, '/api/login', {'username': 'alice', 'password': 'alicepass1'}).status_code == 200, 're-enabled leader can log in')

# ── End gig ──────────────────────────────────────────────────────────────────
b_bass.get_received()
check(post(B, f"/api/gigs/{gb['id']}/end").status_code == 200, 'bob ends his gig')
check(not b_bass.is_connected() and not b_drums.is_connected(), 'all gig B devices disconnected on end')
check(not member(gb['join_token']).is_connected(), 'ended gig link refused')
check(a_keys.is_connected(), 'ending gig B leaves gig A running')
check(gb['id'] not in live.gigs, 'gig B live state dropped')

# ── Expiry ───────────────────────────────────────────────────────────────────
with store._db() as c:
    c.execute('UPDATE gigs SET last_activity=0 WHERE id=?', (ga['id'],))
for gid in store.expire_idle_gigs():
    server.teardown(gid, 'expired')
check(not a_keys.is_connected() and not member(ga['join_token']).is_connected(), 'idle gig expires and its link dies')

# ── Brute-force throttle ─────────────────────────────────────────────────────
for _ in range(25):
    member('guess-' + os.urandom(8).hex())
live.limiter._hits.clear()
g3 = post(B, '/api/gigs', {'name': 'Bob 2'}).get_json()['gig']
for _ in range(20):
    member('guess-' + os.urandom(8).hex())
check(not member(g3['join_token']).is_connected(), 'after 20 bad links from one IP even a valid link is refused (rate-limited)')
live.limiter._hits.clear()
check(member(g3['join_token']).is_connected(), 'valid link works again once the window clears')
for _ in range(10):
    post(app.test_client(), '/api/login', {'username': 'bob', 'password': 'bad'})
check(post(app.test_client(), '/api/login', {'username': 'bob', 'password': 'bobpass12'}).status_code == 429, 'login throttled after 10 failures')

# ── Pages ────────────────────────────────────────────────────────────────────
r = anon.get('/healthz')
check(r.status_code == 200 and r.headers['Referrer-Policy'] == 'no-referrer', 'healthz + no-referrer header')
check(anon.get('/server.py').status_code == 404 and anon.get('/../store.py').status_code == 404, 'source files not served')
m = anon.get('/app.webmanifest?start=/g/abc?name=bass').get_json()
check(m['start_url'] == '/g/abc?name=bass' and 'BASS' in m['name'], 'PWA manifest for a join link')
check(anon.get('/app.webmanifest?start=//evil.com').get_json()['start_url'] == '/', 'manifest rejects off-site start')

# ── Old rack-Pi links redirect to joseph.stage-messenger.com ─────────────────
J = 'https://joseph.stage-messenger.com'
for path, want in [('/mixer', J + '/mixer'), ('/mixer/', J + '/mixer/'), ('/mixer/api/state', J + '/mixer/api/state'),
                   ('/watch/abc123', J + '/watch/abc123'), ('/control', J + '/control'),
                   ('/?name=BASS&role=sender', J + '/?name=BASS&role=sender'), ('/?role=receiver', J + '/?role=receiver')]:
    r = anon.get(path)
    check(r.status_code == 302 and r.headers['Location'] == want, f'legacy {path} -> {want}')
check(anon.get('/').status_code == 200, 'bare / still serves the hosted landing page')
check(anon.get('/mixers').status_code == 404 and anon.get('/controller').status_code == 404, 'only exact legacy prefixes redirect')
check(anon.get('/g/sometoken?name=BASS&role=sender').status_code == 200, 'hosted named links are not redirected')
check(anon.post('/control', json={}).status_code != 302, 'non-GET requests are never redirected')

# ── Cache busting ────────────────────────────────────────────────────────────
import re as _re
html = anon.get('/login').get_data(as_text=True)
tags = _re.findall(r'(?:src|href)="(/[\w./-]+\.(?:js|css))(\?v=[0-9a-f]{12})?"', html)
check(tags and all(v for _, v in tags), 'every local script/stylesheet on a page carries ?v=<hash>')
vurl = [p + v for p, v in tags if p == '/sm-ui.js'][0]
r = anon.get(vurl)
check('immutable' in r.headers.get('Cache-Control', ''), 'versioned asset is cached as immutable')
r = anon.get('/sm-ui.js')
check(r.headers.get('Cache-Control') == 'no-cache', 'unversioned asset is no-cache')
r = anon.get('/sm-ui.js?v=000000000000')
check(r.headers.get('Cache-Control') == 'no-cache', 'stale version tag is not cached as immutable')
check(anon.get('/login').headers.get('Cache-Control') == 'no-cache', 'pages stay no-cache')

print(f'\nALL {PASSED} CHECKS PASSED')
