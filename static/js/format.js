// SQL formatter. Only whitespace and keyword case ever change; identifiers, literals and comments are preserved.
const HARD = new Set(('select from where group by having order limit offset join left right full inner outer cross natural on using as and or not in is null like glob between ' +
  'exists case when then else end distinct all union intersect except with recursive over partition asc desc nulls filter values true false collate window materialized escape').split(' ').map(s => s.toUpperCase()));
const SOFT = new Set('ROWS RANGE GROUPS UNBOUNDED PRECEDING FOLLOWING CURRENT ROW EXCLUDE TIES OTHERS NO FIRST LAST'.split(' '));
const FUNCS = new Set(('COUNT SUM AVG MIN MAX TOTAL GROUP_CONCAT ROW_NUMBER RANK DENSE_RANK NTILE LAG LEAD FIRST_VALUE LAST_VALUE NTH_VALUE PERCENT_RANK CUME_DIST ' +
  'COALESCE IFNULL NULLIF IIF CAST SUBSTR SUBSTRING LENGTH LOWER UPPER TRIM LTRIM RTRIM REPLACE ROUND ABS DATE TIME DATETIME JULIANDAY STRFTIME RANDOM TYPEOF INSTR PRINTF').split(' '));
const CONST = new Set(['CURRENT_DATE', 'CURRENT_TIME', 'CURRENT_TIMESTAMP']);
const CLAUSE = new Set(['SELECT', 'FROM', 'WHERE', 'HAVING', 'WINDOW', 'LIMIT', 'VALUES']);
const COMPOUND = new Set(['UNION', 'INTERSECT', 'EXCEPT']);
const JOINW = new Set(['NATURAL', 'LEFT', 'RIGHT', 'FULL', 'INNER', 'CROSS', 'JOIN']);
const END_LIST = new Set(['FROM', 'WHERE', 'GROUP', 'HAVING', 'WINDOW', 'ORDER', 'LIMIT', 'UNION', 'INTERSECT', 'EXCEPT']);
const UNARY_AFTER = new Set(['SELECT', 'WHERE', 'AND', 'OR', 'NOT', 'WHEN', 'THEN', 'ELSE', 'BETWEEN', 'IN', 'ON', 'BY', 'LIMIT', 'OFFSET', 'HAVING', 'DISTINCT', 'ALL', 'IS']);

const LEX = /(\s+)|(--[^\n]*)|(\/\*[\s\S]*?(?:\*\/|$))|('(?:[^']|'')*'?)|("(?:[^"]|"")*"?|`(?:[^`]|``)*`?|\[[^\]\n]*\]?)|(\d+\.?\d*(?:[eE][+-]?\d+)?|\.\d+|0[xX][0-9a-fA-F]+)|([A-Za-z_][A-Za-z_0-9$]*)|(\?\d*|[:@$][A-Za-z_]\w*)|(<>|!=|==|<=|>=|<<|>>|\|\||->>|->|[-+*/%<>=&|~^.,;()])|([\s\S])/g;

function lex(src) {
  const toks = []; let m, nl = false, sp = false;
  LEX.lastIndex = 0;
  while ((m = LEX.exec(src))) {
    if (m[1] !== undefined) { sp = true; if (m[1].includes('\n')) nl = true; continue; }
    const k = m[2] !== undefined ? 'lc' : m[3] !== undefined ? 'bc' : m[4] !== undefined ? 'str' : m[5] !== undefined ? 'qid'
      : m[6] !== undefined ? 'num' : m[7] !== undefined ? 'word' : m[8] !== undefined ? 'param' : 'op';
    toks.push({ k, x: m[0], a: m.index, b: m.index + m[0].length, nl, sp });
    nl = false; sp = false;
  }
  return toks;
}

const isW = (t, ...ws) => t && t.k === 'word' && (!ws.length || ws.includes(t.x.toUpperCase()));
const isO = (t, x) => t && t.k === 'op' && t.x === x;

export function formatSql(src) {
  const T = lex(src);
  const match = new Map(), st = [];
  T.forEach((t, i) => { if (isO(t, '(')) st.push(i); else if (isO(t, ')') && st.length) match.set(st.pop(), i); });

  const out = [];
  let pend = null, lineLevel = 0, prev = null, atStart = true;
  const newBlock = base => ({ block: true, base, clause: null, list: false, between: 0, caseD: 0, caseStack: [], inWith: false, inOn: false, joinPhrase: false, closeLevel: base });
  const frames = [newBlock(0)];
  const top = () => frames[frames.length - 1];
  const blk = () => { for (let i = frames.length - 1; i >= 0; i--) if (frames[i].block) return frames[i]; return frames[0]; };
  const inl = () => !!top().inline;
  const winCtx = () => frames.some(f => f.win) || blk().clause === 'WINDOW';

  function space(p, c) {
    if (!p) return false;
    if (c.x === ',' || c.x === ')' || c.x === ';' || c.x === '.') return false;
    if (p.x === '(' || p.x === '.' || p.unary) return false;
    if (c.x === '(') {
      if (p.kind === 'word') return p.hard;
      if (p.kind === 'op') return p.x !== '(';
      return true;
    }
    return true;
  }
  function put(text, kind, extra = {}) {
    if (pend != null && out.length) {
      while (out.length && out[out.length - 1] === ' ') out.pop();
      out.push('\n' + '  '.repeat(pend)); lineLevel = pend; atStart = true;
    }
    pend = null;
    const cur = { kind, x: text, ...extra };
    if (!atStart && space(prev, cur)) out.push(' ');
    out.push(text); atStart = false; prev = cur;
  }
  const level = () => (pend != null ? pend : lineLevel);

  function countItems(i) {            // commas at depth 0 of the select list starting after token i
    let d = 0, c = 0;
    for (let j = i + 1; j < T.length; j++) {
      const t = T[j];
      if (isO(t, '(')) d++;
      else if (isO(t, ')')) { if (d === 0) break; d--; }
      else if (d === 0) {
        if (isO(t, ',')) c++;
        else if (isO(t, ';')) break;
        else if (t.k === 'word' && END_LIST.has(t.x.toUpperCase()) && !(t.x.toUpperCase() === 'FROM' && isW(T[j - 1], 'DISTINCT'))) break;
      }
    }
    return c;
  }
  function caseLen(i) {                // source length of CASE ... END
    let d = 0;
    for (let j = i; j < T.length; j++) {
      if (isW(T[j], 'CASE')) d++;
      else if (isW(T[j], 'END')) { d--; if (d === 0) return T[j].b - T[i].a; }
    }
    return 0;
  }
  function hasNestedSub(open, close) {
    for (let j = open + 1; j < close; j++) if (isO(T[j], '(') && isW(T[j + 1], 'SELECT', 'WITH')) return true;
    return false;
  }

  for (let i = 0; i < T.length; i++) {
    const t = T[i], f = blk(), inline = inl();

    if (t.k === 'lc' || t.k === 'bc') {
      const saved = pend;
      if (t.nl && prev) { pend = top().block ? top().base : lineLevel; }
      else pend = null;
      put(t.x, 'comment');
      pend = t.k === 'lc' ? (saved != null ? saved : (t.nl && prev ? level() : lineLevel)) : saved;
      if (t.k === 'lc' && pend == null) pend = lineLevel;
      continue;
    }

    if (t.k === 'op') {
      const x = t.x;
      if (x === '(') {
        const nx = T[i + 1], close = match.get(i);
        const isSub = isW(nx, 'SELECT', 'WITH', 'VALUES') && close != null;
        const prevAs = isW(T[i - 1], 'AS', 'MATERIALIZED');
        const prevOver = isW(T[i - 1], 'OVER');
        let block = false;
        if (isSub && !inline) block = (f.inWith && prevAs) || (T[close].a - t.a) > 60 || hasNestedSub(i, close);
        put('(', 'op');
        const lvl = lineLevel;
        if (block) {
          const nb = newBlock(lvl + 1); nb.paren = true; nb.closeLevel = lvl; frames.push(nb); pend = lvl + 1;
        } else frames.push({ inline: true, win: prevOver || (f.clause === 'WINDOW' && prevAs) || frames.some(fr => fr.win) });
        continue;
      }
      if (x === ')') {
        if (frames.length > 1) {
          const fr = frames.pop();
          if (fr.block) { pend = fr.closeLevel; }
        }
        put(')', 'op');
        continue;
      }
      if (x === ',') {
        const cur = top();
        put(',', 'op');
        if (cur.block && !cur.inline) {
          if (cur.clause === 'SELECT' && cur.list) pend = cur.base + 1;
          else if (cur.inWith && cur.clause === null) pend = cur.base;
        }
        continue;
      }
      if (x === ';') {
        put(';', 'op');
        if (i + 1 < T.length) { out.push('\n'); pend = 0; frames.length = 1; frames[0] = newBlock(0); }
        continue;
      }
      let unary = false;
      if (x === '-' || x === '+') {
        const p = prev;
        unary = !p || p.kind === 'comment' || (p.kind === 'op' && p.x !== ')') || (p.kind === 'word' && p.hard && UNARY_AFTER.has(p.x.toUpperCase()));
      }
      put(x, 'op', { unary });
      continue;
    }

    if (t.k !== 'word') { put(t.x, t.k); continue; }

    // ---- words
    const u = t.x.toUpperCase(), nx = T[i + 1], pv = T[i - 1];
    const qualified = isO(pv, '.') || isO(nx, '.');
    let text = t.x, hard = false;
    if (!qualified) {
      if (HARD.has(u)) { text = u; hard = true; }
      else if (FUNCS.has(u) && isO(nx, '(')) text = u;
      else if (CONST.has(u)) text = u;
      else if (SOFT.has(u) && (winCtx() || (isW(pv, 'NULLS') && (u === 'FIRST' || u === 'LAST')))) { text = u; hard = true; }
    }
    const cur = top();
    if (!inline && !qualified) {
      if (u === 'WITH' && (f.clause === null)) { f.inWith = true; if (out.length && !(prev && prev.x === '(')) pend = f.base; put('WITH', 'word', { hard: true }); continue; }
      if (CLAUSE.has(u) && !(u === 'FROM' && isW(pv, 'DISTINCT'))) {
        f.clause = u; f.list = false; f.inOn = false; f.between = 0; f.joinPhrase = false;
        if (u === 'SELECT') f.inWith = false;
        pend = f.base; put(u, 'word', { hard: true });
        if (u === 'SELECT') {
          let j = i + 1;
          while (isW(T[j], 'DISTINCT', 'ALL')) { put(T[j].x.toUpperCase(), 'word', { hard: true }); j++; }
          if (countItems(j - 1) > 0) { f.list = true; pend = f.base + 1; }
          i = j - 1;
        }
        continue;
      }
      if ((u === 'GROUP' || u === 'ORDER') && isW(nx, 'BY')) {
        f.clause = u + ' BY'; f.list = false; f.inOn = false; f.between = 0; f.joinPhrase = false;
        pend = f.base; put(u, 'word', { hard: true }); put('BY', 'word', { hard: true }); i++;
        continue;
      }
      if (COMPOUND.has(u)) {
        f.clause = null; f.list = false; f.inOn = false;
        pend = f.base; put(u, 'word', { hard: true });
        if (isW(nx, 'ALL')) { put('ALL', 'word', { hard: true }); i++; }
        pend = f.base;
        continue;
      }
      if (JOINW.has(u) && !isO(nx, '(')) {
        if (!f.joinPhrase) { pend = f.base; f.joinPhrase = true; f.inOn = false; }
        if (u === 'JOIN') f.joinPhrase = false;
        put(text, 'word', { hard: true });
        continue;
      }
      if (u === 'ON') { f.inOn = true; put('ON', 'word', { hard: true }); continue; }
      if (u === 'BETWEEN') { f.between++; put('BETWEEN', 'word', { hard: true }); continue; }
      if (u === 'CASE') {
        f.caseD++;
        const ml = caseLen(i) > 60;
        f.caseStack.push(ml ? level() : null);
        put('CASE', 'word', { hard: true });
        continue;
      }
      if (u === 'END' && f.caseD > 0) {
        const base = f.caseStack.pop(); f.caseD--;
        if (base != null) pend = base;
        put('END', 'word', { hard: true });
        continue;
      }
      if ((u === 'WHEN' || u === 'ELSE') && f.caseD > 0) {
        const base = f.caseStack[f.caseStack.length - 1];
        if (base != null) pend = base + 1;
        put(u, 'word', { hard: true });
        continue;
      }
      if (u === 'AND' || u === 'OR') {
        const want = f.clause === 'WHERE' || f.clause === 'HAVING' || f.inOn;
        if (u === 'AND' && f.between > 0) f.between--;
        else if (want && f.caseD === 0) pend = f.base + 1;
        put(u, 'word', { hard: true });
        continue;
      }
    }
    put(text, 'word', { hard });
  }
  return out.join('').replace(/[ \t]+\n/g, '\n').replace(/^\n+|\s+$/g, '');
}

// number of non-whitespace chars before position `pos` (caret mapping across a reformat)
export const nonWsBefore = (s, pos) => s.slice(0, pos).replace(/\s/g, '').length;
export function posAfterNonWs(s, n) {
  if (n <= 0) return 0;
  let c = 0;
  for (let i = 0; i < s.length; i++) { if (!/\s/.test(s[i])) c++; if (c === n) return i + 1; }
  return s.length;
}
