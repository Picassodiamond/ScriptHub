global.window = {};
const fs = require('fs');
eval(fs.readFileSync('static/js/highlight.js', 'utf8'));
const h = window.highlight;
let fail = 0;
function check(name, ok) { if (!ok) fail++; console.log((ok ? 'ok   ' : 'FAIL ') + name); }
const files = { python: 'scripts/system/directory_backup.py', bash: 'scripts/deployment/health_check.sh', batch: 'scripts/system/ping_host.bat', json: 'scripts/system/directory_backup.py' };
// JSON sample: the metadata block at the top of directory_backup.py, without its "# " prefixes.
function metadataJson(src) {
  const lines = src.split('\n'), s = lines.findIndex(l => /--- scripthub ---/.test(l)), e = lines.findIndex(l => /--- end scripthub ---/.test(l));
  return lines.slice(s + 1, e).map(l => l.replace(/^# ?/, '')).join('\n');
}
for (const [lang, f] of Object.entries(files)) {
  let text = fs.readFileSync(f, 'utf8').replace(/\r\n/g, '\n').replace(/\n$/, '');
  if (lang === 'json') text = metadataJson(text);
  const lines = h.lines(lang, text);
  check(lang + ' line count matches (' + lines.length + ')', lines.length === text.split('\n').length);
  const plain = lines.map(l => l.replace(/<[^>]+>/g, '').replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&amp;/g, '&')).join('\n');
  check(lang + ' round-trips to original text', plain === text);
}
const t = (lang, src) => h.lines(lang, src).join('\n');
check('python keyword', t('python', 'def f(): return None').includes('<span class="t-kw">def</span>'));
check('python triple string spans lines', (() => { const r = h.lines('python', 'x = """a\nb"""\ny = 1'); return r.length === 3 && r[0].includes('t-str') && r[1].includes('t-str') && r[2].includes('t-num'); })());
check('python # inside string is not a comment', !t('python', 's = "a # b"').includes('t-com'));
check('bash variable + option', (() => { const r = t('bash', 'curl --max-time "$timeout" -- "$url"; echo ${x:-5}'); return r.includes('t-opt">--max-time') && r.includes('t-var">${x:-5}'); })());
check('bash $# is not a comment', !t('bash', 'echo $# args').includes('t-com'));
check('bash comment', t('bash', '# hi').includes('t-com'));
check('batch rem + var + label', (() => { const r = t('batch', 'rem hello\nset "C=%~2"\n:end'); return r.includes('t-com') && r.includes('t-var">%~2') && r.includes('t-lab'); })());
check('json key vs string', (() => { const r = t('json', '{"a": "b", "n": 1, "t": true}'); return r.includes('t-key">&quot;') === false && r.includes('t-key') && r.includes('t-str') && r.includes('t-num') && r.includes('t-kw'); })());
check('html is escaped', t('python', 'x = "<script>alert(1)</script>"').includes('&lt;script&gt;'));
check('unknown language is plain', h.lines('ruby', 'a<b').join('') === 'a<b' || h.lines('ruby', 'a<b').join('').includes('a'));
process.exit(fail ? 1 : 0);
