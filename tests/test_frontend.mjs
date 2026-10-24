// Pure-logic tests for the frontend modules (no browser needed). Run: node tests/test_frontend.mjs
import fs from 'fs'; import path from 'path'; import { fileURLToPath } from 'url';
const here = path.dirname(fileURLToPath(import.meta.url));
const load = (file, strip = []) => {
  let code = fs.readFileSync(path.join(here, '../static/js', file), 'utf8');
  for (const [a, b] of strip) code = code.replace(a, b);
  return import('data:text/javascript;base64,' + Buffer.from(code).toString('base64'));
};
const util = await load('util.js');
const preview = await load('preview.js', [["import { h, table, Colors } from './util.js';", 'const h = () => 0, table = () => 0, Colors = class {};']]);
const { formatSql } = await load('format.js');

let failures = 0, checks = 0;
const eq = (name, got, want) => {
  checks++;
  if (JSON.stringify(got) !== JSON.stringify(want)) { failures++; console.log('FAIL', name, '\n  got ', JSON.stringify(got), '\n  want', JSON.stringify(want)); }
};
const A = (cols, rows) => ({ cols, rows });

// ---- diffRows: multiset semantics + the invariant |base| - removed + added = |current|
let d = util.diffRows(A(['a'], [[1], [2], [3]]), A(['a'], [[1], [3], [4]]));
eq('diff basic', [[...d.added], d.removed], [[2], [[2]]]);
d = util.diffRows(A(['a'], [[1], [1], [2]]), A(['a'], [[1], [2], [2]]));
eq('diff multiset', [[...d.added], d.removed], [[2], [[1]]]);
d = util.diffRows(A(['a'], [[0.1 + 0.2]]), A(['a'], [[0.3]]));
eq('diff float tolerance', [d.added.size, d.removed.length], [0, 0]);
d = util.diffRows(A(['a'], [[null]]), A(['a'], [[null], [null]]));
eq('diff nulls', [[...d.added], d.removed.length], [[1], 0]);
eq('diff column change', util.diffRows(A(['a'], []), A(['a', 'b'], [])).cols, true);
eq('sameResult yes', util.sameResult(A(['a'], [[1], [2]]), A(['a'], [[1], [2]])), true);
eq('sameResult no', util.sameResult(A(['a'], [[1]]), A(['a'], [[1], [1]])), false);
for (let t = 0; t < 300; t++) {
  const g = () => Array.from({ length: Math.floor(Math.random() * 8) }, () => [Math.floor(Math.random() * 4)]);
  const b = A(['a'], g()), c = A(['a'], g()), dd = util.diffRows(b, c);
  eq('diff invariant', b.rows.length - dd.removed.length + dd.added.size, c.rows.length);
}

// ---- preview.parsePartial: which tables does a half-typed query mention?
const S = [{ name: 'employees' }, { name: 'departments' }];
const pp = q => { const p = preview.parsePartial(q, S); return [p.items.map(i => `${i.kind}:${i.name}/${i.alias}`), p.pendingJoin]; };
eq('partial: JOIN with alias', pp('SELECT * FROM employees JOIN departments d ON 1'), [['from:employees/employees', 'join:departments/d'], null]);
eq('partial: dangling JOIN', pp('SELECT * FROM employees JOIN'), [['from:employees/employees'], 'join']);
eq('partial: LEFT JOIN kind kept', pp('SELECT * FROM employees LEFT JOIN departments d ON 1')[0][1], 'left join:departments/d');
eq('partial: NATURAL JOIN kind kept', pp('SELECT * FROM employees NATURAL JOIN departments')[0][1], 'natural join:departments/departments');
eq('partial: keyword is not an alias', pp('SELECT * FROM employees WHERE x'), [['from:employees/employees'], null]);
eq('partial: dangling FROM', pp('SELECT * FROM'), [[], 'from']);

// ---- formatter: whitespace/case only, idempotent, tricky constructs
const norm = s => s.replace(/\s+/g, '').toLowerCase();
const SAMPLES = [
  "select name from employees where dept_id is not distinct from 1 and salary>1",
  "SELECT a, -- trailing\n b /* mid */ FROM t\n-- own line\nWHERE x BETWEEN 1 AND 2 AND y = 'it''s'",
  "with a as (select 1), b as (select 2) select * from a join b on 1=1",
  "select case when x>1 then 'a' when x>0 then 'b' else 'c' end as k, count(*) over (partition by g order by h rows between 1 preceding and current row) from t",
  "select -x, 3 - -2, a*-1 from t where y in (select z from u where z is not null) order by 1 desc limit 5 offset 2",
  "select a from t union all select b from u except select c from v",
];
for (const q of SAMPLES) {
  const f = formatSql(q);
  eq('format idempotent: ' + q.slice(0, 28), formatSql(f), f);
  eq('format preserves tokens: ' + q.slice(0, 28), norm(f), norm(q));
  eq('format keeps string literals: ' + q.slice(0, 28), (f.match(/'(?:[^']|'')*'/g) || []).join('|'), (q.match(/'(?:[^']|'')*'/g) || []).join('|'));
}
eq('format: IS NOT DISTINCT FROM stays on one line', /IS NOT DISTINCT FROM 1/.test(formatSql(SAMPLES[0])), true);

console.log(`${checks} frontend checks, ${failures} failures`);
process.exit(failures ? 1 : 0);
