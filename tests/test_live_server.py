"""End-to-end over real WebSockets: runs gunicorn exactly as the container does
(-w 1 --threads 100, threading mode + simple-websocket) and connects real clients.

    python3 tests/test_live_server.py
Needs: pip install "python-socketio[client]" requests websocket-client
"""
import os
import subprocess
import sys
import tempfile
import time

import requests
import socketio

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
PORT = 8765
BASE = f'http://127.0.0.1:{PORT}'
PASSED = 0


def check(cond, label):
    global PASSED
    if not cond:
        print('FAIL', label)
        raise SystemExit(1)
    PASSED += 1
    print('ok  ', label)


tmp = tempfile.mkdtemp()
env = dict(os.environ, SM_DEV='1', SM_DATA=tmp, SECRET_KEY='live-test-key', PUBLIC_URL=BASE)
sys.path.insert(0, ROOT)
os.environ['SM_DATA'] = tmp
import store  # noqa: E402
store.init()
store.set_admin_password('admin-pass-123')

proc = subprocess.Popen(['gunicorn', '-w', '1', '--threads', '100', '-b', f'127.0.0.1:{PORT}',
                         '--graceful-timeout', '5', 'server:app'],
                        cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
clients = []
try:
    for _ in range(50):
        try:
            if requests.get(BASE + '/healthz', timeout=1).ok:
                break
        except requests.RequestException:
            time.sleep(0.2)
    check(requests.get(BASE + '/healthz').json()['ok'], 'gunicorn up, /healthz ok')

    adm = requests.Session()
    check(adm.post(BASE + '/admin/api/login', json={'password': 'admin-pass-123'}).ok, 'admin login over HTTP')
    for u in ('alice', 'bob'):
        adm.post(BASE + '/admin/api/leaders', json={'username': u, 'password': u + 'pass12'})
    leaders = {}
    gigs = {}
    for u in ('alice', 'bob'):
        s = requests.Session()
        assert s.post(BASE + '/api/login', json={'username': u, 'password': u + 'pass12'}).ok
        gigs[u] = s.post(BASE + '/api/gigs', json={'name': u + ' gig'}).json()['gig']
        leaders[u] = s

    def client(auth, cookies=None, origin=None):
        # websocket-client sends Origin: <BASE> itself; a foreign origin is tested over polling.
        got = []
        c = socketio.Client(reconnection=False)
        c.on('*', lambda ev, *a: got.append((ev, a[0] if a else None)))
        hdr = {'Origin': origin} if origin else {}
        if cookies:
            hdr['Cookie'] = '; '.join(f'{k}={v}' for k, v in cookies.items())
        c.connect(BASE, transports=['polling'] if origin else ['websocket'], auth=auth,
                  headers=hdr, wait_timeout=5)
        clients.append(c)
        return c, got

    def wait_for(got, pred, t=3.0):
        end = time.time() + t
        while time.time() < end:
            if any(pred(e) for e in got):
                return True
            time.sleep(0.05)
        return False

    a1, a1got = client({'kind': 'member', 'token': gigs['alice']['join_token']})
    a2, a2got = client({'kind': 'member', 'token': gigs['alice']['join_token']})
    b1, b1got = client({'kind': 'member', 'token': gigs['bob']['join_token']})
    check(a1.transport() == 'websocket', 'member connected over a real WebSocket')
    ctl, ctlgot = client({'kind': 'control', 'gig': gigs['alice']['id']},
                         cookies=leaders['alice'].cookies.get_dict())
    check(ctl.connected, 'leader control socket authenticates with session cookie')

    a1.emit('register', {'name': 'BASS', 'role': 'sender'})
    a2.emit('register', {'name': 'KEYS'})
    b1.emit('register', {'name': 'BASS', 'role': 'sender'})
    check(wait_for(a1got, lambda e: e[0] == 'register-accepted'), 'A BASS registered')
    check(wait_for(b1got, lambda e: e[0] == 'register-accepted'), 'B BASS registered (same name, other gig)')
    time.sleep(0.3)

    a1.emit('send-message', {'message': 'live A', 'targets': ['all']})
    check(wait_for(a2got, lambda e: e[0] == 'message' and e[1]['message'] == 'live A'), 'A message delivered live')
    check(wait_for(ctlgot, lambda e: e[0] == 'log-entry' and e[1]['message'] == 'live A'), 'A control got log entry')
    time.sleep(0.5)
    check(not any(e[0] == 'message' for e in b1got), 'gig B received nothing from gig A')
    names_b = [e[1] for e in b1got if e[0] == 'devices-updated']
    check(names_b and all(d['name'] == 'BASS' for d in names_b[-1]), "B's device list never shows A devices")

    try:
        client({'kind': 'member', 'token': gigs['alice']['join_token']}, origin='https://evil.example')
        check(False, 'foreign-origin socket refused')
    except socketio.exceptions.ConnectionError:
        check(True, 'foreign-origin socket refused')

    try:
        client({'kind': 'member', 'token': 'bogus-token-123'})
        check(False, 'bogus link refused')
    except socketio.exceptions.ConnectionError as e:
        check(True, 'bogus link refused over the wire')

    leaders['alice'].post(BASE + f"/api/gigs/{gigs['alice']['id']}/end", json={})
    check(wait_for(a2got, lambda e: e[0] == 'gig-ended'), 'ending the gig notifies members live')
    time.sleep(0.5)
    check(not a2.connected and b1.connected, 'A members disconnected, B untouched')
finally:
    for c in clients:
        try:
            c.disconnect()
        except Exception:
            pass
    proc.terminate()
    out = proc.communicate(timeout=10)[0].decode(errors='replace')
    if 'Traceback' in out:
        print(out[-3000:])
        print('FAIL server logged a traceback')
        raise SystemExit(1)

print(f'\nALL {PASSED} LIVE CHECKS PASSED')
