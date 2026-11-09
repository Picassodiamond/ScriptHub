/* ScriptHub — workbench shell: flashes, confirms, theme, side panel,
   explorer tree, keyboard shortcuts (data-hotkey), command palette (Ctrl+K)
   and the shortcut sheet (?). Vanilla JS, no dependencies. */
(function () {
  'use strict';
  var root = document.documentElement;
  var sr = window.sr = window.sr || {};

  function store(key, value) {
    try { if (value === undefined) return localStorage.getItem(key); localStorage.setItem(key, value); } catch (e) {}
    return null;
  }
  function esc(s) { return String(s).replace(/[&<>"]/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]; }); }
  function visible(el) { return !!(el && (el.offsetWidth || el.offsetHeight || el.getClientRects().length)) && !el.closest('[hidden]'); }

  // ------------------------------------------------------------ clipboard --
  sr.copy = function (text, label) {
    function done() { if (label) { var old = label.textContent; label.textContent = 'Copied'; setTimeout(function () { label.textContent = old; }, 1400); } }
    if (navigator.clipboard && window.isSecureContext) { navigator.clipboard.writeText(text).then(done); return; }
    var ta = document.createElement('textarea'); ta.value = text; ta.style.position = 'fixed'; ta.style.opacity = '0';
    document.body.appendChild(ta); ta.select();
    try { document.execCommand('copy'); done(); } catch (e) {}
    ta.remove();
  };
  document.addEventListener('click', function (e) {
    var b = e.target.closest('[data-copy-text]');
    if (b) sr.copy(b.getAttribute('data-copy-text'), b.querySelector('span'));
  });

  // -------------------------------------------------------------- flashes --
  document.addEventListener('click', function (e) {
    var b = e.target.closest('[data-dismiss]');
    if (b) b.closest('.flash').remove();
  });
  [].forEach.call(document.querySelectorAll('.flash[data-autohide]'), function (el) {
    setTimeout(function () { el.classList.add('flash-out'); setTimeout(function () { el.remove(); }, 350); }, 5000);
  });

  // ------------------------------------------------ confirm / submit once --
  document.addEventListener('submit', function (e) {
    var form = e.target, msg = form.getAttribute('data-confirm');
    if (msg && !window.confirm(msg)) { e.preventDefault(); return; }
    // form.elements includes buttons outside the form that point at it with form="…"
    [].forEach.call(form.elements, function (b) {
      if (b.hasAttribute && b.hasAttribute('data-once')) setTimeout(function () { b.disabled = true; }, 0);
    });
  });

  // -------------------------------------------------- theme & side panel --
  function toggleTheme() {
    var t = root.getAttribute('data-theme') === 'light' ? 'dark' : 'light';
    root.setAttribute('data-theme', t);
    store('sr.theme', t);
  }
  var narrow = window.matchMedia('(max-width: 860px)');
  function toggleSide(force) {
    if (narrow.matches) { root.classList.toggle('side-open', force); return; }
    var off = !root.classList.contains('side-off');
    if (typeof force === 'boolean') off = !force;
    root.classList.toggle('side-off', off);
    store('sr.side', off ? '0' : '1');
  }
  // Right column of the script pages (Overview / Parameters / Run).
  function toggleSide2() {
    var off = !root.classList.contains('side2-off');
    root.classList.toggle('side2-off', off);
    store('sr.side2', off ? '0' : '1');
  }
  // Bottom panel of the script pages (saved values): Ctrl+J, its header, drag its top edge.
  function toggleBottom(force) {
    var on = typeof force === 'boolean' ? force : !root.classList.contains('bottom-on');
    root.classList.toggle('bottom-on', on);
    store('sr.bottom', on ? '1' : '0');
    var head = document.querySelector('.bp-head');
    if (head) head.setAttribute('aria-expanded', on ? 'true' : 'false');
  }
  (function () {
    var panel = document.getElementById('saved-panel');
    if (!panel) return;
    if (panel.hasAttribute('data-force-open')) toggleBottom(true);      // ?tab=params
    else toggleBottom(root.classList.contains('bottom-on'));
    var grip = panel.querySelector('[data-bp-resize]');
    if (!grip) return;
    grip.addEventListener('pointerdown', function (e) {
      e.preventDefault();
      var body = panel.querySelector('.bp-body'), startY = e.clientY, startH = body.getBoundingClientRect().height;
      grip.setPointerCapture(e.pointerId);
      function move(ev) {
        var h = Math.max(110, Math.min(window.innerHeight * 0.75, startH + (startY - ev.clientY)));
        root.style.setProperty('--bp-h', Math.round(h) + 'px');
      }
      function up() {
        grip.removeEventListener('pointermove', move);
        grip.removeEventListener('pointerup', up);
        store('sr.bottomH', String(parseInt(getComputedStyle(root).getPropertyValue('--bp-h'), 10) || ''));
      }
      grip.addEventListener('pointermove', move);
      grip.addEventListener('pointerup', up);
    });
  })();
  document.addEventListener('click', function (e) {
    var a = e.target.closest('[data-action]');
    if (!a) return;
    var act = a.getAttribute('data-action');
    if (act === 'theme') toggleTheme();
    else if (act === 'side') toggleSide();
    else if (act === 'side2') toggleSide2();
    else if (act === 'bottom') toggleBottom();
    else if (act === 'palette') palette.open();
    else if (act === 'help') help.open();
  });

  // --------------------------------------------------------------- tree --
  var tree = document.getElementById('tree');
  if (tree) {
    var groups = [].slice.call(tree.querySelectorAll('.tree-group'));
    var closed = {};
    try { closed = JSON.parse(store('sr.tree.closed') || '{}') || {}; } catch (e) { closed = {}; }
    groups.forEach(function (g) {
      if (closed[g.dataset.group] && !g.querySelector('.is-active')) g.open = false;
      g.addEventListener('toggle', function () {
        if (g.dataset.filtering) return;
        if (g.open) delete closed[g.dataset.group]; else closed[g.dataset.group] = 1;
        store('sr.tree.closed', JSON.stringify(closed));
      });
    });
    var activeItem = tree.querySelector('.tree-item.is-active');
    if (activeItem && activeItem.scrollIntoView) activeItem.scrollIntoView({ block: 'center' });
    var filter = document.getElementById('tree-filter'), noneEl = document.getElementById('tree-none');
    if (filter) filter.addEventListener('input', function () {
      var q = filter.value.trim().toLowerCase(), any = false;
      groups.forEach(function (g) {
        var hits = 0;
        [].forEach.call(g.querySelectorAll('.tree-item'), function (it) {
          var ok = !q || it.dataset.search.indexOf(q) !== -1 || g.dataset.group.toLowerCase().indexOf(q) !== -1;
          it.hidden = !ok; if (ok) hits++;
        });
        g.hidden = hits === 0; any = any || hits > 0;
        g.dataset.filtering = '1'; if (q) g.open = true; else g.open = !closed[g.dataset.group] || !!g.querySelector('.is-active');
        delete g.dataset.filtering;
      });
      if (noneEl) noneEl.hidden = any || !groups.length;
    });
    if (filter) filter.addEventListener('keydown', function (e) {
      if (e.key === 'Escape') { filter.value = ''; filter.dispatchEvent(new Event('input')); filter.blur(); }
      if (e.key === 'Enter') { var first = tree.querySelector('.tree-item:not([hidden])'); if (first) first.click(); }
    });
    document.addEventListener('click', function (e) {    // explorer header icon or View > Collapse explorer
      if (e.target.closest && e.target.closest('[data-tree-collapse]')) groups.forEach(function (g) { g.open = false; });
    });
  }

  // ----------------------------------------------- sequences side panel --
  // Sequences pages: the sequences (new / rename inline, filter, ★) above the scripts.
  // The Sequences section folds; the divider resizes the two (sr.seqFold, sr.seqPaneH).
  var seqSide = document.getElementById('seq-side');
  if (seqSide) {
    var seqPane = seqSide.querySelector('.side-pane-seq'), newForm = document.getElementById('seq-new-form');
    var paneH = parseInt(store('sr.seqPaneH'), 10);
    if (paneH > 0) seqSide.style.setProperty('--seq-pane-h', paneH + 'px');
    function fold(on) {
      seqPane.classList.toggle('is-folded', on);
      seqSide.querySelector('[data-seq-fold]').setAttribute('aria-expanded', on ? 'false' : 'true');
    }
    fold(store('sr.seqFold') === '1');
    function showNew() {
      fold(false); store('sr.seqFold', '0');
      newForm.hidden = false;
      var input = newForm.querySelector('input[name=name]'); input.value = ''; input.focus();
    }
    function cancelRename(item) {
      item.classList.remove('is-renaming');
      var f = item.querySelector('.seq-rename-form'); if (f) f.hidden = true;
    }
    seqSide.addEventListener('click', function (e) {
      var t = e.target;
      if (t.closest('[data-seq-fold]')) {
        var on = !seqPane.classList.contains('is-folded'); fold(on); store('sr.seqFold', on ? '1' : '0');
      } else if (t.closest('[data-seq-new]')) {
        showNew();
      } else if (t.closest('[data-seq-rename]')) {
        var item = t.closest('.seq-item'), f = item.querySelector('.seq-rename-form');
        [].forEach.call(seqSide.querySelectorAll('.seq-item.is-renaming'), cancelRename);
        item.classList.add('is-renaming'); f.hidden = false;
        var input = f.querySelector('input[name=name]'); input.focus(); input.select();
      }
    });
    seqSide.addEventListener('keydown', function (e) {
      if (e.key !== 'Escape') return;
      var item = e.target.closest('.seq-item.is-renaming');
      if (item) { e.preventDefault(); cancelRename(item); }
      else if (e.target.closest('#seq-new-form')) { e.preventDefault(); newForm.hidden = true; }
    });
    seqSide.addEventListener('focusout', function (e) {   // leaving an inline field without Enter: no change
      var f = e.target.closest('.seq-rename-form, #seq-new-form');
      if (!f || f.contains(e.relatedTarget)) return;
      setTimeout(function () {
        if (f.contains(document.activeElement)) return;
        if (f.id === 'seq-new-form') { if (!f.querySelector('input').value.trim()) f.hidden = true; }
        else cancelRename(f.closest('.seq-item'));
      }, 150);
    });
    var seqFilter = document.getElementById('seq-filter'), seqNone = document.getElementById('seq-none');
    if (seqFilter) seqFilter.addEventListener('input', function () {
      var q = seqFilter.value.trim().toLowerCase(), any = false;
      [].forEach.call(seqSide.querySelectorAll('.seq-item'), function (it) {
        var ok = !q || it.dataset.search.indexOf(q) !== -1; it.hidden = !ok; any = any || ok;
      });
      if (seqNone) seqNone.hidden = any;
    });
    var divider = document.getElementById('seq-divider');
    if (divider) divider.addEventListener('pointerdown', function (e) {
      if (e.button !== 0) return;
      e.preventDefault();
      fold(false); store('sr.seqFold', '0');
      var top = seqSide.getBoundingClientRect().top, total = seqSide.getBoundingClientRect().height;
      divider.setPointerCapture(e.pointerId); divider.classList.add('is-dragging');
      function move(ev) {
        var h = Math.max(70, Math.min(total - 120, ev.clientY - top));
        seqSide.style.setProperty('--seq-pane-h', h + 'px');
      }
      function up(ev) {
        divider.removeEventListener('pointermove', move); divider.removeEventListener('pointerup', up);
        divider.classList.remove('is-dragging');
        store('sr.seqPaneH', String(parseInt(getComputedStyle(seqPane).height, 10)));
      }
      divider.addEventListener('pointermove', move); divider.addEventListener('pointerup', up);
    });
    var activeSeq = seqSide.querySelector('.seq-item.is-active');
    if (activeSeq && activeSeq.scrollIntoView) activeSeq.scrollIntoView({ block: 'nearest' });
  }

  // ------------------------------------------------------------ hotkeys --
  // Elements declare their own shortcut: data-hotkey="r", "g l" (chord),
  // "Ctrl+Enter", "Shift+F5", "F10|n" (alternatives). Pressing it clicks the element.
  function typing(el) {
    el = el || document.activeElement;
    if (!el) return false;
    if (el.isContentEditable || el.tagName === 'TEXTAREA' || el.tagName === 'SELECT') return true;
    return el.tagName === 'INPUT' && !/^(checkbox|radio|button|submit|reset|range|color|file)$/i.test(el.type);
  }
  function keyName(e) {
    var k = e.key;
    if (k === ' ') k = 'Space';
    if (k.length === 1) k = k.toLowerCase();
    var mods = [];
    if (e.ctrlKey || e.metaKey) mods.push('Ctrl');
    if (e.altKey) mods.push('Alt');
    if (e.shiftKey && (k.length > 1)) mods.push('Shift');   // Shift is implied by "?" etc.
    return mods.concat([k]).join('+');
  }
  function norm(spec) {
    return spec.split('+').map(function (p, i, a) { return i === a.length - 1 && p.length === 1 ? p.toLowerCase() : p; }).join('+');
  }
  function hotkeyTargets() {
    var list = [];
    [].forEach.call(document.querySelectorAll('[data-hotkey]'), function (el) {
      el.getAttribute('data-hotkey').split('|').forEach(function (spec) { list.push({ el: el, spec: norm(spec.trim()) }); });
    });
    return list;
  }
  // Menu bar items keep their shortcuts while their menu is closed.
  function usable(el) { return !el.disabled && !el.classList.contains('btn-disabled') && (visible(el) || el.closest('.activity') || el.closest('.menubar')); }
  var pending = null, pendingTimer = null;

  document.addEventListener('keydown', function (e) {
    if (e.defaultPrevented || e.isComposing) return;
    var name = keyName(e), inField = typing();
    if (overlayOpen()) return;                                  // dialogs handle their own keys
    // Global shortcuts
    if ((name === 'Ctrl+k') || (name === 'Ctrl+Shift+P') || (e.ctrlKey && e.shiftKey && (e.key === 'P' || e.key === 'p'))) {
      e.preventDefault(); palette.open(); return;
    }
    if (name === 'Ctrl+b') { e.preventDefault(); toggleSide(); return; }
    if (name === 'Ctrl+Alt+b') { if (document.querySelector('.split-side')) { e.preventDefault(); toggleSide2(); } return; }
    if (name === 'Ctrl+j') { if (document.getElementById('saved-panel')) { e.preventDefault(); toggleBottom(); } return; }
    var fKey = /^(Shift\+)?F\d+$/.test(name), modded = /^(Ctrl|Alt)\+/.test(name);
    if (inField && !fKey && !modded && name !== 'Escape') return;
    if (inField && name === 'Escape') return;
    if (!inField && !modded && !fKey) {
      if (e.key === '?') { e.preventDefault(); help.open(); return; }
      if (e.key === '/') {
        var s = document.querySelector('[data-page-search]');
        if (!s || !visible(s)) s = document.getElementById('tree-filter');
        if (s && narrow.matches && !visible(s)) toggleSide(true);
        if (s) { e.preventDefault(); s.focus(); s.select && s.select(); }
        return;
      }
    }
    var targets = hotkeyTargets();
    var spec = pending ? pending + ' ' + name : name;
    var exact = targets.filter(function (t) { return t.spec === spec && usable(t.el); });
    var prefix = !pending && targets.some(function (t) { return t.spec.indexOf(spec + ' ') === 0; });
    clearTimeout(pendingTimer); pending = null;
    // F-keys that belong to a (currently disabled) control must not fall through to the browser (F5 = reload).
    if (!exact.length && fKey && targets.some(function (t) { return t.spec === spec; })) { e.preventDefault(); return; }
    if (exact.length) {
      e.preventDefault();
      exact[0].el.click();
    } else if (prefix) {
      e.preventDefault();
      pending = name; pendingTimer = setTimeout(function () { pending = null; }, 1200);
    }
  });

  // ------------------------------------------------------------ overlays --
  var current = null;
  function overlayOpen() { return !!current; }
  function openOverlay(inner, onKey) {
    closeOverlay();
    var ov = document.createElement('div');
    ov.className = 'overlay';
    ov.appendChild(inner);
    ov.addEventListener('mousedown', function (e) { if (e.target === ov) closeOverlay(); });
    ov.addEventListener('keydown', function (e) {
      if (e.key === 'Escape') { e.preventDefault(); closeOverlay(); return; }
      if (onKey) onKey(e);
    });
    document.body.appendChild(ov);
    current = { el: ov, back: document.activeElement };
    return ov;
  }
  function closeOverlay() {
    if (!current) return;
    current.el.remove();
    try { if (current.back) current.back.focus(); } catch (e) {}
    current = null;
  }

  // ---------------------------------------------------- command palette --
  var urls = {};
  try { urls = JSON.parse(document.getElementById('wb-urls').textContent); } catch (e) {}
  function url(kind, id) { return urls[kind] ? urls[kind].replace('XID', encodeURIComponent(id)) : null; }
  var ICON = function (n) { return '<svg class="i"><use href="#i-' + n + '"/></svg>'; };
  // A normal (navigating) POST from script, with the page's CSRF token.
  function postForm(action, data) {
    var f = document.createElement('form'), tok = document.querySelector('input[name=csrfmiddlewaretoken]');
    var m = /(?:^|;\s*)csrftoken=([^;]+)/.exec(document.cookie);
    data = Object.assign({ csrfmiddlewaretoken: tok ? tok.value : (m ? decodeURIComponent(m[1]) : '') }, data);
    f.method = 'post'; f.action = action; f.hidden = true;
    Object.keys(data).forEach(function (k) { var i = document.createElement('input'); i.type = 'hidden'; i.name = k; i.value = data[k]; f.appendChild(i); });
    document.body.appendChild(f); f.submit();
  }

  // Drop-down menus (<details data-menu>): close on outside click and Escape, and after an item
  // is chosen. In the menu bar, hovering another menu while one is open switches to it.
  document.addEventListener('click', function (e) {
    [].forEach.call(document.querySelectorAll('details[data-menu][open]'), function (d) { if (!d.contains(e.target)) d.open = false; });
    var item = e.target.closest && e.target.closest('.menubar .menu-item');
    if (item && !item.closest('.menu-sub > .menu-item') && !item.classList.contains('is-disabled')) {
      var menu = item.closest('details[data-menu]');
      if (menu) setTimeout(function () { menu.open = false; }, 0);
    }
  });
  [].forEach.call(document.querySelectorAll('.menubar details[data-menu]'), function (d) {
    d.querySelector('summary').addEventListener('mouseenter', function () {
      var other = document.querySelector('.menubar details[data-menu][open]');
      if (other && other !== d) { other.open = false; d.open = true; }
    });
  });
  document.addEventListener('keydown', function (e) {
    if (e.key !== 'Escape') return;
    [].forEach.call(document.querySelectorAll('details[data-menu][open]'), function (d) { d.open = false; var s = d.querySelector('summary'); if (s) s.focus(); });
  });

  function score(q, text) {
    // Subsequence fuzzy match. Returns null or {s: score, idx: [positions]}.
    if (!q) return { s: 0, idx: [] };
    var t = text.toLowerCase(), qi = 0, s = 0, idx = [], last = -2;
    for (var i = 0; i < t.length && qi < q.length; i++) {
      if (t[i] === q[qi]) {
        idx.push(i);
        s += 1;
        if (i === last + 1) s += 3;                                  // consecutive
        if (i === 0 || /[\s_\-/.]/.test(t[i - 1])) s += 4;           // word start
        last = i; qi++;
      }
    }
    if (qi < q.length) return null;
    return { s: s - (t.length * 0.02) - (idx[0] || 0) * 0.1, idx: idx };
  }
  function mark(text, idx) {
    if (!idx || !idx.length) return esc(text);
    var out = '', set = {};
    idx.forEach(function (i) { set[i] = 1; });
    for (var i = 0; i < text.length; i++) out += set[i] ? '<mark>' + esc(text[i]) + '</mark>' : esc(text[i]);
    return out;
  }

  function buildItems() {
    var items = [];
    var scripts = [];
    try { scripts = JSON.parse(document.getElementById('wb-palette').textContent) || []; } catch (e) {}
    scripts.forEach(function (s) {
      items.push({ group: 'Scripts', text: s.name, hint: s.cat + ' · ' + s.path, icon: '<span class="lang lang-' + esc(s.ext) + '">' + esc(s.ext) + '</span>',
        href: url('script', s.id), alt: s.run ? url('run', s.id) : null, key: s.name + ' ' + s.path + ' ' + s.cat });
      if (s.run) items.push({ group: 'Run', text: 'Run ' + s.name, hint: s.path, icon: ICON('play'), href: url('run', s.id), runOnly: true, key: 'run ' + s.name + ' ' + s.path });
    });
    [].forEach.call(document.querySelectorAll('.activity a[data-hint]'), function (a) {
      items.push({ group: 'Go to', text: a.getAttribute('data-hint').replace(/^Go to /, ''), hint: a.getAttribute('data-hotkey'), icon: a.querySelector('svg').outerHTML, href: a.href, key: a.getAttribute('data-hint') });
    });
    var seen = {};
    [].forEach.call(document.querySelectorAll('main [data-hotkey][data-hint]'), function (el) {
      var hint = el.getAttribute('data-hint');
      if (seen[hint] || !usable(el)) return;
      seen[hint] = 1;
      items.push({ group: 'This page', text: hint, hint: el.getAttribute('data-hotkey').split('|')[0], icon: ICON('zap'), el: el, key: hint });
    });
    items.push({ group: 'Commands', text: 'Toggle light / dark theme', icon: ICON('sun'), run: toggleTheme, key: 'theme dark light toggle' });
    items.push({ group: 'Commands', text: 'Toggle side panel', hint: 'Ctrl+B', icon: ICON('panel'), run: function () { toggleSide(); }, key: 'side panel sidebar explorer toggle' });
    if (document.querySelector('.split-side')) items.push({ group: 'Commands', text: 'Toggle right panel', hint: 'Ctrl+Alt+B', icon: ICON('panel'), run: toggleSide2, key: 'right panel column collapse toggle' });
    if (document.getElementById('saved-panel')) items.push({ group: 'Commands', text: 'Toggle saved values panel', hint: 'Ctrl+J', icon: ICON('sliders'), run: function () { toggleBottom(); }, key: 'saved values presets bottom panel compare toggle' });
    items.push({ group: 'Commands', text: 'Keyboard shortcuts', hint: '?', icon: ICON('keyboard'), run: function () { setTimeout(help.open, 0); }, key: 'keyboard shortcuts help keys' });
    // Script folders: the system folder dialog, favourites and recent.
    var folders = [];
    try { folders = JSON.parse(document.getElementById('wb-folders').textContent) || []; } catch (e) {}
    var newScript = document.querySelector('[data-new-script]');
    // New script: from the explorer's "+" (keeps its folder), else on any page while editing is on.
    if (newScript || urls.edit) items.push({ group: 'Commands', text: 'New script', hint: newScript ? 'g n' : '', icon: ICON('plus'), href: newScript ? newScript.href : '/scripts/new/', key: 'new script create file' });
    items.push({ group: 'Folders', text: 'Open folder…', icon: ICON('folder'), run: function () { sr.openFolder(); }, key: 'open folder browse directory workspace' });
    folders.forEach(function (f) {
      items.push({ group: 'Folders', text: f.name, hint: f.path, icon: ICON(f.favorite ? 'star' : 'folder'),
        run: function () { postForm('/folder/open/', { path: f.path }); }, key: 'folder ' + f.name + ' ' + f.path });
    });
    var refresh = document.querySelector('form[action$="/scripts/refresh/"]');
    if (refresh) items.push({ group: 'Commands', text: 'Rescan the folder', icon: ICON('refresh'), run: function () { refresh.submit(); }, key: 'rescan refresh library scan' });
    return items;
  }

  var palette = {
    open: function () {
      var items = buildItems(), sel = 0, shown = [];
      var box = document.createElement('div');
      box.className = 'palette'; box.setAttribute('role', 'dialog'); box.setAttribute('aria-label', 'Command palette');
      box.innerHTML = '<div class="pal-input">' + ICON('search') + '<input type="text" placeholder="Search scripts, pages and commands…  (type “run ” to run, or a RUN-id)" aria-label="Search" autocomplete="off" spellcheck="false"><kbd>Esc</kbd></div>' +
        '<ul class="pal-list" role="listbox"></ul>' +
        '<div class="pal-foot"><span><kbd>↑</kbd><kbd>↓</kbd> move</span><span><kbd>↵</kbd> open</span><span><kbd>Ctrl</kbd><kbd>↵</kbd> run script</span></div>';
      var input = box.querySelector('input'), list = box.querySelector('.pal-list');

      function render() {
        var raw = input.value.trim(), q = raw.toLowerCase(), runMode = /^run\s/.test(q);
        var qq = runMode ? q.replace(/^run\s+/, '') : q;
        shown = [];
        var m = /^run-\d{8}-\d{3,}$/i.exec(raw);
        if (m && urls.execution) shown.push({ item: { group: 'Execution', text: 'Open ' + raw.toUpperCase(), icon: ICON('terminal'), href: url('execution', raw.toUpperCase()) }, idx: [] });
        items.forEach(function (it) {
          if (!!it.runOnly !== runMode) return;            // "run …" lists run actions only
          var hit = score(qq, it.key.toLowerCase());
          if (!hit) return;
          var onText = score(qq, it.text);                 // highlight when the visible text matches
          shown.push({ item: it, idx: onText ? onText.idx : [], s: onText ? onText.s + 5 : hit.s });
        });
        if (q) shown.sort(function (a, b) { return (b.s || 0) - (a.s || 0); });
        shown = shown.slice(0, 60);
        sel = Math.min(sel, Math.max(0, shown.length - 1));
        var html = '', lastGroup = null;
        shown.forEach(function (row, i) {
          var it = row.item;
          if (!q && it.group !== lastGroup) { html += '<li class="pal-group">' + esc(it.group) + '</li>'; lastGroup = it.group; }
          html += '<li class="pal-item' + (i === sel ? ' is-sel' : '') + '" data-i="' + i + '" role="option">' +
            '<span class="pal-ic">' + (it.icon || '') + '</span><span class="pal-text">' + mark(it.text, row.idx) + '</span>' +
            (it.hint ? '<span class="pal-hint">' + esc(it.hint) + '</span>' : '') + '</li>';
        });
        list.innerHTML = html || '<li class="pal-empty">No matches</li>';
        var cur = list.querySelector('.is-sel');
        if (cur) cur.scrollIntoView({ block: 'nearest' });
      }
      function choose(i, alt) {
        var row = shown[i];
        if (!row) return;
        var it = row.item;
        closeOverlay();
        if (alt && it.alt) location.href = it.alt;
        else if (it.href) location.href = it.href;
        else if (it.el) it.el.click();
        else if (it.run) it.run();
      }
      openOverlay(box, function (e) {
        if (e.key === 'ArrowDown') { e.preventDefault(); sel = Math.min(sel + 1, shown.length - 1); render(); }
        else if (e.key === 'ArrowUp') { e.preventDefault(); sel = Math.max(sel - 1, 0); render(); }
        else if (e.key === 'Enter') { e.preventDefault(); choose(sel, e.ctrlKey || e.metaKey); }
      });
      input.addEventListener('input', function () { sel = 0; render(); });
      list.addEventListener('mousemove', function (e) {
        var li = e.target.closest('.pal-item');
        if (li && +li.dataset.i !== sel) { sel = +li.dataset.i; [].forEach.call(list.querySelectorAll('.pal-item'), function (x) { x.classList.toggle('is-sel', +x.dataset.i === sel); }); }
      });
      list.addEventListener('click', function (e) { var li = e.target.closest('.pal-item'); if (li) choose(+li.dataset.i, e.ctrlKey || e.metaKey); });
      render();
      input.focus();
    }
  };

  // ------------------------------------------------------- shortcut sheet --
  var help = {
    open: function () {
      var rows = [
        ['Global', [['Command palette', 'Ctrl K'], ['Toggle side panel', 'Ctrl B'], ['Toggle right panel (script pages)', 'Ctrl Alt B'], ['Toggle saved values panel', 'Ctrl J'], ['Focus search / explorer filter', '/'], ['This sheet', '?']]],
        ['Go to', [['Home', 'g o'], ['Script Library', 'g l'], ['Sequences', 'g q'], ['Runs (running + history)', 'g h'], ['Settings', 'g s']]],
        ['Script', [['Overview: settings', 'i  or  c'], ['Parameters and saved values', 'p'], ['Run: run, view, edit', 'r  or  s'], ['Save the script / settings / parameters', 'Ctrl S']]],
        ['Run & debug', [['Review / launch', 'Ctrl ↵'], ['Back from review', 'Esc'], ['Continue', 'F5'], ['Step to next line', 'F10  or  n'], ['Pause', 'F6'], ['Stop the run', 'Shift F5']]]
      ];
      var seen = {}, page = [];
      [].forEach.call(document.querySelectorAll('main [data-hotkey][data-hint]'), function (el) {
        var h = el.getAttribute('data-hint');
        if (seen[h] || !usable(el)) return; seen[h] = 1;
        page.push([h, el.getAttribute('data-hotkey').replace(/\|/g, '  or  ').replace(/\+/g, ' ')]);
      });
      if (page.length) rows.unshift(['On this page', page]);
      function keys(spec) {
        return spec.split('  or  ').map(function (alt) {
          return alt.split(' ').filter(Boolean).map(function (k) { return '<kbd>' + esc(k) + '</kbd>'; }).join(' ');
        }).join(' <span class="faint">or</span> ');
      }
      var html = '<div class="dialog-head"><h2>Keyboard shortcuts</h2><button type="button" class="btn btn-ghost btn-icon btn-sm" data-close aria-label="Close">' + ICON('x') + '</button></div><div class="dialog-body"><table class="keys">';
      rows.forEach(function (g) {
        html += '<tr class="k-group"><td colspan="2">' + esc(g[0]) + '</td></tr>';
        g[1].forEach(function (r) { html += '<tr><td>' + esc(r[0]) + '</td><td>' + keys(r[1]) + '</td></tr>'; });
      });
      html += '</table></div>';
      var box = document.createElement('div');
      box.className = 'dialog'; box.setAttribute('role', 'dialog'); box.setAttribute('aria-label', 'Keyboard shortcuts'); box.tabIndex = -1;
      box.innerHTML = html;
      openOverlay(box);
      box.querySelector('[data-close]').addEventListener('click', closeOverlay);
      box.focus();
    }
  };

  // -------------------------------------------------------- folder scan --
  // A big folder is scanned in the background; while it runs the status bar shows the
  // progress. When it is done the overview pages reload, other pages offer to.
  var scanEl = document.getElementById('sb-scan');
  if (scanEl) (function () {
    var text = scanEl.querySelector('[data-scan-text]'), version = scanEl.dataset.version;
    var fmt = function (n) { return Number(n || 0).toLocaleString(); };
    var tick = function () {
      fetch(scanEl.dataset.url, { credentials: 'same-origin' }).then(function (r) { return r.json(); }).then(function (s) {
        if (s.scanning) {
          text.textContent = 'Scanning ' + s.folder + ' · ' + fmt(s.dirs) + ' folders · ' + fmt(s.found) + ' scripts · ' + s.elapsed + 's';
          scanEl.title = s.current || '';
          setTimeout(tick, 1000);
          return;
        }
        scanEl.classList.remove('sb-live');
        if (String(s.version) === version) { scanEl.remove(); return; }
        if (document.body.hasAttribute('data-scan-reload')) { location.reload(); return; }
        var a = document.createElement('a');                 // keep this page (forms) as it is
        a.className = 'sb-item'; a.href = location.href; a.id = 'sb-scan';
        a.textContent = 'Scan finished · ' + fmt(s.count) + ' scripts — reload';
        scanEl.replaceWith(a);
      }).catch(function () { setTimeout(tick, 3000); });
    };
    setTimeout(tick, 800);
  })();

  // ------------------------------------------------- system folder dialog --
  // On the machine running ScriptHub (desktop mode, or a browser on that machine:
  // body[data-folder-picker]) "Open folder…" shows the system folder dialog and opens
  // the folder picked. Elsewhere it goes to the Open folder page.
  function csrf() {
    var m = /(?:^|;\s*)csrftoken=([^;]+)/.exec(document.cookie);
    if (m) return decodeURIComponent(m[1]);
    var i = document.querySelector('input[name=csrfmiddlewaretoken]');
    return i ? i.value : '';
  }
  function post(url, data) {
    var body = new FormData();
    Object.keys(data).forEach(function (k) { body.append(k, data[k]); });
    return fetch(url, { method: 'POST', body: body, headers: { 'X-CSRFToken': csrf() } })
      .then(function (r) { return r.json().catch(function () { return {}; }).then(function (j) { if (!r.ok) throw j; return j; }); });
  }
  var canPick = document.body.dataset.folderPicker === '1', picking = false;
  function pickFolder(initial, title) {
    if (picking) return Promise.resolve({});
    picking = true;
    document.documentElement.style.cursor = 'progress';
    return post('/desktop/pick-folder/', { initial: initial || '', title: title || 'Open a script folder' })
      .finally(function () { picking = false; document.documentElement.style.cursor = ''; });
  }
  function openFolder() {
    if (!canPick) { location.href = '/folder/'; return; }
    pickFolder(document.body.dataset.folder, 'Open a script folder')
      .then(function (j) { if (j.path) postForm('/folder/open/', { path: j.path }); })
      .catch(function () { location.href = '/folder/'; });               // no dialog here: use the page
  }
  // File > Open file…: one script on its own (the system dialog, filtered to script types).
  function openFile() {
    if (!canPick) { location.href = '/folder/'; return; }
    if (picking) return;
    picking = true;
    document.documentElement.style.cursor = 'progress';
    post('/desktop/pick-file/', { initial: document.body.dataset.folder || '', title: 'Open scripts' })
      .then(function (j) {
        var paths = j.paths || (j.path ? [j.path] : []);
        if (!paths.length) return;
        var f = document.createElement('form'); f.method = 'post'; f.action = '/folder/open/'; f.hidden = true;
        var tok = document.createElement('input'); tok.type = 'hidden'; tok.name = 'csrfmiddlewaretoken'; tok.value = csrf(); f.appendChild(tok);
        paths.forEach(function (p) { var i = document.createElement('input'); i.type = 'hidden'; i.name = 'path'; i.value = p; f.appendChild(i); });
        document.body.appendChild(f); f.submit();                       // several scripts: each becomes a tab
      })
      .catch(function () { location.href = '/folder/'; })
      .finally(function () { picking = false; document.documentElement.style.cursor = ''; });
  }
  sr.openFile = openFile;
  document.addEventListener('click', function (e) {
    var f = e.target.closest('[data-open-file]');
    if (f && canPick) { e.preventDefault(); openFile(); }
  });
  // "Versions…" next to Restore previous: picking one restores it (after the form's confirmation).
  document.addEventListener('change', function (e) {
    var sel = e.target.closest && e.target.closest('[data-restore-pick]');
    if (!sel || !sel.value) return;
    if (sel.form.requestSubmit) sel.form.requestSubmit(); else sel.form.submit();
    setTimeout(function () { sel.value = ''; }, 0);        // cancelled: back to "Versions…"
  });
  // Run menu: on the Run tab it submits the form (with the chosen mode); elsewhere it opens the Run tab.
  document.addEventListener('click', function (e) {
    var a = e.target.closest('[data-action="run"]');
    var form = document.getElementById('run-form');
    if (!a || !form) return;
    e.preventDefault();
    var radio = document.querySelector('input[name="mode"][value="' + (a.dataset.mode || 'normal') + '"]');
    if (radio) radio.checked = true;
    if (form.requestSubmit) form.requestSubmit(); else form.submit();
  });
  sr.openFolder = openFolder;
  document.addEventListener('click', function (e) {
    var open = e.target.closest('[data-open-folder]');
    if (open && canPick) {
      e.preventDefault();
      [].forEach.call(document.querySelectorAll('details[data-menu][open]'), function (d) { d.open = false; });
      openFolder();
      return;
    }
    var pick = e.target.closest('[data-pick-folder]');
    if (pick) {                                     // "Browse…" next to a path field
      e.preventDefault();
      var input = document.querySelector(pick.getAttribute('data-pick-folder'));
      pick.disabled = true;
      pickFolder(input ? input.value : '', pick.getAttribute('data-title'))
        .then(function (j) {
          if (!j.path || !input) return;
          input.value = j.path; input.dispatchEvent(new Event('input', { bubbles: true })); input.focus();
          if (pick.hasAttribute('data-pick-submit') && input.form) {   // e.g. Open folder: picking = opening
            if (input.form.requestSubmit) input.form.requestSubmit(); else input.form.submit();
          }
        })
        .catch(function (j) { window.alert((j && j.error) || 'The folder dialog is not available.'); })
        .then(function () { pick.disabled = false; });
    }
  });

  // ------------------------------------------------------- desktop mode --
  // Only when started by the desktop launcher (body[data-desktop]).
  if (document.body.dataset.desktop === '1') {
    var ping = function () { fetch('/desktop/ping/', { method: 'POST', keepalive: true }).catch(function () {}); };
    ping();
    setInterval(ping, 15000);
    document.addEventListener('visibilitychange', function () { if (!document.hidden) ping(); });
    window.addEventListener('pagehide', function () { if (navigator.sendBeacon) navigator.sendBeacon('/desktop/bye/'); });

    document.addEventListener('click', function (e) {
      var reveal = e.target.closest('[data-reveal-script], [data-reveal-run]');
      if (reveal) {
        e.preventDefault();
        post('/desktop/reveal/', reveal.hasAttribute('data-reveal-run') ? { run: reveal.getAttribute('data-reveal-run') }
          : { script: reveal.getAttribute('data-reveal-script') })
          .catch(function (j) { window.alert((j && j.error) || 'Could not open the folder.'); });
      }
    });
  }

  // ------------------------------------------------------------ open tabs --
  // The tabs of the centre bar: open scripts (their ids) and open sequences ("seq:<slug>"),
  // remembered per open folder (``ws``: the folder path, or "files"). Script pages
  // (designer.js) and the sequence page (sequence.js) both draw them with sr.tabs.tab().
  sr.tabs = (function () {
    var scripts = {}, seqs = {};
    function data(id) { try { return JSON.parse(document.getElementById(id).textContent) || []; } catch (e) { return []; } }
    data('wb-palette').forEach(function (s) { scripts[s.id] = s; });
    data('wb-sequences').forEach(function (s) { seqs['seq:' + s.slug] = s; });
    function key(ws) { return 'scripthub.openScripts.' + (ws || ''); }
    function isSeq(id) { return String(id).indexOf('seq:') === 0; }
    var api = {
      isSeq: isSeq,
      script: function (id) { return scripts[id] || null; },
      known: function (id) { return !!(scripts[id] || seqs[id]); },
      load: function (ws) { try { return JSON.parse(localStorage.getItem(key(ws))) || []; } catch (e) { return []; } },
      save: function (ws, list) { try { localStorage.setItem(key(ws), JSON.stringify(list.slice(-20))); } catch (e) { /* storage unavailable */ } },
      add: function (ws, id) { var list = api.load(ws); if (list.indexOf(id) < 0) { list.push(id); api.save(ws, list); } },
      remove: function (ws, id) { api.save(ws, api.load(ws).filter(function (v) { return v !== id; })); },
      /* where a tab leads; ``sub`` = the script page's right-hand tab ('', 'parameters/', 'run/') */
      url: function (id, sub) {
        return isSeq(id) ? '/sequences/' + encodeURIComponent(id.slice(4)) + '/'
          : '/scripts/' + encodeURIComponent(id) + '/' + (sub || '');
      },
      closeX: function () {
        var x = document.createElement('span');
        x.className = 'tab-x'; x.setAttribute('role', 'button'); x.title = 'Close the tab'; x.setAttribute('aria-label', 'Close'); x.textContent = '×';
        return x;
      },
      /* a tab (link) for a known script or sequence; null for an unknown one */
      tab: function (id, sub) {
        var a = document.createElement('a'), name = document.createElement('span');
        a.className = 'doc-tab doc-script-tab'; a.href = api.url(id, sub); a.dataset.script = id;
        name.className = 'tab-name';
        if (isSeq(id)) {
          if (!seqs[id]) return null;
          a.classList.add('doc-seq-tab');
          a.title = 'Sequence: ' + seqs[id].name;
          a.innerHTML = '<svg class="i"><use href="#i-sequence"/></svg>';
          name.textContent = seqs[id].name;
        } else {
          var s = scripts[id];
          if (!s) return null;
          a.title = s.path;
          var lang = document.createElement('span');
          lang.className = 'lang lang-' + s.ext; lang.textContent = s.ext;
          a.appendChild(lang);
          name.textContent = String(s.path || '').split('/').pop();
        }
        a.appendChild(name);
        a.appendChild(api.closeX());
        return a;
      }
    };
    return api;
  })();

  // --------------------------------------------------------------- flash --
  // A message at the top of the page, like the server's (success ones fade out).
  sr.flash = function (text, kind) {
    var body = document.getElementById('main-body');
    if (!body) return;
    var box = body.querySelector(':scope > .flashes');
    if (!box) { box = document.createElement('div'); box.className = 'flashes notes'; body.insertBefore(box, body.firstChild); }
    var el = document.createElement('div');
    el.className = 'flash flash-' + (kind || 'success'); el.setAttribute('role', 'alert');
    el.appendChild(document.createElement('span')).textContent = text;
    box.textContent = ''; box.appendChild(el);
    if ((kind || 'success') === 'success') setTimeout(function () { el.classList.add('flash-out'); setTimeout(function () { el.remove(); }, 350); }, 4000);
  };

  // ---------------------------------------------------- save project as --
  // File > Save project as…: the system Save As dialog (File System Access API, Edge/Chrome), then the
  // project file of the open folder is written to the chosen file. Without that API: a normal download.
  document.addEventListener('click', function (e) {
    var a = e.target.closest && e.target.closest('[data-save-project]');
    if (!a || typeof window.showSaveFilePicker !== 'function') return;     // fallback: the link downloads
    e.preventDefault();
    [].forEach.call(document.querySelectorAll('details[data-menu][open]'), function (d) { d.open = false; });
    var base = (a.getAttribute('data-save-project') || 'project').replace(/[^A-Za-z0-9._ -]+/g, '').trim() || 'project';
    window.showSaveFilePicker({
      suggestedName: base + '.shp',
      types: [{ description: 'ScriptHub project', accept: { 'application/zip': ['.shp'] } }]
    }).then(function (handle) {
      sr.flash('Saving ' + handle.name + '…', 'info');
      var url = a.getAttribute('href') + '?name=' + encodeURIComponent(handle.name);
      return fetch(url, { credentials: 'same-origin', cache: 'no-store' }).then(function (r) {
        var type = r.headers.get('Content-Type') || '';
        if (!r.ok || type.indexOf('application/zip') !== 0) throw new Error('The project file was not made (see the page after a reload).');
        var report = r.headers.get('X-ScriptHub-Report') || '';
        return r.blob().then(function (blob) {
          return handle.createWritable().then(function (w) {
            return w.write(blob).then(function () { return w.close(); });
          });
        }).then(function () { sr.flash('Saved ' + handle.name + (report ? ': ' + report + '.' : '.')); });
      });
    }).catch(function (err) {
      if (err && err.name === 'AbortError') return;                       // the dialog was cancelled
      sr.flash((err && err.message) || 'The project could not be saved.', 'error');
    });
  });

  sr.palette = palette;
  sr.help = help;
  sr.closeOverlay = closeOverlay;
})();
