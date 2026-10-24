export const $ = (s, r = document) => r.querySelector(s);

export function h(tag, attrs, ...kids) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k === 'class') e.className = v;
    else if (k === 'style' && typeof v === 'object') Object.assign(e.style, v);
    else if (k.startsWith('on')) e.addEventListener(k.slice(2), v);
    else if (k === 'text') e.textContent = v;
    else e.setAttribute(k, v === true ? '' : v);
  }
  add(e, kids);
  return e;
}
function add(e, kids) {
  for (const k of kids) {
    if (k == null || k === false) continue;
    if (Array.isArray(k)) add(e, k);
    else e.append(k.nodeType ? k : document.createTextNode(String(k)));
  }
}

export const isNum = v => typeof v === 'number';
export function show(v) {           // display string for a value
  if (v === null || v === undefined) return 'NULL';
  if (typeof v === 'number') return Number.isInteger(v) ? String(v) : String(+v.toPrecision(10));
  return String(v);
}
export function cell(v) {
  const td = document.createElement('td');
  if (v === null || v === undefined) { td.className = 'null'; td.textContent = 'NULL'; }
  else {
    const s = show(v);
    td.textContent = s;
    if (isNum(v)) td.className = 'num';
    if (s.length > 28) td.title = s;
  }
  return td;
}

// alias -> colour slot, stable across a trace
export class Colors {
  constructor() { this.m = new Map(); }
  of(alias) {
    if (alias == null) return 'var(--faint)';
    const k = alias.toLowerCase();
    if (!this.m.has(k)) this.m.set(k, this.m.size % 6);
    return `var(--c${this.m.get(k)})`;
  }
}

/**
 * Build a data table.
 * cols: [{alias,name}|string]; rows: arrays; opts: {colors, show:[colIdx], idx, extra:[{head, cell(i)->Node}], refs:Set<colIdx>}
 */
export function table(cols, rows, opts = {}) {
  const idxs = opts.show || cols.map((_, i) => i);
  const multi = new Set(cols.map(c => c.alias).filter(Boolean)).size > 1;
  const thead = h('tr', {}, opts.idx ? h('th', { class: 'ix' }, '#') : null,
    idxs.map(i => {
      const c = cols[i];
      const nm = typeof c === 'string' ? c : c.name;
      const th = h('th', { title: typeof c === 'string' ? c : `${c.alias}.${c.name}` },
        multi && c.alias ? h('span', { class: 'al' }, c.alias + '.') : null, nm);
      if (typeof c !== 'string' && opts.colors && c.alias) th.style.setProperty('--ac', opts.colors.of(c.alias));
      if (opts.refs && opts.refs.has(i)) th.style.color = 'var(--accent)';
      return th;
    }),
    (opts.extra || []).map(x => h('th', { title: x.head }, x.label || x.head)));
  const trs = [], tds = [];
  const tbody = h('tbody');
  rows.forEach((r, ri) => {
    const tr = h('tr');
    if (opts.idx) tr.append(h('td', { class: 'ix' }, ri + 1));
    const row = [];
    idxs.forEach(i => {
      const td = cell(r[i]);
      if (opts.refs && opts.refs.has(i)) td.classList.add('ref');
      tr.append(td); row.push(td);
    });
    (opts.extra || []).forEach(x => tr.append(x.cell(ri)));
    tbody.append(tr); trs.push(tr); tds.push(row);
  });
  const el = h('table', { class: 'dt' }, h('thead', {}, thead), tbody);
  const wrap = h('div', { class: 'tw' + (opts.plain ? ' plain' : '') }, el);
  return { el: wrap, table: el, trs, tds };
}

// columns of `cols` that a piece of SQL text refers to
export function refCols(text, cols) {
  const out = new Set();
  const t = (text || '').replace(/'(?:[^']|'')*'/g, ' ');
  for (const tok of t.match(/[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)?/g) || []) {
    if (tok.includes('.')) {
      const [a, c] = tok.toLowerCase().split('.');
      cols.forEach((col, i) => { if (col.alias && col.alias.toLowerCase() === a && col.name.toLowerCase() === c) out.add(i); });
    } else {
      const c = tok.toLowerCase();
      cols.forEach((col, i) => { if ((col.name || col).toLowerCase() === c) out.add(i); });
    }
  }
  return out;
}

export function keepVisible(container, node) {
  if (!container || !node) return;
  const c = container.getBoundingClientRect(), n = node.getBoundingClientRect();
  if (n.top < c.top + 28) container.scrollTop -= (c.top + 28 - n.top);
  else if (n.bottom > c.bottom) container.scrollTop += (n.bottom - c.bottom + 6);
}

// multiset diff of two result sets: which rows of `cur` are new, which rows of `base` disappeared
const rkey = r => JSON.stringify(r.map(v => (typeof v === 'number' && !Number.isInteger(v) ? Math.round(v * 1e9) / 1e9 : v)));
export function diffRows(base, cur) {
  if (base.cols.length !== cur.cols.length) return { cols: true, added: new Set(), removed: [] };
  const pool = new Map();
  base.rows.forEach(r => { const k = rkey(r); pool.set(k, (pool.get(k) || []).concat([r])); });
  const added = new Set();
  cur.rows.forEach((r, i) => {
    const k = rkey(r), l = pool.get(k);
    if (l && l.length) l.pop(); else added.add(i);
  });
  const removed = [...pool.values()].flat();
  return { cols: false, added, removed };
}
export const sameResult = (a, b) => { const d = diffRows(a, b); return !d.cols && !d.added.size && !d.removed.length && a.rows.length === b.rows.length; };

export function debounce(fn, ms) {
  let t;
  const d = (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); };
  d.flush = (...a) => { clearTimeout(t); fn(...a); };
  d.cancel = () => clearTimeout(t);
  return d;
}
