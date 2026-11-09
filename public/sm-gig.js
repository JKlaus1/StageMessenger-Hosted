// Stage Messenger (hosted) — gig socket helper shared by the member, display and control pages.
// Handles: join token from the URL, the per-device rejoin token, refusal / ended / kicked
// screens, and the reconnect-on-focus cycle (mobile sockets can look connected when they aren't).
(function () {
  'use strict';

  var CSS = '' +
    '#smg-dead{position:fixed;inset:0;z-index:3000;background:#0d0d1a;color:#e0e0f0;display:flex;' +
    'flex-direction:column;align-items:center;justify-content:center;gap:1rem;padding:2rem 1.5rem;text-align:center;' +
    "font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',system-ui,sans-serif;cursor:default}" +
    '#smg-dead .smg-logo{font-size:.8rem;font-weight:900;letter-spacing:.2em;text-transform:uppercase;color:#00ff88}' +
    '#smg-dead h1{font-size:clamp(1.4rem,6vw,2.2rem);font-weight:900;letter-spacing:.04em}' +
    '#smg-dead p{max-width:28rem;color:#9a9ac0;font-size:.95rem;line-height:1.45}' +
    '#smg-dead .smg-actions{display:flex;gap:.6rem;flex-wrap:wrap;justify-content:center;margin-top:.5rem}' +
    '#smg-dead button,#smg-dead a{padding:.8rem 1.4rem;border-radius:12px;border:1px solid #2a2a4a;background:#14142a;' +
    'color:#e0e0f0;font-weight:800;font-size:.8rem;letter-spacing:.12em;text-transform:uppercase;text-decoration:none;cursor:pointer}' +
    '#smg-dead .smg-primary{background:#00ff88;border-color:#00ff88;color:#000}';

  var REASONS = {
    'invalid-link':   ['Link not valid', 'This gig link is invalid, has expired, or was replaced. Ask your band leader for the current link.'],
    'rate-limited':   ['Too many attempts', 'Too many bad links from this network. Wait a few minutes, then try again.'],
    'ended':          ['Gig ended', 'The band leader ended this gig. Thanks for playing!'],
    'ended-by-admin': ['Gig ended', 'This gig has been closed.'],
    'expired':        ['Gig expired', 'This gig was idle for two days and has closed. Ask your band leader for a new link.'],
    'kicked':         ['Removed from gig', 'The band leader removed this device. If that was a mistake, ask them for the link again.'],
    'not-authorized': ['Can’t open Control', 'You are signed out, or this gig has ended.'],
    'signed-out':     ['Signed out', 'Your leader account was signed out on this device.']
  };

  function lsGet(k) { try { return localStorage.getItem(k); } catch (e) { return null; } }
  function lsSet(k, v) { try { if (v == null) localStorage.removeItem(k); else localStorage.setItem(k, v); } catch (e) { /* private mode */ } }

  function tokenFromPath() {
    var m = location.pathname.match(/^\/g\/([^/]+)/);
    return m ? decodeURIComponent(m[1]) : '';
  }

  function showDead(reason, actions) {
    var r = REASONS[reason] || ['Disconnected', 'This gig is no longer available.'];
    var old = document.getElementById('smg-dead');
    if (old) old.remove();
    if (!document.getElementById('smg-css')) {
      var st = document.createElement('style');
      st.id = 'smg-css';
      st.textContent = CSS;
      document.head.appendChild(st);
    }
    var d = document.createElement('div');
    d.id = 'smg-dead';
    var logo = document.createElement('div'); logo.className = 'smg-logo'; logo.textContent = '🎸 Stage Messenger';
    var h = document.createElement('h1'); h.textContent = r[0];
    var p = document.createElement('p'); p.textContent = r[1];
    var row = document.createElement('div'); row.className = 'smg-actions';
    (actions || []).forEach(function (a) {
      var el;
      if (a.href) { el = document.createElement('a'); el.href = a.href; }
      else { el = document.createElement('button'); el.addEventListener('click', a.onClick); }
      el.textContent = a.label;
      if (a.primary) el.className = 'smg-primary';
      row.appendChild(el);
    });
    d.appendChild(logo); d.appendChild(h); d.appendChild(p); d.appendChild(row);
    document.body.appendChild(d);
  }

  function wireFocusReconnect(socket, isDead) {
    document.addEventListener('visibilitychange', function () {
      if (document.visibilityState === 'visible' && !isDead()) {
        socket.disconnect();
        setTimeout(function () { if (!isDead()) socket.connect(); }, 200);
      }
    });
  }

  // kind: 'member' (personal device) or 'display' (stage monitor). Separate rejoin tokens per kind.
  function member(kind) {
    var token = tokenFromPath();
    var key = 'smdev:' + (kind === 'display' ? 'display' : 'member') + ':' + token;
    var dead = false;
    var socket = io({
      auth: function (cb) {
        var dev = lsGet(key);
        cb(dev ? { kind: 'member', token: token, device: dev } : { kind: 'member', token: token });
      }
    });
    function die(reason) {
      dead = true;
      socket.io.opts.reconnection = false;
      socket.disconnect();
      var acts = reason === 'rate-limited' ? [{ label: 'Try again', primary: true, onClick: function () { location.reload(); } }] : [];
      showDead(reason, acts);
    }
    socket.on('register-accepted', function (d) { if (d && d.device) lsSet(key, d.device); });
    socket.on('connect_error', function (err) {
      var r = err && err.message;
      if (r === 'invalid-link' && lsGet(key)) { lsSet(key, null); }      // stale rejoin token
      if (r === 'invalid-link' || r === 'rate-limited') die(r);
    });
    socket.on('gig-ended', function (d) { lsSet(key, null); die((d && d.reason) || 'ended'); });
    socket.on('kicked', function () { lsSet(key, null); die('kicked'); });
    wireFocusReconnect(socket, function () { return dead; });
    socket.smToken = token;
    return socket;
  }

  function control(gid) {
    var dead = false;
    var socket = io({ auth: { kind: 'control', gig: gid } });
    var back = [{ label: 'My gigs', href: '/dashboard', primary: true }, { label: 'Sign in', href: '/login' }];
    function die(reason) {
      dead = true;
      socket.io.opts.reconnection = false;
      socket.disconnect();
      showDead(reason, back);
    }
    socket.on('connect_error', function (err) { if (err && err.message === 'not-authorized') die('not-authorized'); });
    socket.on('gig-ended', function (d) { die((d && d.reason) || 'ended'); });
    socket.on('signed-out', function () { die('signed-out'); });
    wireFocusReconnect(socket, function () { return dead; });
    return socket;
  }

  // ── Reliable send ────────────────────────────────────────────────────────
  // sender(socket, isReady) -> { send(payload, onState), flush(), reset() }
  //   isReady(): socket connected AND (for phones) registered with the server.
  //   onState(state, reason): 'sending' | 'queued' | 'retrying' | 'sent' | 'failed'.
  // Every message carries an id; the server acks it ({ok:true}) and ignores a repeat id, so
  // a resend after a lost ack can never show twice. No ack in 4 s = dead link: force a fresh
  // connection (the page re-registers), then resend once. Offline taps queue for up to 20 s.
  var ACK_MS = 4000, QUEUE_MS = 20000, MAX_TRIES = 2;
  var RETRYABLE = { 'not-registered': 1 };

  function newId() {
    try {
      var b = new Uint8Array(12); crypto.getRandomValues(b);
      return Array.prototype.map.call(b, function (x) { return ('0' + x.toString(16)).slice(-2); }).join('');
    } catch (e) { return Date.now().toString(36) + Math.random().toString(36).slice(2); }
  }

  function sender(socket, isReady) {
    var queue = [];

    function fail(item, reason) {
      clearTimeout(item.qtimer);
      item.done = true;
      item.cb('failed', reason);
    }

    function enqueue(item) {
      queue.push(item);
      clearTimeout(item.qtimer);
      item.qtimer = setTimeout(function () {
        var i = queue.indexOf(item);
        if (i >= 0) { queue.splice(i, 1); fail(item, 'offline'); }
      }, QUEUE_MS);
    }

    function fire(item) {
      clearTimeout(item.qtimer);
      item.tries++;
      item.cb(item.tries === 1 ? 'sending' : 'retrying');
      socket.timeout(ACK_MS).emit('send-message', item.payload, function (err, res) {
        if (item.done) return;
        if (!err && res && res.ok) { item.done = true; item.cb('sent'); return; }
        var reason = err ? 'timeout' : ((res && res.reason) || 'error');
        if (!err && !RETRYABLE[reason]) { fail(item, reason); return; }   // e.g. rate limit, receive-only
        if (item.tries >= MAX_TRIES) { fail(item, reason); return; }
        item.cb('retrying');
        enqueue(item);
        // Dead or unregistered connection: start a fresh one; the page re-registers, then flush().
        socket.disconnect();
        setTimeout(function () { socket.connect(); }, 150);
      });
    }

    function send(payload, onState) {
      var item = { payload: payload, cb: onState || function () {}, tries: 0, done: false };
      payload.id = payload.id || newId();
      if (isReady()) fire(item);
      else { item.cb('queued'); enqueue(item); }
    }

    function flush() {
      while (queue.length && isReady()) fire(queue.shift());
    }

    return { send: send, flush: flush, pending: function () { return queue.length; } };
  }

  // Button feedback for a send: label per state, green on sent, red on failed.
  var LABELS = { sending: 'Sending…', queued: 'Reconnecting… will send', retrying: 'Retrying…',
                 sent: 'Sent ✓', failed: 'Not sent — tap to retry' };
  var REASON_TEXT = { 'not-allowed': 'This device is receive-only', rate: 'Slow down — too many messages',
                      empty: 'Nothing to send', 'no-target': 'Pick who to send to' };

  function buttonState(btn, idleLabel) {
    var t = null;
    return function (state, reason) {
      clearTimeout(t);
      btn.classList.remove('sent', 'failed', 'busy');
      btn.dataset.busy = (state === 'sending' || state === 'queued' || state === 'retrying') ? '1' : '';
      btn.textContent = (state === 'failed' && REASON_TEXT[reason]) ? REASON_TEXT[reason] : LABELS[state];
      if (state === 'sent') { btn.classList.add('sent'); t = setTimeout(function () { btn.textContent = idleLabel; btn.classList.remove('sent'); }, 1200); }
      else if (state === 'failed') { btn.classList.add('failed'); t = setTimeout(function () { btn.textContent = idleLabel; btn.classList.remove('failed'); }, 6000); }
      else btn.classList.add('busy');
    };
  }

  window.SMGig = { member: member, control: control, tokenFromPath: tokenFromPath, showDead: showDead,
                   sender: sender, buttonState: buttonState };
})();
