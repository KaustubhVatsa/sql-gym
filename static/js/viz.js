import { h, table, show, cell, refCols, keepVisible, Colors } from './util.js';
import { renderPlan } from './plan.js';
import { buildGraph, follow, labelOf, story } from './lineage.js';

const SVGNS = 'http://www.w3.org/2000/svg';
const q = v => (typeof v === 'string' ? `'${v}'` : show(v));
const LABEL = {
  source: s => 'FROM', join: s => 'JOIN ' + s.rightAlias, where: () => 'WHERE', group: s => (s.implicit ? 'AGGREGATE' : 'GROUP BY'),
  having: () => 'HAVING', window: () => 'WINDOW', select: () => 'SELECT', distinct: () => 'DISTINCT', order: () => 'ORDER BY',
  limit: () => 'LIMIT', setop: s => s.op, recursive_iter: s => (s.n === 0 ? 'ANCHOR' : 'STEP ' + s.n), result: () => 'RESULT', opaque: () => 'RESULT',
};
const cap = (t, n = 26) => (t.length > n ? t.slice(0, n - 1) + '…' : t);
const paneH = (label, name, count, alias, colors) => h('div', { class: 'pane-h' },
  alias ? h('i', { class: 'dot-c', style: { '--ac': colors.of(alias) } }) : null, label, name ? h('b', {}, name) : null,
  count != null ? h('span', { class: 'tag' }, count + (count === 1 ? ' row' : ' rows')) : null);

function vdCell(v, pend = true) {
  const td = h('td', { class: 'vd ' + (v === 1 ? 't' : v === 0 ? 'f' : 'u') + (pend ? ' pend' : '') }, v === 1 ? '✓' : v === 0 ? '✗' : '?');
  td.title = v === 1 ? 'TRUE' : v === 0 ? 'FALSE' : 'NULL (unknown)';
  return td;
}
const verdWord = v => (v === 1 ? 'TRUE' : v === 0 ? 'FALSE' : 'NULL');
function refVals(text, cols, row, max = 3) {
  const idx = [...refCols(text, cols)].slice(0, max);
  return idx.map(i => `${cols[i].name || cols[i]} = ${q(row[i])}`).join(', ');
}
function usedIdx(cols, texts, fallback = 4) {
  const set = new Set();
  texts.forEach(t => refCols(t, cols).forEach(i => set.add(i)));
  const a = [...set].sort((x, y) => x - y);
  return a.length ? a : cols.map((_, i) => i).slice(0, fallback);
}

const R = {};
const tagRows = (c, trs, ids) => { if (!c.N || !ids) return; trs.forEach((tr, i) => { if (ids[i] != null) tr.dataset.node = ids[i]; }); };

/* ---------------------------------------------------------------- FROM */
R.source = (s, c) => {
  const t = table(s.cols, s.rows, { colors: c.colors, idx: true });
  tagRows(c, t.trs, c.N && c.N.out);
  const N = s.rows.length;
  return {
    el: h('div', {}, paneH('table ', s.alias, N, s.alias, c.colors), t.el), n: N + 1,
    apply(k) {
      t.trs.forEach((tr, i) => { tr.classList.toggle('pend', k <= N && i >= k); tr.classList.toggle('act', k >= 1 && k <= N && i === k - 1); });
      if (k >= 1 && k <= N) keepVisible(t.el, t.trs[k - 1]);
      return k === 0 ? 'The scan reads the table one row at a time.' : k <= N ? `Read row ${k} of ${N}.` : `${N} rows read. Nothing is filtered or computed yet.`;
    },
  };
};

/* ---------------------------------------------------------------- JOIN */
R.join = (s, c) => {
  const Ln = s.left.length, kind = s.joinType;
  const L = table(s.leftCols, s.left, { colors: c.colors, idx: true });
  const Rt = table(s.rightCols, s.right, { colors: c.colors, idx: true });
  const O = table([...s.leftCols, ...s.rightCols], s.out, { colors: c.colors, idx: true });
  L.el.classList.add('mx'); Rt.el.classList.add('mx');
  tagRows(c, L.trs, c.N && c.N.in); tagRows(c, Rt.trs, c.N && c.N.right); tagRows(c, O.trs, c.N && c.N.out);
  const svg = document.createElementNS(SVGNS, 'svg');
  const lAlias = s.leftCols.length ? [...new Set(s.leftCols.map(x => x.alias))].join(' ⋈ ') : 'left';
  const top = h('div', { class: 'jn-top' },
    h('div', {}, paneH('left ', lAlias, Ln), L.el), h('div'),
    h('div', {}, paneH('right ', s.rightAlias, s.right.length, s.rightAlias, c.colors), Rt.el), svg);
  const byL = new Map();
  s.pairs.forEach(([a, b]) => { if (!byL.has(a)) byL.set(a, []); byL.get(a).push(b); });
  const mL = new Set(s.pairs.map(p => p[0])), mR = new Set(s.pairs.map(p => p[1]));
  const keepL = kind === 'LEFT' || kind === 'FULL', keepR = kind === 'RIGHT' || kind === 'FULL';
  const cond = h('div', { class: 'cond' },
    h('span', { class: 'tag' }, (s.comma ? 'CROSS' : s.natural ? 'NATURAL ' + kind : kind) + ' JOIN'),
    s.on ? ['ON', h('code', {}, cap(s.on, 70))] : s.using ? ['USING', h('code', {}, s.using.join(', '))] : h('span', {}, 'no condition: every pair'),
    (s.on || s.using) ? h('span', { class: 'swap' }, ['INNER', 'LEFT', 'RIGHT', 'FULL'].map(k => {
      const n = s.pairs.length + (k === 'LEFT' || k === 'FULL' ? s.leftUnmatched.length : 0) + (k === 'RIGHT' || k === 'FULL' ? s.rightUnmatched.length : 0);
      const adds = [...(k === 'LEFT' || k === 'FULL' ? s.leftUnmatched.map(i => ['L', i]) : []), ...(k === 'RIGHT' || k === 'FULL' ? s.rightUnmatched.map(i => ['R', i]) : [])];
      const peek = on => adds.forEach(([side, i]) => (side === 'L' ? L : Rt).trs[i].classList.toggle('peek', on));
      return h('button', {
        class: k === kind ? 'on' : '', title: `${k} JOIN returns ${n} row${n === 1 ? '' : 's'}` + (s.kwSpan ? ' (click to rewrite)' : ''),
        disabled: (!s.kwSpan || c.noRewrite) && k !== kind, onclick: () => s.kwSpan && !c.noRewrite && c.edit(s.kwSpan, k + ' JOIN'),
        onmouseenter: () => peek(true), onmouseleave: () => peek(false),
      }, k, h('i', {}, n));
    })) : null);
  const refs = s.on ? [...s.on.matchAll(/([A-Za-z_]\w*)\.([A-Za-z_]\w*)/g)].map(m => [m[1].toLowerCase(), m[2].toLowerCase()]) : (s.using || []).map(u => [null, u.toLowerCase()]);
  const keyIdx = s.leftCols.map((col, i) => [col, i]).filter(([col]) => refs.some(([a, n]) => (a === null || a === col.alias.toLowerCase()) && n === col.name.toLowerCase())).map(x => x[1]);
  const keyText = li => keyIdx.length ? keyIdx.map(i => `${s.leftCols[i].alias}.${s.leftCols[i].name} = ${q(s.left[li][i])}`).join(', ') : '';
  const lblOut = paneH('output ', null, s.out.length);
  const draw = (active, complete) => {
    svg.replaceChildren();
    const tr = top.getBoundingClientRect();
    const lr = L.el.getBoundingClientRect(), rr = Rt.el.getBoundingClientRect();
    const list = [];
    if (active >= 0) (byL.get(active) || []).forEach(b => list.push([active, b, false]));
    else if (complete && s.pairs.length <= 60) s.pairs.forEach(([a, b]) => list.push([a, b, true]));
    for (const [a, b, faint] of list) {
      const A = L.trs[a].getBoundingClientRect(), B = Rt.trs[b].getBoundingClientRect();
      if (A.bottom < lr.top || A.top > lr.bottom || B.bottom < rr.top || B.top > rr.bottom) continue;
      const x1 = lr.right - tr.left, x2 = rr.left - tr.left, y1 = A.top + A.height / 2 - tr.top, y2 = B.top + B.height / 2 - tr.top;
      const p = document.createElementNS(SVGNS, 'path');
      p.setAttribute('d', `M${x1} ${y1} C${x1 + 26} ${y1},${x2 - 26} ${y2},${x2} ${y2}`);
      if (faint) p.setAttribute('class', 'faint');
      svg.append(p);
    }
  };
  let last = [-1, false];
  const redraw = () => requestAnimationFrame(() => draw(...last));
  L.el.addEventListener('scroll', redraw); Rt.el.addEventListener('scroll', redraw);
  const ro = new ResizeObserver(redraw); ro.observe(top);
  return {
    el: h('div', {}, cond, top, lblOut, O.el), n: Ln + 1, dispose: () => ro.disconnect(),
    apply(k) {
      const complete = k > Ln, active = k >= 1 && k <= Ln ? k - 1 : -1, done = Math.min(k, Ln);
      const hits = new Set(active >= 0 ? byL.get(active) || [] : []);
      L.trs.forEach((tr, i) => {
        tr.className = '';
        if (i === active) tr.classList.add('act');
        else if (i < done && !mL.has(i)) tr.classList.add(keepL ? 'nm' : 'drop');
      });
      Rt.trs.forEach((tr, i) => {
        tr.className = hits.has(i) ? 'hit' : '';
        if (complete && !mR.has(i)) tr.classList.add(keepR ? 'nm' : 'dim');
      });
      let lastNew = null;
      O.trs.forEach((tr, i) => {
        const li = s.outLi[i];
        const vis = li != null ? li < done : complete;
        tr.className = vis ? (li === active || (complete && li == null) ? 'new' : '') : 'pend';
        if (tr.classList.contains('new')) lastNew = tr;
      });
      if (active >= 0) keepVisible(L.el, L.trs[active]);
      if (hits.size) keepVisible(Rt.el, Rt.trs[[...hits][0]]);
      if (lastNew) keepVisible(O.el, lastNew);
      last = [active, complete]; redraw();
      if (k === 0) return s.comma || (!s.on && !s.using) ? `Pair every left row with every right row.` : `For each left row, look for right rows where ${s.on || 'the USING columns are equal'}.`;
      if (complete) {
        const bits = [`${s.out.length} row(s) out.`];
        if (s.leftUnmatched.length) bits.push(`${s.leftUnmatched.length} left row(s) unmatched.`);
        if (s.rightUnmatched.length) bits.push(`${s.rightUnmatched.length} right row(s) unmatched.`);
        return bits.join(' ');
      }
      const m = (byL.get(active) || []).length, kt = keyText(active), pre = `Left row ${active + 1}${kt ? ' (' + kt + ')' : ''}`;
      if (m) return `${pre} → ${m} match${m > 1 ? 'es' : ''} → ${m} row${m > 1 ? 's' : ''} out.`;
      const nullKey = keyIdx.some(i => s.left[active][i] === null);
      return `${pre} → no match${nullKey ? ' (NULL never equals anything)' : ''} → ${keepL ? 'kept, right side filled with NULL' : 'dropped'}.`;
    },
  };
};

/* ---------------------------------------------------------------- sub-queries, per row */
function subPane(subs) {
  const el = h('div', { class: 'subpane' });
  const rule = (sb, d) => {
    if (sb.kind === 'EXISTS') return d.n > 0 ? 'rows found, so EXISTS is TRUE' : 'no rows, so EXISTS is FALSE';
    if (sb.kind === 'NOT EXISTS') return d.n > 0 ? 'rows found, so NOT EXISTS is FALSE' : 'no rows, so NOT EXISTS is TRUE';
    if (sb.kind === 'SCALAR') return d.n === 0 ? 'no rows, so the value is NULL' : d.n > 1 ? `${d.n} rows: SQLite takes the first` : 'one row: that value is used';
    const hasNull = (d.rows || []).some(r => r[0] === null);
    return hasNull ? (sb.kind === 'NOT IN' ? 'the list contains NULL: NOT IN can never be TRUE' : 'the list contains NULL: a non-match gives NULL, not FALSE') : `${sb.kind} compares the row's value with this list`;
  };
  const update = idx => {
    el.replaceChildren(...subs.map(sb => {
      const d = sb.correlated ? (idx == null ? null : sb.per[idx]) : sb.once;
      const cols = sb.correlated ? sb.cols : sb.once && sb.once.cols;
      const head = h('div', { class: 'sub-h' }, h('span', { class: 'tag' }, sb.kind), h('code', {}, cap(sb.text.replace(/\s+/g, ' '), 64)),
        h('span', { class: 'muted' }, sb.correlated ? 're-run for each row' : 'computed once'));
      if (!d) return h('div', { class: 'subq' }, head, h('div', { class: 'muted' }, "Step through the rows to see this sub-query re-run with each row's values."));
      const n = d.n;
      const body = cols && d.rows && d.rows.length ? table(cols, d.rows, { plain: true }).el : null;
      return h('div', { class: 'subq' }, head,
        h('div', { class: 'sub-r' }, h('b', {}, `${n} row${n === 1 ? '' : 's'}`), h('span', { class: 'muted' }, rule(sb, { ...d, n, rows: d.rows }))), body);
    }));
  };
  update(null);
  return { el, update };
}

/* ---------------------------------------------------------------- WHERE */
R.where = (s, c) => {
  const N = s.rows.length;
  const refs = refCols(s.whole, s.cols);
  const extra = s.preds.map((p, pi) => ({ head: p, label: cap(p, 22), cell: i => vdCell(s.verdicts[i][pi]) }));
  if (s.combine) extra.push({ head: `all terms combined with ${s.combine}`, label: s.combine === 'OR' ? '= any (OR)' : '= all (AND)', cell: i => vdCell(s.wholeV[i]) });
  const I = table(s.cols, s.rows, { colors: c.colors, idx: true, extra, refs });
  const keepIdx = s.rows.map((_, i) => i).filter(i => s.keep[i]);
  const O = table(s.cols, keepIdx.map(i => s.rows[i]), { colors: c.colors, idx: true });
  tagRows(c, I.trs, c.N && c.N.in); tagRows(c, O.trs, c.N && c.N.out);
  const vds = I.trs.map(tr => [...tr.querySelectorAll('.vd')]);
  const sp = s.subs && s.subs.length ? subPane(s.subs) : null;
  return {
    el: h('div', {}, h('div', { class: 'two' },
      h('div', {}, paneH('input ', null, N), I.el), h('div', {}, paneH('passes ', null, keepIdx.length), O.el)), sp ? sp.el : null), n: N + 1,
    apply(k) {
      const j = Math.min(k, N), act = k >= 1 && k <= N ? k - 1 : -1;
      if (sp) sp.update(act >= 0 ? act : null);
      I.trs.forEach((tr, i) => {
        tr.className = i === act ? 'act' : i < j && !s.keep[i] ? 'drop' : '';
        vds[i].forEach(v => v.classList.toggle('pend', i >= j));
      });
      O.trs.forEach((tr, oi) => { const i = keepIdx[oi]; tr.className = i < j ? (i === act ? 'new' : '') : 'pend'; });
      if (act >= 0) { keepVisible(I.el, I.trs[act]); const oi = keepIdx.indexOf(act); if (oi >= 0) keepVisible(O.el, O.trs[oi]); }
      if (k === 0) return `Test every row against: ${cap(s.whole, 90)}`;
      if (act < 0) return `${keepIdx.length} of ${N} rows pass.`;
      const v = s.wholeV ? s.wholeV[act] : s.verdicts[act][s.verdicts[act].length - 1];
      const vals = refVals(s.whole, s.cols, s.rows[act]);
      const parts = s.combine ? ` [${s.verdicts[act].map(verdWord).join(` ${s.combine} `)}]` : '';
      return `Row ${act + 1}${vals ? ' (' + vals + ')' : ''}${parts} → ${verdWord(v)} → ${s.keep[act] ? 'kept' : 'dropped'}.`;
    },
  };
};

/* ---------------------------------------------------------------- GROUP BY */
function formula(a, vals, members) {
  const nn = vals.filter(v => v !== null && v !== undefined);
  const list = arr => (arr.length > 6 ? arr.slice(0, 5).map(show).join(', ') + ', …' : arr.map(show).join(', '));
  switch (a.name) {
    case 'COUNT': return a.star ? `${members} row(s)` : `${nn.length} non-NULL of ${members}`;
    case 'SUM': case 'TOTAL': return nn.length ? nn.map(show).slice(0, 6).join(' + ') + (nn.length > 6 ? ' + …' : '') : 'no non-NULL values';
    case 'AVG': return nn.length ? `sum ÷ ${nn.length}` : 'no non-NULL values';
    case 'MIN': case 'MAX': return nn.length ? `of ${list(nn)}` : 'no non-NULL values';
    default: return '';
  }
}
function groupLabel(s, g) {
  return s.keys.length ? s.keys.map((k, i) => `${cap(k, 24)} = ${q(g.key[i])}`).join(', ') : 'all rows';
}
R.group = (s, c) => {
  const showIdx = usedIdx(s.cols, [...s.keys, ...s.aggs.map(a => a.arg || '')], 3);
  const argPos = []; let p = 0;
  s.aggs.forEach(a => argPos.push(a.arg ? p++ : -1));
  const extraAggs = s.aggs.map((a, ai) => ({ a, ai })).filter(({ a }) => a.arg && !/^[\w.]+$/.test(a.arg));
  const cards = s.groups.map((g, gi) => {
    const rows = g.members.map(m => s.rows[m]);
    const extra = extraAggs.map(({ a, ai }) => ({ head: a.arg, label: cap(a.arg, 20), cell: ri => cell(s.rowArgs[g.members[ri]][argPos[ai]]) }));
    const t = rows.length ? table(s.cols, rows, { colors: c.colors, show: showIdx, extra, plain: true }) : null;
    if (t && c.N) t.trs.forEach((tr, mi) => { tr.dataset.node = c.N.in[g.members[mi]]; });
    const foot = s.aggs.map((a, ai) => {
      const vals = a.arg ? g.members.map(m => s.rowArgs[m][argPos[ai]]) : [];
      return h('div', { class: 'agg' }, h('b', {}, cap(a.text, 34)), ' = ', show(g.aggs[ai]), '  ', h('span', { class: 'fm' }, formula(a, vals, g.members.length)));
    });
    const card = h('div', { class: 'gcard pend' },
      h('div', { class: 'gh' }, h('span', {}, groupLabel(s, g)), h('span', {}, `${rows.length} row${rows.length === 1 ? '' : 's'}`)),
      h('div', { class: 'gb' }, t ? t.el : h('div', { class: 'empty' }, 'no rows')), foot.length ? h('div', { class: 'gf' }, foot) : null);
    if (c.N) card.dataset.node = c.N.out[gi];
    return card;
  });
  const N = cards.length;
  return {
    el: h('div', {}, h('div', { class: 'pane-h' }, h('b', {}, `${s.rows.length} row${s.rows.length === 1 ? '' : 's'}`), '→', h('b', {}, `${N} group${N === 1 ? '' : 's'}`),
      s.keys.length ? h('span', {}, 'by ' + s.keys.join(', ')) : null), h('div', { class: 'cards' }, cards)), n: N + 1,
    apply(k) {
      const act = k >= 1 && k <= N ? k - 1 : -1;
      cards.forEach((cd, i) => { cd.classList.toggle('pend', i >= k && k <= N); cd.classList.toggle('act', i === act); });
      if (act >= 0) cards[act].scrollIntoView({ block: 'nearest', behavior: 'smooth' });
      if (k === 0) return s.implicit ? 'Without GROUP BY, all rows form one group.' : `Rows with equal ${s.keys.join(', ')} are collected into the same group.`;
      if (act < 0) return `${N} group${N === 1 ? '' : 's'} formed; each will become one row.`;
      const g = s.groups[act];
      return `Group ${groupLabel(s, g)}: ${g.members.length} row(s) → ${s.aggs.map(a => a.text).slice(0, 3).map(cap).join(', ') || 'one row'}.`;
    },
  };
};

R.having = (s, c) => {
  const cols = [...s.keys, ...s.aggs.map(a => a.text)].map(x => cap(x, 26));
  const rows = s.groups.map(g => [...g.key, ...g.aggs]);
  const t = table(cols, rows, { extra: [{ head: s.text, label: 'HAVING', cell: i => vdCell(s.verdicts[i] === 1 ? 1 : s.verdicts[i] === 0 ? 0 : s.verdicts[i]) }] });
  const N = rows.length, vds = t.trs.map(tr => tr.querySelector('.vd'));
  tagRows(c, t.trs, c.N && c.N.in);
  return {
    el: h('div', {}, h('div', { class: 'cond' }, h('span', { class: 'tag' }, 'HAVING'), h('code', {}, cap(s.text, 80))), t.el), n: N + 1,
    apply(k) {
      const j = Math.min(k, N), act = k >= 1 && k <= N ? k - 1 : -1;
      t.trs.forEach((tr, i) => { tr.className = i === act ? 'act' : i < j && !s.keep[i] ? 'drop' : ''; vds[i].classList.toggle('pend', i >= j); });
      if (k === 0) return 'HAVING tests each finished group (WHERE could not: aggregates did not exist yet).';
      if (act < 0) return `${s.keep.filter(Boolean).length} of ${N} groups survive.`;
      return `Group ${groupLabel(s, s.groups[act])} → ${verdWord(s.verdicts[act])} → ${s.keep[act] ? 'kept' : 'dropped'}.`;
    },
  };
};

/* ---------------------------------------------------------------- WINDOW */
const FRAMES = [
  ['ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW', 'strict running'],
  ['RANGE BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW', 'default (peers)'],
  ['ROWS BETWEEN 1 PRECEDING AND 1 FOLLOWING', 'neighbours'],
  ['ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING', 'whole partition'],
];
const RANKS = ['ROW_NUMBER', 'RANK', 'DENSE_RANK'];
const normF = t => t.replace(/\s+/g, ' ').trim().toUpperCase();
function windowTry(f, c) {
  const chips = [];
  const sp = f.spans || {};
  if (f.cat === 'frame' && f.orderBy.length && (sp.frame || sp.inner != null)) {
    FRAMES.forEach(([fr, label]) => {
      const cur = f.frameExplicit ? normF(f.frame) === fr : fr.startsWith('RANGE');
      chips.push(h('button', { class: cur ? 'on' : '', title: `Rewrite the frame as ${fr}`, onclick: () => (sp.frame ? c.edit(sp.frame, fr) : c.edit([sp.inner, sp.inner], ' ' + fr)) }, label));
    });
  }
  if (RANKS.includes(f.name) && sp.fn) RANKS.forEach(n => chips.push(h('button', { class: n === f.name ? 'on' : '', title: `Use ${n}()`, onclick: () => c.edit(sp.fn, n) }, n)));
  if (f.hasTies && f.tiebreak && sp.order) chips.push(h('button', { title: `Break ties with ${f.tiebreak}`, onclick: () => c.edit([sp.order[1], sp.order[1]], ', ' + f.tiebreak) }, '+ tiebreaker'));
  if (!chips.length) return null;
  return h('div', { class: 'try' }, h('span', { class: 'muted' }, 'Try'), h('span', { class: 'swap' }, chips), f.namedWindow ? h('span', { class: 'muted' }, 'edits the shared WINDOW definition') : null);
}

R.window = (s, c) => {
  const wrap = h('div');
  const inst = { el: wrap, n: 0, apply: () => '' };
  let fi = 0, cur = null;
  const build = i => {
    const f = s.funcs[i];
    const showIdx = usedIdx(s.cols, [...f.partitionBy, ...f.orderBy.map(o => o.text), f.arg || ''], 3);
    const flat = []; const tables = [];
    const cards = f.partitions.map((p, pi) => {
      const rows = p.rows.map(e => s.rows[f.entries[e].row]);
      const t = table(s.cols, rows, {
        colors: c.colors, show: showIdx, plain: true, refs: refCols([...f.partitionBy, ...f.orderBy.map(o => o.text)].join(','), s.cols),
        extra: [{ head: f.text, label: cap(f.text, 26), cell: ri => { const td = cell(f.entries[p.rows[ri]].value); td.classList.add('cc', 'pend'); return td; } }],
      });
      p.rows.forEach((e, pos) => {
        flat.push({ e, pi, pos });
        if (pos > 0 && f.orderBy.length && f.entries[e].peer === f.entries[p.rows[pos - 1]].peer) t.trs[pos].classList.add('tie');
      });
      tables.push(t);
      if (c.N) p.rows.forEach((e, pos) => { t.trs[pos].dataset.node = c.N.in[f.entries[e].row]; });
      const lab = f.partitionBy.length ? `${cap(f.partitionBy.join(', '), 22)} = ${p.key.map(q).join(', ')}` : 'whole result';
      return h('div', { class: 'gcard' }, h('div', { class: 'gh' }, h('span', {}, lab), h('span', {}, p.rows.length + ' rows')), h('div', { class: 'gb' }, t.el));
    });
    const N = flat.length;
    const spec = h('div', { class: 'spec' },
      f.partitionBy.length ? h('span', {}, 'PARTITION BY ', h('b', {}, f.partitionBy.join(', '))) : h('span', {}, 'one partition'),
      f.orderBy.length ? h('span', {}, 'ORDER BY ', h('b', {}, f.orderBy.map(o => o.text).join(', '))) : null,
      f.cat === 'frame' ? h('span', { title: f.frameExplicit ? 'explicit frame' : 'default frame' }, 'FRAME ', h('b', {}, f.frameExplicit ? f.frame : 'default · ' + f.frame)) : null);
    const ins = f.insights.map(([k, t]) => h('div', { class: 'ins ' + k }, t));
    const tryRow = windowTry(f, c);
    const el = h('div', {}, spec, tryRow, h('div', { class: 'cards' }, cards), ins.length ? h('div', { class: 'ins-list' }, ins) : null);
    const cells = flat.map(({ e, pi, pos }) => tables[pi].trs[pos].querySelector('td.cc'));
    return {
      el, n: N + 1,
      apply(k) {
        const j = Math.min(k, N), act = k >= 1 && k <= N ? k - 1 : -1;
        flat.forEach(({ e, pi, pos }, gi) => {
          const tr = tables[pi].trs[pos], ent = f.entries[e];
          const tie = tr.classList.contains('tie');
          tr.className = tie ? 'tie' : '';
          cells[gi].classList.toggle('pend', gi >= j);
        });
        cards.forEach(cd => cd.classList.remove('act'));
        let narr;
        if (act >= 0) {
          const { e, pi, pos } = flat[act], ent = f.entries[e], p = f.partitions[pi];
          cards[pi].classList.add('act');
          const trs = tables[pi].trs;
          trs[pos].classList.add('cur');
          const name = f.name;
          if (f.cat === 'frame') {
            const [a, b] = ent.frame, ex = new Set(ent.excl || []);
            const ids = [];
            for (let x = a; x <= b; x++) if (!ex.has(x)) { trs[x].classList.add('fr'); ids.push(f.entries[p.rows[x]].arg); }
            const vals = f.arg ? ` [${ids.slice(0, 8).map(show).join(', ')}${ids.length > 8 ? ', …' : ''}]` : '';
            narr = `Row ${ent.row + 1}: frame covers ${ids.length} row(s)${vals} → ${name}${f.arg ? '(' + cap(f.arg, 18) + ')' : '(*)'} = ${show(ent.value)}.`;
          } else if (f.cat === 'offset') {
            if (ent.src != null) trs[ent.src].classList.add('srcrow');
            narr = `Row ${ent.row + 1}: ${name} looks ${ent.off} row(s) ${name === 'LAG' ? 'back' : 'ahead'} in the partition → ${show(ent.value)}${ent.src == null ? ' (no such row → NULL/default)' : ''}.`;
          } else {
            const tied = p.rows.filter(x => f.entries[x].peer === ent.peer).length;
            narr = `Row ${ent.row + 1}: position ${pos + 1} of ${p.rows.length} in its partition${tied > 1 ? `, tied with ${tied - 1} other row(s)` : ''} → ${name} = ${show(ent.value)}.`;
          }
          keepVisible(tables[pi].el, trs[pos]);
          cards[pi].scrollIntoView({ block: 'nearest', behavior: 'smooth' });
        } else narr = k === 0 ? `${f.name} is computed per row from its partition${f.cat === 'frame' ? ' and frame' : ''}; rows are never collapsed.` : `Computed for all ${N} rows; no row was removed.`;
        return narr;
      },
    };
  };
  const tabs = h('div', { class: 'tabs' });
  const body = h('div');
  const pick = i => {
    fi = i; cur = build(i);
    body.replaceChildren(cur.el);
    inst.n = cur.n; inst.apply = cur.apply;
    [...tabs.children].forEach((b, j) => b.classList.toggle('on', j === i));
  };
  if (s.funcs.length > 1) s.funcs.forEach((f, i) => tabs.append(h('button', { onclick: () => { pick(i); c.complete(); } }, cap(f.text, 34))));
  wrap.append(tabs, body);
  pick(0);
  return inst;
};

/* ---------------------------------------------------------------- SELECT */
R.select = (s, c) => {
  const N = s.rows.length;
  const O = table(s.cols, s.rows, { colors: c.colors, idx: true });
  const heads = O.table.querySelectorAll('thead th:not(.ix)');
  s.exprs.forEach((e, i) => {
    if (heads[i] && e.kind !== 'col' && e.text !== s.cols[i]) { heads[i].append(h('div', { class: 'sub', title: e.text }, cap(e.text, 30))); }
  });
  let I = null;
  if (s.inRows && s.inRows.length <= 80) {
    const idx = usedIdx(s.inCols, s.exprs.map(e => e.text), 5);
    I = table(s.inCols, s.inRows, { colors: c.colors, idx: true, show: idx, refs: new Set(idx) });
  }
  tagRows(c, O.trs, c.N && c.N.out); if (I && c.N) tagRows(c, I.trs, c.N.in);
  const el = I ? h('div', { class: 'two' }, h('div', {}, paneH('input ', null, s.inRows.length), I.el), h('div', {}, paneH('output ', null, N), O.el))
    : h('div', {}, paneH('output ', null, N), O.el);
  const sp = s.subs && s.subs.length ? subPane(s.subs) : null;
  return {
    el: sp ? h('div', {}, el, sp.el) : el, n: N + 1,
    apply(k) {
      const j = Math.min(k, N), act = k >= 1 && k <= N ? k - 1 : -1;
      if (sp) sp.update(act >= 0 && s.src[act] != null ? s.src[act] : null);
      O.trs.forEach((tr, i) => { tr.className = i === act ? 'act' : i >= j ? 'pend' : ''; });
      if (I) I.trs.forEach((tr, i) => { tr.className = act >= 0 && s.src[act] === i ? 'act' : ''; });
      if (act >= 0) { keepVisible(O.el, O.trs[act]); if (I && s.src[act] != null) keepVisible(I.el, I.trs[s.src[act]]); }
      if (k === 0) return s.noFrom ? 'Evaluate the expressions once.' : 'Compute each output column for every surviving row.';
      if (act < 0) return `${N} row(s) × ${s.cols.length} column(s).`;
      return `${s.agg ? 'Group' : 'Row'} ${act + 1} → ${s.rows[act].slice(0, 4).map(q).join(', ')}${s.rows[act].length > 4 ? ', …' : ''}`;
    },
  };
};

R.distinct = (s, c) => {
  const N = s.rows.length;
  const t = table(s.cols, s.rows, { idx: true });
  tagRows(c, t.trs, c.N && c.N.in);
  const seen = new Map();
  const firstOf = s.rows.map((r, i) => { const k = JSON.stringify(r); if (seen.has(k)) return seen.get(k); seen.set(k, i); return null; });
  return {
    el: h('div', {}, paneH('rows ', null, N), t.el), n: N + 1,
    apply(k) {
      const j = Math.min(k, N), act = k >= 1 && k <= N ? k - 1 : -1;
      t.trs.forEach((tr, i) => { tr.className = i === act ? 'act' : i < j && firstOf[i] != null ? 'drop' : ''; });
      if (act >= 0) keepVisible(t.el, t.trs[act]);
      if (k === 0) return 'Rows identical to an earlier row will be removed.';
      if (act < 0) return `${s.out.length} distinct row(s) remain.`;
      return firstOf[act] != null ? `Row ${act + 1} duplicates row ${firstOf[act] + 1} → removed.` : `Row ${act + 1} is new → kept.`;
    },
  };
};

R.order = (s, c) => {
  const N = s.rows.length;
  const refs = refCols(s.keys.join(','), s.cols);
  const t = table(s.cols, s.before, { idx: true, refs });
  const posAfter = new Array(N).fill(0);
  s.from.forEach((b, i) => { if (b != null) posAfter[b] = i; });
  if (c.N) t.trs.forEach((tr, i) => { tr.dataset.node = c.N.in[i] + ' ' + c.N.out[posAfter[i]]; });
  return {
    el: h('div', {}, paneH('sorted by ', s.keys.join(', '), N), t.el), n: 1,
    apply(k) {
      requestAnimationFrame(() => {
        const rh = t.trs[0] ? t.trs[0].getBoundingClientRect().height : 0;
        t.trs.forEach((tr, i) => { tr.style.transform = k >= 1 ? `translateY(${(posAfter[i] - i) * rh}px)` : ''; });
      });
      t.el.querySelectorAll('td.ix').forEach((td, i) => { td.title = 'original position'; });
      return k === 0 ? 'Rows as SELECT produced them (the # column is the original position).' : `Sorted by ${s.keys.join(', ')}.`;
    },
  };
};

R.limit = (s, c) => {
  const N = s.rows.length;
  const t = table(s.cols, s.rows, { idx: true });
  if (c.N) t.trs.forEach((tr, i) => { const k = i - s.offset; tr.dataset.node = c.N.in[i] + (k >= 0 && k < c.N.out.length ? ' ' + c.N.out[k] : ''); });
  return {
    el: h('div', {}, paneH('rows ', null, N), t.el), n: 1,
    apply(k) {
      t.trs.forEach((tr, i) => { tr.className = k >= 1 && (i < s.offset || i >= s.offset + s.count) ? 'dim' : ''; });
      return k === 0 ? 'LIMIT is the very last step.' : `Keep ${s.count} row(s)${s.offset ? ' after skipping ' + s.offset : ''}.`;
    },
  };
};

R.setop = (s, c) => {
  const tl = table(s.left.cols, s.left.rows, {}), tr_ = table(s.right.cols, s.right.rows, {});
  const to = table(s.out.cols, s.out.rows, { extra: [{ head: 'from', label: 'from', cell: i => h('td', { class: 'setmark' }, s.origin[i] === 'LR' ? 'L+R' : s.origin[i]) }] });
  return {
    el: h('div', { class: 'three' }, h('div', {}, paneH('first ', null, s.left.rows.length), tl.el),
      h('div', {}, paneH('second ', null, s.right.rows.length), tr_.el), h('div', {}, paneH(s.op + ' ', null, s.out.rows.length), to.el)),
    n: 0, apply: () => `${s.op} combines the two results.`,
  };
};

R.recursive_iter = (s, c) => {
  const total = s.rows.length, base = s.n === 0 ? 0 : Math.min(total, s.iterStarts ? s.iterStarts[s.n] : total - s.newCount), newN = Math.max(0, Math.min(s.newCount, total - base));
  const t = table(s.cols, s.rows, { idx: true });
  return {
    el: h('div', {}, paneH(s.n === 0 ? 'anchor result ' : `working table after iteration ${s.n} `, null, s.total), t.el), n: newN + 1,
    apply(k) {
      const j = Math.min(k, newN), act = k >= 1 && k <= newN ? k - 1 : -1;
      t.trs.forEach((tr, i) => { tr.className = i < base ? 'dim' : i - base >= j ? 'pend' : i - base === act ? 'act' : 'new'; });
      if (act >= 0) keepVisible(t.el, t.trs[base + act]);
      return s.n === 0 ? 'Seed rows from the anchor query.' : k === 0 ? 'Previous rows are the input of the recursive step.' : `${newN} new row(s) appended.`;
    },
  };
};

R.result = (s, c) => {
  const extra = s.iterOf ? [{ head: 'iteration', label: 'iter', cell: i => h('td', { class: 'num null' }, s.iterOf[i]) }] : [];
  const t = table(s.cols, s.rows, { idx: true, extra });
  tagRows(c, t.trs, c.N && c.N.out);
  return { el: h('div', {}, paneH('rows ', null, s.rows.length), t.el), n: 0, apply: () => `${s.rows.length} row(s).` };
};
R.opaque = (s, c) => {
  const t = table(s.cols, s.rows, { idx: true });
  return { el: h('div', {}, h('div', { class: 'ins' }, s.message), h('div', { style: { height: '10px' } }), t.el), n: 0, apply: () => 'Shown as a plain result.' };
};

/* ================================================================ player */
export class Viz {
  constructor(root, { onSpan, onEdit, onTables }) {
    this.root = root; this.onSpan = onSpan; this.onEdit = onEdit; this.onTables = onTables; this.alt = null; this.plan = [];
    this.rail = h('div', { class: 'rail' });
    this.head = h('div', { class: 'stage-head' });
    this.narr = h('div', { class: 'narr' });
    this.body = h('div', { class: 'stage-body' });
    this.notes = h('div', { class: 'notes', hidden: true });
    this.linBar = h('div', { class: 'lin-bar', hidden: true });
    this.prevBtn = h('button', { class: 'btn ghost', title: 'Previous step (←)', onclick: () => this.step(-1) }, '◀');
    this.playBtn = h('button', { class: 'btn', title: 'Play / pause (Space)', onclick: () => this.toggle() }, '▶ Play');
    this.nextBtn = h('button', { class: 'btn ghost', title: 'Next step (→)', onclick: () => this.step(1) }, '▶|');
    this.stepn = h('span', { class: 'stepn' });
    this.speed = h('select', { class: 'sel', title: 'Speed', style: { maxWidth: '70px' }, onchange: () => { if (this.timer) this.play(true); } },
      h('option', { value: '2' }, '0.5×'), h('option', { value: '1', selected: true }, '1×'), h('option', { value: '.5' }, '2×'), h('option', { value: '.25' }, '4×'));
    this.dbBtn = h('button', { class: 'btn ghost', title: 'Show all tables in the database', onclick: () => this.toggleAlt('db') }, 'Tables');
    this.planBtn = h('button', { class: 'btn ghost', title: 'How SQLite physically runs this query (EXPLAIN QUERY PLAN)', onclick: () => this.toggleAlt('plan') }, 'Plan');
    this.ctl = h('div', { class: 'ctl' }, this.prevBtn, this.nextBtn, this.playBtn, this.stepn, h('span', { class: 'grow' }), this.dbBtn, this.planBtn, this.speed);
    root.append(this.rail, this.head, this.narr, this.notes, this.linBar, this.body, this.ctl);
    this.body.addEventListener('click', e => {
      if (e.target.closest('button, a, .try, .swap, .tabs')) return;
      const el = e.target.closest('[data-node]');
      if (el && this.flat[this.i]) this.pinElement(el, this.flat[this.i].bi);
    });
    this.graphs = null; this.lin = null; this.resultEls = []; this.resultBi = null;
    this.flat = []; this.i = 0; this.k = 0; this.r = null; this.timer = null;
    this.clear('Write a query to see it run.');
    window.addEventListener('keydown', e => {
      if (e.key === 'Escape') this.unpin();
      if (e.target.closest('textarea,input,select,dialog')) return;
      if (e.key === 'ArrowRight') { e.preventDefault(); this.step(1); }
      else if (e.key === 'ArrowLeft') { e.preventDefault(); this.step(-1); }
      else if (e.key === ' ') { e.preventDefault(); this.toggle(); }
    });
  }

  clear(msg) {
    this.pause(); this.dispose(); this.resetAlt();
    this.flat = []; this.r = null;
    this.rail.replaceChildren(); this.head.replaceChildren(); this.narr.textContent = ''; this.notes.hidden = true;
    this.body.replaceChildren(typeof msg === 'string' ? h('div', { class: 'empty' }, msg) : msg);
    this.ctl.style.visibility = 'hidden'; this.onSpan(null);
  }
  showNode(node, label) {
    this.pause(); this.dispose(); this.resetAlt();
    this.flat = []; this.r = null;
    this.rail.replaceChildren(); this.head.replaceChildren(h('span', { class: 'stage-title' }, label || 'Database'));
    this.narr.textContent = ''; this.notes.hidden = true;
    this.body.replaceChildren(node); this.ctl.style.visibility = 'hidden'; this.onSpan(null);
  }
  dispose() { if (this.r && this.r.dispose) this.r.dispose(); }
  resetAlt() { this.alt = null; this.dbBtn.classList.remove('on'); this.planBtn.classList.remove('on'); }
  toggleAlt(kind) {
    if (!this.flat.length) return;
    this.pause();
    this.alt = this.alt === kind ? null : kind;
    this.dbBtn.classList.toggle('on', this.alt === 'db');
    this.planBtn.classList.toggle('on', this.alt === 'plan');
    if (this.alt) { this.body.replaceChildren(this.alt === 'db' ? this.onTables() : renderPlan(this.plan)); this.narr.textContent = ''; }
    else this.mount(this.i, 'end');
  }

  load(trace, caret, { restart = false, prev = null } = {}) {
    this.pause(); this.resetAlt(); this.plan = trace.plan || [];
    const prevKey = this.flat[this.i] ? this.keyOf(this.flat[this.i]) : null;
    this.trace = trace; this.prevTrace = prev; this.colors = new Colors();
    this.graphs = trace.blocks.map(buildGraph); this.lin = null; this.resultEls = []; this.linBar.hidden = true;
    this.flat = [];
    trace.blocks.forEach((b, bi) => b.stages.forEach((st, si) => this.flat.push({ bi, si, b, st })));
    this.ctl.style.visibility = this.flat.length ? 'visible' : 'hidden';
    this.notes.hidden = !trace.notes.length; this.notes.textContent = trace.notes.join(' ');
    this.buildRail();
    if (!this.flat.length) { this.body.replaceChildren(h('div', { class: 'empty' }, 'No step-by-step view for this query.')); return; }
    let idx = restart ? 0 : this.stageAt(caret);
    if (idx < 0) { const j = prevKey ? this.flat.findIndex(f => this.keyOf(f) === prevKey) : -1; idx = j >= 0 ? j : 0; }
    this.mount(idx, restart ? 'start' : 'end');
  }
  keyOf(f) { return `${f.b.title}|${f.st.type}|${f.st.title}`; }
  stageAt(pos) {
    if (pos == null) return -1;
    let best = -1, len = Infinity;
    this.flat.forEach((f, i) => {
      const sp = f.st.span;
      if (sp && pos >= sp[0] && pos <= sp[1] && sp[1] - sp[0] < len && f.st.type !== 'result') { best = i; len = sp[1] - sp[0]; }
    });
    return best;
  }
  followCaret(pos) {
    if (this.timer || !this.flat.length) return;
    const i = this.stageAt(pos);
    if (i >= 0 && i !== this.i) this.mount(i, 'end');
  }

  // ---- row lineage -------------------------------------------------------------------------
  attachResult(trs) {
    this.resultEls = []; this.resultBi = null;
    if (!this.graphs || !this.graphs.length) return;
    const bi = this.graphs.length - 1, G = this.graphs[bi];
    if (!G.ok) return;
    const out = G.stages[G.stages.length - 1].out;
    trs.forEach((tr, i) => { if (out[i]) { tr.dataset.node = out[i]; this.resultEls.push(tr); } });
    this.resultBi = bi;
    this.applyLineage();
  }
  pinElement(el, bi) {
    const id = el.dataset.node.split(' ')[0];
    if (this.lin && this.lin.id === id && this.lin.bi === bi) return this.unpin();
    const G = this.graphs[bi];
    if (!G || !G.ok) return;
    const f = follow(G, id);
    const stageLabel = si => (LABEL[G.block.stages[si].type] || (() => ''))(G.block.stages[si]);
    this.lin = { ...f, bi, label: labelOf(G, id), story: story(G, f, stageLabel), stageLabel };
    this.applyLineage();
  }
  unpin() { if (this.lin) { this.lin = null; this.applyLineage(); } }
  applyLineage() {
    const lin = this.lin, cur = this.flat[this.i];
    const els = [...this.body.querySelectorAll('[data-node]'), ...this.resultEls.filter(e => e.isConnected)];
    this.body.classList.toggle('lin-on', !!lin);
    this.rail.querySelectorAll('.chip').forEach(ch => ch.removeAttribute('data-lin'));
    if (!lin) { els.forEach(e => e.removeAttribute('data-lin')); this.linBar.hidden = true; return; }
    const G = this.graphs[lin.bi];
    const classify = (e, bi) => {
      if (bi !== lin.bi) return e.removeAttribute('data-lin');
      const ids = e.dataset.node.split(' ');
      e.dataset.lin = ids.includes(lin.id) ? 'pin' : ids.some(x => lin.set.has(x)) ? 'on' : 'off';
    };
    this.body.querySelectorAll('[data-node]').forEach(e => classify(e, cur ? cur.bi : -1));
    this.resultEls.forEach(e => classify(e, this.resultBi));
    // rail: where the followed row is present, and where it ends
    this.flat.forEach((f, gi) => {
      if (f.bi !== lin.bi) return;
      const N = G.stages[f.si];
      const present = [...N.in, ...N.right, ...N.out].some(x => x && lin.set.has(x));
      const chip = this.rail.querySelector(`.chip[data-i="${gi}"]`);
      if (chip && present) chip.dataset.lin = f.si === lin.story.dropSi ? 'end' : 'on';
      if (chip && !present && f.si === lin.story.dropSi) chip.dataset.lin = 'end';
    });
    const sg = lin.story;
    const parts = [];
    if (sg.origin.length) parts.push('built from ' + sg.origin.slice(0, 5).join(' + '));
    sg.beats.slice(0, 2).forEach(b => parts.push(b));
    parts.push(sg.fate);
    const here = cur && cur.bi !== lin.bi ? `  (followed in "${G.block.title}")` : '';
    this.linBar.hidden = false;
    this.linBar.replaceChildren(h('span', { class: 'lin-t' }, 'Following'), h('b', {}, lin.label), h('span', { class: 'lin-s' }, parts.join(' · ') + here),
      h('button', { class: 'btn ghost', title: 'Stop following (Esc)', onclick: () => this.unpin() }, 'Unpin'));
  }

  buildRail() {
    this.rail.replaceChildren();
    this.prevRows = this.prevTrace ? new Map(this.prevTrace.blocks.flatMap(b => b.stages.map(st => [`${b.title}|${st.type}|${st.title}`, st.rowsOut]))) : null;
    this.trace.blocks.forEach((b, bi) => {
      const chips = h('div', { class: 'chips' });
      b.stages.forEach((st, si) => {
        const gi = this.flat.findIndex(f => f.bi === bi && f.si === si);
        const cnt = st.rowsIn != null && st.rowsOut != null && st.rowsIn !== st.rowsOut && st.type !== 'source' && st.type !== 'result' ? `${st.rowsIn}→${st.rowsOut}` : st.rowsOut != null ? st.rowsOut : '';
        const pv = this.prevRows && this.prevRows.get(`${b.title}|${st.type}|${st.title}`);
        const dl = pv != null && st.rowsOut != null && st.rowsOut !== pv ? st.rowsOut - pv : 0;
        const chip = h('button', { class: 'chip', 'data-i': gi, title: dl ? `${dl > 0 ? '+' : ''}${dl} row(s) vs the previous version of the query` : null, onclick: () => { this.pause(); this.mount(gi, 'end'); } }, (LABEL[st.type] || (() => st.type))(st), h('span', { class: 'cnt' }, cnt), dl ? h('span', { class: 'dlt' }, (dl > 0 ? '+' : '') + dl) : null);
        chips.append(chip);
      });
      this.rail.append(h('div', { class: 'blk' }, h('div', { class: 'blk-t' }, b.title), chips));
    });
  }

  mount(i, mode) {
    this.dispose();
    this.i = i = Math.max(0, Math.min(this.flat.length - 1, i));
    const { st, b, bi, si } = this.flat[i];
    const G = this.graphs && this.graphs[bi];
    const ctx = { N: G && G.ok ? G.stages[si] : null, colors: this.colors, edit: (span, t) => this.onEdit(span, t), complete: () => this.set(this.r.n), noRewrite: b.kind === 'rec' };
    this.r = R[st.type] ? R[st.type](st, ctx) : R.opaque({ cols: [], rows: [], message: 'Unsupported stage' }, ctx);
    const ins = (st.insights || []).filter(() => st.type !== 'window');
    this.body.replaceChildren(...[this.r.el, ins.length ? h('div', { class: 'ins-list' }, ins.map(([k, t]) => h('div', { class: 'ins ' + k }, t))) : null].filter(Boolean));
    this.body.classList.remove('fade-in'); void this.body.offsetWidth; this.body.classList.add('fade-in');
    this.head.replaceChildren(...[h('span', { class: 'stage-title', title: st.title }, st.title),
      st.rowsIn != null && st.type !== 'result' ? h('span', { class: 'stage-sub' }, `${st.rowsIn} → ${st.rowsOut} rows`) : null,
      ctx.N ? h('span', { class: 'stage-sub hint' }, 'click a row to follow it') : null].filter(Boolean));
    this.rail.querySelectorAll('.chip').forEach(ch => ch.classList.toggle('active', +ch.dataset.i === i));
    const act = this.rail.querySelector('.chip.active'); if (act) act.scrollIntoView({ block: 'nearest', inline: 'nearest' });
    this.onSpan(st.span);
    this.set(mode === 'start' ? 0 : this.r.n);
    this.applyLineage();
  }
  set(k) {
    if (!this.r) return;
    this.k = Math.max(0, Math.min(this.r.n, k));
    this.narr.textContent = this.r.apply(this.k) || '';
    this.stepn.textContent = this.r.n ? `${this.k} / ${this.r.n}` : '';
    this.prevBtn.disabled = this.i === 0 && this.k === 0;
    this.nextBtn.disabled = this.i === this.flat.length - 1 && this.k >= this.r.n;
  }
  step(d) {
    this.pause();
    if (!this.r) return;
    if (d > 0) { if (this.k < this.r.n) this.set(this.k + 1); else if (this.i < this.flat.length - 1) this.mount(this.i + 1, 'start'); }
    else { if (this.k > 0) this.set(this.k - 1); else if (this.i > 0) { this.mount(this.i - 1, 'end'); this.set(Math.max(0, this.r.n - 1)); } }
  }
  toggle() { this.timer ? this.pause() : this.play(); }
  play(keep) {
    if (!this.flat.length) return;
    clearInterval(this.timer);
    if (!keep && this.k >= this.r.n && this.i === this.flat.length - 1) this.mount(0, 'start');
    else if (!keep && this.k >= this.r.n) this.mount(this.i + 1, 'start');
    this.playBtn.textContent = '❚❚ Pause';
    const tick = () => {
      if (this.k < this.r.n) this.set(this.k + 1);
      else if (this.i < this.flat.length - 1) this.mount(this.i + 1, 'start');
      else { this.pause(); return; }
      if (this.r.n === 0) { /* static stage: hold one beat */ }
    };
    const base = 650 * parseFloat(this.speed.value);
    this.timer = setInterval(tick, Math.max(120, Math.min(base, 1200)));
  }
  pause() { clearInterval(this.timer); this.timer = null; this.playBtn.textContent = '▶ Play'; }
  playFromStart() { if (!this.flat.length) return; this.pause(); this.mount(0, 'start'); this.play(true); }
}
