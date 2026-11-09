"""Headless-browser walkthrough of every page against a real gunicorn server.

    python3 tests/test_browser.py [screenshot_dir]
Needs: pip install playwright requests && playwright install chromium

admin creates a leader → leader signs in, creates a gig, shares the link →
phone joins as a sender, a stage display and a named receiver join →
Control sends to all; the phone sends song + key → Control kicks a device →
the leader rotates the link (old link dead; reloaded phone rejoins with its device token) →
the leader ends the gig (everyone sees the ended screen). Fails on any JS error.
"""
import os
import re
import subprocess
import sys
import tempfile
import time

import requests
from playwright.sync_api import expect, sync_playwright

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
PORT = 8777
BASE = f'http://127.0.0.1:{PORT}'
SHOTS = sys.argv[1] if len(sys.argv) > 1 else None
PASSED = 0
PHONE = {'viewport': {'width': 390, 'height': 844}, 'device_scale_factor': 2, 'is_mobile': True, 'has_touch': True}


def check(cond, label):
    global PASSED
    if not cond:
        print('FAIL', label)
        raise SystemExit(1)
    PASSED += 1
    print('ok  ', label)


def shot(page, name):
    if SHOTS:
        os.makedirs(SHOTS, exist_ok=True)
        page.screenshot(path=os.path.join(SHOTS, name + '.png'))


tmp = tempfile.mkdtemp()
env = dict(os.environ, SM_DEV='1', SM_DATA=tmp, SECRET_KEY='browser-test', PUBLIC_URL=BASE)
sys.path.insert(0, ROOT)
os.environ['SM_DATA'] = tmp
import store  # noqa: E402
store.init()
store.set_admin_password('admin-pass-123')

proc = subprocess.Popen(['gunicorn', '-w', '1', '--threads', '100', '-b', f'127.0.0.1:{PORT}', 'server:app'],
                        cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
errors = []
try:
    for _ in range(50):
        try:
            if requests.get(BASE + '/healthz', timeout=1).ok:
                break
        except requests.RequestException:
            time.sleep(0.2)

    with sync_playwright() as pw:
        browser = pw.chromium.launch()

        def ctx(**kw):
            c = browser.new_context(**kw)
            return c

        def watch(page, tag):
            page.on('pageerror', lambda e: errors.append(f'{tag}: {e}'))
            page.on('console', lambda m: errors.append(f'{tag} console: {m.text}') if m.type == 'error' else None)
            return page

        # ── Landing + admin ──────────────────────────────────────────────
        adm = watch(ctx(**PHONE).new_page(), 'admin')
        adm.goto(BASE + '/')
        expect(adm.get_by_text('Band leader sign in')).to_be_visible()
        shot(adm, '01-landing')
        adm.goto(BASE + '/admin')
        adm.fill('#apw', 'wrong-password')
        adm.click('#login button[type=submit]')
        expect(adm.locator('#login-err')).to_contain_text('Wrong password')
        adm.fill('#apw', 'admin-pass-123')
        adm.click('#login button[type=submit]')
        expect(adm.locator('#main')).to_be_visible()
        check(True, 'admin signs in (wrong password rejected first)')
        adm.fill('#iv-label', 'Alice — RDB')
        adm.click('#invite-form button[type=submit]')
        expect(adm.locator('.smu-box .smu-link')).to_be_visible()
        invite_url = adm.locator('.smu-box .smu-link').inner_text().strip()
        check(re.fullmatch(re.escape(BASE) + r'/invite/[\w-]{22}', invite_url) is not None, 'admin makes a one-time invite link')
        adm.locator('.smu-box button', has_text='QR').click()
        expect(adm.locator('.smu-box .smu-qr img')).to_be_visible()
        shot(adm, '02-admin-invite')
        adm.locator('.smu-box .smu-ok').click()
        expect(adm.locator('#pending')).to_contain_text('Alice — RDB')
        check(True, 'invite shows under pending links')
        shot(adm, '03-admin')

        # ── Leader: invite -> own account -> dashboard ───────────────────
        lc = ctx(**PHONE)
        lead = watch(lc.new_page(), 'leader')
        lead.goto(BASE + '/dashboard')
        check(lead.url.endswith('/login'), 'dashboard redirects to /login when signed out')
        shot(lead, '04-login')
        lead.goto(invite_url)
        expect(lead.locator('#title')).to_contain_text('invited')
        expect(lead.locator('#d')).to_have_value('Alice — RDB')
        lead.fill('#u', 'alice')
        lead.fill('#p1', 'alicepass12')
        lead.fill('#p2', 'alicepass1X')
        lead.click('#go')
        expect(lead.locator('#err')).to_contain_text('match')
        lead.fill('#p2', 'alicepass12')
        shot(lead, '04b-invite')
        lead.click('#go')
        lead.wait_for_url('**/dashboard')
        expect(lead.locator('#who')).to_have_text('Alice — RDB')
        check(True, 'leader creates their own account from the invite and lands on the dashboard')
        reuse = watch(ctx(**PHONE).new_page(), 'reuse')
        reuse.goto(invite_url)
        expect(reuse.locator('#bad')).to_be_visible()
        check(True, 'the invite link is dead after one use')
        reuse.close()
        errors[:] = [e for e in errors if not e.startswith('reuse')]
        adm.click('#refresh-btn')
        expect(adm.locator('#leaders')).to_contain_text('alice')
        expect(adm.locator('#pending-wrap')).to_be_hidden()
        check(True, 'admin sees the new leader; no pending links left')
        lead.fill('#g-name', 'Smith Wedding')
        lead.fill('#g-date', '2026-11-07')
        lead.click('#new-btn')
        url_el = lead.locator('.join-url').first
        expect(url_el).to_be_visible()
        join_url = url_el.inner_text().strip()
        check(re.fullmatch(re.escape(BASE) + r'/g/[\w-]{22}', join_url) is not None, 'gig created; share panel shows 128-bit join link')
        lead.locator('.share .btn', has_text='QR').first.click()
        expect(lead.locator('.qr-box img').first).to_be_visible()
        lead.locator('.sml input[type=text]').first.fill('stage l')
        lead.locator('.sml-roles button', has_text='Stage display').first.click()
        lead.locator('.sml input.sml-mon').first.fill('1')
        disp_url = lead.locator('.sml-url').first.inner_text().strip()
        check(disp_url == join_url + '/display?monitor=1&name=STAGE%20L', 'display link builder output')
        shot(lead, '05-dashboard-share')

        # ── Members ──────────────────────────────────────────────────────
        m1 = watch(ctx(**PHONE).new_page(), 'phone1')
        m1.goto(join_url)
        expect(m1.locator('#join-sub')).to_contain_text('Smith Wedding')
        shot(m1, '06-member-join')
        m1.fill('#name-input', 'bass')
        m1.click('#sender-toggle-wrap')
        m1.click('#join-btn')
        expect(m1.locator('#sender-screen')).not_to_have_class(re.compile('hidden'))
        expect(m1.locator('#sender-id')).to_contain_text('BASS')
        check(True, 'phone joins by link as sender BASS')
        shot(m1, '07-member-sender')

        disp = watch(ctx(viewport={'width': 1280, 'height': 720}).new_page(), 'display')
        disp.goto(disp_url)
        expect(disp.locator('#device-badge')).to_have_text('STAGE L')
        check(True, 'stage display joins via display link')

        m2 = watch(ctx(**PHONE).new_page(), 'phone2')
        m2.goto(join_url + '?name=keys&role=receiver')
        expect(m2.locator('#receiver-screen')).not_to_have_class(re.compile('hidden'))
        expect(m2.locator('#device-badge')).to_have_text('KEYS')
        check(True, 'named link auto-joins KEYS as receiver')

        dup = watch(ctx(**PHONE).new_page(), 'dup')
        dup.goto(join_url)
        dup.fill('#name-input', 'BASS')
        dup.click('#join-btn')
        expect(dup.locator('#join-error')).to_contain_text('already taken')
        check(True, 'duplicate name rejected on the join screen')
        dup.close()

        # ── Control ──────────────────────────────────────────────────────
        song_href = lead.locator('.gig-acts a', has_text='Send songs').first.get_attribute('href')
        check(song_href == join_url + '?name=ALICE%20RDB&role=sender', 'dashboard "Send songs" links to the sender page as the leader')
        lp = watch(lc.new_page(), 'leader-sender')
        lp.goto(song_href)
        expect(lp.locator('#sender-screen')).not_to_have_class(re.compile('hidden'))
        expect(lp.locator('#sender-id')).to_contain_text('ALICE RDB')
        check(True, '"Send songs" opens the song + key page joined as ALICE RDB')
        lp.close()
        lead.locator('.gig-acts a', has_text='Control').first.click()
        lead.wait_for_url('**/c/*')
        expect(lead.locator('#gig-title')).to_have_text('Smith Wedding')
        expect(lead.locator('.device-chip')).to_have_count(3)
        expect(lead.locator('#sender-page-link')).to_have_attribute('href', join_url + '?name=ALICE%20RDB&role=sender')
        check(True, 'Control links to the phone sender page as the leader')
        check(True, 'Control shows gig name and 3 devices')
        shot(lead, '08-control')
        lead.click('#qr-btn')
        expect(lead.locator('#qr-canvas img').first).to_be_visible()
        lead.click('#qr-close-btn')
        lead.fill('#message-input', 'hello band')
        lead.click('#send-btn')
        expect(disp.locator('#msg-screen')).to_have_class(re.compile('visible'))
        expect(disp.locator('#msg-text')).to_have_text('HELLO BAND')
        expect(m2.locator('#msg-text')).to_have_text('HELLO BAND')
        check(True, 'Control message reaches display and phones')
        shot(disp, '09-display-message')
        expect(m1.locator('#msg-text')).to_have_text('HELLO BAND')
        for pg, sel in ((disp, '#msg-screen'), (m1, '#msg-overlay'), (m2, '#msg-overlay'), (lead, '#msg-overlay')):
            pg.click(sel)

        m1.fill('#song-input', 'wonderwall')
        m1.click('.key-btn[data-key="Gb"]')
        m1.click('.majmin-btn[data-mode="min"]')
        m1.click('#song-send-btn')
        expect(disp.locator('#msg-text')).to_have_text('WONDERWALL')
        expect(disp.locator('#msg-key')).to_have_text('KEY OF Gbm')
        check(True, 'phone sends song + key to the display')
        shot(m2, '10-phone-message')
        for pg, sel in ((disp, '#msg-screen'), (m1, '#msg-overlay'), (m2, '#msg-overlay'), (lead, '#msg-overlay')):
            pg.click(sel)

        def dismiss_all():
            for pg, sel in ((disp, '#msg-screen'), (m1, '#msg-overlay'), (m2, '#msg-overlay'), (lead, '#msg-overlay')):
                if pg.locator(sel + '.visible').count():
                    pg.click(sel)

        # ── Stays-up-for picker on the phone ─────────────────────────────
        m2.evaluate("() => { window.__dur = []; socket.on('message', m => window.__dur.push(m.duration)); }")
        m1.evaluate("() => { window.__songHist = [document.getElementById('song-send-btn').textContent]; new MutationObserver(() => window.__songHist.push(document.getElementById('song-send-btn').textContent)).observe(document.getElementById('song-send-btn'), {childList: true, characterData: true, subtree: true}); }")
        m1.click('#dur-row .dur-btn[data-dur="10000"]')
        m1.fill('#sender-msg-input', 'short one')
        m1.click('#sender-send-btn')
        expect(m2.locator('#msg-text')).to_have_text('SHORT ONE')
        check(m2.evaluate('() => window.__dur.at(-1)') == 10000, 'phone picks 10s: message carries 10 s')
        check(m1.evaluate("() => localStorage.getItem('sm-dur')") == '10000', 'duration choice remembered on the phone')
        expect(m1.locator('#sender-send-btn')).to_have_text('Sent ✓')
        check(True, 'button shows Sent ✓ only after the server receipt')
        dismiss_all()
        m1.click('#dur-row .dur-btn[data-dur="0"]')
        m1.fill('#sender-msg-input', 'stay up')
        m1.click('#sender-send-btn')
        expect(m2.locator('#msg-text')).to_have_text('STAY UP')
        check(m2.evaluate('() => window.__dur.at(-1)') == 86400000, 'until dismissed: message stays up (no auto-dismiss)')
        shot(m1, '07b-sender-duration')
        dismiss_all()

        # count how often the display receives a given message
        disp.evaluate("""() => { window.__got = {}; socket.on('message', m => { window.__got[m.message] = (window.__got[m.message] || 0) + 1; }); }""")

        # ── Dead link mid-send (looks connected, packets vanish) ──────────
        # Drop every outgoing packet (messages and heartbeat replies) while the socket still says "connected".
        # Bring the phone to the front first: coming back to a tab triggers its own reconnect,
        # which would replace the patched connection before the send.
        m1.bring_to_front()
        m1.wait_for_function('() => socket.connected', timeout=5000)
        time.sleep(1.5)
        m1.wait_for_function('() => socket.connected && socket.io.engine.transport.name === "websocket"', timeout=5000)
        m1.evaluate("() => { window.__dropped = 0; const ws = socket.io.engine.transport.ws; ws.send = () => { window.__dropped++; }; window.__sid0 = socket.id; window.__st = []; new MutationObserver(() => window.__st.push(Math.round(performance.now()) + ':' + document.getElementById('sender-send-btn').textContent)).observe(document.getElementById('sender-send-btn'), {childList: true, characterData: true, subtree: true}); }")
        check(m1.evaluate('() => socket.connected') is True, 'simulated dead link still looks connected')
        m1.fill('#sender-msg-input', 'through the outage')
        m1.click('#sender-send-btn')
        # Retrying… can last well under a second, so read the button's recorded history.
        m1.wait_for_function("() => window.__st.some(x => x.endsWith('Sent ✓'))", timeout=15000)
        expect(disp.locator('#msg-text')).to_have_text('THROUGH THE OUTAGE', timeout=5000)
        dbg = m1.evaluate('() => ({dropped: window.__dropped, sid0: window.__sid0, sid: socket.id, st: window.__st.map(x => x.split(":").slice(1).join(":"))})')
        check(dbg['dropped'] >= 1 and dbg['sid'] != dbg['sid0'], 'first send was lost on the dead link; page reconnected on its own')
        check(dbg['st'][:2] == ['Sending…', 'Retrying…'] and 'Sent ✓' in dbg['st'], 'button went Sending… → Retrying… → Sent ✓')
        time.sleep(1)
        check(disp.evaluate("() => window.__got['through the outage']") == 1, 'dead connection: auto-reconnect + resend, shown exactly once')
        dismiss_all()

        # ── Tapped while offline: queued, sent on reconnect ───────────────
        m1.bring_to_front()
        time.sleep(1.5)
        m1.evaluate('() => socket.disconnect()')
        m1.fill('#sender-msg-input', 'queued while offline')
        m1.click('#sender-send-btn')
        expect(m1.locator('#sender-send-btn')).to_have_text('Reconnecting… will send')
        time.sleep(1)
        check(disp.evaluate("() => window.__got['queued while offline']") is None, 'nothing sent while offline')
        m1.evaluate('() => socket.connect()')
        expect(disp.locator('#msg-text')).to_have_text('QUEUED WHILE OFFLINE', timeout=10000)
        expect(m1.locator('#sender-send-btn')).to_have_text('Sent ✓', timeout=5000)
        time.sleep(1)
        check(disp.evaluate("() => window.__got['queued while offline']") == 1, 'offline tap is delivered once after reconnect')
        hist = m1.evaluate('() => window.__songHist')
        check('Sending…' not in hist, f'song button never fired during quick-message sends ({hist})')
        dismiss_all()

        lead.fill('#song-input', 'sweet caroline')
        lead.click('#song-section .key-btn[data-key="B"]')
        lead.click('#song-send-btn')
        expect(disp.locator('#msg-text')).to_have_text('SWEET CAROLINE')
        expect(disp.locator('#msg-key')).to_have_text('KEY OF B')
        expect(m2.locator('#msg-key')).to_have_text('KEY OF B')
        check(True, 'Control sends song + key to everyone')
        for pg, sel in ((disp, '#msg-screen'), (m1, '#msg-overlay'), (m2, '#msg-overlay'), (lead, '#msg-overlay')):
            pg.click(sel)
        lead.locator('.device-chip', has_text='STAGE L').click()
        lead.fill('#song-input', 'hey jude')
        lead.click('#song-section .majmin-btn[data-mode="min"]')
        lead.click('#song-section .key-btn[data-key="F"]')
        lead.click('#song-send-btn')
        expect(disp.locator('#msg-key')).to_have_text('KEY OF Fm')
        time.sleep(0.7)
        check(m2.locator('#msg-overlay.visible').count() == 0, 'song sent to one monitor reaches only that monitor')
        shot(lead, '08b-control-song')
        disp.click('#msg-screen')
        lead.click('#msg-overlay')
        lead.click('#all-chip')
        lead.locator('.device-chip', has_text='KEYS').locator('.chip-kick').click()
        expect(lead.locator('.smu-box')).to_contain_text('Remove KEYS?')
        lead.locator('.smu-box .smu-danger').click()
        expect(m2.locator('#smg-dead')).to_contain_text('Removed from gig')
        expect(lead.locator('.device-chip')).to_have_count(2)
        check(True, 'kick removes KEYS; their phone shows the removed screen')
        shot(m2, '11-kicked')

        # ── Rotate link ──────────────────────────────────────────────────
        dash = watch(lc.new_page(), 'dash2')
        dash.goto(BASE + '/dashboard')
        dash.locator('.gig-acts .btn', has_text='Share').click()
        dash.locator('.share .btn', has_text='Rotate link').click()
        dash.locator('.smu-box .smu-ok').click()
        expect(dash.locator('.join-url').first).not_to_have_text(join_url)
        new_url = dash.locator('.join-url').first.inner_text().strip()
        check(new_url != join_url, 'rotate issues a new join link')

        stranger = watch(ctx(**PHONE).new_page(), 'stranger')
        stranger.goto(join_url)
        expect(stranger.locator('#smg-dead')).to_contain_text('Link not valid')
        check(True, 'old link shows "Link not valid" after rotation')
        shot(stranger, '12-invalid-link')
        errors[:] = [e for e in errors if not e.startswith('stranger')]    # expected refused-socket console noise

        m1.reload()
        m1.fill('#name-input', 'BASS')
        m1.click('#sender-toggle-wrap')
        m1.click('#join-btn')
        expect(m1.locator('#sender-id')).to_contain_text('BASS')
        check(m1.locator('#smg-dead').count() == 0, 'reloaded phone on the old link rejoins with its device token')

        # ── End gig ──────────────────────────────────────────────────────
        dash.locator('.share .btn', has_text='End gig').click()
        dash.locator('.smu-box .smu-danger').click()
        expect(m1.locator('#smg-dead')).to_contain_text('Gig ended')
        expect(disp.locator('#smg-dead')).to_contain_text('Gig ended')
        expect(lead.locator('#smg-dead')).to_contain_text('Gig ended')
        expect(dash.locator('#none')).to_be_visible()
        expect(dash.locator('#ended')).to_contain_text('Smith Wedding')
        check(True, 'ending the gig shows the ended screen everywhere; dashboard lists it as ended')
        shot(m1, '13-ended')
        shot(dash, '14-dashboard-after')
        browser.close()
finally:
    proc.terminate()
    out = proc.communicate(timeout=10)[0].decode(errors='replace')

noise = re.compile(r'Failed to load resource.*(401|400)|WebSocket connection .* failed')
real = [e for e in errors if not noise.search(e)]
if real:
    print('\n'.join(real))
    print('FAIL JS errors above')
    raise SystemExit(1)
check('Traceback' not in out, 'server logged no tracebacks')
print(f'\nALL {PASSED} BROWSER CHECKS PASSED')
