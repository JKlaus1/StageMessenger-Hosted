// Stage Messenger (hosted) — small UI kit for the leader/admin pages and Control:
// JSON API calls, in-page confirm/prompt dialogs (no native dialogs), copy, share, QR, toast.
(function () {
  'use strict';

  var CSS = '' +
    '.smu-back{position:fixed;inset:0;z-index:2500;background:rgba(0,0,0,.78);display:flex;align-items:center;justify-content:center;padding:1rem}' +
    '.smu-box{width:min(92vw,400px);background:#14142a;border:1px solid #2a2a4a;border-radius:16px;padding:1.25rem;color:#e0e0f0;' +
    "font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',system-ui,sans-serif}" +
    '.smu-box h2{font-size:1rem;font-weight:900;letter-spacing:.06em;margin-bottom:.5rem}' +
    '.smu-box p{font-size:.85rem;color:#9a9ac0;line-height:1.45;margin-bottom:1rem;white-space:pre-line}' +
    '.smu-box input{width:100%;padding:.8rem .9rem;background:#0d0d1a;border:2px solid #2a2a4a;border-radius:10px;color:#fff;' +
    'font-size:1rem;outline:none;margin-bottom:1rem}' +
    '.smu-box input:focus{border-color:#00ff88}' +
    '.smu-row{display:flex;gap:.5rem;justify-content:flex-end}' +
    '.smu-row button{padding:.7rem 1.1rem;border-radius:10px;border:1px solid #2a2a4a;background:transparent;color:#9a9ac0;' +
    'font-weight:800;font-size:.75rem;letter-spacing:.1em;text-transform:uppercase;cursor:pointer}' +
    '.smu-row .smu-ok{background:#00ff88;border-color:#00ff88;color:#000}' +
    '.smu-row .smu-danger{background:#cc2233;border-color:#cc2233;color:#fff}' +
    '.smu-toast{position:fixed;left:50%;bottom:1.5rem;transform:translateX(-50%);z-index:2600;background:#00ff88;color:#000;' +
    'padding:.6rem 1.1rem;border-radius:999px;font-weight:800;font-size:.75rem;letter-spacing:.08em;' +
    "font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',system-ui,sans-serif;box-shadow:0 4px 20px rgba(0,0,0,.4)}" +
    '.smu-toast.err{background:#cc2233;color:#fff}' +
    '.smu-link{font-family:ui-monospace,Menlo,Consolas,monospace;word-break:break-all;font-size:.75rem;color:#00ff88;' +
    'background:#0d0d1a;border:1px solid #2a2a4a;border-radius:10px;padding:.7rem .85rem;margin-bottom:.75rem;user-select:all}' +
    '.smu-qr{text-align:center;margin-bottom:.75rem}' +
    '.smu-qr > div{display:inline-block;background:#fff;padding:10px;border-radius:12px;line-height:0}' +
    '.smu-row.smu-wrap{flex-wrap:wrap}';

  function injectCss() {
    if (document.getElementById('smu-css')) return;
    var st = document.createElement('style');
    st.id = 'smu-css';
    st.textContent = CSS;
    document.head.appendChild(st);
  }

  function el(tag, attrs, text) {
    var e = document.createElement(tag);
    if (attrs) Object.keys(attrs).forEach(function (k) {
      if (k === 'class') e.className = attrs[k];
      else if (k.indexOf('on') === 0) e.addEventListener(k.slice(2), attrs[k]);
      else e.setAttribute(k, attrs[k]);
    });
    if (text != null) e.textContent = text;
    return e;
  }

  // ── API ──────────────────────────────────────────────────────────────────
  function api(path, body) {
    var opt = { credentials: 'same-origin', headers: {} };
    if (body !== undefined) {
      opt.method = 'POST';
      opt.headers['Content-Type'] = 'application/json';
      opt.body = JSON.stringify(body || {});
    }
    return fetch(path, opt).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (data) {
        return { ok: r.ok, status: r.status, data: data, error: data.error || (r.ok ? '' : 'Request failed (' + r.status + ')') };
      });
    }).catch(function () {
      return { ok: false, status: 0, data: {}, error: 'Network error — check your connection.' };
    });
  }

  // ── Dialogs ──────────────────────────────────────────────────────────────
  function dialog(opts) {
    injectCss();
    return new Promise(function (resolve) {
      var back = el('div', { class: 'smu-back' });
      var box = el('div', { class: 'smu-box', role: 'dialog', 'aria-modal': 'true' });
      box.appendChild(el('h2', null, opts.title || ''));
      if (opts.body) box.appendChild(el('p', null, opts.body));
      var input = null;
      if (opts.input) {
        input = el('input', { type: opts.input.type || 'text', placeholder: opts.input.placeholder || '',
                              autocomplete: 'off', autocapitalize: 'off', spellcheck: 'false' });
        input.value = opts.input.value || '';
        box.appendChild(input);
      }
      var row = el('div', { class: 'smu-row' });
      function done(v) { back.remove(); document.removeEventListener('keydown', onKey); resolve(v); }
      function ok() { done(input ? input.value : true); }
      function onKey(e) { if (e.key === 'Escape') done(input ? null : false); if (e.key === 'Enter' && input) ok(); }
      row.appendChild(el('button', { onclick: function () { done(input ? null : false); } }, opts.cancel || 'Cancel'));
      row.appendChild(el('button', { class: opts.danger ? 'smu-danger' : 'smu-ok', onclick: ok }, opts.ok || 'OK'));
      box.appendChild(row);
      back.appendChild(box);
      back.addEventListener('click', function (e) { if (e.target === back) done(input ? null : false); });
      document.addEventListener('keydown', onKey);
      document.body.appendChild(back);
      if (input) setTimeout(function () { input.focus(); input.select(); }, 30);
    });
  }

  function confirmBox(title, body, okLabel, danger) {
    return dialog({ title: title, body: body, ok: okLabel || 'OK', danger: !!danger });
  }

  function promptBox(title, body, value, type, okLabel) {
    return dialog({ title: title, body: body, ok: okLabel || 'Save', input: { value: value || '', type: type } });
  }

  function alertBox(title, body, copyable) {
    injectCss();
    return new Promise(function (resolve) {
      var back = el('div', { class: 'smu-back' });
      var box = el('div', { class: 'smu-box', role: 'dialog' });
      box.appendChild(el('h2', null, title));
      if (body) box.appendChild(el('p', null, body));
      var row = el('div', { class: 'smu-row' });
      if (copyable) row.appendChild(el('button', { onclick: function () { copyText(copyable); } }, 'Copy'));
      row.appendChild(el('button', { class: 'smu-ok', onclick: function () { back.remove(); resolve(); } }, 'OK'));
      box.appendChild(row);
      back.appendChild(box);
      document.body.appendChild(back);
    });
  }

  // One-time link dialog: the link, Copy / Share / QR, Done.
  function linkBox(title, body, url, shareText) {
    injectCss();
    return new Promise(function (resolve) {
      var back = el('div', { class: 'smu-back' });
      var box = el('div', { class: 'smu-box', role: 'dialog', 'aria-modal': 'true' });
      box.appendChild(el('h2', null, title));
      if (body) box.appendChild(el('p', null, body));
      box.appendChild(el('div', { class: 'smu-link' }, url));
      var qrWrap = el('div', { class: 'smu-qr' }); var qrIn = el('div'); qrWrap.appendChild(qrIn);
      qrWrap.style.display = 'none';
      box.appendChild(qrWrap);
      var row = el('div', { class: 'smu-row smu-wrap' });
      row.appendChild(el('button', { onclick: function () { copyText(url); } }, 'Copy'));
      row.appendChild(el('button', { onclick: function () { shareLink(url, 'Stage Messenger', shareText || title); } }, 'Share'));
      row.appendChild(el('button', { onclick: function () {
        var show = qrWrap.style.display === 'none';
        qrWrap.style.display = show ? 'block' : 'none';
        if (show && !qrIn.firstChild) qr(qrIn, url, 200);
      } }, 'QR'));
      row.appendChild(el('button', { class: 'smu-ok', onclick: function () { back.remove(); resolve(); } }, 'Done'));
      box.appendChild(row);
      back.appendChild(box);
      document.body.appendChild(back);
    });
  }

  // ── Toast / copy / share / QR ────────────────────────────────────────────
  var toastTimer = null;
  function toast(msg, isErr) {
    injectCss();
    var t = document.querySelector('.smu-toast');
    if (!t) { t = el('div', { class: 'smu-toast' }); document.body.appendChild(t); }
    t.textContent = msg;
    t.classList.toggle('err', !!isErr);
    t.style.display = 'block';
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { t.style.display = 'none'; }, 2200);
  }

  function copyText(text) {
    function fallback() {
      var ta = el('textarea');
      ta.value = text;
      ta.style.position = 'fixed'; ta.style.opacity = '0';
      document.body.appendChild(ta);
      ta.select();
      var ok = false;
      try { ok = document.execCommand('copy'); } catch (e) { ok = false; }
      ta.remove();
      return ok;
    }
    var p = (navigator.clipboard && window.isSecureContext)
      ? navigator.clipboard.writeText(text).then(function () { return true; }, fallback)
      : Promise.resolve(fallback());
    return p.then(function (ok) { toast(ok ? 'Copied' : 'Copy failed — long-press the link', !ok); return ok; });
  }

  function shareLink(url, title, text) {
    if (navigator.share) {
      return navigator.share({ title: title, text: text, url: url }).catch(function () { /* cancelled */ });
    }
    return copyText(url);
  }

  function qr(container, text, size) {
    container.innerHTML = '';
    new QRCode(container, { text: text, width: size || 200, height: size || 200,
      colorDark: '#000000', colorLight: '#ffffff', correctLevel: QRCode.CorrectLevel.M });
  }

  function randomPassword() {
    var a = 'abcdefghjkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789';
    var b = new Uint32Array(12);
    crypto.getRandomValues(b);
    var s = '';
    for (var i = 0; i < b.length; i++) s += a[b[i] % a.length];
    return s.slice(0, 4) + '-' + s.slice(4, 8) + '-' + s.slice(8, 12);
  }

  function fmtAgo(epochS) {
    if (!epochS) return 'never';
    var d = Math.max(0, Date.now() / 1000 - epochS);
    if (d < 90) return 'just now';
    if (d < 5400) return Math.round(d / 60) + ' min ago';
    if (d < 129600) return Math.round(d / 3600) + ' h ago';
    return Math.round(d / 86400) + ' days ago';
  }

  // A leader's name as a Stage Messenger device name: A-Z 0-9 space _ -, max 16.
  function senderName(display, username) {
    function clean(s) { return String(s || '').toUpperCase().replace(/[^A-Z0-9 _-]+/g, ' ').replace(/\s+/g, ' ').trim().slice(0, 16).trim(); }
    return clean(display) || clean(username) || 'LEADER';
  }

  function senderUrl(token, name) {
    return location.origin + '/g/' + encodeURIComponent(token) + '?name=' + encodeURIComponent(name) + '&role=sender';
  }

  window.SMUI = { linkBox: linkBox, senderName: senderName, senderUrl: senderUrl, api: api, el: el, confirm: confirmBox, prompt: promptBox, alert: alertBox, toast: toast,
                  copy: copyText, share: shareLink, qr: qr, randomPassword: randomPassword, ago: fmtAgo };
})();
