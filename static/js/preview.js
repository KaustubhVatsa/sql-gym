// Instant, client-side "what's in this query" view. Runs on every keystroke with no network:
// tables used by the query are separated at the top, every other table is shifted below.
import { h, table, Colors } from './util.js';

const STOP = new Set(('on using where group having order limit offset join left right full inner outer cross natural union intersect except window select set values returning').split(' '));
const unq = s => s.replace(/^["`\[]|["`\]]$/g, '');

export function parsePartial(text, schema) {
  const t = text.replace(/--[^\n]*|\/\*[\s\S]*?(\*\/|$)/g, ' ').replace(/'(?:[^']|'')*'?/g, "''");
  const KW = 'on|using|where|group|having|order|limit|offset|join|left|right|full|inner|outer|cross|natural|union|intersect|except|window';
  const re = new RegExp(String.raw`\b(from|(?:natural\s+)?(?:(?:left|right|full|inner|cross)(?:\s+outer)?\s+)?join|,)\s*("?[A-Za-z_]\w*"?)?(?:\s+(?:as\s+)?(?!(?:${KW})\b)("?[A-Za-z_]\w*"?))?`, 'gi');
  const byName = new Map(schema.map(s => [s.name.toLowerCase(), s]));
  const ctes = new Set([...t.matchAll(/\b([A-Za-z_]\w*)\s*(?:\([^)]*\))?\s+as\s*\(/gi)].map(x => x[1].toLowerCase()));
  const items = []; let m, pendingJoin = null;
  while ((m = re.exec(t))) {
    const kind = m[1].toLowerCase().replace(/\s+/g, ' ');
    if (kind === ',' && !items.length) continue;
    const name = m[2] ? unq(m[2]) : null;
    if (!name || STOP.has(name.toLowerCase())) { if (kind === 'from' || kind.endsWith('join')) pendingJoin = kind; continue; }
    const tab = byName.get(name.toLowerCase());
    if (!tab) { if (!ctes.has(name.toLowerCase()) && /^from|join/.test(kind)) items.push({ kind, name, unknown: true, alias: name }); continue; }
    const alias = m[3] && !STOP.has(unq(m[3]).toLowerCase()) ? unq(m[3]) : null;
    items.push({ kind, name: tab.name, alias: alias || tab.name, tab });
    pendingJoin = null;
  }
  return { items, pendingJoin };
}

/** shared column between `tab` and any used table (key columns first), or null */
export function relation(tab, used) {
  for (const u of used) {
    for (const c of tab.cols) {
      const o = u.tab.cols.find(x => x.name.toLowerCase() === c.name.toLowerCase());
      if (o && (c.pk || o.pk)) return { col: c.name, alias: u.alias, with: u.name };
    }
  }
  return null;
}

const miniTable = (tab, alias, colors, rows, opts = {}) =>
  table(tab.cols.map(c => ({ alias, name: c.name })), tab.data.rows.slice(0, rows), { colors, ...opts });

/**
 * Always shows the whole database: used tables on top, the rest below.
 * onPick(tab, relation|null) is called when an "other" table is clicked.
 */
export function renderPreview(text, schema, colors = new Colors(), { onPick } = {}) {
  const { items, pendingJoin } = parsePartial(text, schema);
  const used = items.filter(i => !i.unknown);
  const usedNames = new Set(used.map(i => i.name.toLowerCase()));
  const root = h('div', { class: 'pv fade-in' });

  if (items.length || pendingJoin) {
    const grid = h('div', { class: 'pv-grid' });
    items.forEach((it, i) => {
      if (i > 0) {
        const lbl = it.kind === ',' ? 'CROSS' : it.kind.replace(/\s*join$/, '').toUpperCase() || 'INNER';
        grid.append(h('span', { class: 'pv-link' }, `${lbl} ⋈`));
      }
      if (it.unknown) { grid.append(h('div', { class: 'pv-ghost' }, `“${it.name}” isn't a table`)); return; }
      const tb = miniTable(it.tab, it.alias, colors, 12);
      tb.el.classList.add('pv-used');
      grid.append(h('div', {},
        h('div', { class: 'pv-h' }, h('i', { class: 'dot-c', style: { '--ac': colors.of(it.alias) } }), it.name,
          it.alias !== it.name ? h('span', { class: 'muted' }, 'as ' + it.alias) : null, h('span', { class: 'tag' }, it.tab.rows + ' rows')), tb.el));
    });
    if (pendingJoin) {
      if (used.length && pendingJoin !== 'from') grid.append(h('span', { class: 'pv-link' }, pendingJoin.toUpperCase().replace(/^JOIN$/, 'INNER JOIN') + ' ⋈'));
      grid.append(h('div', { class: 'pv-ghost' }, pendingJoin === 'from' ? 'pick a table below…' : 'pick a table to join below…'));
    }
    root.append(h('div', { class: 'pv-sec-h' }, 'In your query', h('span', { class: 'tag' }, used.length)), grid);
  }

  const others = schema.filter(t => !usedNames.has(t.name.toLowerCase()))
    .map(t => ({ t, rel: used.length ? relation(t, used) : null }))
    .sort((a, b) => (b.rel ? 1 : 0) - (a.rel ? 1 : 0));
  if (others.length) {
    const pickable = !!onPick && !!pendingJoin;
    root.append(h('div', { class: 'pv-sec-h' }, used.length || pendingJoin ? 'Other tables' : 'Tables', h('span', { class: 'tag' }, others.length),
      pickable ? h('span', { class: 'muted' }, 'click one to use it') : null));
    root.append(h('div', { class: 'pv-others' }, others.map(({ t, rel }) => {
      const tb = miniTable(t, t.name, colors, 3);
      return h('div', { class: 'pv-card' + (rel ? ' related' : '') + (pickable ? ' pickable' : ''), onclick: pickable ? () => onPick(t, rel) : null },
        h('div', { class: 'pv-h' }, t.name, h('span', { class: 'tag' }, t.rows + ' rows'),
          rel ? h('span', { class: 'pv-rel', title: `shares ${rel.col} with ${rel.with}` }, '⋈ ' + rel.col) : null), tb.el);
    })));
  }
  return root;
}
