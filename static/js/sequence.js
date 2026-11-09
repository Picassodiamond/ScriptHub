/* Sequences: the diagram of a sequence (boxes = steps, arrows = when the next step runs).

   Sequence page (#seq-editor): scripts are dragged in from the explorer, boxes are moved,
   connected (drag the dot under a box onto another box) and removed; a click on an
   arrow's label cycles its condition (succeeded / failed / always). Every change is one
   POST to the graph endpoint, which answers with the stored graph (the server decides).
   A click on a box shows its settings (embedded step page) in the right-hand column.
   Play / Pause / Stop in the toolbar run the sequence in place (the boxes show live status).

   Sequence run page (#seq-run): the same diagram, read-only, with each step's live status;
   a click on a box shows that step's console.

   Plain DOM + SVG, no libraries. Coordinates are page ("world") pixels; the view pans and zooms. */
(function () {
  'use strict';

  var W = 200, H = 60, GRID = 20, MIME = 'application/x-scripthub-script';
  var WHEN = ['success', 'failure', 'always'];
  var WHEN_TEXT = { success: '✓ succeeded', failure: '✗ failed', always: '↷ always' };
  var WHEN_TITLE = {
    success: 'The next step runs if this one succeeded',
    failure: 'The next step runs if this one did not succeed',
    always: 'The next step runs when this one has finished, either way'
  };
  var FINAL = ['success', 'failed', 'timed_out', 'cancelled', 'interrupted'];
  var SVGNS = 'http://www.w3.org/2000/svg';

  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  }
  function svgEl(tag, attrs) {
    var e = document.createElementNS(SVGNS, tag);
    Object.keys(attrs || {}).forEach(function (k) { e.setAttribute(k, attrs[k]); });
    return e;
  }
  function snap(v) { return Math.max(0, Math.round(v / GRID) * GRID); }
  function clamp(v, a, b) { return Math.max(a, Math.min(b, v)); }
  function storeGet(k) { try { return localStorage.getItem(k); } catch (e) { return null; } }
  function storeSet(k, v) { try { localStorage.setItem(k, v); } catch (e) { /* storage unavailable */ } }

  /* Does an arrow apply, given the status of the step it starts from? true / false / null (not finished). */
  function linkState(link, statusOf) {
    var s = statusOf[link.from];
    if (s === 'skipped') return false;
    if (s === 'success') return link.when === 'success' || link.when === 'always';
    if (FINAL.indexOf(s) >= 0) return link.when === 'failure' || link.when === 'always';
    return null;
  }

  /* Where an arrow from box a to box b runs: rows apart -> bottom to top, same row -> side to side. */
  function geometry(a, b) {
    var ax = a.x + W / 2, ay = a.y + H / 2, bx = b.x + W / 2, by = b.y + H / 2, p1, p2, n1, n2;
    if (Math.abs(by - ay) >= H + GRID) {
      if (by >= ay) { p1 = [ax, a.y + H]; n1 = [0, 1]; p2 = [bx, b.y]; n2 = [0, -1]; }
      else { p1 = [ax, a.y]; n1 = [0, -1]; p2 = [bx, b.y + H]; n2 = [0, 1]; }
    } else if (bx >= ax) { p1 = [a.x + W, ay]; n1 = [1, 0]; p2 = [b.x, by]; n2 = [-1, 0]; }
    else { p1 = [a.x, ay]; n1 = [-1, 0]; p2 = [b.x + W, by]; n2 = [1, 0]; }
    return curve(p1, n1, p2, n2);
  }
  function curve(p1, n1, p2, n2) {
    var d = Math.sqrt(Math.pow(p2[0] - p1[0], 2) + Math.pow(p2[1] - p1[1], 2));
    var c = clamp(d * 0.45, 24, 120);
    var c1 = [p1[0] + n1[0] * c, p1[1] + n1[1] * c], c2 = [p2[0] + n2[0] * c, p2[1] + n2[1] * c];
    return {
      d: 'M' + p1.join(',') + ' C' + c1.join(',') + ' ' + c2.join(',') + ' ' + p2.join(','),
      mid: [(p1[0] + 3 * c1[0] + 3 * c2[0] + p2[0]) / 8, (p1[1] + 3 * c1[1] + 3 * c2[1] + p2[1]) / 8]
    };
  }

  /* ------------------------------------------------------------------ diagram --- */
  function Diagram(canvas, opts) {
    var self = this;
    this.canvas = canvas;
    this.opts = opts;
    this.data = { steps: [], links: [] };
    this.byId = {};
    this.view = { x: 0, y: 0, z: 1 };
    this.selected = null;

    canvas.classList.add('seq-canvas');
    if (!opts.editable) canvas.classList.add('seq-readonly');
    this.world = el('div', 'seq-world');
    this.svg = svgEl('svg', { 'class': 'seq-links', width: '1', height: '1', 'aria-hidden': 'true' });
    var defs = svgEl('defs');
    WHEN.concat(['temp']).forEach(function (w) {
      var m = svgEl('marker', { id: 'seq-arrow-' + w, 'class': 'seq-arrow-' + w, viewBox: '0 0 10 10', refX: '9', refY: '5',
                                markerWidth: '11', markerHeight: '11', markerUnits: 'userSpaceOnUse', orient: 'auto' });
      m.appendChild(svgEl('path', { d: 'M0,0 L10,5 L0,10 z' }));
      defs.appendChild(m);
    });
    this.svg.appendChild(defs);
    this.paths = svgEl('g');
    this.svg.appendChild(this.paths);
    this.temp = svgEl('path', { 'class': 'seq-temp', 'marker-end': 'url(#seq-arrow-temp)' });
    this.temp.style.display = 'none';
    this.svg.appendChild(this.temp);
    this.nodes = el('div', 'seq-nodes');
    this.labels = el('div', 'seq-labels');
    this.world.appendChild(this.svg);
    this.world.appendChild(this.nodes);
    this.world.appendChild(this.labels);
    canvas.appendChild(this.world);
    this.empty = el('div', 'seq-empty');
    if (canvas.getAttribute('data-empty-title')) {
      this.empty.innerHTML = '<svg class="i"><use href="#i-sequence"/></svg>';
      this.empty.appendChild(el('h2', '', canvas.getAttribute('data-empty-title')));
      this.empty.appendChild(el('p', 'small', canvas.getAttribute('data-empty-text') || ''));
    }
    this.empty.hidden = true;
    canvas.appendChild(this.empty);

    this.bindPointer();
    canvas.addEventListener('wheel', function (e) {
      e.preventDefault();
      var r = canvas.getBoundingClientRect();
      if (e.ctrlKey || e.metaKey) self.zoomAt(Math.exp(-e.deltaY * 0.0015), e.clientX - r.left, e.clientY - r.top);
      else { self.view.x -= e.deltaX; self.view.y -= e.deltaY; self.apply(); }
    }, { passive: false });
  }

  Diagram.prototype.set = function (data) {
    this.data = data;
    this.byId = {};
    var self = this;
    data.steps.forEach(function (s) { self.byId[s.id] = s; });
    if (this.selected && !this.byId[this.selected]) this.selected = null;
    this.render();
  };

  /* While a run is going the diagram is not editable (the run uses the sequence as it was at Play). */
  Diagram.prototype.lock = function (on) {
    this.locked = !!on;
    this.canvas.classList.toggle('is-locked', this.locked);
  };

  Diagram.prototype.statusOf = function () {
    var out = {};
    this.data.steps.forEach(function (s) { out[s.id] = s.status; });
    return out;
  };

  Diagram.prototype.render = function () {
    var self = this, o = this.opts;
    this.nodes.textContent = '';
    this.data.steps.forEach(function (s) {
      var n = el('div', 'seq-node');
      n.setAttribute('data-id', s.id);
      n.style.left = s.x + 'px';
      n.style.top = s.y + 'px';
      n.title = s.path || '';
      if (s.problem) n.classList.add('has-problem');
      if (s.status) n.setAttribute('data-status', s.status);
      if (s.run_id) n.classList.add('has-run');
      if (s.held) n.classList.add('is-held');
      if (s.id === self.selected) n.classList.add('is-selected');
      var top = el('div', 'seq-node-top');
      top.appendChild(el('span', 'seq-n', String(s.n)));
      if (s.ext) top.appendChild(el('span', 'lang lang-' + s.ext, s.ext));
      top.appendChild(el('span', 'seq-node-name', s.name));
      var act = o.boxAction && o.boxAction(s);
      if (act) {
        var ab = el('button', 'seq-boxbtn' + (act.on ? ' is-on' : ''));
        ab.type = 'button'; ab.title = act.title; ab.setAttribute('aria-label', act.title + ' (step ' + s.n + ')');
        if (act.on) ab.setAttribute('aria-pressed', 'true');
        ab.innerHTML = '<svg class="i"><use href="#i-' + act.icon + '"/></svg>';
        top.appendChild(ab);
      }
      if (o.onOpen) {
        var op = el('button', 'seq-open');
        op.type = 'button'; op.title = 'Open the script (double-click the step)'; op.setAttribute('aria-label', 'Open the script of step ' + s.n);
        op.innerHTML = '<svg class="i"><use href="#i-file"/></svg>';
        top.appendChild(op);
      }
      if (o.editable) {
        var x = el('button', 'seq-x', '×');
        x.type = 'button'; x.title = 'Remove step'; x.setAttribute('aria-label', 'Remove step ' + s.n);
        top.appendChild(x);
      }
      n.appendChild(top);
      var sub = el('div', 'seq-node-sub');
      if (s.status) {
        sub.appendChild(el('span', 'st st-sm st-' + (s.held ? 'paused' : s.status), s.status_label || s.status));
        sub.appendChild(el('span', '', s.host || ''));
      } else {
        var bits = s.problem ? [s.problem] : [s.source, s.host];
        if (!s.problem && s.join === 'any' && s.incoming > 1) bits.push('starts after the first arrow');
        sub.appendChild(el('span', '', bits.filter(Boolean).join(' · ')));
        sub.title = bits.filter(Boolean).join(' · ');
      }
      n.appendChild(sub);
      if (o.editable) {
        var port = el('span', 'seq-port');
        port.title = 'Drag onto another step to connect';
        n.appendChild(port);
      }
      self.nodes.appendChild(n);
    });
    this.empty.hidden = this.data.steps.length > 0;
    this.drawLinks();
    this.apply();
  };

  Diagram.prototype.drawLinks = function () {
    var self = this, o = this.opts, statusOf = this.statusOf(), run = this.data.steps.some(function (s) { return s.status; });
    this.paths.textContent = '';
    this.labels.textContent = '';
    this.data.links.forEach(function (k) {
      var a = self.byId[k.from], b = self.byId[k.to];
      if (!a || !b) return;
      var g = geometry(a, b), state = run ? linkState(k, statusOf) : null;
      var path = svgEl('path', { d: g.d, 'class': 'seq-link seq-link-' + k.when, 'marker-end': 'url(#seq-arrow-' + k.when + ')' });
      var label = el('div', 'seq-label seq-when-' + k.when);
      if (state === true) path.classList.add('is-taken');
      if (state === false) { path.classList.add('is-dead'); label.classList.add('is-dead'); }
      self.paths.appendChild(path);
      label.style.left = g.mid[0] + 'px';
      label.style.top = g.mid[1] + 'px';
      label.setAttribute('data-from', k.from);
      label.setAttribute('data-to', k.to);
      if (o.editable) {
        var b1 = el('button', 'seq-when', WHEN_TEXT[k.when]);
        b1.type = 'button'; b1.title = WHEN_TITLE[k.when] + ' — click to change';
        var b2 = el('button', 'seq-unlink', '×');
        b2.type = 'button'; b2.title = 'Remove this arrow'; b2.setAttribute('aria-label', 'Remove arrow');
        label.appendChild(b1); label.appendChild(b2);
      } else {
        var t = el('span', '', WHEN_TEXT[k.when]);
        t.title = WHEN_TITLE[k.when];
        label.appendChild(t);
      }
      self.labels.appendChild(label);
    });
  };

  Diagram.prototype.apply = function () {
    var v = this.view;
    this.world.style.transform = 'translate(' + v.x + 'px,' + v.y + 'px) scale(' + v.z + ')';
    this.canvas.style.backgroundPosition = v.x + 'px ' + v.y + 'px';
    this.canvas.style.backgroundSize = (GRID * v.z) + 'px ' + (GRID * v.z) + 'px';
    if (this.opts.viewKey) {
      clearTimeout(this._saveView);
      var key = this.opts.viewKey;
      this._saveView = setTimeout(function () { storeSet(key, JSON.stringify(v)); }, 300);
    }
  };

  Diagram.prototype.zoomAt = function (f, cx, cy) {
    var v = this.view, z = clamp(v.z * f, 0.3, 1.6);
    v.x = cx - (cx - v.x) * (z / v.z);
    v.y = cy - (cy - v.y) * (z / v.z);
    v.z = z;
    this.apply();
  };
  Diagram.prototype.zoom = function (f) {
    var r = this.canvas.getBoundingClientRect();
    this.zoomAt(f, r.width / 2, r.height / 2);
  };

  Diagram.prototype.fit = function () {
    var steps = this.data.steps, r = this.canvas.getBoundingClientRect();
    if (!steps.length || !r.width) { this.view = { x: 0, y: 0, z: 1 }; this.apply(); return; }
    var x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity, pad = 40;
    steps.forEach(function (s) { x0 = Math.min(x0, s.x); y0 = Math.min(y0, s.y); x1 = Math.max(x1, s.x + W); y1 = Math.max(y1, s.y + H); });
    var z = clamp(Math.min((r.width - 2 * pad) / (x1 - x0), (r.height - 2 * pad) / (y1 - y0)), 0.3, 1);
    this.view = { x: (r.width - (x1 - x0) * z) / 2 - x0 * z, y: Math.max(pad, (r.height - (y1 - y0) * z) / 2) - y0 * z, z: z };
    this.apply();
  };

  Diagram.prototype.restoreView = function () {
    var saved = null;
    try { saved = JSON.parse(storeGet(this.opts.viewKey) || 'null'); } catch (e) { saved = null; }
    if (saved && isFinite(saved.x) && isFinite(saved.y) && isFinite(saved.z)) {
      this.view = { x: saved.x, y: saved.y, z: clamp(saved.z, 0.3, 1.6) };
      this.apply();
    } else this.fit();
  };

  Diagram.prototype.toWorld = function (clientX, clientY) {
    var r = this.canvas.getBoundingClientRect(), v = this.view;
    return { x: (clientX - r.left - v.x) / v.z, y: (clientY - r.top - v.y) / v.z };
  };

  Diagram.prototype.select = function (id) {
    this.selected = id || null;
    [].forEach.call(this.nodes.children, function (n) { n.classList.toggle('is-selected', n.getAttribute('data-id') === id); });
  };

  Diagram.prototype.nodeAt = function (clientX, clientY) {
    var hit = document.elementFromPoint(clientX, clientY), n = hit && hit.closest && hit.closest('.seq-node');
    return n && this.nodes.contains(n) ? n : null;
  };

  Diagram.prototype.bindPointer = function () {
    var self = this, canvas = this.canvas, o = this.opts, drag = null;

    canvas.addEventListener('pointerdown', function (e) {
      if (e.button !== 0 || drag) return;
      var t = e.target;
      if (t.closest('.seq-x, .seq-open, .seq-boxbtn, .seq-label')) return;    // their own click handlers
      var node = t.closest('.seq-node');
      if (o.editable && !self.locked && t.closest('.seq-port') && node) {
        drag = { type: 'link', from: node.getAttribute('data-id') };
        canvas.classList.add('is-linking');
      } else if (node) {
        var s = self.byId[node.getAttribute('data-id')];
        drag = { type: 'node', id: s.id, node: node, sx: e.clientX, sy: e.clientY, ox: s.x, oy: s.y, moved: false };
      } else {
        drag = { type: 'pan', sx: e.clientX, sy: e.clientY, vx: self.view.x, vy: self.view.y, moved: false };
        canvas.classList.add('is-panning');
      }
      canvas.focus({ preventScroll: true });
      try { canvas.setPointerCapture(e.pointerId); } catch (err) { /* old browser */ }
      e.preventDefault();
    });

    canvas.addEventListener('pointermove', function (e) {
      if (!drag) return;
      var dx = e.clientX - (drag.sx || 0), dy = e.clientY - (drag.sy || 0);
      if (drag.type === 'pan') {
        if (Math.abs(dx) + Math.abs(dy) > 3) drag.moved = true;
        self.view.x = drag.vx + dx; self.view.y = drag.vy + dy; self.apply();
      } else if (drag.type === 'node') {
        if (!o.editable || self.locked) return;
        if (!drag.moved && Math.abs(dx) + Math.abs(dy) < 5) return;
        drag.moved = true;
        drag.node.classList.add('is-dragging');
        var s = self.byId[drag.id];
        s.x = snap(drag.ox + dx / self.view.z); s.y = snap(drag.oy + dy / self.view.z);
        drag.node.style.left = s.x + 'px'; drag.node.style.top = s.y + 'px';
        self.drawLinks();
      } else if (drag.type === 'link') {
        var a = self.byId[drag.from], p = self.toWorld(e.clientX, e.clientY);
        var target = self.nodeAt(e.clientX, e.clientY);
        [].forEach.call(self.nodes.children, function (n) { n.classList.toggle('is-target', n === target && n.getAttribute('data-id') !== drag.from); });
        var c = curve([a.x + W / 2, a.y + H], [0, 1], [p.x, p.y], [0, p.y >= a.y + H ? -1 : 1]);
        self.temp.setAttribute('d', c.d);
        self.temp.style.display = '';
      }
    });

    function end(e, cancelled) {
      if (!drag) return;
      var d = drag;
      drag = null;
      canvas.classList.remove('is-panning', 'is-linking');
      self.temp.style.display = 'none';
      [].forEach.call(self.nodes.children, function (n) { n.classList.remove('is-target', 'is-dragging'); });
      if (cancelled) { if (d.type === 'node' && d.moved) self.render(); return; }
      if (d.type === 'pan' && !d.moved && o.onSelect) o.onSelect(null);
      else if (d.type === 'node') {
        if (d.moved) { var s = self.byId[d.id]; if (s.x !== d.ox || s.y !== d.oy) o.onMove(d.id, s.x, s.y); }
        else if (o.onSelect) o.onSelect(d.id);
      } else if (d.type === 'link') {
        var target = self.nodeAt(e.clientX, e.clientY), to = target && target.getAttribute('data-id');
        if (to && to !== d.from) {
          var exists = self.data.links.some(function (k) { return k.from === d.from && k.to === to; });
          if (!exists) o.onLink(d.from, to, 'success');
        }
      }
    }
    canvas.addEventListener('pointerup', function (e) { end(e, false); });
    canvas.addEventListener('pointercancel', function (e) { end(e, true); });

    /* the box's own ▶ / ⏸ (works while the diagram is locked) */
    if (o.onBoxAction) {
      canvas.addEventListener('click', function (e) {
        var b = e.target.closest('.seq-boxbtn');
        if (b) o.onBoxAction(b.closest('.seq-node').getAttribute('data-id'));
      });
    }

    /* open a step's script: its button, or a double-click on the box */
    if (o.onOpen) {
      canvas.addEventListener('click', function (e) {
        var b = e.target.closest('.seq-open');
        if (b) o.onOpen(b.closest('.seq-node').getAttribute('data-id'));
      });
      canvas.addEventListener('dblclick', function (e) {
        /* the canvas holds the pointer while a box is pressed (dragging), so the event's target is
           the canvas: find the box under the pointer instead */
        var hit = document.elementFromPoint(e.clientX, e.clientY), n = self.nodeAt(e.clientX, e.clientY);
        if (n && !(hit && hit.closest('.seq-x, .seq-port, .seq-open, .seq-boxbtn'))) o.onOpen(n.getAttribute('data-id'));
      });
    }

    if (!o.editable) return;
    canvas.addEventListener('click', function (e) {
      if (self.locked) return;
      var x = e.target.closest('.seq-x');
      if (x) { o.onRemove(x.closest('.seq-node').getAttribute('data-id')); return; }
      var label = e.target.closest('.seq-label');
      if (!label) return;
      var from = label.getAttribute('data-from'), to = label.getAttribute('data-to');
      if (e.target.closest('.seq-unlink')) { o.onUnlink(from, to); return; }
      if (e.target.closest('.seq-when')) {
        var link = self.data.links.filter(function (k) { return k.from === from && k.to === to; })[0];
        if (link) o.onLink(from, to, WHEN[(WHEN.indexOf(link.when) + 1) % WHEN.length]);
      }
    });
    canvas.addEventListener('keydown', function (e) {
      if (e.target !== canvas) return;
      if ((e.key === 'Delete' || e.key === 'Backspace') && self.selected && !self.locked) { e.preventDefault(); o.onRemove(self.selected); }
    });

    /* scripts dragged in from the explorer */
    function ours(e) { return e.dataTransfer && [].indexOf.call(e.dataTransfer.types || [], MIME) >= 0; }
    canvas.addEventListener('dragover', function (e) {
      if (!ours(e) || self.locked) return;
      e.preventDefault();
      e.dataTransfer.dropEffect = 'copy';
      canvas.classList.add('is-drop');
    });
    canvas.addEventListener('dragleave', function (e) {
      if (!e.relatedTarget || !canvas.contains(e.relatedTarget)) canvas.classList.remove('is-drop');
    });
    canvas.addEventListener('drop', function (e) {
      canvas.classList.remove('is-drop');
      if (!ours(e) || self.locked) return;
      e.preventDefault();
      var p = self.toWorld(e.clientX, e.clientY);
      o.onAdd(e.dataTransfer.getData(MIME), snap(p.x - W / 2), snap(p.y - H / 2));
    });
  };

  /* Rows that follow the arrows: a step sits one row below the lowest step leading to it;
     in a row, steps are ordered by where the steps leading to them are. */
  Diagram.prototype.tidyPositions = function () {
    var steps = this.data.steps, byId = this.byId, ins = {}, outs = {}, indeg = {}, layer = {}, order = [];
    steps.forEach(function (s) { ins[s.id] = []; outs[s.id] = []; });
    this.data.links.forEach(function (k) { if (ins[k.to] && outs[k.from]) { outs[k.from].push(k.to); ins[k.to].push(k.from); } });
    steps.forEach(function (s) { indeg[s.id] = ins[s.id].length; });
    var ready = steps.filter(function (s) { return !indeg[s.id]; }).map(function (s) { return s.id; });
    function byPlace(a, b) { return byId[a].y - byId[b].y || byId[a].x - byId[b].x; }
    while (ready.length) {
      ready.sort(byPlace);
      var cur = ready.shift();
      order.push(cur);
      layer[cur] = ins[cur].reduce(function (m, p) { return Math.max(m, layer[p] + 1); }, 0);
      outs[cur].forEach(function (n) { if (--indeg[n] === 0) ready.push(n); });
    }
    /* a step with no arrow into it sits just above the first step it leads to (its arrow
       then never crosses a row of boxes) */
    order.slice().reverse().forEach(function (id) {
      if (!ins[id].length && outs[id].length) layer[id] = Math.min.apply(null, outs[id].map(function (n) { return layer[n]; })) - 1;
    });
    var rows = [], col = {}, widest = 0;
    order.forEach(function (id) { (rows[layer[id]] = rows[layer[id]] || []).push(id); });
    rows.forEach(function (row) {
      var key = {};
      row.forEach(function (id) {
        var ps = ins[id];
        key[id] = ps.length ? ps.reduce(function (t, p) { return t + col[p]; }, 0) / ps.length : byId[id].x / (W + 2 * GRID);
      });
      row.sort(function (a, b) { return key[a] - key[b] || byPlace(a, b); });
      row.forEach(function (id, i) { col[id] = i; });
      widest = Math.max(widest, row.length);
    });
    var out = {};
    rows.forEach(function (row, r) {
      var shift = (widest - row.length) / 2;
      row.forEach(function (id, i) { out[id] = [snap(2 * GRID + (i + shift) * (W + 2 * GRID)), 2 * GRID + r * (H + 3 * GRID)]; });
    });
    return out;
  };

  /* --------------------------------------------------------------- helpers --- */
  function csrf() {
    var m = /(?:^|;\s*)csrftoken=([^;]+)/.exec(document.cookie);
    if (m) return decodeURIComponent(m[1]);
    var i = document.querySelector('input[name=csrfmiddlewaretoken]');
    return i ? i.value : '';
  }
  function flash(text, kind) {
    var body = document.getElementById('main-body'), box = body.querySelector(':scope > .flashes');
    if (!box) { box = el('div', 'flashes notes'); body.insertBefore(box, body.firstChild); }
    var f = el('div', 'flash flash-' + (kind || 'success'));
    f.setAttribute('role', 'alert');
    f.appendChild(el('span', '', text));
    box.textContent = '';
    box.appendChild(f);
    setTimeout(function () { f.classList.add('flash-out'); setTimeout(function () { f.remove(); }, 350); }, kind === 'error' ? 6000 : 2500);
  }
  function toolbar(root, diagram, extra) {
    root.addEventListener('click', function (e) {
      var b = e.target.closest('[data-seq]');
      if (!b) return;
      var what = b.getAttribute('data-seq');
      if (what === 'in') diagram.zoom(1.2);
      else if (what === 'out') diagram.zoom(1 / 1.2);
      else if (what === 'fit') diagram.fit();
      else if (extra) extra(what);
    });
  }
  function readGraph() {
    var g = document.getElementById('seq-graph');
    return g ? JSON.parse(g.textContent) : { steps: [], links: [] };
  }

  /* Open a step's script on its usual script page, as a tab of the centre bar (sr.tabs, app.js).
     Only scripts of the open folder have a script page. */
  function openScript(diagram, ws, stepId, before) {
    var s = diagram.byId[stepId], tabs = window.sr && window.sr.tabs;
    if (!s || !tabs) return;
    if (!s.script || !tabs.script(s.script)) {
      flash('"' + s.name + '" is not in the open folder: open its folder to view it' + (s.path ? ' (' + s.path + ').' : '.'), 'error');
      return;
    }
    if (before) before();
    tabs.add(ws, s.script);
    window.location.href = tabs.url(s.script);
  }

  /* The centre tab bar of the sequence page: the open scripts and sequences, this one selected. */
  function tabBar(bar, ws, current) {
    var tabs = window.sr && window.sr.tabs;
    if (!bar || !tabs) return;
    var list = tabs.load(ws).filter(function (id) { return id === current || tabs.known(id); });
    if (list.indexOf(current) < 0) list.push(current);
    tabs.save(ws, list);
    list.forEach(function (id) {
      var a = tabs.tab(id);
      if (!a) return;
      if (id === current) { a.classList.add('is-active'); a.setAttribute('aria-current', 'page'); }
      bar.appendChild(a);
    });
    bar.addEventListener('click', function (e) {
      var x = e.target.closest('.tab-x');
      if (!x) { if (e.target.closest('.doc-tab.is-active')) e.preventDefault(); return; }
      e.preventDefault(); e.stopPropagation();
      var id = x.parentNode.dataset.script, now = tabs.load(ws), at = now.indexOf(id);
      now = now.filter(function (v) { return v !== id; });
      tabs.save(ws, now);
      if (id !== current) { x.parentNode.remove(); return; }
      var next = now[Math.min(at, now.length - 1)];          // closing this sequence: its neighbour
      window.location.href = next ? tabs.url(next) : '/sequences/';
    }, true);
  }

  /* ------------------------------------------------------------ sequence page --- */
  /* Editing, and running in place: Play (toolbar) starts the sequence and the boxes show each step's live
     status; Pause = no new step starts (the running ones finish), Resume, Stop. While a run is going the
     diagram is locked and a click on a box shows that step's console. After the run the statuses stay
     until the next change or run. */
  function editor(root) {
    var slug = root.getAttribute('data-slug'), graphUrl = root.getAttribute('data-graph-url');
    var stepUrl = root.getAttribute('data-step-url'), max = parseInt(root.getAttribute('data-max'), 10) || 50;
    var sideMain = document.getElementById('seq-side-main'), sideStep = document.getElementById('seq-side-step');
    var frame = document.getElementById('seq-step-frame'), title = document.getElementById('seq-step-title');
    var viewBtn = document.getElementById('seq-step-view');
    var form = document.getElementById('seq-run-form'), pwBox = document.getElementById('seq-passwords');
    var playBtn = document.getElementById('seq-play'), stopBtn = document.getElementById('seq-stop');
    var live = document.getElementById('seq-live'), liveStatus = document.getElementById('seq-live-status');
    var liveText = document.getElementById('seq-live-text');
    var summary = document.getElementById('seq-summary'), busy = 0, current = null, mode = null;
    var ws = root.getAttribute('data-ws') || '', SEL_KEY = 'sr.seqSel.' + slug;
    var interval = Math.max(500, parseInt(root.getAttribute('data-interval'), 10) || 2000);
    var base = { steps: [], links: [] };          // the sequence as stored
    var overlay = null;                          // step id -> live status of the shown run
    var run = { id: null, active: false, paused: false, pinned: false, timer: null };

    function runUrl(attr) { return root.getAttribute(attr).replace('XRID', encodeURIComponent(run.id)); }

    var diagram = new Diagram(document.getElementById('seq-canvas'), {
      editable: true,
      viewKey: 'sr.seqView.' + slug,
      onSelect: function (id) {
        if (run.active) run.pinned = true;          // the user picked a box: stop following the running step
        open(id);
      },
      onOpen: function (id) {
        openScript(diagram, ws, id, function () { try { sessionStorage.setItem(SEL_KEY, id); } catch (e) { /* storage unavailable */ } });
      },
      /* ▶ on a box: run this step alone; during a run, ⏸ on a step that has not started: pause before it */
      boxAction: function (s) {
        if (!run.active) return { icon: 'play', title: 'Run this step only' };
        if (s.status !== 'pending') return null;
        return s.hold ? { icon: 'pause', title: 'The run pauses before this step: click to take that back', on: true }
                      : { icon: 'pause', title: 'Pause the run before this step' };
      },
      onBoxAction: function (id) {
        if (!run.active) { play(id); return; }
        var o = overlay && overlay[id];
        if (!o || o.status !== 'pending') return;
        var body = new FormData();
        body.append('step', id);
        body.append('on', o.hold ? '0' : '1');
        o.hold = !o.hold;                                  // at once; the next poll confirms
        show();
        fetch(runUrl('data-hold-url'), { method: 'POST', credentials: 'same-origin', body: body,
                                         headers: { 'Accept': 'application/json', 'X-CSRFToken': csrf() } })
          .then(function (r) { return r.json().catch(function () { return {}; }); })
          .then(function (d) { if (d.error) flash(d.error, 'error'); })
          .catch(function () { flash('Could not reach ScriptHub.', 'error'); })
          .then(poll);
      },
      onMove: function (id, x, y) { var p = {}; p[id] = [x, y]; send({ op: 'move', positions: p }); },
      onLink: function (from, to, when) { send({ op: 'link', from: from, to: to, when: when }); },
      onUnlink: function (from, to) { send({ op: 'unlink', from: from, to: to }); },
      onRemove: function (id) {
        var s = diagram.byId[id];
        if (!s || !window.confirm('Remove step ' + s.n + ' (' + s.name + ') and its arrows?')) return;
        send({ op: 'remove', id: id });
      },
      onAdd: function (script, x, y) {
        if (!script) return;
        if (diagram.data.steps.length >= max) { flash('A sequence has at most ' + max + ' steps.', 'error'); return; }
        send({ op: 'add', script: script, x: x, y: y }).then(function (d) { if (d && d.id) open(d.id); });
      }
    });

    /* the stored graph with the shown run's statuses on its boxes */
    function show() {
      var g = base;
      if (overlay) {
        g = { steps: base.steps.map(function (s) {
          var o = overlay[s.id];
          return o ? Object.assign({}, s, o) : s;
        }), links: base.links };
      }
      var sel = diagram.selected;
      diagram.set(g);
      if (sel && diagram.byId[sel]) diagram.select(sel);
    }

    function update(graph) {
      base = graph;
      show();
      if (current && !diagram.byId[current]) close();
      else if (current) stepTitle();
      passwords(graph.passwords || []);
      var n = graph.steps.length, starts = graph.steps.filter(function (s) { return !graph.links.some(function (k) { return k.to === s.id; }); }).length;
      buttons();
      summary.textContent = !n ? 'No steps yet: drag scripts onto the page.'
        : n + ' step' + (n === 1 ? '' : 's') + ', ' + starts + ' starting at once (no arrow into them). Every step is checked before anything starts.';
    }

    function send(body) {
      if (run.active) { flash('The sequence is running: stop it or wait before changing it.', 'error'); return Promise.resolve(null); }
      if (overlay) {                       // a change: the last run's statuses no longer describe the diagram
        overlay = null;
        if (mode === 'console') close();
      }
      busy++;
      return fetch(graphUrl, {
        method: 'POST', credentials: 'same-origin', body: JSON.stringify(body),
        headers: { 'Content-Type': 'application/json', 'Accept': 'application/json', 'X-CSRFToken': csrf() }
      }).then(function (r) {
        return r.json().catch(function () { return { error: 'The server answered ' + r.status + '.' }; });
      }).then(function (d) {
        if (d.graph) update(d.graph);
        if (d.error) { flash(d.error, 'error'); if (!d.graph) refresh(); }
        return d;
      }).catch(function () {
        flash('Could not reach ScriptHub; the change was not saved.', 'error');
        refresh();
        return null;
      }).then(function (d) { busy--; return d; });
    }

    function refresh() {
      return fetch(graphUrl, { credentials: 'same-origin', headers: { 'Accept': 'application/json' }, cache: 'no-store' })
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (d) { if (d && d.graph) update(d.graph); })
        .catch(function () { /* next change retries */ });
    }

    /* ---- right-hand column: the sequence, or a step's settings or console ---- */
    function stepTitle() {
      var s = diagram.byId[current];
      if (s) title.textContent = 'Step ' + s.n + ' · ' + s.name + (mode === 'console' ? ' · console' : '');
    }
    function open(id, want) {
      if (!id) { close(); return; }
      var o = overlay && overlay[id], hasRun = !!(o && o.run_id);
      var m = want || (hasRun ? 'console' : 'settings');
      if (m === 'console' && !hasRun) m = 'settings';
      if (m === 'settings' && run.active) {         // settings are locked during a run
        diagram.select(id);
        if (!hasRun) return;
        m = 'console';
      }
      current = id;
      mode = m;
      diagram.select(id);
      stepTitle();
      var src = m === 'console'
        ? root.getAttribute('data-console-url').replace('XID', encodeURIComponent(o.run_id))
        : stepUrl.replace('XID', encodeURIComponent(id)) + '?embed=1';
      if (frame.getAttribute('src') !== src) frame.setAttribute('src', src);
      viewBtn.hidden = !(hasRun && !run.active);
      viewBtn.textContent = m === 'console' ? 'Settings' : 'Console';
      sideMain.hidden = true;
      sideStep.hidden = false;
    }
    function close() {
      current = null;
      mode = null;
      diagram.select(null);
      sideStep.hidden = true;
      sideMain.hidden = false;
      frame.removeAttribute('src');
    }

    function passwords(list) {
      var keep = {};
      [].forEach.call(pwBox.querySelectorAll('input'), function (i) { keep[i.name] = i.value; });
      pwBox.textContent = '';
      pwBox.hidden = !list.length;
      if (!list.length) return;
      pwBox.appendChild(el('p', 'small muted', 'Passwords are asked for each run and never saved.'));
      list.forEach(function (p) {
        var f = el('div', 'field'), lab = el('label', 'field-label', 'Step ' + p.step + ' · ' + p.script + ' · ' + p.label);
        lab.setAttribute('for', p.input);
        if (p.required) { var req = el('span', 'req', '*'); req.title = 'Required'; lab.appendChild(req); }
        var input = el('input', 'input');
        input.type = 'password'; input.name = p.input; input.id = p.input; input.autocomplete = 'new-password';
        if (p.required) input.setAttribute('data-required', '1');
        if (keep[p.input]) input.value = keep[p.input];
        f.appendChild(lab); f.appendChild(input);
        pwBox.appendChild(f);
      });
    }

    /* ---- running ---- */
    function buttons() {
      var icon = playBtn.querySelector('use'), label = playBtn.querySelector('span'), what, tip;
      if (!run.active) { what = 'Run'; tip = 'Run the sequence  (Ctrl+Enter)'; icon.setAttribute('href', '#i-play'); }
      else if (run.paused) { what = 'Resume'; tip = 'Resume: the next steps start  (Ctrl+Enter)'; icon.setAttribute('href', '#i-play'); }
      else { what = 'Pause'; tip = 'Pause: the running steps finish, the next ones wait  (Ctrl+Enter)'; icon.setAttribute('href', '#i-pause'); }
      label.textContent = what;
      playBtn.title = tip;
      playBtn.setAttribute('data-hint', what === 'Run' ? 'Run sequence' : what + ' sequence');
      playBtn.classList.toggle('btn-primary', what !== 'Pause');
      playBtn.disabled = !run.active && !base.steps.length;
      stopBtn.hidden = !run.active;
      diagram.lock(run.active);
    }

    function play(only) {
      if (run.active) { if (!only) control(run.paused ? 'data-resume-url' : 'data-pause-url'); return; }
      if (!base.steps.length || playBtn.disabled) return;
      var missing = [].filter.call(pwBox.querySelectorAll('input[data-required]'), function (i) {
        return !i.value && (!only || i.name.indexOf('pw_' + only + '_') === 0);
      });
      if (missing.length) {
        close();
        missing[0].focus();
        flash('Enter the passwords in the right-hand column first (they are never saved).', 'error');
        return;
      }
      playBtn.disabled = true;
      var body = new FormData(form);
      if (only) body.append('only', only);
      fetch(form.action, {
        method: 'POST', credentials: 'same-origin', body: body,
        headers: { 'Accept': 'application/json', 'X-CSRFToken': csrf() }
      }).then(function (r) {
        return r.json().catch(function () { return { errors: ['The server answered ' + r.status + '.'] }; });
      }).then(function (d) {
        playBtn.disabled = false;
        if (d.errors) {
          flash(d.errors.slice(0, 3).join(' · ') + (d.errors.length > 3 ? ' · … and ' + (d.errors.length - 3) + ' more' : ''), 'error');
          return;
        }
        form.elements.submit_token.value = d.token;              // the next Play is a new run
        [].forEach.call(pwBox.querySelectorAll('input'), function (i) { i.value = ''; });
        watch(d.run);
      }).catch(function () {
        playBtn.disabled = false;
        flash('Could not reach ScriptHub; the sequence was not started.', 'error');
      });
    }

    function control(attr) {
      if (!run.id) return;
      if (attr === 'data-stop-url' && !window.confirm('Stop this sequence? The running steps are stopped and the remaining steps are skipped.')) return;
      playBtn.disabled = true;
      fetch(runUrl(attr), { method: 'POST', credentials: 'same-origin', headers: { 'Accept': 'application/json', 'X-CSRFToken': csrf() } })
        .then(function (r) { return r.json().catch(function () { return {}; }); })
        .then(function (d) { if (d.error) flash(d.error, 'error'); })
        .catch(function () { flash('Could not reach ScriptHub.', 'error'); })
        .then(function () { playBtn.disabled = false; poll(); });
    }

    function watch(id) {
      run = { id: id, active: true, paused: false, pinned: false, timer: null };
      overlay = {};
      live.href = runUrl('data-run-url');
      buttons();
      poll();
    }

    function poll() {
      clearTimeout(run.timer);
      var id = run.id;
      fetch(runUrl('data-status-url'), { credentials: 'same-origin', headers: { 'Accept': 'application/json' }, cache: 'no-store' })
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (d) {
          if (id !== run.id) return;
          if (d) apply(d);
          if (!d || d.active) run.timer = setTimeout(poll, interval);
        })
        .catch(function () { if (id === run.id) run.timer = setTimeout(poll, interval * 2); });
    }

    function apply(d) {
      var was = run.active;
      overlay = {};
      d.graph.steps.forEach(function (s) {
        overlay[s.id] = { status: s.status, status_label: s.status_label, run_id: s.run_id, error: s.error, hold: !!s.hold, held: !!s.held };
      });
      run.active = d.active;
      run.paused = !!d.paused;
      liveStatus.className = 'st st-' + (run.paused ? 'paused' : d.status);
      liveStatus.textContent = d.status_label;
      liveText.textContent = d.done + '/' + d.total + ' · ' + d.duration;
      live.title = (d.error ? d.error + '  ·  ' : '') + "Open this run's page";
      live.hidden = false;
      buttons();
      show();
      if (current && mode === 'console') stepTitle();
      if (!run.pinned && (run.active || was)) follow();
      if (was && !run.active) {
        if (d.status !== 'success') flash(d.status_label + (d.error ? ': ' + d.error : '.'), 'error');   // success: the chip says it
        if (mode === 'console' && current) open(current, 'console');   // offers Settings again
        refresh();
      }
    }

    function follow() {                 /* the console of a running step, else of the last one that started */
      var pick = null;
      diagram.data.steps.forEach(function (s) {
        if (!s.run_id) return;
        var running = s.status === 'running' || s.status === 'queued';
        if (!pick || !pick.running || running) pick = { s: s, running: running };
      });
      if (pick && !(current === pick.s.id && mode === 'console')) open(pick.s.id, 'console');
    }

    toolbar(root, diagram, function (what) {
      if (what === 'close') { if (run.active) run.pinned = true; close(); }
      else if (what === 'play') play(null);
      else if (what === 'stop') control('data-stop-url');
      else if (what === 'view' && current) open(current, mode === 'console' ? 'settings' : 'console');
      else if (what === 'tidy' && diagram.data.steps.length) {
        if (run.active) { flash('The sequence is running: stop it or wait before changing it.', 'error'); return; }
        var pos = diagram.tidyPositions();
        base.steps.forEach(function (s) { if (pos[s.id]) { s.x = pos[s.id][0]; s.y = pos[s.id][1]; } });
        show();
        diagram.fit();
        send({ op: 'move', positions: pos });
      }
    });
    form.addEventListener('submit', function (e) { e.preventDefault(); play(); });   // Enter in a password field
    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape' && current && !e.defaultPrevented && !document.querySelector('.palette:not([hidden]), dialog[open]')) {
        if (run.active) run.pinned = true;
        close();
      }
    });
    window.addEventListener('message', function (e) {
      if (e.origin === window.location.origin && e.data && e.data.sr === 'seq-step-saved') refresh();
    });
    /* explorer items carry their script id: dragging one offers it to the page */
    document.addEventListener('dragstart', function (e) {
      var a = e.target && e.target.closest && e.target.closest('.tree-item[data-id]');
      if (!a) return;
      e.dataTransfer.setData(MIME, a.getAttribute('data-id'));
      e.dataTransfer.effectAllowed = 'copy';
    });
    window.addEventListener('beforeunload', function (e) { if (busy) { e.preventDefault(); e.returnValue = ''; } });

    tabBar(document.getElementById('seq-tabs'), ws, 'seq:' + slug);
    update(readGraph());
    diagram.restoreView();
    /* back from a script opened here: the same box is selected again */
    var back = null;
    try { back = sessionStorage.getItem(SEL_KEY); sessionStorage.removeItem(SEL_KEY); } catch (e) { /* storage unavailable */ }
    if (back && diagram.byId[back]) diagram.select(back);
    /* a run of this sequence that is still going (started here before a reload, or elsewhere) */
    if (root.getAttribute('data-active-run')) watch(root.getAttribute('data-active-run'));
  }

  /* -------------------------------------------------------- sequence run page --- */
  function runPage(root) {
    var frame = document.getElementById('seq-frame'), empty = document.getElementById('seq-empty');
    var interval = Math.max(500, parseInt(root.getAttribute('data-interval'), 10) || 2000);
    var shown = null, pinned = false;
    var diagram = new Diagram(document.getElementById('seq-canvas'), {
      editable: false,
      onOpen: function (id) { openScript(diagram, root.getAttribute('data-ws') || '', id); },
      onSelect: function (id) {
        var s = id && diagram.byId[id];
        if (!s || !s.run_id) return;
        pinned = true;
        show(s);
      }
    });

    function show(s) {
      diagram.select(s.id);
      if (s.run_id === shown) return;
      shown = s.run_id;
      frame.src = root.getAttribute('data-console-url').replace('XID', encodeURIComponent(s.run_id));
      frame.hidden = false; empty.hidden = true;
    }
    function follow() {                 /* a running step, else the last one that started */
      var pick = null;
      diagram.data.steps.forEach(function (s) {
        if (!s.run_id) return;
        var live = s.status === 'running' || s.status === 'queued';
        if (!pick || !pick.live || live) pick = { s: s, live: live };
      });
      if (pick) show(pick.s);
    }
    function apply(d) {
      diagram.set(d.graph);
      if (shown) diagram.data.steps.forEach(function (s) { if (s.run_id === shown) diagram.select(s.id); });
      var top = document.getElementById('seq-status');
      top.className = 'st st-' + (d.paused ? 'paused' : d.status); top.textContent = d.status_label;
      var pause = document.getElementById('seq-pause'), resume = document.getElementById('seq-resume');
      if (pause) pause.hidden = !!d.paused;
      if (resume) resume.hidden = !d.paused;
      document.getElementById('seq-progress').firstChild.textContent = d.done + '/' + d.total + ' steps · ';
      document.getElementById('seq-duration').textContent = d.duration;
      var err = document.getElementById('seq-error');
      err.textContent = d.error || ''; err.hidden = !d.error;
      if (!pinned) follow();
      if (!d.active) {
        var again = document.getElementById('seq-again');
        ['seq-stop', 'seq-pause', 'seq-resume'].forEach(function (id) { var f = document.getElementById(id); if (f) f.remove(); });
        if (again) again.hidden = false;
      }
      return d.active;
    }
    function poll() {
      fetch(root.getAttribute('data-status-url'), { headers: { 'Accept': 'application/json' }, cache: 'no-store' })
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (d) { if (d === null || apply(d)) setTimeout(poll, interval); })
        .catch(function () { setTimeout(poll, interval * 2); });
    }

    toolbar(root, diagram);
    diagram.set(readGraph());
    diagram.fit();
    follow();
    if (root.getAttribute('data-active')) poll();
  }

  var ed = document.getElementById('seq-editor'), run = document.getElementById('seq-run');
  if (ed) editor(ed);
  if (run) runPage(run);
})();
