// stdin: JSON {name: sql}  ->  stdout: JSON {name: {fmt, idempotent, sameTokens}}  (used by test_format.py)
import fs from 'fs'; import path from 'path'; import { fileURLToPath } from 'url';
const here = path.dirname(fileURLToPath(import.meta.url));
const code = fs.readFileSync(path.join(here, '../static/js/format.js'), 'utf8');
const { formatSql } = await import('data:text/javascript;base64,' + Buffer.from(code).toString('base64'));
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const norm = s => s.replace(/\s+/g, '').toLowerCase();
const out = {};
for (const [k, v] of Object.entries(input)) {
  const f = formatSql(v);
  out[k] = { fmt: f, idempotent: formatSql(f) === f, sameTokens: norm(f) === norm(v) };
}
process.stdout.write(JSON.stringify(out));
