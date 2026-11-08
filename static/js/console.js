/* Live execution console: polls the status endpoint with byte offsets and
   appends only the new output. Vanilla JS. */
(function () {
  var cfg = document.getElementById('console-config');
  if (!cfg) return;
  var url = cfg.dataset.statusUrl;
  var interval = Math.max(500, parseInt(cfg.dataset.interval, 10) || 2000);

  var out = document.getElementById('out'), err = document.getElementById('err');
  var badge = document.getElementById('status-badge'), durationEl = document.getElementById('duration');
  var exitEl = document.getElementById('exit-code'), startedEl = document.getElementById('started');
  var finishedEl = document.getElementById('finished'), stopForm = document.getElementById('stop-form');
  var queueNote = document.getElementById('queue-note');
  var errorNote = document.getElementById('error-note'), errorText = document.getElementById('error-text');
  var liveNote = document.getElementById('live-note'), auto = document.getElementById('autoscroll');
  var MAX_CHARS = 1500000, TRIM_TO = 1000000;

  var offsets = { out: 0, err: 0 };
  var active = true, timer = null, idleAfterDone = 0, inFlight = false;
  var base = { seconds: null, at: 0, running: false };

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

  // ---- running-line highlight (only when the run was started with tracing)
  var traceCard = document.getElementById('trace-card');
  var traceSrc = document.getElementById('trace-src');
  var traceLines = traceSrc ? traceSrc.querySelectorAll('.ln') : [];
  var traceCur = null, traceStopBtn = document.getElementById('trace-stop');
  if (window.highlight && traceSrc) window.highlight.apply(traceCard);

  function showTrace(t, d) {
    if (!traceCard || !t) return;
    var stateEl = document.getElementById('trace-state');
    if (t.line && traceLines[t.line - 1] && traceCur !== t.line) {
      if (traceCur) traceLines[traceCur - 1].classList.remove('is-current');
      traceCur = t.line;
      var el = traceLines[t.line - 1];
      el.classList.add('is-current');
      document.getElementById('trace-line').textContent = t.line;
      var box = traceSrc, top = el.offsetTop, h = el.offsetHeight;
      if (top < box.scrollTop + 20 || top + h > box.scrollTop + box.clientHeight - 20) {
        box.scrollTop = Math.max(0, top - box.clientHeight / 2);
      }
    }
    if (traceStopBtn) traceStopBtn.hidden = t.stopped || !d.active;
    stateEl.textContent = t.stopped ? (d.active ? 'Tracing stopped — running at full speed' : 'Tracing was stopped during the run')
      : (d.active ? 'Tracing…' : 'Last line reached');
  }

  if (traceStopBtn) traceStopBtn.addEventListener('click', function () {
    traceStopBtn.disabled = true;
    fetch(cfg.dataset.traceStopUrl, { method: 'POST', headers: { 'X-CSRFToken': cfg.dataset.csrf } })
      .then(function (r) { if (!r.ok) throw new Error(); traceStopBtn.hidden = true; })
      .catch(function () { traceStopBtn.disabled = false; liveNote.textContent = 'Could not stop tracing (the run may have just finished).'; });
  });

  function render(d) {
    showTrace(d.trace, d);
    badge.className = 'st st-' + d.status;
    badge.textContent = d.status_label;
    exitEl.textContent = d.exit_code === null || d.exit_code === undefined ? '—' : d.exit_code;
    startedEl.textContent = d.started_at ? d.started_at.replace('T', ' ') : '—';
    finishedEl.textContent = d.finished_at ? d.finished_at.replace('T', ' ') : '—';
    base = { seconds: d.duration_seconds, at: Date.now(), running: d.status === 'running' };
    durationEl.textContent = d.duration_seconds === null ? '—' : d.duration;
    if (stopForm) stopForm.hidden = !d.active;
    var delForm = document.getElementById('delete-form');
    if (delForm) delForm.hidden = d.active;
    if (d.status === 'queued') {
      queueNote.hidden = false;
      queueNote.textContent = 'Queued' + (d.queue_position ? ' (position ' + d.queue_position + ')' : '') +
        ' — waiting for a free execution slot.';
    } else queueNote.hidden = true;
    var show = d.error && d.status !== 'success';
    errorNote.hidden = !show;
    if (show) errorText.textContent = d.error;
    liveNote.textContent = d.active ? 'Live · updating every ' + (interval / 1000) + 's' : 'Finished';
    document.title = d.status_label + ' · ' + document.title.replace(/^.*? · /, '');
  }

  function poll() {
    if (inFlight) return;
    inFlight = true;
    fetch(url + '?out=' + offsets.out + '&err=' + offsets.err, { headers: { 'Accept': 'application/json' }, cache: 'no-store' })
      .then(function (r) { if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); })
      .then(function (d) {
        append(out, d.stdout); append(err, d.stderr);
        offsets.out = d.stdout_next; offsets.err = d.stderr_next;
        render(d);
        active = d.active;
        // Once finished, keep draining until a poll returns nothing new.
        if (!active) idleAfterDone = (d.stdout || d.stderr) ? 0 : idleAfterDone + 1;
      })
      .catch(function () { liveNote.textContent = 'Connection problem — retrying…'; })
      .then(function () {
        inFlight = false;
        schedule();
      });
  }

  function schedule() {
    clearTimeout(timer);
    if (!active && idleAfterDone >= 1) return;          // fully drained
    timer = setTimeout(poll, active ? interval : 200);
  }

  // Duration ticks every second between polls while running.
  setInterval(function () {
    if (base.running && base.seconds !== null) {
      durationEl.textContent = fmt(base.seconds + (Date.now() - base.at) / 1000);
    }
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

  document.getElementById('refresh-now').addEventListener('click', function () { clearTimeout(timer); active = true; idleAfterDone = 0; poll(); });

  [].forEach.call(document.querySelectorAll('[data-copy]'), function (btn) {
    btn.addEventListener('click', function () {
      var text = document.getElementById(btn.dataset.copy).textContent;
      var label = btn.querySelector('span');
      function done() { label.textContent = 'Copied'; setTimeout(function () { label.textContent = 'Copy'; }, 1500); }
      if (navigator.clipboard && window.isSecureContext) navigator.clipboard.writeText(text).then(done);
      else {
        var ta = document.createElement('textarea'); ta.value = text; document.body.appendChild(ta);
        ta.select(); try { document.execCommand('copy'); done(); } catch (e) {} ta.remove();
      }
    });
  });

  poll();
})();
