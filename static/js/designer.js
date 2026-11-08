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

  // ---------------------------------------------------------- run tabs --
  // A run started from the script page opens as a centre tab next to the files: the whole
  // console (output, error, status, Stop, debugger) in a frame. The open run tabs of a
  // script are remembered for this browser tab; × closes the tab, the run keeps going.
  var docSection = docEl || document.querySelector('.doc[data-script]');
  var docBar = docSection ? docSection.querySelector('.doc-bar') : null;
  var scriptId = docSection ? docSection.getAttribute('data-script') : '';
  var RUNS_KEY = 'scripthub.runTabs.' + scriptId;
  var consoleUrl = '/executions/XID/';
  try { consoleUrl = JSON.parse(document.getElementById('wb-urls').textContent).execution || consoleUrl; } catch (e) { /* default */ }
  function openRuns() { try { return JSON.parse(sessionStorage.getItem(RUNS_KEY)) || []; } catch (e) { return []; } }
  function keepRuns(list) { try { sessionStorage.setItem(RUNS_KEY, JSON.stringify(list.slice(-8))); } catch (e) { /* storage unavailable */ } }
  function runTab(id) { return tabs.filter(function (t) { return t.dataset.run === id; })[0]; }
  function addRunTab(id, focus) {
    if (!docBar || !/^RUN-\d{8}-\d{3,}$/.test(id)) return;
    var tab = runTab(id);
    if (!tab) {
      tab = document.createElement('button');
      tab.type = 'button'; tab.className = 'doc-tab doc-run-tab'; tab.setAttribute('role', 'tab');
      tab.dataset.run = id; tab.dataset.docTab = 'run-panel-' + id; tab.title = id + ' (output, status, Stop)';
      tab.innerHTML = '<span class="dot st-queued"></span><span>' + id.replace(/^RUN-\d{8}-/, 'run ') + '</span>' +
        '<span class="tab-x" role="button" title="Close the tab (the run keeps going)" aria-label="Close">×</span>';
      var after = [].slice.call(docBar.querySelectorAll('.doc-tab')).pop();
      after.parentNode.insertBefore(tab, after.nextSibling);
      var panel = document.createElement('div');
      panel.className = 'doc-panel doc-run-panel'; panel.id = tab.dataset.docTab; panel.hidden = true;
      var frame = document.createElement('iframe');
      frame.title = id; frame.src = consoleUrl.replace('XID', encodeURIComponent(id)) + '?embed=1';
      panel.appendChild(frame);
      docSection.appendChild(panel);
      tabs.push(tab);
      tab.addEventListener('click', function (e) {
        if (e.target.closest('.tab-x')) { closeRunTab(id); return; }
        activate(tab, false);
      });
    }
    if (focus) activate(tab, false);
  }
  function closeRunTab(id) {
    var tab = runTab(id);
    if (!tab) return;
    var wasActive = tab.classList.contains('is-active');
    tabs.splice(tabs.indexOf(tab), 1);
    var panel = $(tab.dataset.docTab);
    if (panel) panel.remove();
    tab.remove();
    keepRuns(openRuns().filter(function (x) { return x !== id; }));
    if (wasActive && tabs.length) activate(tabs[0], false);
  }
  openRuns().forEach(function (id) { addRunTab(id, false); });
  var justStarted = new URLSearchParams(location.search).get('open');
  if (justStarted) {
    keepRuns(openRuns().filter(function (x) { return x !== justStarted; }).concat([justStarted]));
    addRunTab(justStarted, true);
    var clean = new URLSearchParams(location.search); clean.delete('open');
    history.replaceState(null, '', location.pathname + (clean.toString() ? '?' + clean : ''));
  }
  window.addEventListener('message', function (e) {      // the console in a run tab reports its status
    if (e.origin !== location.origin || !e.data || e.data.sr !== 'run-status') return;
    var tab = runTab(e.data.run), dot = tab && tab.querySelector('.dot');
    if (dot) { dot.className = 'dot st-' + e.data.status; tab.title = e.data.run + ' · ' + e.data.label; }
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

  // ----------------------------------------------------------- one Save --
  // A script page has ONE Save (centre tab bar, Ctrl+S anywhere). It saves everything
  // changed on the page: the script (in place), then the Overview / Parameters forms.
  // The metadata is the comment block at the top of the script, so editing that block
  // AND a form at once is refused (the same settings). After a metadata change the page
  // reloads so every panel shows it. Errors open the server's explanation.
  var editForm = $('edit-form'), runForm = $('run-form'), saveBtn = document.querySelector('[data-save-all]');
  var metaForms = ['configure-form', 'params-form'].map($).filter(Boolean);
  var saving = null;

  function snap(f) {
    if (f.srSerialize) f.srSerialize();                    // parameter editor -> hidden JSON field
    return new URLSearchParams(new FormData(f)).toString();
  }
  var snaps = metaForms.map(snap);
  function scriptDirty() { return !!(editForm && edRoot && window.srEditor.isDirty(edRoot)); }
  // The metadata block in the script text ("# --- scripthub ---" … "# --- end scripthub ---").
  var MARK = /^\s*(?:#|::|rem\b)\s*--- (end )?scripthub ---\s*$/i;
  function blockOf(text) {
    var lines = String(text).split(/\r?\n/), start = -1;
    for (var i = 0; i < lines.length; i++) {
      var m = MARK.exec(lines[i]);
      if (!m) continue;
      if (!m[1] && start < 0) start = i;
      else if (m[1] && start >= 0) return lines.slice(start, i + 1).join('\n');
    }
    return '';
  }
  var savedBlock = edTa ? blockOf(edTa.value) : '';
  function blockChanged() { return !!edTa && blockOf(edTa.value) !== savedBlock; }
  function dirtyForms() { return metaForms.filter(function (f, i) { return snap(f) !== snaps[i]; }); }
  function changes() {
    var what = [];
    if (scriptDirty()) what.push(blockChanged() ? 'script (with its metadata)' : 'script');
    dirtyForms().forEach(function (f) { what.push(f.id === 'params-form' ? 'parameters' : 'settings'); });
    return what;
  }
  function updateDirty() {
    if (!saveBtn) return;
    var what = changes();
    saveBtn.classList.toggle('is-dirty', what.length > 0);
    saveBtn.title = what.length ? 'Save ' + what.join(', ') + '  (Ctrl+S)' : 'Nothing to save  (Ctrl+S)';
  }
  ['input', 'change'].forEach(function (ev) { document.addEventListener(ev, function () { setTimeout(updateDirty, 0); }); });
  document.addEventListener('click', function (e) {          // parameter editor: add, move, delete
    if (e.target.closest && e.target.closest('#param-list, #add-param')) setTimeout(updateDirty, 0);
  });
  window.addEventListener('beforeunload', function (e) {   // the script editor warns about itself
    if (!saving && dirtyForms().length) { e.preventDefault(); e.returnValue = ''; }
  });

  function flash(text, kind) {
    var body = $('main-body'), box = body.querySelector(':scope > .flashes');
    if (!box) { box = document.createElement('div'); box.className = 'flashes notes'; body.insertBefore(box, body.firstChild); }
    var el = document.createElement('div');
    el.className = 'flash flash-' + (kind || 'success'); el.setAttribute('role', 'alert');
    el.appendChild(document.createElement('span')).textContent = text;
    box.textContent = ''; box.appendChild(el);
    setTimeout(function () { el.classList.add('flash-out'); setTimeout(function () { el.remove(); }, 350); }, 3000);
  }
  try { if (sessionStorage.getItem('sr.saved')) { sessionStorage.removeItem('sr.saved'); flash('Saved.'); } } catch (e) { /* storage unavailable */ }

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
  function nativeSubmit(f) {                // shows the server's explanation (conflict, invalid data …)
    f.dataset.native = '1';
    saving = 'leaving';
    HTMLFormElement.prototype.submit.call(f);
  }
  function saveScript() {
    var fd = new FormData(editForm);
    fd.set('action', 'save');
    return fetch(editForm.action, { method: 'POST', body: fd, credentials: 'same-origin' })
      .then(function (r) { return r.text().then(function (html) { return { ok: r.ok && r.redirected, html: html }; }); })
      .then(function (res) {
        var doc = new DOMParser().parseFromString(res.html, 'text/html');
        var hash = doc.querySelector('#edit-form [name="base_hash"]');
        if (!res.ok || !hash) { nativeSubmit(editForm); return false; }
        editForm.querySelector('[name="base_hash"]').value = hash.value;
        var theirs = doc.getElementById('restore-form'), mine = $('restore-form');
        if (theirs && mine) mine.hidden = theirs.hidden;
        window.srEditor.markSaved(edRoot);
        savedBlock = blockOf(edTa.value);
        addFlashes(doc);
        return true;
      })
      .catch(function () { nativeSubmit(editForm); return false; });
  }
  function saveForm(f) {                    // a metadata form, in the background
    if (f.srSerialize) f.srSerialize();
    return fetch(f.action, { method: 'POST', body: new FormData(f), credentials: 'same-origin' })
      .then(function (r) { if (r.ok && r.redirected) return true; nativeSubmit(f); return false; })
      .catch(function () { nativeSubmit(f); return false; });
  }
  function saveAll() {
    if (saving) return saving;
    var meta = dirtyForms(), script = scriptDirty(), block = blockChanged();
    if (block && meta.length) {
      window.alert('The metadata block at the top of the script and the ' +
        meta.map(function (f) { return f.id === 'params-form' ? 'Parameters' : 'Overview'; }).join(' / ') +
        ' form were both changed. They are the same settings: undo one of them, then save.');
      return Promise.resolve(false);
    }
    if (!script && !meta.length) { flash('Nothing to save.', 'info'); return Promise.resolve(true); }
    if (saveBtn) saveBtn.disabled = true;
    var chain = Promise.resolve(true);
    if (script) chain = chain.then(function (ok) { return ok && saveScript(); });
    meta.forEach(function (f) { chain = chain.then(function (ok) { return ok && saveForm(f); }); });
    saving = chain.then(function (ok) {
      if (ok && (meta.length || block)) {     // metadata changed: reload so every panel shows it
        saving = 'leaving';
        try { sessionStorage.setItem('sr.saved', '1'); } catch (e) { /* storage unavailable */ }
        location.replace(location.pathname + location.search);
        return ok;
      }
      if (saving !== 'leaving') saving = null;
      if (saveBtn) saveBtn.disabled = false;
      updateDirty();
      return ok;
    });
    return saving;
  }
  if (saveBtn) saveBtn.addEventListener('click', function (e) { e.preventDefault(); saveAll(); });
  // Forms that would post on Enter save through the same path.
  metaForms.concat(editForm ? [editForm] : []).forEach(function (f) {
    f.addEventListener('submit', function (e) {
      if (f.dataset.native) return;
      e.preventDefault();
      saveAll();
    });
  });
  // Run with unsaved script edits: save the script first.
  if (runForm && editForm && edRoot) runForm.addEventListener('submit', function (e) {
    if (!window.srEditor.isDirty(edRoot)) return;
    e.preventDefault();
    var submitter = e.submitter;
    saveScript().then(function (ok) {
      if (!ok) return;
      if (runForm.requestSubmit) runForm.requestSubmit(submitter && submitter.form === runForm ? submitter : undefined);
      else runForm.submit();
    });
  });
  updateDirty();
})();
