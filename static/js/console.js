/* Live execution console: polls the status endpoint with byte offsets and
   appends only the new output; highlights the running line (trace runs) and
   drives the step debugger (debug runs). Vanilla JS. */
(function () {
  'use strict';
  var cfg = document.getElementById('console-config');
  if (!cfg) return;
  var $ = function (id) { return document.getElementById(id); };
  var statusUrl = cfg.dataset.statusUrl, debugUrl = cfg.dataset.debugUrl || '', csrf = cfg.dataset.csrf;
  var interval = Math.max(500, parseInt(cfg.dataset.interval, 10) || 2000);
  var isDebug = !!debugUrl, fast = Math.min(interval, 350);

  var out = $('out'), err = $('err'), badge = $('status-badge'), durationEl = $('duration');
  var exitEl = $('exit-code'), startedEl = $('started'), finishedEl = $('finished'), pidEl = $('pid');
  var stopForm = $('stop-form'), delForm = $('delete-form'), queueNote = $('queue-note');
  var errorNote = $('error-note'), errorText = $('error-text');
  var live = $('live'), liveNote = $('live-note'), auto = $('autoscroll');
  var MAX_CHARS = 1500000, TRIM_TO = 1000000;

  var offsets = { out: 0, err: 0 };
  var active = true, timer = null, idleAfterDone = 0, inFlight = false;
  var base = { seconds: null, at: 0, running: false };
  var baseTitle = document.title.replace(/^.*? · (?=.* · )/, '');

  function fmt(sec) {
    sec = Math.floor(sec);
    var h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = sec % 60;
    if (h) return h + 'h ' + ('0' + m).slice(-2) + 'm ' + ('0' + s).slice(-2) + 's';
    if (m) return m + 'm ' + ('0' + s).slice(-2) + 's';
    return s + 's';
  }
  function append(el, text) {
    if (!text) return;
    el.appendChild(document.createTextNode(text));
    if (el.textContent.length > MAX_CHARS) {            // keep the page light on huge outputs
      var t = el.textContent;
      el.textContent = '[… earlier output trimmed in this view; download the log for everything …]\n' + t.slice(t.length - TRIM_TO);
    }
    if (auto.checked) el.scrollTop = el.scrollHeight;
  }

  // ------------------------------------------------------- output layout --
  var panes = $('panes'), layoutBtns = [].slice.call(document.querySelectorAll('[data-panes]'));
  function setLayout(v) {
    panes.classList.remove('layout-stack', 'layout-out');
    if (v !== 'side') panes.classList.add('layout-' + v);
    layoutBtns.forEach(function (b) { b.classList.toggle('is-active', b.dataset.panes === v); });
    try { localStorage.setItem('sr.panes', v); } catch (e) {}
  }
  layoutBtns.forEach(function (b) { b.addEventListener('click', function () { setLayout(b.dataset.panes); }); });
  var savedLayout = null;
  try { savedLayout = localStorage.getItem('sr.panes'); } catch (e) {}
  setLayout(/^(side|stack|out)$/.test(savedLayout || '') ? savedLayout : (document.getElementById('trace-src') ? 'side' : 'stack'));

  // -------------------------------------------------------- source pane --
  var traceCard = $('trace-card'), src = $('trace-src');
  var lines = src ? src.querySelectorAll('.ln') : [];
  var curLine = null;
  if (window.highlight && traceCard) window.highlight.apply(traceCard);

  function showLine(n, running) {
    if (!src) return;
    src.classList.toggle('is-running', !!running);
    if (curLine === n) return;
    if (curLine && lines[curLine - 1]) lines[curLine - 1].classList.remove('is-current');
    curLine = n;
    if (!n || !lines[n - 1]) return;
    var el = lines[n - 1];
    el.classList.add('is-current');
    var top = el.offsetTop, h = el.offsetHeight;
    if (top < src.scrollTop + 24 || top + h > src.scrollTop + src.clientHeight - 24) {
      src.scrollTop = Math.max(0, top - src.clientHeight / 2);
    }
  }

  // ---------------------------------------------------- trace (no debug) --
  var traceStopBtn = $('trace-stop');
  function renderTrace(t, d) {
    if (!t || isDebug) return;
    var stateEl = $('trace-state'), lineEl = $('trace-line');
    if (t.line) { showLine(t.line, false); if (lineEl) lineEl.textContent = t.line; }
    if (traceStopBtn) traceStopBtn.hidden = t.stopped || !d.active;
    if (stateEl) stateEl.textContent = t.stopped ? (d.active ? 'Tracing stopped — full speed' : 'Tracing was stopped during the run')
      : (d.active ? 'Tracing…' : 'Last line reached');
  }
  if (traceStopBtn) traceStopBtn.addEventListener('click', function () {
    traceStopBtn.disabled = true;
    fetch(cfg.dataset.traceStopUrl, { method: 'POST', headers: { 'X-CSRFToken': csrf } })
      .then(function (r) { if (!r.ok) throw new Error(); traceStopBtn.hidden = true; })
      .catch(function () { traceStopBtn.disabled = false; liveNote.textContent = 'Could not stop tracing (the run may have just finished).'; });
  });

  // ------------------------------------------------------------ debugger --
  var dbg = { state: isDebug ? 'starting' : null, line: null, bps: {}, bpBusy: 0, sent: false };
  var dbgBtns = [].slice.call(document.querySelectorAll('[data-dbg]'));
  var prevVals = {};

  function bpList() { return Object.keys(dbg.bps).map(Number).sort(function (a, b) { return a - b; }); }
  function paintBps() {
    [].forEach.call(lines, function (ln, i) { ln.classList.toggle('is-bp', !!dbg.bps[i + 1]); });
    var box = $('dbg-bps'), clear = $('bp-clear');
    if (!box) return;
    var list = bpList();
    if (clear) clear.hidden = !list.length;
    box.textContent = '';
    if (!list.length) { box.innerHTML = '<p class="insp-empty">Click a line number to add one.</p>'; return; }
    var ul = document.createElement('ul'); ul.className = 'bp-list';
    list.forEach(function (n) {
      var li = document.createElement('li');
      li.innerHTML = '<span class="dot"></span><span class="mono"></span><span class="bp-code"></span><button type="button" title="Remove" aria-label="Remove breakpoint">×</button>';
      li.children[1].textContent = n;
      li.children[2].textContent = lines[n - 1] ? lines[n - 1].textContent.trim() : '';
      li.children[2].addEventListener('click', function () { reveal(n); });
      li.children[3].addEventListener('click', function () { toggleBp(n); });
      ul.appendChild(li);
    });
    box.appendChild(ul);
  }
  function reveal(n) {
    var el = lines[n - 1];
    if (el) src.scrollTop = Math.max(0, el.offsetTop - src.clientHeight / 2);
  }
  function syncBps() {
    if (!active) return;
    dbg.bpBusy++;
    send('breakpoints', { lines: bpList().join(',') }).then(function () { dbg.bpBusy--; }, function () { dbg.bpBusy--; });
  }
  function toggleBp(n) {
    if (dbg.bps[n]) delete dbg.bps[n]; else dbg.bps[n] = 1;
    paintBps(); syncBps();
  }
  if (isDebug && src) {
    src.addEventListener('click', function (e) {
      var ln = e.target.closest('.ln');
      if (!ln) return;
      var pad = parseFloat(getComputedStyle(ln).paddingLeft) || 0;
      if (e.clientX - ln.getBoundingClientRect().left > pad) return;   // only the line-number gutter
      toggleBp([].indexOf.call(lines, ln) + 1);
    });
    var clearBtn = $('bp-clear');
    if (clearBtn) clearBtn.addEventListener('click', function () { dbg.bps = {}; paintBps(); syncBps(); });
  }

  function send(action, extra) {
    var body = new FormData();
    body.append('action', action);
    Object.keys(extra || {}).forEach(function (k) { body.append(k, extra[k]); });
    return fetch(debugUrl, { method: 'POST', body: body, headers: { 'X-CSRFToken': csrf, 'Accept': 'application/json' } })
      .then(function (r) { return r.json().catch(function () { return {}; }).then(function (j) { if (!r.ok) throw j; return j; }); })
      .then(function (j) { if (j.debug && action !== 'breakpoints') renderDebug(j.debug); return j; })
      .catch(function (j) { liveNote.textContent = (j && j.error) || 'The debugger did not accept that command (the run may have finished).'; throw j; });
  }
  dbgBtns.forEach(function (b) {
    b.addEventListener('click', function () {
      var act = b.dataset.dbg;
      if (act === 'stop') { var s = $('stop-btn'); if (s) s.click(); return; }
      dbgBtns.forEach(function (x) { if (x.dataset.dbg !== 'stop') x.disabled = true; });   // until the next state arrives
      if (act === 'step' || act === 'continue') { dbg.state = 'running'; showLine(curLine, true); setDbgLabel('Running…'); }
      send(act).catch(function () {}).then(function () { kick(80); });
    });
  });

  function setDbgLabel(t) { var el = $('dbg-state'); if (el) el.textContent = t; }
  function renderVars(d) {
    var box = $('dbg-vars'), where = $('dbg-where');
    if (!box) return;
    if (where) where.textContent = d.state === 'paused' && d.line ? 'line ' + d.line : '';
    if (d.state !== 'paused') {
      if (d.state !== 'starting') box.innerHTML = '<p class="insp-empty">' + (d.state === 'running' ? 'Running… pause to inspect.' : 'Not paused.') + '</p>';
      return;
    }
    var scopes = d.scopes || [];
    if (!scopes.length || !scopes.some(function (s) { return (s.vars || []).length; })) {
      box.innerHTML = '<p class="insp-empty">' + (src && src.dataset.lang === 'python' ? 'No variables in this scope yet.' : 'Variables are shown for Python scripts.') + '</p>';
      return;
    }
    var table = document.createElement('table'); table.className = 'vars';
    var next = {};
    scopes.forEach(function (sc) {
      if (scopes.length > 1) {
        var hr = table.insertRow(); hr.className = 'v-scope';
        var c = hr.insertCell(); c.colSpan = 2; c.textContent = sc.name;
      }
      (sc.vars || []).forEach(function (v) {
        var key = sc.name + ':' + v.name, tr = table.insertRow();
        next[key] = v.value;
        if (prevVals[key] !== undefined && prevVals[key] !== v.value) tr.className = 'is-changed';
        var n = tr.insertCell(); n.className = 'v-name'; n.textContent = v.name; n.title = v.name + ' : ' + v.type;
        var val = tr.insertCell(); val.className = 'v-val'; val.textContent = v.value;
        var ty = document.createElement('span'); ty.className = 'v-type'; ty.textContent = v.type; val.appendChild(ty);
      });
    });
    prevVals = next;
    box.textContent = ''; box.appendChild(table);
  }
  function renderStack(d) {
    var box = $('dbg-stack');
    if (!box || d.state === 'running' || d.state === 'starting') return;
    var st = d.state === 'paused' ? (d.stack || []) : [];
    if (!st.length) { box.innerHTML = '<p class="insp-empty">' + (d.state === 'paused' && d.line ? '&lt;script&gt; · line ' + d.line : '—') + '</p>'; return; }
    var ul = document.createElement('ul'); ul.className = 'stack-list';
    st.forEach(function (f) {
      var li = document.createElement('li');
      var fn = document.createElement('span'); fn.textContent = f.fn;
      var ln = document.createElement('span'); ln.className = 's-line'; ln.textContent = ':' + f.line;
      li.appendChild(fn); li.appendChild(ln);
      li.addEventListener('click', function () { reveal(f.line); });
      ul.appendChild(li);
    });
    box.textContent = ''; box.appendChild(ul);
  }
  function renderDebug(d) {
    if (!isDebug || !d) return;
    dbg.state = d.state; dbg.line = d.line;
    var paused = d.state === 'paused', running = d.state === 'running';
    var can = { 'continue': paused, step: paused, pause: running, detach: paused || running, stop: active };
    dbgBtns.forEach(function (b) { b.disabled = !can[b.dataset.dbg]; });
    var label = { starting: 'Starting…', paused: 'Paused at line ' + d.line, running: 'Running…',
      detached: 'Detached — running at full speed', ended: 'Ended' }[d.state] || d.state;
    setDbgLabel(label);
    if (paused || d.state === 'ended' || d.state === 'detached') showLine(d.line, false);
    else if (running) showLine(d.line || curLine, true);
    if (paused && badge) { badge.className = 'st st-paused'; badge.textContent = 'Paused'; }
    if (Array.isArray(d.breakpoints) && !dbg.bpBusy && active && d.state !== 'ended') {
      var srv = {}; d.breakpoints.forEach(function (n) { srv[n] = 1; });
      if (bpList().join(',') !== d.breakpoints.slice().sort(function (a, b) { return a - b; }).join(',')) { dbg.bps = srv; paintBps(); }
    }
    renderVars(d); renderStack(d);
    if (paused && !dbg.sent) { dbg.sent = true; var step = document.querySelector('[data-dbg="step"]'); if (step) step.focus({ preventScroll: true }); }
  }

  // -------------------------------------------------------------- status --
  function render(d) {
    renderTrace(d.trace, d);
    if (badge) { badge.className = 'st st-' + d.status; badge.textContent = d.status_label; }
    // Shown as a tab of a script page (iframe): tell it, so the tab shows the status.
    if (window.parent !== window) {
      try { window.parent.postMessage({ sr: 'run-status', run: d.id || cfg.dataset.runId, status: d.status, label: d.status_label }, location.origin); } catch (e) { /* not same-origin */ }
    }
    exitEl.textContent = d.exit_code === null || d.exit_code === undefined ? '—' : d.exit_code;
    startedEl.textContent = d.started_at ? d.started_at.replace('T', ' ') : '—';
    finishedEl.textContent = d.finished_at ? d.finished_at.replace('T', ' ') : '—';
    if (pidEl && d.pid) pidEl.textContent = d.pid;
    base = { seconds: d.duration_seconds, at: Date.now(), running: d.status === 'running' };
    durationEl.textContent = d.duration_seconds === null ? '—' : d.duration;
    if (stopForm) stopForm.hidden = !d.active;
    if (delForm) delForm.hidden = d.active;
    if (d.status === 'queued') {
      queueNote.hidden = false;
      queueNote.textContent = 'Queued' + (d.queue_position ? ' (position ' + d.queue_position + ')' : '') + ' — waiting for a free execution slot.';
    } else queueNote.hidden = true;
    var show = d.error && d.status !== 'success';
    errorNote.hidden = !show;
    if (show) errorText.textContent = d.error;
    live.classList.toggle('is-live', !!d.active);
    liveNote.textContent = d.active ? 'Live · every ' + ((isDebug ? fast : interval) / 1000) + 's' : 'Finished';
    if (isDebug) renderDebug(d.debug || (d.active ? null : { state: 'ended', line: curLine, scopes: [], stack: [], breakpoints: [] }));
    var shownLabel = isDebug && dbg.state === 'paused' && d.active ? 'Paused' : d.status_label;
    document.title = shownLabel + ' · ' + baseTitle;
  }

  function poll() {
    if (inFlight) return;
    inFlight = true;
    fetch(statusUrl + '?out=' + offsets.out + '&err=' + offsets.err, { headers: { 'Accept': 'application/json' }, cache: 'no-store' })
      .then(function (r) { if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); })
      .then(function (d) {
        append(out, d.stdout); append(err, d.stderr);
        offsets.out = d.stdout_next; offsets.err = d.stderr_next;
        active = d.active;
        render(d);
        if (!active) idleAfterDone = (d.stdout || d.stderr) ? 0 : idleAfterDone + 1;   // drain, then stop
      })
      .catch(function () { live.classList.remove('is-live'); liveNote.textContent = 'Connection problem — retrying…'; })
      .then(function () { inFlight = false; schedule(); });
  }
  function schedule(delay) {
    clearTimeout(timer);
    if (!active && idleAfterDone >= 1) return;          // fully drained
    var every = active ? ((isDebug && dbg.state !== 'detached') ? fast : interval) : 200;
    timer = setTimeout(poll, delay === undefined ? every : delay);
  }
  function kick(delay) { if (!inFlight) schedule(delay || 0); }

  setInterval(function () {                              // duration ticks between polls
    if (base.running && base.seconds !== null) durationEl.textContent = fmt(base.seconds + (Date.now() - base.at) / 1000);
  }, 1000);

  // Scrolling up pauses auto-scroll; ticking the box resumes it.
  [out, err].forEach(function (el) {
    el.addEventListener('scroll', function () {
      var atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 24;
      if (!atBottom && auto.checked && el.dataset.userScroll === '1') auto.checked = false;
    });
    ['wheel', 'touchmove', 'keydown', 'mousedown'].forEach(function (ev) {
      el.addEventListener(ev, function () { el.dataset.userScroll = '1'; }, { passive: true });
    });
  });
  auto.addEventListener('change', function () {
    if (auto.checked) { out.scrollTop = out.scrollHeight; err.scrollTop = err.scrollHeight; }
  });
  $('refresh-now').addEventListener('click', function () { active = true; idleAfterDone = 0; kick(0); });
  [].forEach.call(document.querySelectorAll('[data-copy]'), function (btn) {
    btn.addEventListener('click', function () { window.sr.copy($(btn.dataset.copy).textContent, btn.querySelector('span')); });
  });

  paintBps();
  poll();
})();
