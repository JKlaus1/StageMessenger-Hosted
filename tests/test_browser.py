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
        adm.fill('#nl-user', 'alice')
        adm.fill('#nl-name', 'Alice — RDB')
        adm.click('#nl-gen')
        pw_val = adm.input_value('#nl-pw')
        check(re.fullmatch(r'\w{4}-\w{4}-\w{4}', pw_val) is not None, 'generate password fills the field')
        adm.click('#new-leader button[type=submit]')
        expect(adm.locator('.smu-box')).to_contain_text('Username: alice')
        shot(adm, '02-admin-created')
        adm.locator('.smu-box .smu-ok').click()
        expect(adm.locator('#leaders')).to_contain_text('alice')
        check(True, 'admin creates leader; credentials dialog shown')
        shot(adm, '03-admin')

        # ── Leader: login, create gig, share ─────────────────────────────
        lc = ctx(**PHONE)
        lead = watch(lc.new_page(), 'leader')
        lead.goto(BASE + '/dashboard')
        check(lead.url.endswith('/login'), 'dashboard redirects to /login when signed out')
        shot(lead, '04-login')
        lead.fill('#u', 'alice')
        lead.fill('#p', pw_val)
        lead.click('#go')
        lead.wait_for_url('**/dashboard')
        expect(lead.locator('#who')).to_have_text('Alice — RDB')
        check(True, 'leader signs in with the admin-issued password')
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
        lead.locator('a', has_text='Open Control').first.click()
        lead.wait_for_url('**/c/*')
        expect(lead.locator('#gig-title')).to_have_text('Smith Wedding')
        expect(lead.locator('.device-chip')).to_have_count(3)
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
        dash.locator('.gig-acts .btn', has_text='Share link').click()
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
