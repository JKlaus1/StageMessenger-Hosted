// Stage Messenger (hosted) — named-link / stage-display link builder (dashboard + Control).
// SMLinks.mount(container, getToken, gigName): getToken() returns the gig's current join token,
// so links built after a rotation use the new one. Needs sm-ui.js and qrcode.min.js.
(function () {
  'use strict';

  var CSS = '' +
    '.sml{display:flex;flex-direction:column;gap:.5rem}' +
    '.sml-roles{display:flex;gap:.35rem}' +
    '.sml-roles button{flex:1;padding:.5rem 0;background:#14142a;border:1px solid #2a2a4a;border-radius:8px;color:#55557a;' +
    'font-size:.68rem;font-weight:700;letter-spacing:.06em;cursor:pointer;-webkit-tap-highlight-color:transparent}' +
    '.sml-roles button.on{border-color:#00ff88;color:#00ff88;background:rgba(0,255,136,.07)}' +
    '.sml-fields{display:flex;gap:.35rem}' +
    '.sml input{flex:1;min-width:0;padding:.7rem .85rem;background:#14142a;border:2px solid #2a2a4a;border-radius:10px;color:#fff;' +
    'font-size:.9rem;font-weight:700;letter-spacing:.06em;text-transform:uppercase;outline:none;-webkit-appearance:none}' +
    '.sml input:focus{border-color:#00ff88}' +
    '.sml input.sml-mon{flex:0 0 5.5rem;text-align:center}' +
    '.sml-url{word-break:break-all;font-size:.72rem;color:#00ff88;background:#14142a;border:1px solid #2a2a4a;border-radius:10px;' +
    'padding:.7rem .9rem;font-family:monospace}' +
    '.sml-acts{display:flex;gap:.35rem}' +
    '.sml-acts button{flex:1;padding:.55rem;background:transparent;border:1px solid #2a2a4a;border-radius:10px;color:#9a9ac0;' +
    'font-size:.65rem;font-weight:700;letter-spacing:.1em;text-transform:uppercase;cursor:pointer}' +
    '.sml-qr{text-align:center}' +
    '.sml-qr > div{display:inline-block;background:#fff;padding:10px;border-radius:12px;line-height:0}' +
    '.sml-hint{font-size:.68rem;color:#55557a;line-height:1.4}';

  function mount(container, getToken, gigName) {
    if (!document.getElementById('sml-css')) {
      var st = document.createElement('style'); st.id = 'sml-css'; st.textContent = CSS; document.head.appendChild(st);
    }
    var E = SMUI.el, role = 'receiver';
    var wrap = E('div', { class: 'sml' });
    var roles = E('div', { class: 'sml-roles' });
    var defs = [['receiver', 'Receive only'], ['sender', 'Can send'], ['display', 'Stage display']];
    var btns = {};
    defs.forEach(function (d) {
      var b = E('button', { type: 'button', onclick: function () { setRole(d[0]); } }, d[1]);
      btns[d[0]] = b; roles.appendChild(b);
    });
    var fields = E('div', { class: 'sml-fields' });
    var name = E('input', { type: 'text', maxlength: '16', placeholder: 'Name…', autocomplete: 'off',
                            autocapitalize: 'characters', spellcheck: 'false' });
    var mon = E('input', { type: 'number', class: 'sml-mon', min: '0', max: '999', placeholder: 'Mon #', inputmode: 'numeric' });
    fields.appendChild(name); fields.appendChild(mon);
    var hint = E('div', { class: 'sml-hint' });
    var url = E('div', { class: 'sml-url' });
    var acts = E('div', { class: 'sml-acts' });
    var qrWrap = E('div', { class: 'sml-qr' }); var qrBox = E('div'); qrWrap.appendChild(qrBox);
    qrWrap.style.display = 'none';
    var current = '';
    acts.appendChild(E('button', { type: 'button', onclick: function () { if (build()) SMUI.copy(current); } }, 'Copy'));
    acts.appendChild(E('button', { type: 'button', onclick: function () {
      if (build()) SMUI.share(current, 'Stage Messenger', (gigName ? gigName + ' — ' : '') + 'your Stage Messenger link');
    } }, 'Share'));
    acts.appendChild(E('button', { type: 'button', onclick: function () {
      if (!build()) return;
      var show = qrWrap.style.display === 'none';
      qrWrap.style.display = show ? 'block' : 'none';
      if (show) SMUI.qr(qrBox, current, 200);
    } }, 'QR'));
    [roles, fields, hint, url, acts, qrWrap].forEach(function (n) { wrap.appendChild(n); });
    container.appendChild(wrap);

    function setRole(r) {
      role = r;
      Object.keys(btns).forEach(function (k) { btns[k].classList.toggle('on', k === r); });
      mon.style.display = r === 'display' ? '' : 'none';
      name.placeholder = r === 'display' ? 'Screen name, e.g. STAGE L' : 'Name…';
      hint.textContent = r === 'display'
        ? 'Open this on the monitor screen and bookmark it. Each screen gets its own number so Control can target it.'
        : 'Opens straight into the gig with this name' + (r === 'sender' ? ', able to send cues.' : '.');
      build();
    }

    function build() {
      var n = name.value.trim().toUpperCase();
      var tok = getToken();
      qrWrap.style.display = 'none';
      if (!tok) { url.textContent = 'Gig link unavailable'; current = ''; return false; }
      if (!n) { url.textContent = 'Enter a name to build a link'; current = ''; return false; }
      var base = location.origin + '/g/' + encodeURIComponent(tok);
      if (role === 'display') {
        var m = parseInt(mon.value, 10);
        if (isNaN(m) || m < 0 || m > 999) { url.textContent = 'Enter a monitor number (0–999)'; current = ''; return false; }
        current = base + '/display?monitor=' + m + '&name=' + encodeURIComponent(n);
      } else {
        current = base + '?name=' + encodeURIComponent(n) + '&role=' + role;
      }
      url.textContent = current;
      return true;
    }

    name.addEventListener('input', build);
    mon.addEventListener('input', build);
    setRole('receiver');
    return { refresh: build };
  }

  window.SMLinks = { mount: mount };
})();
