/* Parameter editor: a table with one summary row per parameter. "Edit" opens
   that parameter's form inline. Everything is serialised to JSON in a hidden
   field on submit. Vanilla JS, no dependencies. */
(function () {
  var data = JSON.parse(document.getElementById('params-data').textContent);
  var info = JSON.parse(document.getElementById('type-info').textContent);
  var list = document.getElementById('param-list');   // the <table>
  var empty = document.getElementById('param-empty');
  var form = list.closest('form');
  var hidden = document.getElementById('parameters_json');
  var KNOWN = ['name', 'label', 'type', 'default', 'required', 'help', 'placeholder', 'choices',
    'min', 'max', 'min_length', 'max_length', 'pattern', 'editable_at_runtime', 'arg',
    'pass_as', 'position', 'env_name'];

  function items() {
    return [].filter.call(list.children, function (c) { return c.classList.contains('pe-item'); });
  }
  function h(tag, attrs, kids) {
    var e = document.createElement(tag);
    Object.keys(attrs || {}).forEach(function (k) {
      if (k === 'text') e.textContent = attrs[k];
      else if (k === 'class') e.className = attrs[k];
      else e.setAttribute(k, attrs[k]);
    });
    (kids || []).forEach(function (c) { if (c) e.appendChild(c); });
    return e;
  }
  function field(label, input, cls, help) {
    return h('div', { 'class': 'field ' + (cls || '') }, [
      h('label', { 'class': 'field-label', text: label }), input,
      help ? h('div', { 'class': 'help', text: help }) : null]);
  }
  function text(ph, cls) { return h('input', { type: 'text', 'class': 'input ' + (cls || ''), placeholder: ph || '', autocomplete: 'off' }); }
  function check(label) {
    var i = h('input', { type: 'checkbox' });
    return { input: i, el: h('label', { 'class': 'check' }, [i, h('span', { text: label })]) };
  }
  function num(s) {
    s = String(s).trim();
    if (s === '') return undefined;
    var n = Number(s);
    return isNaN(n) ? s : n;
  }
  function choicesToText(ch) {
    return (ch || []).map(function (c) {
      if (c && typeof c === 'object') return c.label && c.label !== c.value ? c.value + '|' + c.label : String(c.value);
      return String(c);
    }).join('\n');
  }
  function textToChoices(t) {
    return t.split('\n').map(function (l) { return l.trim(); }).filter(Boolean).map(function (l) {
      var i = l.indexOf('|');
      if (i < 0) return l;
      return { value: l.slice(0, i).trim(), label: l.slice(i + 1).trim() };
    });
  }

  function makeItem(p) {
    var extra = {};
    Object.keys(p).forEach(function (k) { if (KNOWN.indexOf(k) < 0) extra[k] = p[k]; });

    var f = {};
    f.name = text('e.g. backup_path', 'mono');
    f.label = text('Shown on the form');
    f.type = h('select', { 'class': 'input' }, Object.keys(info).map(function (k) { return h('option', { value: k, text: info[k].label }); }));
    f.def = text('Default value');
    f.defBool = check('Checked by default');
    f.required = check('Required');
    f.editable = check('Editable at run time');
    f.help = text('Help text under the field');
    f.placeholder = text('Placeholder');
    f.choices = h('textarea', { 'class': 'input', rows: '3', placeholder: 'one per line:  value  or  value|Label' });
    f.min = text(''); f.max = text('');
    f.min_length = text(''); f.max_length = text('');
    f.pattern = text('Regular expression (must match the whole value)', 'mono');
    f.pass_as = h('select', { 'class': 'input' }, [
      h('option', { value: 'flag', text: 'Option flag (--name value)' }),
      h('option', { value: 'positional', text: 'Positional argument' }),
      h('option', { value: 'env', text: 'Environment variable' })]);
    f.arg = text('--option-name', 'mono');
    f.position = text('1, 2, …');
    f.env_name = text('VARIABLE_NAME', 'mono');

    // initial values
    f.name.value = p.name || '';
    f.label.value = p.label || '';
    f.type.value = p.type || 'text';
    if (typeof p['default'] === 'boolean') f.defBool.input.checked = p['default'];
    else if (Array.isArray(p['default'])) f.def.value = p['default'].join(', ');
    else if (p['default'] !== undefined && p['default'] !== null) f.def.value = String(p['default']);
    f.required.input.checked = !!p.required;
    f.editable.input.checked = p.editable_at_runtime !== false;
    f.help.value = p.help || '';
    f.placeholder.value = p.placeholder || '';
    f.choices.value = choicesToText(p.choices);
    ['min', 'max', 'min_length', 'max_length', 'pattern', 'arg', 'position', 'env_name'].forEach(function (k) {
      if (p[k] !== undefined && p[k] !== null) f[k].value = String(p[k]);
    });
    f.pass_as.value = p.pass_as || 'flag';

    var groups = {
      choices: field('Choices', f.choices, 'field-wide'),
      range: h('div', { 'class': 'form-grid field-wide' }, [field('Minimum', f.min), field('Maximum', f.max)]),
      length: h('div', { 'class': 'form-grid field-wide' }, [field('Min length', f.min_length), field('Max length', f.max_length)]),
      pattern: field('Validation pattern', f.pattern, 'field-wide'),
      def: field('Default value', f.def, '', 'Multi-select: comma-separated values.'),
      defBool: h('div', { 'class': 'field field-check' }, [f.defBool.el]),
      arg: field('Flag', f.arg, '', 'Blank = --name-with-hyphens.'),
      position: field('Position', f.position),
      env: field('Variable name', f.env_name, '', 'Blank = NAME in upper case.')
    };

    var card = h('div', { 'class': 'pe-card' }, [
      h('div', { 'class': 'form-grid' }, [
        field('Name (used by the script)', f.name),
        field('Label', f.label),
        field('Type', f.type),
        groups.def, groups.defBool,
        field('Help text', f.help, 'field-wide'),
        field('Placeholder', f.placeholder),
        h('div', { 'class': 'field field-check' }, [f.required.el, f.editable.el]),
        groups.choices, groups.range, groups.length, groups.pattern,
        field('Pass to script as', f.pass_as),
        groups.arg, groups.position, groups.env
      ])
    ]);

    function get() {
      var o = Object.assign({}, extra), t = f.type.value, allowed = (info[t] || { allowed: [] }).allowed;
      o.name = f.name.value.trim();
      if (f.label.value.trim()) o.label = f.label.value.trim();
      o.type = t;
      if (t === 'boolean') o['default'] = f.defBool.input.checked;
      else if (f.def.value.trim() !== '' || (t === 'password' && f.def.value !== '')) {
        if (t === 'number') o['default'] = num(f.def.value);
        else if (t === 'multiselect') o['default'] = f.def.value.split(',').map(function (s) { return s.trim(); }).filter(Boolean);
        else if (t === 'password' || t === 'textarea') o['default'] = f.def.value;
        else o['default'] = f.def.value.trim();
      }
      if (t !== 'boolean') o.required = f.required.input.checked;
      o.editable_at_runtime = f.editable.input.checked;
      if (f.help.value.trim()) o.help = f.help.value.trim();
      if (f.placeholder.value.trim()) o.placeholder = f.placeholder.value.trim();
      if (allowed.indexOf('choices') >= 0) o.choices = textToChoices(f.choices.value);
      if (allowed.indexOf('min') >= 0) {
        if (num(f.min.value) !== undefined) o.min = num(f.min.value);
        if (num(f.max.value) !== undefined) o.max = num(f.max.value);
      }
      if (allowed.indexOf('min_length') >= 0) {
        if (num(f.min_length.value) !== undefined) o.min_length = num(f.min_length.value);
        if (num(f.max_length.value) !== undefined) o.max_length = num(f.max_length.value);
      }
      if (allowed.indexOf('pattern') >= 0 && f.pattern.value) o.pattern = f.pattern.value;
      o.pass_as = f.pass_as.value;
      if (o.pass_as === 'flag' && f.arg.value.trim()) o.arg = f.arg.value.trim();
      if (o.pass_as === 'positional' && num(f.position.value) !== undefined) o.position = num(f.position.value);
      if (o.pass_as === 'env' && f.env_name.value.trim()) o.env_name = f.env_name.value.trim();
      return o;
    }

    // ---- summary row (the table) ----
    function iconBtn(icon, title, extra) {
      var b = h('button', { type: 'button', 'class': 'btn btn-ghost btn-icon btn-sm' + (extra || ''), title: title, 'aria-label': title });
      b.innerHTML = '<svg class="i i-sm"><use href="#i-' + icon + '"/></svg>';
      return b;
    }
    var up = iconBtn('up', 'Move up');
    var down = iconBtn('down', 'Move down');
    var del = iconBtn('trash', 'Delete parameter', ' btn-del');
    var edit = iconBtn('edit', 'Edit');

    var cells = {};
    var sum = h('tr', { 'class': 'pe-row' });
    ['num', 'label', 'name', 'type', 'def', 'req', 'rt', 'pass'].forEach(function (k) {
      cells[k] = h('td', { 'class': 'pe-c-' + k + (k === 'name' || k === 'def' || k === 'pass' ? ' mono' : '') });
      sum.appendChild(cells[k]);
    });
    sum.appendChild(h('td', { 'class': 'pe-actions' }, [h('div', { 'class': 'btn-row' }, [up, down, edit, del])]));
    sum.addEventListener('click', function (e) {          // the row itself opens / closes the editor
      if (!e.target.closest('button')) edit.click();
    });
    var editTr = h('tr', { 'class': 'pe-edit', hidden: '' }, [h('td', { colspan: '9' }, [card])]);
    var item = h('tbody', { 'class': 'pe-item' }, [sum, editTr]);

    function updateSummary() {
      var o = get(), d = o['default'];
      if (o.type === 'password') d = d ? '••••••' : '';
      else if (typeof d === 'boolean') d = d ? 'Yes' : 'No';
      else if (Array.isArray(d)) d = d.join(', ');
      cells.num.textContent = items().indexOf(item) + 1 || '';
      cells.label.textContent = o.label || '—';
      cells.name.textContent = o.name || '—';
      cells.type.textContent = o.type;
      cells.def.textContent = (d === undefined || d === '') ? '—' : String(d);
      cells.req.textContent = o.type === 'boolean' ? '—' : (o.required ? 'Yes' : 'No');
      cells.rt.textContent = o.editable_at_runtime ? 'Editable' : 'Fixed';
      cells.pass.textContent = o.pass_as === 'flag' ? (o.arg || '--' + String(o.name || '').replace(/_/g, '-'))
        : o.pass_as === 'env' ? 'env ' + (o.env_name || String(o.name || '').toUpperCase())
        : 'positional' + (o.position ? ' ' + o.position : '');
      edit.classList.toggle('is-on', !editTr.hidden);
      edit.title = editTr.hidden ? 'Edit' : 'Close';
      sum.classList.toggle('is-open', !editTr.hidden);
    }

    function refresh() {
      var t = f.type.value, allowed = (info[t] || { allowed: [] }).allowed, isBool = t === 'boolean';
      groups.choices.hidden = allowed.indexOf('choices') < 0;
      groups.range.hidden = allowed.indexOf('min') < 0;
      groups.length.hidden = allowed.indexOf('min_length') < 0;
      groups.pattern.hidden = allowed.indexOf('pattern') < 0;
      groups.def.hidden = isBool;
      groups.defBool.hidden = !isBool;
      f.required.el.hidden = isBool;
      var mode = f.pass_as.value;
      groups.arg.hidden = mode !== 'flag';
      groups.position.hidden = mode !== 'positional';
      groups.env.hidden = mode !== 'env';
      updateSummary();
    }

    function renumber() { items().forEach(function (i) { i.updateSummary(); }); }

    edit.addEventListener('click', function () { editTr.hidden = !editTr.hidden; updateSummary(); });
    card.addEventListener('input', updateSummary);
    card.addEventListener('change', refresh);
    up.addEventListener('click', function () {
      var prev = item.previousElementSibling;
      if (prev && prev.classList.contains('pe-item')) { list.insertBefore(item, prev); renumber(); }
    });
    down.addEventListener('click', function () {
      var next = item.nextElementSibling;
      if (next) { list.insertBefore(next, item); renumber(); }
    });
    del.addEventListener('click', function () {
      if (window.confirm('Delete this parameter? It is removed when you save.')) { item.remove(); sync(); renumber(); }
    });

    item.get = get;
    item.updateSummary = updateSummary;
    item.open = function () { editTr.hidden = false; updateSummary(); };
    item.focusName = function () { f.name.focus(); };
    refresh();
    return item;
  }

  function sync() {
    var n = items().length;
    empty.hidden = n !== 0;
    list.hidden = n === 0;
  }

  data.forEach(function (p) { if (p && typeof p === 'object') list.appendChild(makeItem(p)); });
  items().forEach(function (i) { i.updateSummary(); });
  sync();

  document.getElementById('add-param').addEventListener('click', function () {
    var n = items().length + 1, names = items().map(function (c) { return c.get().name; });
    while (names.indexOf('param_' + n) >= 0) n++;
    var item = makeItem({ name: 'param_' + n, label: '', type: 'text', required: false, editable_at_runtime: true });
    list.appendChild(item);
    sync();
    item.open();
    item.scrollIntoView({ behavior: 'smooth', block: 'center' });
    item.focusName();
  });

  // The page's one Save (designer.js) calls this to compare and to post the definitions.
  form.srSerialize = function () { hidden.value = JSON.stringify(items().map(function (c) { return c.get(); })); };
  form.addEventListener('submit', form.srSerialize);
})();
