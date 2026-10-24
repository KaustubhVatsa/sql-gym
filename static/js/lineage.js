// Row lineage: a graph of row identities across the stages of one block, built purely from the trace data.
// Node ids are `${stageIndex}:${role}:${rowIndex}`. Roles: S source row, R join right row, O output row (join/select/order),
// P row that passed WHERE, G group, H group that passed HAVING, D distinct output, L row kept by LIMIT, R (result stage) result row.

const SKIP = new Set(['setop', 'recursive_iter', 'opaque']);
const verdWord = v => (v === 1 ? 'TRUE' : v === 0 ? 'FALSE' : 'NULL');
const cap = (t, n) => (t.length > n ? t.slice(0, n - 1) + '…' : t);

export function buildGraph(block) {
  const st = block.stages;
  const G = { ok: true, why: '', stages: st.map(() => ({ in: [], out: [], right: [] })), fwd: new Map(), rev: new Map(), drops: new Map(), block };
  const edge = (a, b) => {
    if (a == null || b == null) return;
    if (!G.fwd.has(a)) G.fwd.set(a, []);
    if (!G.rev.has(b)) G.rev.set(b, []);
    G.fwd.get(a).push(b); G.rev.get(b).push(a);
  };
  const drop = (id, si, text) => { if (id != null) G.drops.set(id, { si, text }); };
  const fail = why => { G.ok = false; G.why = why; return G; };
  let carrier = null, gOrig = null;

  for (let si = 0; si < st.length; si++) {
    const s = st[si], N = G.stages[si];
    if (SKIP.has(s.type) || (s.type === 'select' && s.noFrom)) return fail(s.type);
    switch (s.type) {
      case 'source':
        N.out = s.rows.map((_, i) => `${si}:S:${i}`); carrier = N.out; break;
      case 'join': {
        if (!carrier || carrier.length !== s.left.length) return fail('join input');
        N.in = carrier; N.right = s.right.map((_, i) => `${si}:R:${i}`); N.out = s.out.map((_, i) => `${si}:O:${i}`);
        s.out.forEach((_, oi) => { edge(N.in[s.outLi[oi]], N.out[oi]); edge(N.right[s.outRi[oi]], N.out[oi]); });
        const kl = s.joinType === 'LEFT' || s.joinType === 'FULL', kr = s.joinType === 'RIGHT' || s.joinType === 'FULL';
        s.leftUnmatched.forEach(li => { if (!kl) drop(N.in[li], si, `matched nothing in ${s.rightAlias}, so the ${s.joinType} JOIN drops it`); });
        s.rightUnmatched.forEach(ri => { if (!kr) drop(N.right[ri], si, `matched no row on the left, so the ${s.joinType} JOIN drops it`); });
        carrier = N.out; break;
      }
      case 'where': {
        if (!carrier || carrier.length !== s.rows.length) return fail('where input');
        N.in = carrier; const out = [];
        s.keep.forEach((k, i) => {
          if (k) { const id = `${si}:P:${out.length}`; out.push(id); edge(carrier[i], id); }
          else drop(carrier[i], si, `WHERE → ${verdWord(s.wholeV ? s.wholeV[i] : s.verdicts[i][s.verdicts[i].length - 1])} (${cap(s.whole, 48)})`);
        });
        N.out = out; carrier = out; break;
      }
      case 'group': {
        if (!carrier || carrier.length !== s.rows.length) return fail('group input');
        N.in = carrier; N.out = s.groups.map((_, g) => `${si}:G:${g}`);
        s.groups.forEach((g, gi) => g.members.forEach(m => edge(carrier[m], N.out[gi])));
        carrier = N.out; gOrig = N.out.slice(); break;
      }
      case 'having': {
        if (!carrier || carrier.length !== s.groups.length) return fail('having input');
        N.in = carrier; const out = [], g2 = new Array(s.groups.length).fill(null);
        s.keep.forEach((k, g) => {
          if (k) { const id = `${si}:H:${out.length}`; out.push(id); edge(carrier[g], id); g2[g] = id; }
          else drop(carrier[g], si, `HAVING → ${verdWord(s.verdicts[g])}`);
        });
        N.out = out; carrier = out; gOrig = g2; break;
      }
      case 'window':
        if (!carrier || carrier.length !== s.rows.length) return fail('window input');
        N.in = carrier; N.out = carrier; break;
      case 'select': {
        N.out = s.rows.map((_, o) => `${si}:O:${o}`);
        if (s.agg) { if (!gOrig) return fail('agg select'); N.in = gOrig; }
        else { if (!carrier || !s.inRows || carrier.length !== s.inRows.length) return fail('select input'); N.in = carrier; }
        s.src.forEach((p, o) => edge(N.in[p], N.out[o]));
        carrier = N.out; break;
      }
      case 'distinct': {
        if (!carrier || carrier.length !== s.rows.length) return fail('distinct input');
        N.in = carrier; N.out = s.out.map((_, j) => `${si}:D:${j}`);
        const used = new Set();
        s.from.forEach((b, j) => { if (b != null) { edge(carrier[b], N.out[j]); used.add(b); } });
        carrier.forEach((id, i) => { if (!used.has(i)) drop(id, si, 'a duplicate of an earlier output row, so DISTINCT removes it'); });
        carrier = N.out; break;
      }
      case 'order': {
        if (!carrier || carrier.length !== s.before.length) return fail('order input');
        N.in = carrier; N.out = s.rows.map((_, a) => `${si}:O:${a}`);
        s.from.forEach((b, a) => { if (b != null) edge(carrier[b], N.out[a]); });
        carrier = N.out; break;
      }
      case 'limit': {
        if (!carrier || carrier.length !== s.rows.length) return fail('limit input');
        N.in = carrier; N.out = s.out.map((_, k) => `${si}:L:${k}`);
        s.out.forEach((_, k) => edge(carrier[s.offset + k], N.out[k]));
        carrier.forEach((id, i) => { if (i < s.offset || i >= s.offset + s.out.length) drop(id, si, 'cut by LIMIT / OFFSET'); });
        carrier = N.out; break;
      }
      case 'result': {
        if (!carrier) return fail('opaque');                      // e.g. a recursion's final result block
        if (carrier.length !== s.rows.length) return fail('result input');
        N.in = carrier; N.out = s.rows.map((_, i) => `${si}:R:${i}`);
        carrier.forEach((id, i) => edge(id, N.out[i]));
        break;
      }
      default: return fail(s.type);
    }
  }
  return G;
}

export function closure(map, start) {
  const seen = new Set([start]), q = [start];
  while (q.length) for (const y of map.get(q.pop()) || []) if (!seen.has(y)) { seen.add(y); q.push(y); }
  return seen;
}

export function follow(G, id) {
  const fwd = closure(G.fwd, id), bwd = closure(G.rev, id);
  return { id, fwd, bwd, set: new Set([...fwd, ...bwd]) };
}

const parse = id => { const [si, role, idx] = id.split(':'); return { si: +si, role, idx: +idx }; };

/** short readable label for a node: "alias #3 · v1, v2, v3" */
export function labelOf(G, id) {
  const { si, role, idx } = parse(id);
  const s = G.block.stages[si];
  const vals = r => (r || []).slice(0, 3).map(v => (v === null || v === undefined ? 'NULL' : String(v))).join(', ');
  const kept = (arr, keep) => arr.filter((_, i) => keep[i])[idx];
  let name = s.type, row = null;
  switch (s.type) {
    case 'source': name = s.alias; row = s.rows[idx]; break;
    case 'join': if (role === 'R') { name = s.rightAlias; row = s.right[idx]; } else { name = 'join row'; row = s.out[idx]; } break;
    case 'where': name = 'row'; row = kept(s.rows, s.keep); break;
    case 'group': name = 'group'; row = (s.groups[idx] || {}).key; break;
    case 'having': name = 'group'; row = (s.groups.filter((_, g) => s.keep[g])[idx] || {}).key; break;
    case 'select': name = 'output row'; row = s.rows[idx]; break;
    case 'distinct': name = 'output row'; row = s.out[idx]; break;
    case 'order': name = 'output row'; row = s.rows[idx]; break;
    case 'limit': name = 'output row'; row = s.out[idx]; break;
    case 'result': name = 'result row'; row = s.rows[idx]; break;
    default: break;
  }
  return `${name} #${idx + 1}${row ? ' · ' + vals(row) : ''}`;
}

/** the story of one followed row: where it came from, notable beats, and its fate */
export function story(G, lin, stageLabel) {
  const { id, fwd, bwd } = lin;
  const origin = [...bwd].filter(n => n !== id).filter(n => { const p = parse(n); return p.role === 'S' || (p.role === 'R' && G.block.stages[p.si].type === 'join'); })
    .sort((a, b) => parse(a).si - parse(b).si).map(n => labelOf(G, n).split(' · ')[0]);
  const resultStage = G.block.stages.length - 1;
  const reached = [...fwd].filter(n => { const p = parse(n); return p.si === resultStage && G.block.stages[p.si].type === 'result'; });
  const beats = [];
  const from = new Set([...fwd]);
  G.block.stages.forEach((s, si) => {
    if (s.type !== 'join') return;
    G.stages[si].in.forEach((nid, li) => {
      if (!from.has(nid)) return;
      const outs = (G.fwd.get(nid) || []);
      if (!outs.length) return;
      const nulls = outs.filter(o => s.outRi[parse(o).idx] == null).length;
      if (nulls && nulls === outs.length) beats.push(`${stageLabel(si)} found no match, kept with NULLs`);
    });
  });
  const drops = [...fwd].filter(n => G.drops.has(n)).map(n => G.drops.get(n)).sort((a, b) => a.si - b.si);
  let fate;
  if (reached.length) fate = `reaches the result (${reached.length} row${reached.length > 1 ? 's' : ''})` + (drops.length ? `; ${drops.length} branch${drops.length > 1 ? 'es were' : ' was'} dropped at ${stageLabel(drops[0].si)}` : '');
  else if (drops.length) fate = `ends at ${stageLabel(drops[0].si)}: ${drops[0].text}`;
  else fate = 'ends here';
  return { origin: [...new Set(origin)], beats: [...new Set(beats)], fate, dropSi: drops.length ? drops[0].si : null };
}
