/* Code editor: a transparent <textarea> on top of a syntax-highlighted <pre>.
   Typing happens in the real textarea (so selection, undo, IME and
   accessibility all work); the <pre> underneath only paints the colours.
   Vanilla JS; uses window.highlight when available. */
(function () {
  var INDENT = '    ';
  var editors = new WeakMap();

  function esc(s) { return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;'); }

  function init(root) {
    var ta = root.querySelector('.ed-input');
    var hlBox = root.querySelector('.ed-hl');
    var hl = hlBox.querySelector('code');
    var gutter = root.querySelector('.ed-gutter');
    var status = document.querySelector('[data-editor-status="' + root.id + '"]');
    var form = ta.form;
    var state = { original: ta.value, lang: root.dataset.lang || '', submitting: false };
    editors.set(root, { ta: ta, state: state });

    function render() {
      var text = ta.value, html;
      var canHighlight = window.highlight && text.length < 200000 && window.highlight.languages.indexOf(state.lang) >= 0;
      html = canHighlight ? window.highlight.lines(state.lang, text).join('\n') : esc(text);
      hl.innerHTML = html + '\n';
      var n = text.split('\n').length, nums = [];
      for (var i = 1; i <= n; i++) nums.push(i);
      gutter.textContent = nums.join('\n');
      sync();
      updateStatus();
    }
    function sync() {
      hlBox.scrollTop = ta.scrollTop; hlBox.scrollLeft = ta.scrollLeft;
      gutter.scrollTop = ta.scrollTop;
    }
    function updateStatus() {
      if (!status) return;
      var before = ta.value.slice(0, ta.selectionStart);
      var line = before.split('\n').length, col = before.length - before.lastIndexOf('\n');
      status.querySelector('.ed-pos').textContent = 'Ln ' + line + ', Col ' + col;
      status.querySelector('.ed-count').textContent = ta.value.split('\n').length + ' lines';
      var dirty = ta.value !== state.original;
      var d = status.querySelector('.ed-dirty');
      d.textContent = dirty ? '● unsaved changes' : '';
      d.classList.toggle('is-dirty', dirty);
    }

    // Insert text at the caret through the browser so undo/redo keeps working.
    function insert(text) {
      ta.focus();
      var ok = false;
      try { ok = document.execCommand('insertText', false, text); } catch (e) {}
      if (!ok) { ta.setRangeText(text, ta.selectionStart, ta.selectionEnd, 'end'); ta.dispatchEvent(new Event('input')); }
    }
    function lineStart(pos) { return ta.value.lastIndexOf('\n', pos - 1) + 1; }

    function indentSelection(outdent) {
      var v = ta.value, s = ta.selectionStart, e = ta.selectionEnd;
      var from = lineStart(s), to = e;
      if (to > from && v.charAt(to - 1) === '\n') to -= 1;
      var block = v.slice(from, to), lines = block.split('\n');
      var out = lines.map(function (l) {
        if (!outdent) return INDENT + l;
        if (l.indexOf(INDENT) === 0) return l.slice(INDENT.length);
        return l.replace(/^ {1,3}|^\t/, '');
      }).join('\n');
      ta.setSelectionRange(from, to);
      insert(out);
      ta.setSelectionRange(from, from + out.length);
    }

    ta.addEventListener('keydown', function (e) {
      if (e.key === 'Tab' && !e.ctrlKey && !e.altKey && !e.metaKey) {
        e.preventDefault();
        var multi = ta.value.slice(ta.selectionStart, ta.selectionEnd).indexOf('\n') >= 0;
        if (multi || e.shiftKey) indentSelection(e.shiftKey);
        else insert(INDENT);
      } else if (e.key === 'Enter' && !e.ctrlKey && !e.metaKey && !e.shiftKey && ta.selectionStart === ta.selectionEnd) {
        e.preventDefault();
        var pos = ta.selectionStart, ls = lineStart(pos);
        var current = ta.value.slice(ls, pos);
        var indent = (current.match(/^[ \t]*/) || [''])[0];
        if (state.lang === 'python' && /:\s*$/.test(current)) indent += INDENT;
        insert('\n' + indent);
      } else if ((e.ctrlKey || e.metaKey) && (e.key === 's' || e.key === 'S')) {
        e.preventDefault();
        var btn = form && (form.querySelector('[data-save]') || form.querySelector('[type=submit]'));
        if (btn) btn.click();
      }
    });

    ta.addEventListener('input', render);
    ta.addEventListener('scroll', sync);
    ['keyup', 'click', 'select', 'focus'].forEach(function (ev) { ta.addEventListener(ev, updateStatus); });

    if (form) form.addEventListener('submit', function () { state.submitting = true; });
    if (root.dataset.warnUnsaved === '1') {
      window.addEventListener('beforeunload', function (e) {
        if (!state.submitting && ta.value !== state.original) { e.preventDefault(); e.returnValue = ''; }
      });
    }

    state.render = render;
    render();
  }

  window.srEditor = {
    setLanguage: function (root, lang) {
      var ed = editors.get(root); if (!ed) return;
      ed.state.lang = lang; root.dataset.lang = lang; ed.state.render();
    },
    setValue: function (root, text) {
      var ed = editors.get(root); if (!ed) return;
      ed.ta.value = text; ed.ta.scrollTop = 0; ed.state.render();
    },
    isDirty: function (root) {
      var ed = editors.get(root); return !!ed && ed.ta.value !== ed.state.original;
    }
  };

  [].forEach.call(document.querySelectorAll('.editor'), init);
})();
