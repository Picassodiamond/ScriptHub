/* Script pages (Overview, Script, Review, Configure): the script in the body,
   configuration on the right.
   - centre tabs (script / metadata JSON)
   - parameter references: focusing or hovering a parameter (form field, overview
     item or parameter-editor row) marks the script lines that use it
   - Script tab: Ctrl+S saves in place (no reload, the run form keeps its values);
     "Review" with unsaved changes saves first. Errors and conflicts open the
     full-screen editor page, which explains them. */
(function () {
  'use strict';
  var $ = function (id) { return document.getElementById(id); };

  // ------------------------------------------------------- centre tabs --
  // The centre is the same on Overview / Run / Configure, so the picked tab is kept
  // when switching between them (unless the page asks for one: ?tab=params, JSON errors).
  var tabs = [].slice.call(document.querySelectorAll('[data-doc-tab]'));
  var TAB_KEY = 'scripthub.docTab';
  function activate(t, focus) {
    tabs.forEach(function (x) {
      x.classList.toggle('is-active', x === t);
      var panel = $(x.dataset.docTab);
      if (panel) panel.hidden = x !== t;
    });
    var ta = focus && $(t.dataset.docTab).querySelector('textarea');
    if (ta) ta.focus();
  }
  tabs.forEach(function (t) {
    t.addEventListener('click', function () {
      activate(t, true);
      try { sessionStorage.setItem(TAB_KEY, t.dataset.docTab); } catch (e) { /* storage unavailable */ }
    });
  });
  var docEl = document.querySelector('.doc[data-doc-default]');
  if (docEl && !docEl.getAttribute('data-doc-default')) {
    var saved = null;
    try { saved = sessionStorage.getItem(TAB_KEY); } catch (e) { /* storage unavailable */ }
    var pick = tabs.filter(function (t) { return t.dataset.docTab === saved; })[0];
    if (pick) activate(pick, false);
  }
  // Saved parameters: grid / raw file switch.
  var pviews = [].slice.call(document.querySelectorAll('[data-pview]'));
  pviews.forEach(function (b) {
    b.addEventListener('click', function () {
      pviews.forEach(function (x) { x.classList.toggle('is-active', x === b); });
      [].forEach.call(document.querySelectorAll('[data-pview-panel]'), function (p) { p.hidden = p.dataset.pviewPanel !== b.dataset.pview; });
    });
  });
  // Ctrl+S inside the raw JSON editor (Configure) saves that document, not the side form.
  [].forEach.call(document.querySelectorAll('.raw-doc'), function (ta) {
    ta.addEventListener('keydown', function (e) {
      if ((e.ctrlKey || e.metaKey) && (e.key === 's' || e.key === 'S')) {
        e.preventDefault();
        if (ta.form.requestSubmit) ta.form.requestSubmit(); else ta.form.submit();
      } else if (e.key === 'Tab' && !e.shiftKey) {
        e.preventDefault();
        ta.setRangeText('  ', ta.selectionStart, ta.selectionEnd, 'end');
      }
    });
  });

  // ------------------------------------------------------------ source --
  var src = $('doc-src'), edRoot = $('script-editor'), edTa = edRoot ? edRoot.querySelector('.ed-input') : null;
  if (window.highlight) [].forEach.call(document.querySelectorAll('.doc pre[data-lang]'), function (pre) { window.highlight.apply(pre.parentNode); });
  var lines = src ? [].slice.call(src.querySelectorAll('.ln')) : [];
  function texts() { return edTa ? edTa.value.split('\n') : lines.map(function (l) { return l.textContent; }); }

  var copyBtn = document.querySelector('[data-copy-source]');
  if (copyBtn) copyBtn.addEventListener('click', function () {
    window.sr.copy(texts().join('\n') + (edTa ? '' : '\n'), null);
    copyBtn.classList.add('is-on'); setTimeout(function () { copyBtn.classList.remove('is-on'); }, 900);
  });

  // ------------------------------------------------ parameter references --
  var refsEl = $('doc-refs'), marked = [], sticky = null;
  var ROWS = '[data-terms], .pe-item';
  function termsOf(row) {
    if (row.hasAttribute('data-terms')) return row.getAttribute('data-terms').split(/\s+/);
    var name = row.querySelector('.pe-c-name'), pass = row.querySelector('.pe-c-pass');   // parameter editor row
    var out = [];
    if (name) out.push(name.textContent.trim());
    if (pass) out = out.concat(pass.textContent.replace(/^(env|positional)\b/, '').trim().split(/\s+/));
    return out;
  }
  function re(term) {
    return new RegExp('(^|[^A-Za-z0-9_])' + term.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + '(?![A-Za-z0-9_])');
  }
  function show(row, scroll) {
    marked.forEach(function (l) { l.classList.remove('is-ref'); });
    marked = [];
    if (refsEl) refsEl.textContent = '';
    if (edRoot) window.srEditor.mark(edRoot, []);
    if (!row || (!src && !edRoot)) return;
    var terms = termsOf(row).filter(function (t) { return t.length > 1 && t !== '—' && !/^\d+$/.test(t); });
    if (!terms.length) return;
    var pats = terms.map(re), hits = [];
    texts().forEach(function (t, i) { if (pats.some(function (p) { return p.test(t); })) hits.push(i + 1); });
    if (refsEl) refsEl.textContent = terms[0] + ': ' + (hits.length ? hits.length + ' line' + (hits.length === 1 ? '' : 's') : 'not found in the script');
    if (edRoot) {
      window.srEditor.mark(edRoot, hits);
      if (scroll && hits.length) window.srEditor.reveal(edRoot, hits[0]);
      return;
    }
    hits.forEach(function (n) { lines[n - 1].classList.add('is-ref'); marked.push(lines[n - 1]); });
    if (scroll && marked.length) {
      var top = marked[0].offsetTop;
      if (top < src.scrollTop || top > src.scrollTop + src.clientHeight - 30) src.scrollTop = Math.max(0, top - src.clientHeight / 3);
    }
  }
  function rowOf(e) { return e.target.closest ? e.target.closest(ROWS) : null; }
  document.addEventListener('focusin', function (e) { var r = rowOf(e); if (r) { sticky = r; show(r, true); } });
  document.addEventListener('mouseover', function (e) { var r = rowOf(e); if (r) show(r, false); });
  document.addEventListener('mouseout', function (e) {
    var r = rowOf(e);
    if (r && !r.contains(e.relatedTarget)) show(sticky && document.contains(sticky) ? sticky : null, false);
  });

  // ----------------------------------------------- save in place (Script) --
  var editForm = $('edit-form'), runForm = $('run-form');
  if (!editForm || !edRoot) return;
  var saving = null;

  function addFlashes(doc) {
    var body = $('main-body'), box = body.querySelector(':scope > .flashes');
    if (!box) { box = document.createElement('div'); box.className = 'flashes notes'; body.insertBefore(box, body.firstChild); }
    box.textContent = '';
    [].forEach.call(doc.querySelectorAll('.flashes .flash'), function (f) {
      var el = document.importNode(f, true);
      box.appendChild(el);
      if (el.hasAttribute('data-autohide')) setTimeout(function () { el.classList.add('flash-out'); setTimeout(function () { el.remove(); }, 350); }, 4000);
    });
  }
  function nativeSubmit() {                 // shows the server's explanation (conflict, unreadable file …)
    editForm.dataset.native = '1';
    if (editForm.requestSubmit) editForm.requestSubmit(); else editForm.submit();
  }
  function save() {
    if (saving) return saving;
    var fd = new FormData(editForm);
    fd.set('action', 'save');
    var btn = document.querySelector('[data-save][form="edit-form"]');
    if (btn) btn.disabled = true;
    saving = fetch(editForm.action, { method: 'POST', body: fd, credentials: 'same-origin' })
      .then(function (r) { return r.text().then(function (html) { return { ok: r.ok && r.redirected, html: html }; }); })
      .then(function (res) {
        var doc = new DOMParser().parseFromString(res.html, 'text/html');
        var hash = doc.querySelector('#edit-form [name="base_hash"]');
        if (!res.ok || !hash) { nativeSubmit(); return false; }
        editForm.querySelector('[name="base_hash"]').value = hash.value;
        var theirs = doc.getElementById('restore-form'), mine = $('restore-form');
        if (theirs && mine) mine.hidden = theirs.hidden;
        window.srEditor.markSaved(edRoot);
        addFlashes(doc);
        return true;
      })
      .catch(function () { nativeSubmit(); return false; })
      .then(function (ok) { saving = null; if (btn) btn.disabled = false; return ok; });
    return saving;
  }
  editForm.addEventListener('submit', function (e) {
    if (editForm.dataset.native) return;
    e.preventDefault();
    save();
  });
  if (runForm) runForm.addEventListener('submit', function (e) {
    if (!window.srEditor.isDirty(edRoot)) return;
    e.preventDefault();
    var submitter = e.submitter;
    save().then(function (ok) {
      if (!ok) return;
      if (runForm.requestSubmit) runForm.requestSubmit(submitter && submitter.form === runForm ? submitter : undefined);
      else runForm.submit();
    });
  });
})();
