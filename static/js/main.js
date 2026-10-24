import { $, h, table, debounce, Colors, diffRows, sameResult } from './util.js';
import { Editor } from './editor.js';
import { Viz } from './viz.js';
import { parsePartial, renderPreview } from './preview.js';
import { formatSql, nonWsBefore, posAfterNonWs } from './format.js';

const EXAMPLES = [
  ['Examples…', ''],
  ['INNER JOIN', 'SELECT e.name, d.dept_name\nFROM employees e\nJOIN departments d ON e.dept_id = d.dept_id;'],
  ['LEFT JOIN (keep unmatched)', 'SELECT e.name, d.dept_name\nFROM employees e\nLEFT JOIN departments d ON e.dept_id = d.dept_id;'],
  ['Anti-join', 'SELECT d.dept_name\nFROM departments d\nLEFT JOIN employees e ON d.dept_id = e.dept_id\nWHERE e.emp_id IS NULL;'],
  ['GROUP BY + HAVING', 'SELECT dept_id, COUNT(*) AS n, AVG(salary) AS avg_salary\nFROM employees\nWHERE dept_id IS NOT NULL\nGROUP BY dept_id\nHAVING COUNT(*) > 2;'],
  ['Window: RANK vs DENSE_RANK', 'SELECT name, dept_id, salary,\n  RANK() OVER w AS rnk, DENSE_RANK() OVER w AS dense\nFROM employees WHERE dept_id IS NOT NULL\nWINDOW w AS (PARTITION BY dept_id ORDER BY salary DESC);'],
  ['Running total', 'SELECT order_id, order_date,\n  COUNT(*) OVER (ORDER BY order_date ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS running\nFROM orders;'],
  ['CTE', 'WITH spend AS (\n  SELECT o.customer_id, SUM(oi.qty * p.price) AS total\n  FROM orders o\n  JOIN order_items oi ON oi.order_id = o.order_id\n  JOIN products p ON p.product_id = oi.product_id\n  GROUP BY o.customer_id\n)\nSELECT * FROM spend WHERE total > 1000;'],
  ['Practice: students with no enrollments', 'SELECT s.name\nFROM students s\nLEFT JOIN enrollments e ON e.student_id = s.student_id\nWHERE e.student_id IS NULL;'],
  ['Practice: avg grade (NULLs ignored)', 'SELECT c.title, COUNT(*) AS rows_, COUNT(e.grade) AS graded, AVG(e.grade) AS avg_grade\nFROM courses c\nLEFT JOIN enrollments e ON e.course_id = c.course_id\nGROUP BY c.course_id;'],
  ['Practice: top-rated per genre', 'WITH avg_r AS (\n  SELECT m.title, m.genre, AVG(r.stars) AS avg_stars\n  FROM movies m JOIN ratings r ON r.movie_id = m.movie_id\n  GROUP BY m.movie_id\n)\nSELECT genre, title, avg_stars,\n  RANK() OVER (PARTITION BY genre ORDER BY avg_stars DESC) AS rk\nFROM avg_r;'],
  ['Practice: 3-day moving average', 'SELECT sale_date, amount,\n  AVG(amount) OVER (ORDER BY sale_date ROWS BETWEEN 2 PRECEDING AND CURRENT ROW) AS ma3\nFROM daily_sales\nWHERE region = \'South\';'],
  ['Practice: running balance', 'SELECT account_id, txn_date, amount,\n  SUM(amount) OVER (PARTITION BY account_id ORDER BY txn_date, txn_id) AS balance\nFROM transactions;'],
  ['Practice: funnel (users per step)', 'SELECT event, COUNT(DISTINCT user_id) AS users\nFROM web_events\nGROUP BY event\nORDER BY users DESC;'],
  ['Practice: category tree', 'WITH RECURSIVE tree(category_id, name, depth, path) AS (\n  SELECT category_id, name, 0, name FROM categories WHERE parent_id IS NULL\n  UNION ALL\n  SELECT c.category_id, c.name, t.depth + 1, t.path || \' > \' || c.name\n  FROM categories c JOIN tree t ON c.parent_id = t.category_id\n)\nSELECT * FROM tree;'],
  ['Recursive CTE', 'WITH RECURSIVE chain(emp_id, name, depth) AS (\n  SELECT emp_id, name, 0 FROM employees WHERE emp_id = 1\n  UNION ALL\n  SELECT e.emp_id, e.name, c.depth + 1\n  FROM employees e JOIN chain c ON e.manager_id = c.emp_id\n)\nSELECT * FROM chain;'],
];

// mode: 'play' | 'ex' | 'bug' | 'match' | 'predict'
const S = { schema: [], exercises: [], bugs: [], matches: [], mode: 'play', ex: null, bug: null, match: null, starter: '', seq: 0, ctrl: null, shownSig: null, trace: null,
  cur: null, prev: null, pin: null, diffOn: false, curTrace: null, prevTrace: null, lastRes: null };
// replaceChildren() would render a null child as the text "null"
const fill = (el, ...kids) => el.replaceChildren(...kids.filter(k => k != null && k !== false));
const resetDiff = () => { S.cur = S.prev = S.pin = S.curTrace = S.prevTrace = S.lastRes = null; S.diffOn = false; };
const store = {
  get: k => { try { return localStorage.getItem(k); } catch { return null; } },
  set: (k, v) => { try { localStorage.setItem(k, v); } catch { /* ignore */ } },
};
const api = {
  get: u => fetch(u).then(r => r.json()),
  send: (m, u, body, signal) => fetch(u, { method: m, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body), signal }).then(r => r.json()),
};

/* ---------------------------------------------------------------- components */
let ed = null;
const viz = new Viz($('#viz'), {
  onSpan: span => ed && ed.setMark(span),
  onEdit: (span, text) => { ed.replace(span, text); schedule.flush(); },
  onTables: () => renderPreview(ed.value, S.schema, new Colors(), { onPick: pickTable }),
});
ed = new Editor($('#editor'), {
  onInput: text => { instantPreview(text); schedule(); autosave(); },
  onCaret: pos => viz.followCaret(pos),
  onRun: () => { if (S.mode !== 'predict') runNow(true); },
  onFormat: () => { if (S.mode !== 'predict') doFormat(); },
});

/* ---------------------------------------------------------------- run loop */
const sigOf = text => { const p = parsePartial(text, S.schema); return p.items.map(i => i.name + '/' + i.alias).join(',') + '|' + (p.pendingJoin || ''); };
const colors = () => new Colors();

function instantPreview(text) {
  if (S.mode === 'predict') return;
  if (!text.trim()) { overview(); S.shownSig = null; return; }
  const sig = sigOf(text);
  if (sig === S.shownSig) return;                      // same tables as what's on screen: wait for the real trace
  viz.showNode(renderPreview(text, S.schema, colors(), { onPick: pickTable }));
  S.shownSig = sig;
}

function doFormat() {
  const t = ed.value;
  if (!t.trim()) return;
  const n = nonWsBefore(t, ed.caret());
  const f = formatSql(t);
  if (f !== t) ed.setText(f, posAfterNonWs(f, n));
  schedule.flush();
}

// clicking an "other table" while a FROM / JOIN is waiting for one completes the clause
function pickTable(tab, rel) {
  const t = ed.value;
  const m = /(\bfrom|\bjoin)\s*$/i.exec(t);
  if (!m || !parsePartial(t, S.schema).pendingJoin || S.mode === 'predict') return showTable(tab);
  const used = parsePartial(t, S.schema).items.filter(i => !i.unknown);
  let ins = tab.name;
  if (/join$/i.test(m[1]) && rel) {
    const taken = new Set(used.map(u => u.alias.toLowerCase()));
    let alias = tab.name[0].toLowerCase();
    if (taken.has(alias) || alias === tab.name.toLowerCase()) alias = tab.name.slice(0, 2).toLowerCase();
    if (taken.has(alias)) alias = tab.name;
    ins = alias === tab.name ? `${tab.name} ON ${rel.alias}.${rel.col} = ${tab.name}.${rel.col}` : `${tab.name} ${alias} ON ${rel.alias}.${rel.col} = ${alias}.${rel.col}`;
  }
  const next = t.replace(/\s*$/, ' ') + ins;
  ed.setText(next, next.length);
  schedule.flush();
  ed.focus();
}

function overview() {
  viz.showNode(h('div', {}, h('div', { class: 'narr', style: { padding: '0 0 6px' } }, 'Write a query, or open an exercise. Every step of the query will play out here.'),
    renderPreview('', S.schema, colors(), {})), 'The database');
}

const schedule = debounce(() => exec(false), 60);
function runNow(explicit) { schedule.cancel(); return exec(true, explicit); }

async function exec(flush, explicit = false) {
  const sql = ed.value;
  if (!sql.trim()) { setError(null); renderResult(null); setStatus(''); return; }
  if (S.ctrl) S.ctrl.abort();
  S.ctrl = new AbortController();
  const my = ++S.seq, t0 = performance.now();
  let r;
  try { r = await api.send('POST', '/api/trace', { sql, exercise: S.mode === 'ex' ? S.ex : null, bug: S.mode === 'bug' ? S.bug : null, match: S.mode === 'match' ? S.match : null }, S.ctrl.signal); }
  catch (e) { if (e.name !== 'AbortError') setError({ error: 'Lost connection to the SQL Gym server.' }); return; }
  if (my !== S.seq) return;
  const ms = Math.round(performance.now() - t0);
  if (!r.ok) {
    setError(r); renderResult(null, true); setStatus('');
    const sig = sigOf(sql);
    if (sig !== S.shownSig && S.mode !== 'predict') {
      viz.showNode(renderPreview(sql, S.schema, colors(), { onPick: pickTable }));
      S.shownSig = sig;
    }
    updateCheck(null);
    return;
  }
  setError(null);
  S.trace = r; S.shownSig = sigOf(sql);
  if (!S.cur || !sameResult(S.cur, r.result)) S.prev = S.cur;
  S.cur = r.result;
  if (S.curTrace && S.curTrace.sql !== r.sql) S.prevTrace = S.curTrace;
  S.curTrace = r;
  renderResult(r.result);
  setStatus(`${r.result.total} row${r.result.total === 1 ? '' : 's'} · ${ms} ms`);
  updateCheck(r.check);
  if (r.consistent === false) r.notes = [...r.notes, 'Heads up: the step-by-step view disagrees with the real result for this query, so treat the intermediate steps with caution.'];
  if (r.blocks.length) { viz.load(r, S.mode === 'predict' ? null : ed.caret(), { restart: explicit, prev: S.mode === 'predict' ? null : S.prevTrace }); attachResultNodes(); }
  else viz.clear(h('div', { class: 'empty' }, r.notes.join(' ') || 'No step-by-step view for this query.'));
  if (explicit && r.blocks.length) viz.play(true);
}

/* ---------------------------------------------------------------- result / error / status */
function setStatus(t) { $('#status').textContent = t; }
function setError(r) {
  const el = $('#error');
  if (!r) { el.hidden = true; return; }
  el.hidden = false;
  el.replaceChildren(...[r.error, r.hint ? h('span', { class: 'hint' }, '  ' + r.hint) : null].filter(Boolean));
}
function renderResult(res, stale) {
  const box = $('#result');
  if (!res) { if (!stale) fill(box, ); box.classList.toggle('stale', !!stale); return; }
  box.classList.remove('stale');
  S.lastRes = res;
  const base = S.pin || S.prev;
  const d = base ? diffRows(base, res) : null;
  const changed = d && (d.cols || d.added.size || d.removed.length);
  const rows = S.diffOn && d && !d.cols ? [...res.rows, ...d.removed] : res.rows;
  const t = table(res.cols, rows, { idx: true, plain: true });
  if (S.diffOn && d && !d.cols) t.trs.forEach((tr, i) => { if (i >= res.rows.length) tr.classList.add('drop'); else if (d.added.has(i)) tr.classList.add('new'); });
  const vs = S.pin ? 'pinned' : 'previous';
  const chip = changed ? h('button', { class: 'dchip' + (S.diffOn ? ' on' : ''), title: 'Show which rows changed', onclick: () => { S.diffOn = !S.diffOn; renderResult(S.lastRes); } },
    d.cols ? `columns changed vs ${vs}` : `${d.added.size ? '+' + d.added.size : ''}${d.added.size && d.removed.length ? ' ' : ''}${d.removed.length ? '−' + d.removed.length : ''} vs ${vs}`) : null;
  const pin = h('button', { class: 'dchip' + (S.pin ? ' on' : ''), title: S.pin ? 'Stop comparing against the pinned result' : 'Pin this result as a baseline: later versions of the query are compared against it',
    onclick: () => { S.pin = S.pin ? null : { cols: res.cols, rows: res.rows }; renderResult(S.lastRes); } }, S.pin ? 'Unpin' : 'Pin');
  fill(box, h('div', { class: 'r-h' }, h('b', {}, 'Result'), `${res.total} row${res.total === 1 ? '' : 's'}${res.truncated ? ' (showing first ' + res.rows.length + ')' : ''}`,
    h('span', { class: 'grow' }), chip, pin), h('div', { class: 'r-b' }, t.el));
  attachResultNodes();
}
function attachResultNodes() {
  if (viz.trace && viz.trace === S.curTrace) viz.attachResult([...document.querySelectorAll('#result tbody tr')]);
}
$('#result').addEventListener('click', e => {
  const tr = e.target.closest('tr[data-node]');
  if (tr && viz.resultBi != null) viz.pinElement(tr, viz.resultBi);
});

/* ---------------------------------------------------------------- sidebar */
const collapsed = new Set(JSON.parse(store.get('sg-collapsed') || '[]'));
function section(key, title, kids, extra) {
  const open = !collapsed.has(key);
  const head = h('button', { class: 'stage-h sec-h', onclick: () => { open ? collapsed.add(key) : collapsed.delete(key); store.set('sg-collapsed', JSON.stringify([...collapsed])); renderSide(); } },
    h('span', { class: 'chev' }, open ? '▾' : '▸'), title, extra ? h('span', { class: 'sec-x' }, extra) : null);
  return h('div', {}, head, open ? kids : null);
}
function renderSide() {
  const nav = $('#ex-list'); nav.replaceChildren();
  const bugsDone = S.bugs.filter(b => b.status === 'pass').length;
  const matchDone = S.matches.filter(m => m.status === 'pass').length;
  nav.append(section('games', 'Games', [
    h('button', { class: `side-item ${S.mode === 'predict' && !(P.round && P.round.daily) ? 'active' : ''}`, onclick: () => startPredict(false) },
      h('span', { class: 'dot ring' }), h('span', { class: 'tx' }, h('b', {}, 'Predict the output'), h('span', { class: 'muted' }, ' · 5 questions'))),
    h('button', { class: `side-item ${S.mode === 'predict' && P.round && P.round.daily ? 'active' : ''}`, onclick: () => startPredict(true) },
      h('span', { class: 'dot ring' }), h('span', { class: 'tx' }, h('b', {}, 'Daily challenge'), h('span', { class: 'muted' }, ' · same 3 for everyone today'))),
    h('div', { class: 'stage-h sub' }, 'Match the output', h('span', { class: 'sec-x' }, `${matchDone}/${S.matches.length}`)),
    ...S.matches.map(m => h('button', { class: `side-item compact ${m.status} ${S.mode === 'match' && S.match === m.id ? 'active' : ''}`, onclick: () => openMatch(m.id) },
      h('span', { class: 'dot' }), h('span', { class: 'tx' }, m.title), h('span', { class: 'tag' }, m.topic))),
    h('div', { class: 'stage-h sub' }, 'Bug hunt', h('span', { class: 'sec-x' }, `${bugsDone}/${S.bugs.length}`)),
    ...S.bugs.map(b => h('button', { class: `side-item compact ${b.status} ${S.mode === 'bug' && S.bug === b.id ? 'active' : ''}`, onclick: () => openBug(b.id) },
      h('span', { class: 'dot' }), h('span', { class: 'tx' }, b.title), h('span', { class: 'tag' }, b.topic))),
  ].filter(Boolean)));
  const done = S.exercises.filter(e => e.status === 'pass').length;
  const exKids = []; let stage = null;
  for (const e of S.exercises) {
    if (e.stage !== stage) { stage = e.stage; exKids.push(h('div', { class: 'stage-h sub' }, stage)); }
    exKids.push(h('button', { class: `side-item ${e.status} ${S.mode === 'ex' && S.ex === e.id ? 'active' : ''}`, onclick: () => openExercise(e.id) },
      h('span', { class: 'dot' }), h('span', { class: 'num' }, e.id), h('span', { class: 'tx' }, e.prompt)));
  }
  nav.append(section('ex', 'Exercises', exKids, `${done}/${S.exercises.length}`));
  $('#playground').classList.toggle('active', S.mode === 'play');
  $('#progress').replaceChildren(h('b', {}, done), ` / ${S.exercises.length} exercises · `, h('b', {}, bugsDone), ` / ${S.bugs.length} bugs`);
}
function setStatusOf(list, id, status) {
  const e = list.find(x => x.id === id);
  if (e && e.status !== status) { e.status = status; renderSide(); }
}

/* ---------------------------------------------------------------- mode chrome */
function setMode(mode) {
  if (S.mode === 'ex' || S.mode === 'bug' || S.mode === 'match') save.flush();
  S.mode = mode; S.shownSig = null; resetDiff();
  if (mode !== 'ex') S.ex = null;
  if (mode !== 'bug') S.bug = null;
  if (mode !== 'match') S.match = null;
  const predict = mode === 'predict';
  $('#task').hidden = mode === 'play';
  $('#examples').hidden = mode !== 'play';
  $('#run').hidden = predict; $('#format').hidden = predict;
  ed.readOnly = predict;
  $('#editor').classList.toggle('ro', predict);
  setError(null); updateCheck(null);
}
const autosave = () => { if (S.mode === 'ex' || S.mode === 'bug' || S.mode === 'match') save(); };
const save = debounce(() => {
  if (S.mode === 'ex') api.send('PUT', `/api/answer/${S.ex}`, { sql: ed.value }).catch(() => {});
  else if (S.mode === 'bug') api.send('PUT', `/api/bunganswer/${S.bug}`, { sql: ed.value }).catch(() => {});
  else if (S.mode === 'match') api.send('PUT', `/api/matchanswer/${S.match}`, { sql: ed.value }).catch(() => {});
}, 500);

function openPlayground() {
  setMode('play'); location.hash = '';
  ed.value = store.get('sg-play') || EXAMPLES[1][1];
  renderSide(); runNow(false); ed.focus();
}

/* ---------------------------------------------------------------- exercises */
async function openExercise(id) {
  const x = await api.get(`/api/exercise/${id}`);
  setMode('ex'); S.ex = id; location.hash = 'ex=' + id;
  ed.value = x.answer || '';
  renderTask(x.stage, `Exercise ${x.id}${x.ordered ? ' · row order matters' : ''}`, x.prompt, x.hint, `/api/solution/${x.id}`);
  renderSide();
  if (ed.value.trim()) runNow(false); else { setError(null); renderResult(null); setStatus(''); overview(); }
  ed.focus();
}

/* ---------------------------------------------------------------- match the output */
async function openMatch(id) {
  const m = await api.get(`/api/match/${id}`);
  setMode('match'); S.match = id; S.starter = ''; location.hash = 'match=' + id;
  ed.value = m.answer || '';
  const t = table(m.target.cols, m.target.rows, { idx: true });
  const target = h('div', { class: 'target' }, h('div', { class: 'pane-h' }, 'target ', h('span', { class: 'tag' }, m.target.total + (m.target.total === 1 ? ' row' : ' rows')), m.ordered ? h('span', { class: 'muted' }, 'row order matters') : null), t.el);
  renderTask('Match the output', m.title, 'Write a query that returns exactly this table. Column names do not matter.', null, `/api/matchsolution/${id}`, { kind: 'match', bugId: id, target });
  renderSide();
  if (ed.value.trim()) runNow(false); else { setError(null); renderResult(null); setStatus(''); overview(); }
  ed.focus();
}

/* ---------------------------------------------------------------- bug hunt */
async function openBug(id) {
  const b = await api.get(`/api/bug/${id}`);
  setMode('bug'); S.bug = id; S.starter = b.broken; location.hash = 'bug=' + id;
  ed.value = b.answer || b.broken;
  renderTask('Bug hunt', b.title, b.story, null, `/api/bugsolution/${id}`, { kind: 'bug', bugId: id, broken: b.broken });
  renderSide(); runNow(false); ed.focus();
}

function renderTask(badge, title, prompt, hint, solUrl, bug) {
  const box = $('#task'); box.hidden = false;
  const reveal = h('div', { hidden: true });
  const chk = h('span', { class: 'chk no', id: 'chk' });
  const show = (head, body, extra) => { reveal.hidden = false; reveal.replaceChildren(h('div', { class: 'reveal' }, h('b', {}, head + '  '), body, extra)); };
  const hintBtn = h('button', { class: 'btn ghost', onclick: async () => {
    const t = hint || (await api.get(`/api/${bug.kind}hint/${bug.bugId}`)).hint;
    show('Hint', t);
  } }, 'Hint');
  const solBtn = h('button', { class: 'btn ghost', onclick: async () => {
    if (!confirm('Reveal the reference solution? Try the hint first.')) return;
    const s = await api.get(solUrl);
    const sql = s.sql.replace(/\n {4}/g, '\n').trim();
    show('Reference solution', h('pre', {}, sql), h('div', { class: 'actions' }, h('button', { class: 'btn', onclick: () => { ed.setText(sql); runNow(true); } }, 'Load & visualize')));
  } }, 'Solution');
  const check = h('button', { class: 'btn', onclick: async () => {
    const r = bug ? await api.send('POST', `/api/${bug.kind}check`, { id: bug.bugId, sql: ed.value }) : await api.send('POST', '/api/check', { id: S.ex, sql: ed.value });
    updateCheck(r, true);
  } }, 'Check');
  const actions = [check, hintBtn, solBtn];
  if (bug && bug.broken) actions.push(h('button', { class: 'btn ghost', title: 'Restore the broken query', onclick: () => { ed.setText(bug.broken); runNow(false); } }, 'Reset'));
  fill(box, 
    h('div', { class: 't-top' }, h('span', { class: 'badge' }, badge), h('span', { class: 'muted' }, title)),
    h('div', { class: 'prompt' }, prompt), bug && bug.target ? bug.target : null,
    h('div', { class: 'actions' }, ...actions, chk), reveal, h('div', { id: 'lesson', class: 'reveal lesson', hidden: true }));
}

function updateCheck(c, verbose) {
  const el = $('#chk'), lesson = $('#lesson');
  if (lesson) lesson.hidden = true;
  if (!el || (S.mode !== 'ex' && S.mode !== 'bug' && S.mode !== 'match')) return;
  const untouched = S.mode === 'bug' && ed.value.trim() === S.starter.trim();
  if (!c) { el.className = 'chk no'; el.textContent = ''; return; }
  if (c.status === 'pass') {
    el.className = 'chk ok'; el.textContent = S.mode === 'bug' ? '✓ Fixed' : S.mode === 'match' ? '✓ Matches the target' : '✓ Matches the expected result';
    if (lesson && c.lesson) { lesson.hidden = false; lesson.replaceChildren(h('b', {}, 'What you learned  '), c.lesson); }
  } else if (c.status === 'fail' || c.status === 'error') {
    el.className = 'chk bad';
    el.textContent = untouched ? (S.mode === 'bug' ? 'Still broken: step through it to find why' : '') : (verbose ? 'Not yet: ' + c.message : 'Not matching yet');
  } else el.className = 'chk no';
  const status = untouched ? 'none' : c.status === 'pass' ? 'pass' : c.status === 'fail' ? 'fail' : null;
  if (status) setStatusOf(S.mode === 'bug' ? S.bugs : S.mode === 'match' ? S.matches : S.exercises, S.mode === 'bug' ? S.bug : S.mode === 'match' ? S.match : S.ex, status);
}

/* ---------------------------------------------------------------- predict game */
const P = { round: null };
const LETTERS = ['A', 'B', 'C', 'D'];

function startPredict(daily = false) {
  setMode('predict'); location.hash = daily ? 'daily' : 'predict';
  const d = new Date(), base = (d.getFullYear() * 10000 + (d.getMonth() + 1) * 100 + d.getDate()) * 10;
  P.round = { n: 0, correct: 0, total: daily ? 3 : 5, daily: daily ? base : null, seen: [], marks: [], combo: 0, best: 0, q: null, answered: false, missed: [] };
  renderSide(); nextQuestion();
}
async function nextQuestion() {
  const r = P.round;
  if (r.n >= r.total) return summary();
  const seed = r.daily != null ? r.daily + r.n : Math.floor(Math.random() * 1e9);
  const q = await api.get(`/api/predict/new?seed=${seed}&exclude=${r.seen.join(',')}`);
  r.q = q; r.seen.push(q.id); r.answered = false;
  ed.value = q.sql;
  setError(null); renderResult(null); setStatus('');
  const blank = q.kind === 'blank' || q.kind === 'join';
  viz.showNode(h('div', {}, h('div', { class: 'narr', style: { padding: '0 0 6px' } }, blank ? 'Decide first, then watch the completed query run.' : 'These are the tables involved. Predict first, then watch the query run.'),
    renderPreview(q.sql.replace(/___/g, ' '), S.schema, colors(), {})), 'Predict');
  renderQuestion();
}
function dots() {
  const r = P.round;
  return h('span', { class: 'dots' }, Array.from({ length: r.total }, (_, i) => h('i', { class: r.marks[i] || (i === r.n ? 'cur' : '') })));
}
function renderQuestion(res) {
  const r = P.round, q = r.q, box = $('#task'); box.hidden = false;
  const opts = q.options.map((o, i) => h('button', { class: 'opt', 'data-v': o, disabled: !!res, onclick: () => choose(o) },
    h('kbd', {}, LETTERS[i]), h('span', {}, o)));
  if (res) opts.forEach(b => {
    if (b.dataset.v === res.answer) b.classList.add('ok');
    else if (b.dataset.v === res.chosen) b.classList.add('no');
  });
  const fb = res ? h('div', { class: 'fb ' + (res.correct ? 'ok' : 'no') }, h('b', {}, res.correct ? 'Correct. ' : `Not quite, it is ${res.answer}. `), res.explain,
    h('div', { class: 'actions' }, h('button', { class: 'btn primary', id: 'next', onclick: next }, r.n + 1 >= r.total ? 'See results ⏎' : 'Next question ⏎'),
      r.combo >= 2 ? h('span', { class: 'chk ok' }, `${r.combo} in a row`) : null)) : null;
  fill(box, 
    h('div', { class: 't-top' }, h('span', { class: 'badge' }, r.daily != null ? 'Daily' : 'Predict'), h('span', { class: 'muted' }, `Question ${r.n + 1} of ${r.total} · ${q.topic}`), h('span', { class: 'grow' }), dots()),
    h('div', { class: 'prompt' }, q.ask),
    q.table ? h('div', { class: 'target' }, h('div', { class: 'pane-h' }, 'output ', h('span', { class: 'tag' }, q.table.total + ' rows')), table(q.table.cols, q.table.rows, { idx: true }).el) : null,
    h('div', { class: 'opts' }, opts), fb);
}
async function choose(choice) {
  const r = P.round; if (r.answered) return;
  r.answered = true;
  const res = await api.send('POST', '/api/predict/answer', { id: r.q.id, seed: r.q.seed, choice });
  res.chosen = choice;
  r.marks[r.n] = res.correct ? 'ok' : 'no';
  if (res.correct) { r.correct++; r.combo++; r.best = Math.max(r.best, r.combo); } else { r.combo = 0; r.missed.push(r.q.topic); }
  renderQuestion(res);
  if (res.fullSql) ed.value = res.fullSql;        // blank / join questions: complete the query before running it
  runNow(true);                                   // reveal: play the real query step by step
}
function next() { P.round.n++; nextQuestion(); }
function summary() {
  const r = P.round, box = $('#task');
  const topics = [...new Set(r.missed)];
  const msg = r.correct === r.total ? 'Perfect round.' : r.correct >= 3 ? 'Solid. A few to revisit.' : 'Good practice: the visualizer shows exactly why.';
  fill(box, 
    h('div', { class: 't-top' }, h('span', { class: 'badge' }, r.daily != null ? 'Daily' : 'Predict'), h('span', { class: 'muted' }, r.daily != null ? 'Daily challenge complete' : 'Round complete'), h('span', { class: 'grow' }), dots()),
    h('div', { class: 'prompt' }, h('b', {}, `${r.correct} / ${r.total}`), '  ', msg, r.best >= 2 ? `  Best run: ${r.best} in a row.` : ''),
    topics.length ? h('div', { class: 'muted', style: { marginTop: '6px' } }, 'Worth another look: ' + topics.join(', ')) : null,
    h('div', { class: 'actions' }, h('button', { class: 'btn primary', onclick: () => startPredict(false) }, 'Play a full round'), h('button', { class: 'btn', onclick: openPlayground }, 'Back to playground')));
  ed.value = '';
  viz.showNode(h('div', { class: 'empty' }, `${r.correct} of ${r.total} correct`), 'Round complete');
  renderResult(null);
}
window.addEventListener('keydown', e => {
  if (S.mode !== 'predict' || !P.round || e.metaKey || e.ctrlKey || e.altKey) return;
  const r = P.round;
  if (r.n >= r.total) return;
  const ki = /^[1-4]$/.test(e.key) ? +e.key - 1 : /^[a-d]$/i.test(e.key) ? e.key.toLowerCase().charCodeAt(0) - 97 : -1;
  if (!r.answered && ki >= 0 && r.q && r.q.options[ki] !== undefined) { e.preventDefault(); choose(r.q.options[ki]); }
  else if (r.answered && e.key === 'Enter') { e.preventDefault(); next(); }
});

/* ---------------------------------------------------------------- tables / chrome */
const CORE = new Set(['employees', 'departments', 'customers', 'products', 'orders', 'order_items']);
function renderTables() {
  const row = t => h('div', { class: 'tbl-item', title: t.cols.map(c => c.name + (c.pk ? ' (pk)' : '') + (c.ref ? ' → ' + c.ref : '')).join('\n'), onclick: () => showTable(t) },
    h('span', {}, t.name), h('span', { class: 'muted' }, t.rows));
  const core = S.schema.filter(t => CORE.has(t.name)), more = S.schema.filter(t => !CORE.has(t.name));
  $('#tables').replaceChildren(...core.map(row), ...(more.length ? [h('div', { class: 'stage-h' }, 'Practice sets'), ...more.map(row)] : []));
}
function showTable(t) {
  const cols = t.cols.map(c => ({ alias: t.name, name: c.name }));
  const meta = h('div', { class: 'muted', style: { margin: '4px 0 10px' } }, t.cols.map(c => `${c.name}${c.pk ? ' (pk)' : ''}${c.ref ? ' → ' + c.ref : ''}`).join('  ·  '));
  const canQuery = S.mode !== 'predict';
  $('#dlg-body').replaceChildren(h('div', { class: 'pv-h' }, t.name, h('span', { class: 'tag' }, t.rows + ' rows')), meta, table(cols, t.data.rows, { colors: colors() }).el,
    h('div', { class: 'actions', style: { marginTop: '12px' } },
      canQuery ? h('button', { class: 'btn', onclick: () => { $('#dlg').close(); if (S.mode !== 'play') openPlayground(); ed.setText(`SELECT * FROM ${t.name};`); runNow(true); } }, 'Query this table') : null,
      ' ', h('button', { class: 'btn ghost', onclick: () => $('#dlg').close() }, 'Close')));
  $('#dlg').showModal();
}
$('#dlg').addEventListener('click', e => { if (e.target === $('#dlg')) $('#dlg').close(); });

$('#run').addEventListener('click', () => runNow(true));
$('#format').addEventListener('click', () => { doFormat(); ed.focus(); });
$('#playground').addEventListener('click', openPlayground);
$('#toggle-side').addEventListener('click', () => $('#side').classList.toggle('closed'));
$('#theme').addEventListener('click', () => {
  const cur = document.documentElement.dataset.theme || (matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark');
  const next = cur === 'light' ? 'dark' : 'light';
  document.documentElement.dataset.theme = next; store.set('sg-theme', next);
});
const exSel = $('#examples');
EXAMPLES.forEach(([l], i) => exSel.append(h('option', { value: i }, l)));
exSel.addEventListener('change', () => { const e = EXAMPLES[+exSel.value]; if (e && e[1]) { ed.setText(e[1]); runNow(true); } exSel.value = 0; });
setInterval(() => { if (S.mode === 'play') store.set('sg-play', ed.value); }, 2000);

/* ---------------------------------------------------------------- boot */
(async function boot() {
  [S.schema, S.exercises, S.bugs, S.matches] = await Promise.all([api.get('/api/schema'), api.get('/api/exercises'), api.get('/api/bugs'), api.get('/api/matches')]);
  renderTables(); renderSide();
  const h0 = location.hash;
  let m;
  if ((m = /ex=(\d+)/.exec(h0)) && S.exercises.some(e => e.id === +m[1])) await openExercise(+m[1]);
  else if ((m = /bug=([\w-]+)/.exec(h0)) && S.bugs.some(b => b.id === m[1])) await openBug(m[1]);
  else if ((m = /match=([\w-]+)/.exec(h0)) && S.matches.some(x => x.id === m[1])) await openMatch(m[1]);
  else if (/daily/.test(h0)) startPredict(true);
  else if (/predict/.test(h0)) startPredict(false);
  else openPlayground();
})();
window.sqlgym = { viz, ed, S, P };
