// EXPLAIN QUERY PLAN as an indented tree with plain-English glosses.
import { h } from './util.js';

const RULES = [
  [/^SEARCH .* USING INTEGER PRIMARY KEY/, 'jumps straight to the row by its primary key'],
  [/^SEARCH .* USING (AUTOMATIC )?(COVERING )?INDEX/, 'looks rows up through an index instead of scanning'],
  [/AUTOMATIC (COVERING )?INDEX/, 'SQLite builds a temporary index just for this query; a real index on that column would help'],
  [/^SCAN CONSTANT ROW/, 'a single computed row, no table involved'],
  [/^SCAN .* USING (COVERING )?INDEX/, 'reads every entry of an index'],
  [/^SCAN /, 'reads every row of the table (a full scan: fine at this size)'],
  [/USE TEMP B-TREE FOR (RIGHT PART OF )?ORDER BY/, 'no index gives this order, so SQLite sorts the rows'],
  [/USE TEMP B-TREE FOR GROUP BY/, 'sorts rows so equal group keys sit next to each other'],
  [/USE TEMP B-TREE FOR DISTINCT/, 'a temporary index drops duplicate rows'],
  [/^CORRELATED (SCALAR|LIST) SUBQUERY/, 'sub-query is re-run for every outer row'],
  [/^SCALAR SUBQUERY/, 'sub-query is computed once'],
  [/^LIST SUBQUERY/, 'the IN (...) list is computed once'],
  [/^CO-ROUTINE/, 'the sub-query streams its rows on demand (not stored)'],
  [/^MATERIALIZE/, 'the sub-query is computed once and its rows stored'],
  [/^COMPOUND QUERY/, 'the parts of a UNION / INTERSECT / EXCEPT'],
  [/^(LEFT-MOST SUBQUERY|UNION ALL|UNION|EXCEPT|INTERSECT)/, 'one side of the set operation'],
  [/^MERGE /, 'merges the two sorted parts'],
  [/^SETUP/, 'the starting (anchor) part of the recursion'],
  [/^RECURSIVE STEP/, 'the recursive part, repeated until it adds no new rows'],
  [/^BLOOM FILTER/, 'a quick pre-check that skips rows that cannot match'],
];
export const gloss = d => { for (const [re, t] of RULES) if (re.test(d)) return t; return null; };

export function renderPlan(plan) {
  if (!plan || !plan.length) return h('div', { class: 'empty' }, 'No plan available for this query.');
  const depth = new Map(), byId = new Map(plan.map(p => [p.id, p]));
  const dep = p => { if (depth.has(p.id)) return depth.get(p.id); const par = byId.get(p.parent); const d = par ? dep(par) + 1 : 0; depth.set(p.id, d); return d; };
  return h('div', {},
    h('div', { class: 'narr', style: { padding: '0 0 8px' } }, 'The plan is how SQLite physically runs the query. The steps view shows what the query means; the engine is free to reorder work, use indexes, and stream rows.'),
    h('div', { class: 'plan' }, plan.map(p => {
      const g = gloss(p.detail);
      return h('div', { class: 'plan-row', style: { paddingLeft: dep(p) * 18 + 'px' } }, h('code', {}, p.detail), g ? h('span', { class: 'plan-g' }, g) : null);
    })),
    h('div', { class: 'muted', style: { marginTop: '10px', fontSize: '11.5px' } }, 'These practice tables have no extra indexes, so scans are expected.'));
}
