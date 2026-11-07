/* Parameter editor: builds one card per parameter and serialises them to JSON
   in a hidden field on submit. Vanilla JS, no dependencies. */
(function () {
  var data = JSON.parse(document.getElementById('params-data').textContent);
  var info = JSON.parse(document.getElementById('type-info').textContent);
  var list = document.getElementById('param-list');
  var empty = document.getElementById('param-empty');
  var form = document.getElementById('configure-form');
  var hidden = document.getElementById('parameters_json');
  var KNOWN = ['name', 'label', 'type', 'default', 'required', 'help', 'placeholder', 'choices',
    'min', 'max', 'min_length', 'max_length', 'pattern', 'editable_at_runtime', 'arg',
    'pass_as', 'position', 'env_name'];

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

  function makeCard(p) {
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

    var title = h('span', { 'class': 'pe-title' });
    var up = h('button', { type: 'button', 'class': 'btn btn-ghost btn-sm', title: 'Move up', text: '↑' });
    var down = h('button', { type: 'button', 'class': 'btn btn-ghost btn-sm', title: 'Move down', text: '↓' });
    var del = h('button', { type: 'button', 'class': 'btn btn-danger btn-sm', title: 'Delete parameter', text: 'Delete' });

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
      h('div', { 'class': 'pe-head' }, [title, h('div', { 'class': 'btn-row' }, [up, down, del])]),
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

    function refresh() {
      var t = f.type.value, allowed = (info[t] || { allowed: [] }).allowed;
      var isBool = t === 'boolean';
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
      title.textContent = (f.label.value.trim() || f.name.value.trim() || 'New parameter') +
        '  ·  ' + (info[t] ? info[t].label : t);
    }
    [f.type, f.pass_as].forEach(function (e) { e.addEventListener('change', refresh); });
    [f.name, f.label].forEach(function (e) { e.addEventListener('input', refresh); });
    refresh();

    card.get = function () {
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
    };

    up.addEventListener('click', function () {
      var prev = card.previousElementSibling;
      if (prev) list.insertBefore(card, prev);
    });
    down.addEventListener('click', function () {
      var next = card.nextElementSibling;
      if (next) list.insertBefore(next, card);
    });
    del.addEventListener('click', function () {
      if (window.confirm('Delete this parameter? It is removed when you save.')) { card.remove(); sync(); }
    });
    card.focusName = function () { f.name.focus(); };
    return card;
  }

  function sync() { empty.hidden = list.children.length !== 0; }

  data.forEach(function (p) { if (p && typeof p === 'object') list.appendChild(makeCard(p)); });
  sync();

  document.getElementById('add-param').addEventListener('click', function () {
    var n = list.children.length + 1, names = [].map.call(list.children, function (c) { return c.get().name; });
    while (names.indexOf('param_' + n) >= 0) n++;
    var card = makeCard({ name: 'param_' + n, label: '', type: 'text', required: false, editable_at_runtime: true });
    list.appendChild(card);
    sync();
    card.scrollIntoView({ behavior: 'smooth', block: 'center' });
    card.focusName();
  });

  form.addEventListener('submit', function () {
    hidden.value = JSON.stringify([].map.call(list.children, function (c) { return c.get(); }));
  });
})();
