/* Tiny dependency-free syntax highlighter: Python, Bash, Batch, JSON.
   highlight.lines(lang, text) -> array of HTML strings, one per source line
   (tokens that span lines, such as Python triple-quoted strings, are closed
   and reopened per line so line numbers stay correct). */
(function (global) {
  function words(list) { return '\\b(?:' + list.split(' ').join('|') + ')\\b'; }

  // Every rule's regex must use only non-capturing groups.
  var LANGS = {
    python: {
      flags: 'gm',
      rules: [
        ['com', /#[^\n]*/],
        ['str', /(?:\b[rRbBfFuU]{1,2})?(?:"""[\s\S]*?"""|'''[\s\S]*?'''|"(?:\\.|[^"\\\n])*"|'(?:\\.|[^'\\\n])*')/],
        ['dec', /@[A-Za-z_][\w.]*/],
        ['kw', new RegExp(words('and as assert async await break class continue def del elif else except finally for from global if import in is lambda nonlocal not or pass raise return try while with yield True False None'))],
        ['bi', new RegExp(words('print len range open str int float bool list dict set tuple sum min max sorted enumerate zip map filter isinstance super type input abs any all reversed repr format'))],
        ['num', /\b(?:0[xX][0-9a-fA-F_]+|\d[\d_]*\.?\d*(?:[eE][+-]?\d+)?)\b/]
      ]
    },
    bash: {
      flags: 'gm',
      rules: [
        ['str', /"(?:\\[\s\S]|[^"\\])*"|'[^']*'/],
        ['var', /\$\{[^}\n]*\}|\$[A-Za-z_]\w*|\$[0-9@#?*!$-]/],
        ['com', /#[^\n]*/],
        ['kw', new RegExp(words('if then else elif fi for while until do done case esac in function select return exit local export readonly declare unset shift break continue set'))],
        ['bi', new RegExp(words('echo printf read cd test command source eval exec trap type wait'))],
        ['opt', /\B--?[A-Za-z][\w-]*/],
        ['num', /\b\d+\b/]
      ]
    },
    batch: {
      flags: 'gim',
      rules: [
        ['com', /^[ \t]*(?:@?rem\b[^\n]*|::[^\n]*)/],
        ['str', /"[^"\n]*"/],
        ['var', /%%[A-Za-z]|%~[a-zA-Z]*\d|%\d|%[A-Za-z_]\w*(?::[^%\n]*)?%|![A-Za-z_]\w*!/],
        ['lab', /^[ \t]*:[A-Za-z_]\w*/],
        ['kw', new RegExp('@?' + words('echo off on if else not exist defined errorlevel goto call exit set setlocal endlocal enabledelayedexpansion for in do pause cd md mkdir del copy move dir type ping start shift equ neq lss leq gtr geq'))],
        ['num', /\b\d+\b/]
      ]
    },
    json: {
      flags: 'gm',
      rules: [
        ['key', /"(?:\\.|[^"\\\n])*"(?=\s*:)/],
        ['str', /"(?:\\.|[^"\\\n])*"/],
        ['num', /-?\b\d+(?:\.\d+)?(?:[eE][+-]?\d+)?\b/],
        ['kw', /\b(?:true|false|null)\b/],
        ['pun', /[{}\[\],:]/]
      ]
    }
  };
  // Variables inside strings ("$x", "%~1") are coloured separately.
  LANGS.bash.inner = { str: /\$\{[^}\n]*\}|\$[A-Za-z_]\w*|\$[0-9@#?*!$-]/g, only: '"' };
  LANGS.batch.inner = { str: /%%[A-Za-z]|%~[a-zA-Z]*\d|%\d|%[A-Za-z_]\w*(?::[^%\n]*)?%|![A-Za-z_]\w*!/gi };
  Object.keys(LANGS).forEach(function (k) {
    var L = LANGS[k];
    L.re = new RegExp(L.rules.map(function (r) { return '(' + r[1].source + ')'; }).join('|'), L.flags);
  });

  function esc(s) {
    return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  }

  function splitInner(out, str, inner) {
    var re = new RegExp(inner.source, inner.flags), pos = 0, m;
    while ((m = re.exec(str))) {
      if (m[0] === '') { re.lastIndex++; continue; }
      if (m.index > pos) out.push({ c: 'str', t: str.slice(pos, m.index) });
      out.push({ c: 'var', t: m[0] });
      pos = re.lastIndex;
    }
    if (pos < str.length) out.push({ c: 'str', t: str.slice(pos) });
  }

  function tokens(lang, text) {
    var L = LANGS[lang];
    if (!L) return [{ c: '', t: text }];
    var re = L.re, out = [], last = 0, m, gi;
    re.lastIndex = 0;
    while ((m = re.exec(text))) {
      if (m[0] === '') { re.lastIndex++; continue; }
      if (m.index > last) out.push({ c: '', t: text.slice(last, m.index) });
      gi = 1;
      while (gi < m.length && m[gi] === undefined) gi++;
      var cls = L.rules[gi - 1][0];
      if (cls === 'str' && L.inner && (!L.inner.only || m[0].charAt(0) === L.inner.only)) {
        splitInner(out, m[0], L.inner.str);
      } else {
        out.push({ c: cls, t: m[0] });
      }
      last = re.lastIndex;
    }
    if (last < text.length) out.push({ c: '', t: text.slice(last) });
    return out;
  }

  function lines(lang, text) {
    var res = [''];
    tokens(lang, text).forEach(function (tok) {
      tok.t.split('\n').forEach(function (piece, i) {
        if (i > 0) res.push('');
        if (piece === '') return;
        var e = esc(piece);
        res[res.length - 1] += tok.c ? '<span class="t-' + tok.c + '">' + e + '</span>' : e;
      });
    });
    return res;
  }

  /* Highlight every <pre data-lang> whose lines are <span class="ln"> elements. */
  function apply(root) {
    [].forEach.call((root || document).querySelectorAll('[data-lang]'), function (block) {
      var lang = block.getAttribute('data-lang');
      if (!LANGS[lang]) return;
      var spans = block.querySelectorAll('.ln');
      var text = [].map.call(spans, function (s) { return s.textContent; }).join('\n');
      var html = lines(lang, text);
      if (html.length !== spans.length) return; // safety: keep plain text
      [].forEach.call(spans, function (s, i) { s.innerHTML = html[i]; });
    });
  }

  global.highlight = { lines: lines, apply: apply, languages: Object.keys(LANGS) };
})(window);
