// Usage: node tests/test_lineage.mjs traces.json   (traces.json: {name: {sql, blocks}} produced by test_lineage.py)
import fs from 'fs'; import path from 'path'; import { fileURLToPath } from 'url';
const here = path.dirname(fileURLToPath(import.meta.url));
const code = fs.readFileSync(path.join(here, '../static/js/lineage.js'), 'utf8');
const L = await import('data:text/javascript;base64,' + Buffer.from(code).toString('base64'));
const traces = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const bad = []; let blocks = 0, built = 0, nodes = 0, valueChecks = 0;
const ALLOWED_FAIL = new Set(['setop', 'recursive_iter', 'opaque', 'select']);
const parse = id => { const [si, role, idx] = id.split(':'); return { si: +si, role, idx: +idx }; };

for (const [name, tr] of Object.entries(traces)) {
  tr.blocks.forEach((b, bi) => {
    blocks++;
    const G = L.buildGraph(b);
    if (!G.ok) { if (!ALLOWED_FAIL.has(G.why)) bad.push(`${name}/${b.title}: graph failed (${G.why})`); return; }
    built++;
    const last = b.stages.length - 1;
    // 1. nobody disappears silently: every node with no outgoing edge is a result row or has a recorded reason
    G.stages.forEach((N, si) => [...N.in, ...N.right, ...N.out].forEach(id => {
      if (id == null) return; nodes++;
      const noOut = !(G.fwd.get(id) || []).length;
      if (noOut && !G.drops.has(id) && !(parse(id).si === last && b.stages[last].type === 'result')) bad.push(`${name}/${b.title}: ${id} vanishes with no reason`);
    }));
    // 2. forward/backward duality
    for (const [a, outs] of G.fwd) for (const o of outs) if (!(G.rev.get(o) || []).includes(a)) bad.push(`${name}: edge ${a}->${o} not mirrored`);
    // 3. every result row has source ancestors
    const rs = b.stages[last];
    if (rs.type === 'result') G.stages[last].out.forEach(id => {
      const f = L.follow(G, id);
      const emptyGroup = b.stages.some(x => x.type === 'group' && x.groups.some(g => !g.members.length));   // aggregate over zero rows
      if (!emptyGroup && ![...f.bwd].some(n => { const p = parse(n); return p.role === 'S' || (p.role === 'R' && b.stages[p.si].type === 'join'); })) bad.push(`${name}/${b.title}: result ${id} has no source ancestor`);
    });
    // 4. value check: a plain column in SELECT must equal the value in the ancestor source row of that alias
    b.stages.forEach((s, si) => {
      if (s.type !== 'select' || !s.exprs) return;
      s.rows.forEach((row, o) => {
        const f = L.follow(G, `${si}:O:${o}`);
        const anc = [...f.bwd].map(n => ({ n, ...parse(n) })).filter(p => b.stages[p.si].type === 'source' || (b.stages[p.si].type === 'join' && p.role === 'R'));
        const dataOf = p => { const st = b.stages[p.si]; return st.type === 'source' ? { cols: st.cols, row: st.rows[p.idx] } : { cols: st.rightCols, row: st.right[p.idx] }; };
        s.exprs.forEach((ex, j) => {
          if (ex.kind !== 'col') return;
          const t = ex.text.replace(/["`]/g, '').trim(); const [al, cn] = t.includes('.') ? t.split('.') : [null, t];
          const cands = [];
          anc.forEach(p => { const d = dataOf(p); d.cols.forEach((c, ci) => { if (c.name.toLowerCase() === cn.toLowerCase() && (!al || c.alias.toLowerCase() === al.toLowerCase())) cands.push(d.row[ci]); }); });
          valueChecks++;
          if (!cands.length) { if (row[j] !== null) bad.push(`${name}: ${ex.text} output ${row[j]} but no ancestor column found`); }
          else if (!cands.some(v => v === row[j] || (typeof v === 'number' && Math.abs(v - row[j]) < 1e-9))) bad.push(`${name}: ${ex.text}=${JSON.stringify(row[j])} not among ancestors ${JSON.stringify(cands)} (output row ${o})`);
        });
      });
    });
  });
}
// 5. the story for a known case: Legal vanishes at WHERE after a LEFT JOIN kept it
const trap = traces['trap'];
if (trap) {
  const b = trap.blocks[0], G = L.buildGraph(b);
  const si = b.stages.findIndex(s => s.type === 'source');
  const legal = b.stages[si].rows.findIndex(r => r[1] === 'Legal');
  const st = L.story(G, L.follow(G, `${si}:S:${legal}`), i => b.stages[i].type);
  if (!/where/i.test(st.fate) || !/NULL/.test(st.fate)) bad.push('trap story: ' + JSON.stringify(st));
  if (!st.beats.length) bad.push('trap story: missing "kept with NULLs" beat');
}
console.log(`${blocks} blocks, ${built} graphs built, ${nodes} nodes, ${valueChecks} value checks; problems: ${bad.length}`);
bad.slice(0, 20).forEach(x => console.log(' -', x));
process.exit(bad.length ? 1 : 0);
