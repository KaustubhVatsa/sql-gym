"""Trace a query through SQL's *logical* processing order and capture every intermediate table.

FROM/JOIN -> WHERE -> GROUP BY -> HAVING -> WINDOW -> SELECT -> DISTINCT -> ORDER BY -> LIMIT

Every stage is computed by running real SQLite queries (never by re-implementing SQL semantics),
with each source table wrapped as `(SELECT row_number() OVER () AS __rn, * FROM src)` so rows carry
an identity. That identity is what lets the UI show which row matched which, who got dropped, etc.
"""
import json
import re

from . import frames
from .db import DB, QueryError, clean, literal
from .sqlparse import (AGG_NAMES, ParseError, Sql, Unsupported, qident, unquote)

MAXR = 300          # max rows we are willing to animate in one table
MAX_BLOCKS = 40
MAX_ITER_BLOCKS = 12


class Degrade(Exception):
    """This phase can't be visualised; fall back to showing only the result."""


class Scope:
    """The CTE definitions visible at some point (kept as SQL text so any block can be re-run standalone)."""
    __slots__ = ("defs", "rec")

    def __init__(self, defs=(), rec=False):
        self.defs, self.rec = list(defs), rec

    def prefix(self):
        if not self.defs:
            return ""
        return ("WITH RECURSIVE " if self.rec else "WITH ") + ", ".join(self.defs) + " "

    def add(self, d, rec=False):
        return Scope(self.defs + [d], self.rec or rec)


def nkey(vals):
    return tuple((round(v, 9) if isinstance(v, float) else v) for v in vals)


def verdict_sql(expr):
    return f"(CASE WHEN ({expr}) THEN 1 WHEN ({expr}) IS NULL THEN NULL ELSE 0 END)"


def kw_regex(alias):
    return re.compile(r'(?<![\w.])' + re.escape(alias) + r'\.', re.I)


class Tracer:
    def __init__(self, db, text, strict=False):
        self.db, self.text, self.strict = db, text, strict
        self.sql = Sql(text)
        self.blocks, self.notes = [], []
        self.pks = {}

    def q(self, sql):
        return self.db.q(sql)

    # ---- orchestration ----------------------------------------------------------------------
    def run(self):
        stmt = self.sql.parse()
        self.trace_stmt(stmt, Scope(), "Main query", "main", 0)
        return self.blocks

    def add_block(self, title, kind, level, stages, cols, rows, span=None):
        if len(self.blocks) >= MAX_BLOCKS:
            raise Degrade("Too many sub-queries to visualise")
        self.blocks.append({"id": f"b{len(self.blocks)}", "title": title, "kind": kind, "level": level,
                            "stages": stages, "cols": cols, "rows": rows[:MAXR], "span": span})

    def trace_stmt(self, st, scope, title, kind, level):
        S = self.sql
        defs, rec = list(scope.defs), scope.rec or st.recursive
        for cte in st.ctes:
            self.trace_cte(cte, Scope(defs, rec), level)
            defs.append(S.txt(cte.lo, cte.hi))
        return self.trace_body(st.body, Scope(defs, rec), title, kind, level)

    def trace_cte(self, cte, scope, level):
        S = self.sql
        lo, hi = cte.body
        refs = any(S.toks[i].k in ("id", "qid") and unquote(S.toks[i].x).lower() == cte.name.lower()
                   for i in range(lo, hi))
        st = S.statement(lo, hi)
        if refs and len(st.body.parts) > 1 and not st.ctes:
            mark = len(self.blocks)
            try:
                self.trace_recursive(cte, st, scope, level)
                return
            except Degrade as e:
                del self.blocks[mark:]
                self.notes.append(f"Recursive CTE '{cte.name}': {e}")
                sc = scope.add(S.txt(cte.lo, cte.hi), True)
                cols, rows = self.q(sc.prefix() + f"SELECT * FROM {qident(cte.name)}")
                self.add_block(f"CTE · {cte.name}", "cte", level, [{
                    "type": "opaque", "phase": "result", "title": f"{cte.name} (recursive)", "message": f"Shown as a plain result: {e}.",
                    "cols": cols, "rows": rows[:MAXR], "rowsIn": len(rows), "rowsOut": len(rows), "insights": []}], cols, rows)
                return
        self.trace_stmt(st, scope, f"CTE · {cte.name}", "cte", level)

    # ---- body (compound / order / limit) ----------------------------------------------------
    def trace_body(self, body, scope, title, kind, level):
        S = self.sql
        if len(body.parts) == 1:
            ct = CoreTrace(self, body.parts[0], scope, body, level, title)
            stages, cols, rows = ct.run_safe()
            self.add_block(title, kind, level, stages, cols, rows, S.span(body.lo, body.hi))
            return cols, rows
        # compound: each part is its own block, then a combine block
        prefix = scope.prefix()
        part_res = []
        for i, core in enumerate(body.parts):
            ct = CoreTrace(self, core, scope, None, level + 1, f"{title} · part {i + 1}")
            stages, cols, rows = ct.run_safe()
            self.add_block(f"{title} · SELECT {i + 1}", "part", level + 1, stages, cols, rows,
                           S.span(core.lo, core.hi))
            part_res.append((cols, rows))
        stages = []
        acc_hi = body.parts[0].hi
        left = part_res[0]
        for i, (opname, tok_i) in enumerate(body.ops):
            acc_hi = body.parts[i + 1].hi
            ocols, orows = self.q(prefix + S.txt(body.lo, acc_hi))
            right = part_res[i + 1]
            lset = {nkey(r) for r in left[1]}
            rset = {nkey(r) for r in right[1]}
            origin = []
            for r in orows:
                k = nkey(r)
                origin.append(("L" if k in lset else "") + ("R" if k in rset else ""))
            stages.append({
                "type": "setop", "phase": "setop", "title": f"{opname}", "op": opname,
                "span": S.span(tok_i, tok_i + (2 if opname == "UNION ALL" else 1)),
                "left": {"cols": left[0], "rows": left[1][:MAXR]},
                "right": {"cols": right[0], "rows": right[1][:MAXR]},
                "out": {"cols": ocols, "rows": orows[:MAXR]}, "origin": origin[:MAXR],
                "rowsIn": len(left[1]) + len(right[1]), "rowsOut": len(orows),
                "insights": self.setop_insights(opname, left[1], right[1], orows)})
            left = (ocols, orows)
        cols, rows = left
        ct = CoreTrace(self, body.parts[0], scope, body, level, title)
        tail = ct.tail_stages(cols, rows, compound_hi=acc_hi)
        if tail:
            stages += tail
        else:
            stages.append({"type": "result", "phase": "result", "title": "Result", "cols": cols, "rows": rows[:MAXR],
                           "rowsIn": len(rows), "rowsOut": len(rows), "insights": []})
        cols, rows = stages[-1]["cols"], stages[-1]["rows"]
        self.add_block(title, kind, level, stages, cols, rows, S.span(body.lo, body.hi))
        return cols, rows

    def setop_insights(self, op, lrows, rrows, orows):
        out = []
        if op == "UNION":
            dup = len(lrows) + len(rrows) - len(orows)
            out.append(("info", f"UNION removes duplicate rows ({dup} removed). UNION ALL would keep all {len(lrows) + len(rrows)}."))
        elif op == "UNION ALL":
            out.append(("info", "UNION ALL just stacks the two results; no duplicate removal (and no sorting), so it is cheaper than UNION."))
        elif op == "INTERSECT":
            out.append(("info", "INTERSECT keeps only rows present in both results (duplicates removed)."))
        elif op == "EXCEPT":
            out.append(("info", "EXCEPT keeps rows from the first result that do not appear in the second (duplicates removed)."))
        return out

    # ---- recursive CTE ---------------------------------------------------------------------
    def trace_recursive(self, cte, st, scope, level):
        S = self.sql
        body = st.body
        ops = [o for o, _ in body.ops]
        if any(o not in ("UNION", "UNION ALL") for o in ops) or len(set(ops)) != 1:
            raise Degrade("only a UNION / UNION ALL recursive form is visualised")
        union_all = ops[0] == "UNION ALL"
        name_l = cte.name.lower()

        def refs(core):
            return any(S.toks[i].k in ("id", "qid") and unquote(S.toks[i].x).lower() == name_l
                       for i in range(core.lo, core.hi))
        anchors = [c for c in body.parts if not refs(c)]
        recs = [c for c in body.parts if refs(c)]
        if len(recs) != 1 or body.parts[-1] is not recs[0] or len(anchors) != len(body.parts) - 1:
            raise Degrade("expected 'anchor UNION [ALL] recursive-step'")
        limit_n = None
        if body.order or body.offset:
            raise Degrade("ORDER BY / OFFSET inside a recursive CTE is not visualised")
        if body.limit:
            try:
                limit_n = int(S.txt(*body.limit))
            except ValueError:
                raise Degrade("only a literal LIMIT inside a recursive CTE is visualised")
        rec_core = recs[0]
        own_def = S.txt(cte.lo, cte.hi)
        sc_full = scope.add(own_def, True)
        # --- anchor
        anchor_scope = scope
        anchor_txt = S.txt(body.parts[0].lo, anchors[-1].hi)
        acols, arows = self.q(scope.prefix() + anchor_txt)
        cols = cte.cols or acols
        if len(cols) != len(acols):
            raise Degrade("column list does not match the anchor")
        ct = CoreTrace(self, anchors[0], anchor_scope, None, level + 1, "anchor") if len(anchors) == 1 else None
        if ct is not None:
            a_stages, _, _ = ct.run_safe()
        else:
            a_stages = []
        a_stages = [s for s in a_stages if s["type"] != "result"]
        all_rows, seen = [], set()
        for r in arows:
            if union_all:
                all_rows.append(r)
            else:
                k = nkey(r)
                if k not in seen:
                    seen.add(k)
                    all_rows.append(r)
        if limit_n is not None:
            all_rows = all_rows[:limit_n]
        iters = [{"n": 0, "rows": list(all_rows)}]
        a_stages.append({"type": "recursive_iter", "phase": "recursive", "title": "Anchor row(s)",
                         "n": 0, "cols": cols, "rows": all_rows[:MAXR], "newCount": len(all_rows),
                         "total": len(all_rows), "unionAll": union_all, "span": S.span(anchors[0].lo, anchors[-1].hi),
                         "rowsIn": 0, "rowsOut": len(all_rows),
                         "insights": [("info", "The anchor query runs once and seeds the recursion. "
                                       "Its rows become the first 'working set'.")]})
        self.add_block(f"CTE · {cte.name} · anchor", "rec", level, a_stages, cols, all_rows, S.span(body.parts[0].lo, anchors[-1].hi))
        working, n = list(all_rows), 0
        cap_hit = False
        while working:
            n += 1
            if n > 200 or len(all_rows) > 2000:
                cap_hit = True
                break
            vals = ", ".join("(" + ", ".join(literal(v) for v in r) + ")" for r in working)
            colsql = ", ".join(qident(c) for c in cols)
            step_def = f"{qident(cte.name)}({colsql}) AS (VALUES {vals})"
            step_scope = scope.add(step_def, False)
            if n <= MAX_ITER_BLOCKS:
                ct = CoreTrace(self, rec_core, step_scope, None, level + 1, f"iteration {n}")
                st_stages, scols_, srows = ct.run_safe()
                st_stages = [s for s in st_stages if s["type"] != "result"]
                produced = srows
            else:
                st_stages = []
                _, produced = self.q(step_scope.prefix() + S.txt(rec_core.lo, rec_core.hi))
            new = []
            if union_all:
                new = list(produced)
            else:
                for r in produced:
                    k = nkey(r)
                    if k not in seen:
                        seen.add(k)
                        new.append(r)
            if limit_n is not None:
                new = new[:max(limit_n - len(all_rows), 0)]
            all_rows += new
            iters.append({"n": n, "rows": new})
            ins = []
            if limit_n is not None and len(all_rows) >= limit_n:
                ins.append(("info", f"The CTE's LIMIT {limit_n} is reached, so SQLite stops recursing here."))
            if not new:
                ins.append(("info", "The recursive step produced no new rows, so recursion stops here."))
            elif not union_all and len(produced) > len(new):
                ins.append(("info", f"{len(produced) - len(new)} row(s) were already seen; UNION discards them (this is what stops cycles)."))
            if n <= MAX_ITER_BLOCKS or not new:
                st_stages.append({"type": "recursive_iter", "phase": "recursive", "title": f"Iteration {n} adds {len(new)} row(s)",
                                  "n": n, "cols": cols, "rows": all_rows[:MAXR], "newCount": len(new), "total": len(all_rows),
                                  "iterStarts": [sum(len(i['rows']) for i in iters[:k]) for k in range(len(iters))],
                                  "unionAll": union_all, "span": S.span(rec_core.lo, rec_core.hi),
                                  "rowsIn": len(working), "rowsOut": len(new), "insights": ins})
                self.add_block(f"CTE · {cte.name} · iteration {n}", "rec", level, st_stages, cols, new,
                               S.span(rec_core.lo, rec_core.hi))
            working = new
            if limit_n is not None and len(all_rows) >= limit_n:
                break
        if limit_n is not None:                       # the LIMIT interaction must reproduce SQLite's real rows
            _, real = self.q(sc_full.prefix() + f"SELECT * FROM {qident(cte.name)}")
            if sorted(map(repr, map(nkey, real))) != sorted(map(repr, map(nkey, all_rows))):
                raise Degrade("the LIMIT changed the recursion in a way this view cannot reproduce")
        if cap_hit:
            self.notes.append(f"Recursion of '{cte.name}' was cut off after {n - 1} iterations for display.")
        iter_of = []
        for it in iters:
            iter_of += [it["n"]] * len(it["rows"])
        final_ins = [("info", f"Recursion finished after {len(iters) - 1} step(s); the CTE now holds {len(all_rows)} row(s). "
                      "Everything after the CTE sees this table like any other.")]
        self.add_block(f"CTE · {cte.name} · result", "rec", level, [{
            "type": "result", "phase": "result", "title": f"{cte.name} (all iterations)", "cols": cols,
            "rows": all_rows[:MAXR], "iterOf": iter_of[:MAXR], "rowsIn": len(all_rows), "rowsOut": len(all_rows),
            "insights": final_ins}], cols, all_rows)


class CoreTrace:
    """Visualise one SELECT core (plus ORDER BY / LIMIT when it is the whole body)."""

    def __init__(self, tr, core, scope, body, level, title):
        self.tr, self.core, self.scope, self.body, self.level, self.title = tr, core, scope, body, level, title
        self.S = tr.sql
        self.prefix = scope.prefix()

    # ---- safety net -----------------------------------------------------------------------
    def run_safe(self):
        try:
            return self.run()
        except Exception as e:
            if self.tr.strict:
                raise
            if isinstance(e, Degrade):
                msg = str(e)
            elif isinstance(e, QueryError):
                msg = f"couldn't break this step down ({e})"
            else:
                msg = "couldn't break this step down"
            return self.fallback(msg)

    def plain_text(self):
        S, core, body = self.S, self.core, self.body
        t = S.txt(core.lo, core.hi)
        if body is not None:
            if body.order:
                t += " ORDER BY " + S.txt(*body.order)
            if body.limit:
                t += " LIMIT " + S.txt(*body.limit)
                if body.offset:
                    t += " OFFSET " + S.txt(*body.offset)
        return t

    def fallback(self, msg):
        cols, rows = self.tr.q(self.prefix + self.plain_text())
        return [{"type": "opaque", "phase": "result", "title": "Result", "message": msg, "cols": cols,
                 "rows": rows[:MAXR], "rowsIn": len(rows), "rowsOut": len(rows), "insights": [],
                 "span": self.S.span(self.core.lo, self.core.hi)}], cols, rows

    # ---- sub-queries ------------------------------------------------------------------------
    def sub_blocks(self):
        S, core = self.S, self.core
        if core.from_:
            for src in [core.from_.first] + [j.source for j in core.from_.joins]:
                if src.kind == "subquery":
                    st = S.statement(*src.sub)
                    self.tr.trace_stmt(st, self.scope, f"Subquery · {src.alias}", "subquery", self.level + 1)
        places = []
        for it in core.items:
            places.append(("SELECT", it.lo, it.hi))
        if core.where:
            places.append(("WHERE", *core.where))
        if core.having:
            places.append(("HAVING", *core.having))
        if core.from_:
            for j in core.from_.joins:
                if j.on:
                    places.append(("JOIN ON", *j.on))
        self.correlated = {}
        self.sub_warns = []
        self.subs = []
        for label, lo, hi in places:
            for (a, b) in S.subqueries(lo, hi):
                if S.kw(a - 2, "EXISTS"):
                    kind = "NOT EXISTS" if S.kw(a - 3, "NOT") else "EXISTS"
                elif S.kw(a - 2, "IN"):
                    kind = "NOT IN" if S.kw(a - 3, "NOT") else "IN"
                else:
                    kind = "SCALAR"
                info = {"label": label, "a": a, "b": b, "kind": kind, "text": S.txt(a, b), "correlated": False}
                if len(self.subs) < 4:
                    self.subs.append(info)
                try:
                    scols_, srows_ = self.tr.q(self.prefix + S.txt(a, b))
                    info["once"] = {"cols": scols_, "n": len(srows_), "rows": [list(r) for r in srows_[:20]]}
                    if label in ("WHERE", "HAVING") and S.kw(a - 2, "IN") and S.kw(a - 3, "NOT"):
                        nulls = sum(1 for r in srows_ if r and r[0] is None)
                        if nulls:
                            self.sub_warns.append(("warn", f"NOT IN (sub-query): the list contains {nulls} NULL value(s), so `x NOT IN (...)` can never be TRUE "
                                                           "(it is FALSE or NULL) and every row is filtered out. Use NOT EXISTS, or exclude NULLs inside the sub-query."))
                except QueryError:
                    info["correlated"] = True
                    self.correlated[label] = self.correlated.get(label, 0) + 1
                    continue
                st = S.statement(a, b)
                self.tr.trace_stmt(st, self.scope, f"Subquery in {label} (runs once)", "subquery", self.level + 1)

    def correlated_rows(self, sub):
        """For each chain row: how many rows the correlated sub-query returns for it, and (up to 8 of) those rows."""
        S, n = self.S, self.n
        st = S.statement(sub["a"], sub["b"])
        body, text = st.body, sub["text"]
        rows_sql, cols = "NULL", None
        if not st.ctes and len(body.parts) == 1 and not body.parts[0].opaque and body.parts[0].from_ and all(not it.star for it in body.parts[0].items):
            core = body.parts[0]
            exprs = [S.txt(it.lo, it.expr_hi) for it in core.items]
            cols = [it.alias or e for it, e in zip(core.items, exprs)]
            inner = f"SELECT {'DISTINCT ' if core.distinct else ''}{', '.join(f'{e} AS __c{i}' for i, e in enumerate(exprs))} {S.txt(core.kw['FROM'], body.hi)}"
            arr = ", ".join(f"__c{i}" for i in range(len(exprs)))
            rows_sql = f"(SELECT json_group_array(json_array({arr})) FROM (SELECT * FROM ({inner}) LIMIT 8))"
        sql = (f"{self.prefix}SELECT {', '.join(self.tag_cols(n))}, (SELECT COUNT(*) FROM ({text})), {rows_sql} "
               f"FROM {self.chain(n)} ORDER BY {self.order_by_tags(n)}")
        _, rws = self.tr.q(sql)
        nt = n + 1
        per = []
        for r in rws:
            js = json.loads(r[nt + 1]) if cols and r[nt + 1] else []
            per.append({"n": r[nt], "rows": js})
        return cols, per

    def sub_panel(self, label, index_map=None):
        """Sub-query info for the WHERE / SELECT stage: shared result (uncorrelated) or per-row results (correlated)."""
        out = []
        for sub in self.subs:
            if sub["label"] != label:
                continue
            e = {"kind": sub["kind"], "text": sub["text"], "correlated": sub["correlated"]}
            if sub["correlated"]:
                try:
                    cols, per = self.correlated_rows(sub)
                except (QueryError, ValueError, KeyError):
                    continue
                e["cols"] = cols
                e["per"] = per if index_map is None else [per[i] for i in index_map]
            else:
                e["once"] = sub.get("once")
            out.append(e)
        return out

    # ---- the pipeline ---------------------------------------------------------------------
    def run(self):
        S, core, tr = self.S, self.core, self.tr
        if core.opaque:
            return self.fallback("VALUES lists are shown as a plain result")
        self.sub_blocks()
        if core.from_ is None:
            return self.no_from()
        self.setup()
        stages = self.from_stages()
        stages += self.where_stage()
        self.has_agg = bool(core.group) or bool(self.agg_in_all())
        if self.has_agg:
            stages += self.group_stages()
            if self.win_calls():
                stages += self.window_stages(grouped=True)
        else:
            self.passed_idx = [i for i, k in enumerate(self.keep) if k]
            if self.win_calls():
                stages += self.window_stages()
        stages += self.select_stages()
        cols, rows = self.final
        stages.append({"type": "result", "phase": "result", "title": "Result", "cols": cols, "rows": rows[:MAXR],
                       "rowsIn": len(rows), "rowsOut": len(rows), "insights": self.result_insights(rows)})
        return stages, cols, rows

    def no_from(self):
        S, core = self.S, self.core
        items = ", ".join(S.txt(i.lo, i.hi) for i in core.items)
        cols, rows = self.tr.q(f"{self.prefix}SELECT {'DISTINCT ' if core.distinct else ''}{items}")
        stages = [{"type": "select", "phase": "select", "title": "SELECT (no FROM)", "noFrom": True,
                   "exprs": [{"text": S.txt(i.lo, i.expr_hi), "kind": "expr"} for i in core.items],
                   "cols": cols, "rows": rows[:MAXR], "span": S.span(core.lo, core.items[-1].hi),
                   "rowsIn": 0, "rowsOut": len(rows),
                   "insights": [("info", "No FROM clause: SELECT evaluates its expressions once and returns a single row.")]}]
        body_stages = self.tail_stages(cols, rows)
        if body_stages:
            stages += body_stages[:-1] if body_stages[-1]["type"] == "result" else body_stages
            cols, rows = body_stages[-1]["cols"], body_stages[-1]["rows"]
        stages.append({"type": "result", "phase": "result", "title": "Result", "cols": cols, "rows": rows[:MAXR],
                       "rowsIn": len(rows), "rowsOut": len(rows), "insights": []})
        return stages, cols, rows

    # ---- setup: sources, columns, SQL builders ------------------------------------------------
    def setup(self):
        S, core = self.S, self.core
        fc = core.from_
        self.srcs = [fc.first] + [j.source for j in fc.joins]
        self.joins = [None] + list(fc.joins)
        self.aliases = [s.alias for s in self.srcs]
        if len({a.lower() for a in self.aliases}) != len(self.aliases):
            raise Degrade("duplicate table aliases")
        self.scols = []
        for s in self.srcs:
            cols, _ = self.tr.q(f"{self.prefix}SELECT * FROM {s.inner_sql} LIMIT 0")
            self.scols.append(cols)
        # USING / NATURAL: columns hidden from an unqualified `*`
        self.hidden = [set() for _ in self.srcs]
        self.natural_using = {}
        for m, j in enumerate(self.joins):
            if m == 0:
                continue
            if j.natural:
                lc = {c.lower() for k in range(m) for c in self.scols[k]}
                common = [c for c in self.scols[m] if c.lower() in lc]
                self.natural_using[m] = common
                self.hidden[m] |= {c.lower() for c in common}
            elif j.using:
                self.hidden[m] |= {c.lower() for c in j.using}
        # RIGHT/FULL JOIN ... USING: SQLite's unqualified `*` shows the merged column as COALESCE(left, right)
        self.coalesce = {}
        for m, j in enumerate(self.joins):
            cols_u = self.natural_using.get(m) if m and j.natural else (j.using if m else None)
            if m and cols_u and j.kw in ("RIGHT", "FULL"):
                for c in cols_u:
                    left = next((a for a in range(m) if c.lower() in {x.lower() for x in self.scols[a]}), None)
                    if left is not None:
                        self.coalesce.setdefault((left, c.lower()), []).append(m)
        self.n = len(self.srcs) - 1
        self.base_cols = [{"alias": a, "name": c} for a, cols in zip(self.aliases, self.scols) for c in cols]
        # which aliases can be NULL-padded by an outer join
        self.nullable = set()
        for m, j in enumerate(self.joins):
            if m == 0:
                continue
            if j.kw in ("LEFT", "FULL"):
                self.nullable.add(self.aliases[m].lower())
            if j.kw in ("RIGHT", "FULL"):
                self.nullable |= {a.lower() for a in self.aliases[:m]}
        self.chain_cache = {}

    def tagged(self, s):
        return f"(SELECT row_number() OVER () AS __rn, * FROM {s.inner_sql}) AS {s.alias_sql}"

    def join_sql(self, m):
        j, s = self.joins[m], self.srcs[m]
        t = self.tagged(s)
        if j.comma:
            return f" CROSS JOIN {t}"
        if j.natural:
            common = self.natural_using.get(m)
            if not common:
                return f" CROSS JOIN {t}"
            kind = "" if j.kw in ("INNER", "CROSS") else j.kw + " "
            return f" {kind}JOIN {t} USING ({', '.join(qident(c) for c in common)})"
        head = " " + self.S.txt(j.kw_lo, j.kw_hi) + " "
        tail = ""
        if j.on:
            tail = " ON " + self.S.txt(*j.on)
        elif j.using:
            tail = " USING (" + ", ".join(qident(u) for u in j.using) + ")"
        return f"{head}{t}{tail}"

    def chain(self, k):
        s = self.tagged(self.srcs[0])
        for m in range(1, k + 1):
            s += self.join_sql(m)
        return s

    def tag_cols(self, k):
        return [f"{self.srcs[i].alias_sql}.__rn" for i in range(k + 1)]

    def data_cols(self, k):
        return [f"{self.srcs[i].alias_sql}.{qident(c)}" for i in range(k + 1) for c in self.scols[i]]

    def order_by_tags(self, k):
        return ", ".join(f"{t} IS NULL, {t}" for t in self.tag_cols(k))

    def chain_rows(self, k):
        if k in self.chain_cache:
            return self.chain_cache[k]
        tags, data = self.tag_cols(k), self.data_cols(k)
        sql = f"{self.prefix}SELECT {', '.join(tags + data)} FROM {self.chain(k)} ORDER BY {self.order_by_tags(k)}"
        _, rows = self.tr.q(sql)
        nt = k + 1
        res = ([tuple(r[:nt]) for r in rows], [tuple(r[nt:]) for r in rows])
        if len(rows) > MAXR:
            raise Degrade(f"more than {MAXR} rows - too many to animate step by step")
        self.chain_cache[k] = res
        return res

    def flat_cols(self, upto):
        return [c for c in self.base_cols[:sum(len(self.scols[i]) for i in range(upto + 1))]]

    def where_text(self):
        return " WHERE " + self.S.txt(*self.core.where) if self.core.where else ""

    def win_clause(self):
        c = self.core
        return " WINDOW " + self.S.txt(*c.windows) if c.windows else ""

    # ---- FROM / JOIN ----------------------------------------------------------------------
    def from_stages(self):
        S, core = self.S, self.core
        fc = core.from_
        stages = []
        tags0, rows0 = self.chain_rows(0)
        s0 = self.srcs[0]
        desc = {"table": f"table {s0.name}", "subquery": "the sub-query result", "func": "a table-valued function"}[s0.kind]
        stages.append({
            "type": "source", "phase": "from",
            "title": f"FROM {s0.name if s0.kind == 'table' else s0.alias}" + (f" {s0.alias}" if s0.kind == "table" and s0.explicit_alias else ""),
            "alias": s0.alias, "cols": self.flat_cols(0), "rows": rows0,
            "span": S.span(core.kw["FROM"], fc.first.hi),
            "rowsIn": len(rows0), "rowsOut": len(rows0),
            "insights": [("info", f"Every query starts here: read {desc}" + (f" (alias {s0.alias})" if s0.explicit_alias else "") +
                          f" - {len(rows0)} row(s). Nothing is filtered or computed yet.")] if self.n == 0 else
                         [("info", f"Start with {desc} as the left side; each JOIN below attaches one more table.")]})
        for k in range(1, self.n + 1):
            stages.append(self.join_stage(k))
        return stages

    def join_stage(self, k):
        S = self.S
        j, s = self.joins[k], self.srcs[k]
        ltags, lrows = self.chain_rows(k - 1)
        otags, orows = self.chain_rows(k)
        _, rrows_raw = self.tr.q(f"{self.prefix}SELECT * FROM {self.tagged(s)} ORDER BY __rn")
        if len(rrows_raw) > MAXR:
            raise Degrade(f"more than {MAXR} rows on the right side - too many to animate")
        rmap = {r[0]: i for i, r in enumerate(rrows_raw)}
        rrows = [tuple(r[1:]) for r in rrows_raw]
        lmap = {t: i for i, t in enumerate(ltags)}
        nl = sum(len(self.scols[i]) for i in range(k))
        out_li, out_ri = [], []
        for t, row in zip(otags, orows):
            lt = tuple(t[:k])
            li = lmap.get(lt)
            if li is None and any(x is not None for x in lt):
                raise Degrade("row identity mismatch while tracing the join")
            ri = rmap.get(t[k]) if t[k] is not None else None
            if t[k] is not None and ri is None:
                raise Degrade("row identity mismatch while tracing the join")
            if li is not None and tuple(row[:nl]) != tuple(lrows[li]):
                raise Degrade("left rows changed between runs")
            if ri is not None and tuple(row[nl:]) != tuple(rrows[ri]):
                raise Degrade("right rows changed between runs")
            out_li.append(li)
            out_ri.append(ri)
        pairs = sorted({(a, b) for a, b in zip(out_li, out_ri) if a is not None and b is not None})
        matched_l = {a for a, _ in pairs}
        matched_r = {b for _, b in pairs}
        lun = [i for i in range(len(lrows)) if i not in matched_l]
        run = [i for i in range(len(rrows)) if i not in matched_r]
        kind = j.kw
        label = ("CROSS" if j.comma else (("NATURAL " if j.natural else "") + (kind if kind != "INNER" else "INNER")))
        on_text = S.txt(*j.on) if j.on else None
        using = j.using or self.natural_using.get(k)
        hi = j.hi
        stage = {
            "type": "join", "phase": "join", "title": f"{label} JOIN {s.alias}", "joinType": kind, "comma": j.comma,
            "natural": j.natural, "on": on_text, "using": using, "index": k,
            "leftTitle": self.aliases[0] if k == 1 else "left side so far",
            "rightAlias": s.alias, "rightName": s.name or s.alias,
            "leftCols": self.flat_cols(k - 1), "rightCols": [{"alias": s.alias, "name": c} for c in self.scols[k]],
            "left": lrows, "right": rrows, "pairs": [list(p) for p in pairs],
            "out": orows, "outLi": out_li, "outRi": out_ri, "leftUnmatched": lun, "rightUnmatched": run,
            "span": S.span(j.kw_lo, hi), "kwSpan": None if (j.comma or j.natural) else S.span(j.kw_lo, j.kw_hi),
            "rowsIn": len(lrows), "rowsOut": len(orows),
        }
        stage["insights"] = self.join_insights(stage, lrows, rrows, k)
        return stage

    def join_insights(self, st, lrows, rrows, k):
        ins = []
        kind, nl, nr, no = st["joinType"], len(lrows), len(rrows), len(st["out"])
        lun, run = st["leftUnmatched"], st["rightUnmatched"]
        pairs = st["pairs"]
        from collections import Counter
        per_left = Counter(a for a, _ in pairs)
        if st["comma"] or (st["on"] is None and not st["using"]):
            ins.append(("info", f"No join condition: every left row pairs with every right row ({nl} x {nr} = {nl * nr} rows). "
                                "That is a CROSS JOIN (Cartesian product)."))
            return ins
        if kind == "INNER":
            ins.append(("info", "INNER JOIN keeps only rows that have a match on both sides."))
        elif kind == "LEFT":
            ins.append(("info", "LEFT JOIN keeps every left row; where nothing matches, right-side columns are filled with NULL."))
        elif kind == "RIGHT":
            ins.append(("info", "RIGHT JOIN keeps every right row; unmatched left-side columns become NULL."))
        elif kind == "FULL":
            ins.append(("info", "FULL JOIN keeps unmatched rows from both sides, NULL-padding the missing side."))
        if per_left and max(per_left.values()) > 1:
            big = max(per_left.values())
            ins.append(("warn", f"Fan-out: some left rows matched up to {big} right rows, so they appear {big} times in the output "
                                f"({nl} left rows became {no} rows). Aggregating afterwards would count/sum them {big}x - "
                                "a classic source of inflated totals."))
        if lun and kind in ("INNER", "RIGHT"):
            nulls = self.null_key_rows(st, lrows, lun)
            extra = (f" {nulls} of them have a NULL join key - NULL never equals anything, not even another NULL." if nulls else "")
            ins.append(("warn", f"{len(lun)} left row(s) had no match and were dropped by the {kind} JOIN.{extra}"))
        if lun and kind in ("LEFT", "FULL"):
            nulls = self.null_key_rows(st, lrows, lun)
            extra = (f" {nulls} have a NULL join key (NULL = anything is never true)." if nulls else "")
            ins.append(("info", f"{len(lun)} left row(s) had no match; they are kept with NULLs on the right.{extra}"))
        if run and kind in ("INNER", "LEFT"):
            ins.append(("info", f"{len(run)} right row(s) matched nothing and are not in the output."
                        + (" Use RIGHT/FULL JOIN to keep them." if kind == "INNER" else "")))
        if run and kind in ("RIGHT", "FULL"):
            ins.append(("info", f"{len(run)} right row(s) matched nothing; they are kept with NULLs on the left."))
        return ins

    def null_key_rows(self, st, lrows, idxs):
        if not st["on"] and not st["using"]:
            return 0
        refs = set()
        if st["on"]:
            for a, c in re.findall(r"([A-Za-z_]\w*)\.([A-Za-z_]\w*)", st["on"]):
                refs.add((a.lower(), c.lower()))
        else:
            for c in st["using"]:
                for col in st["leftCols"]:
                    if col["name"].lower() == c.lower():
                        refs.add((col["alias"].lower(), c.lower()))
        pos = [i for i, c in enumerate(st["leftCols"]) if (c["alias"].lower(), c["name"].lower()) in refs]
        return sum(1 for i in idxs if any(lrows[i][p] is None for p in pos))

    # ---- WHERE -----------------------------------------------------------------------------
    def where_stage(self):
        S, core = self.S, self.core
        tags, base = self.chain_rows(self.n)
        self.tags = tags
        self.base = base
        self.tag2idx = {t: i for i, t in enumerate(tags)}
        if not core.where:
            self.keep = [True] * len(base)
            self.verdicts = None
            return []
        w = core.where
        combine, conj = S.predicate_parts(*w)
        texts = [S.txt(a, b) for a, b in conj]
        whole = S.txt(*w)
        n = self.n
        verd_rows, kept_set = None, None
        sel = ", ".join(self.tag_cols(n) + [verdict_sql(t) for t in texts] + [verdict_sql(whole)])
        try:
            _, rows = self.tr.q(f"{self.prefix}SELECT {sel} FROM {self.chain(n)} ORDER BY {self.order_by_tags(n)}")
            nt, nc = n + 1, len(texts)
            if [tuple(r[:nt]) for r in rows] != tags:
                raise Degrade("row order changed")
            verd_rows = [list(r[nt:nt + nc]) for r in rows]
            whole_v = [r[nt + nc] for r in rows]
        except QueryError:
            # the predicate uses a SELECT-list alias (SQLite allows that in WHERE): aliases resolve only where the
            # items are in the select list, so classify rows with WHERE <p> (TRUE) and WHERE (<p>) IS NULL (unknown)
            alias_items = [S.txt(it.lo, it.hi) for it in core.items if it.alias and not it.star]
            tg = ", ".join(self.tag_cols(n)) + "".join(", " + a for a in alias_items)
            nt = n + 1
            _, kr = self.tr.q(f"{self.prefix}SELECT {tg} FROM {self.chain(n)} WHERE {whole}")
            _, nr = self.tr.q(f"{self.prefix}SELECT {tg} FROM {self.chain(n)} WHERE ({whole}) IS NULL")
            kept, unknown = {tuple(r[:nt]) for r in kr}, {tuple(r[:nt]) for r in nr}
            texts = [whole]
            whole_v = [1 if t in kept else (None if t in unknown else 0) for t in tags]
            verd_rows = [[v] for v in whole_v]
        self.keep = [v == 1 for v in whole_v]
        self.verdicts = verd_rows
        stage = {"type": "where", "phase": "where", "title": "WHERE", "cols": self.base_cols, "rows": base,
                 "preds": texts, "whole": whole, "verdicts": verd_rows, "keep": self.keep, "combine": combine if len(texts) > 1 else None,
                 "wholeV": whole_v,
                 "span": S.span(core.kw["WHERE"], w[1]), "rowsIn": len(base), "rowsOut": sum(self.keep),
                 "insights": self.where_insights(texts, verd_rows, whole_v)}
        subs = self.sub_panel("WHERE")
        if subs:
            stage["subs"] = subs
        return [stage]

    def where_insights(self, texts, verd, whole_v):
        ins = []
        nulls = sum(1 for v in whole_v if v is None)
        dropped = sum(1 for v in whole_v if v != 1)
        ins.append(("info", f"WHERE keeps a row only when its predicate is TRUE: {len(whole_v) - dropped} of {len(whole_v)} rows pass."))
        if nulls:
            ins.append(("warn", f"{nulls} row(s) were dropped because the predicate evaluated to NULL (unknown), not FALSE. "
                                "Comparisons with NULL (= , <>, <, >) are never true; use IS NULL / IS NOT NULL."))
        if self.correlated.get("WHERE"):
            ins.append(("info", "This WHERE contains a correlated sub-query: it is re-evaluated for every row."))
        ins += [w for w in getattr(self, "sub_warns", [])]
        whole_txt = " ".join(texts)
        sloppy = [d for d in re.findall(r"'(\d{4}-\d{1,2}-\d{1,2})'", whole_txt) if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", d)]
        if sloppy:
            ins.append(("warn", f"'{sloppy[0]}' is not a zero-padded ISO date. SQLite dates are TEXT compared character by character, "
                                "so '2024-1-15' sorts after '2024-02-02'. Use YYYY-MM-DD."))
        if re.search(r"\bLIKE\b", whole_txt, re.I):
            ins.append(("tip", "In SQLite, LIKE ignores case for ASCII letters ('a%' matches 'Asha'); GLOB is case-sensitive. % matches any run of characters, _ exactly one."))
        tags = self.tags
        for ci, t in enumerate(texts):
            for alias in self.nullable:
                if not kw_regex(alias).search(t):
                    continue
                idx = self.aliases_index(alias)
                if re.search(r'\bIS\s+NULL\b', t, re.I):
                    cnt = sum(1 for i, tg in enumerate(tags) if tg[idx] is None and verd[i][ci] == 1)
                    if cnt:
                        ins.append(("tip", f"Anti-join pattern: LEFT JOIN + WHERE {alias}.<key> IS NULL keeps exactly the {cnt} left row(s) "
                                           f"that found no partner in {alias}."))
                else:
                    cnt = sum(1 for i, tg in enumerate(tags) if tg[idx] is None and verd[i][ci] != 1)
                    if cnt:
                        ins.append(("warn", f"This predicate removed {cnt} row(s) that the outer JOIN had NULL-padded for '{alias}', so the "
                                            "outer JOIN effectively became an INNER JOIN. To keep unmatched rows, move the condition into the JOIN's ON clause."))
        return ins

    def aliases_index(self, alias_l):
        for i, a in enumerate(self.aliases):
            if a.lower() == alias_l:
                return i
        return 0

    # ---- expressions ----------------------------------------------------------------------
    def items_expanded(self):
        """-> [(sql_expr, display_text, kind)] aligned with output columns; plus ordinal map."""
        S, core = self.S, self.core
        out, ordmap = [], {}
        for idx, it in enumerate(core.items):
            ordmap[idx + 1] = len(out) + 1
            if it.star:
                ali = it.star_alias.lower() if it.star_alias else None
                hit = False
                for ai, a in enumerate(self.aliases):
                    if ali is not None and a.lower() != ali:
                        continue
                    hit = True
                    for c in self.scols[ai]:
                        if ali is None and c.lower() in self.hidden[ai]:
                            continue
                        sql_c = f"{self.srcs[ai].alias_sql}.{qident(c)}"
                        if ali is None and (ai, c.lower()) in self.coalesce:
                            others = [f"{self.srcs[m].alias_sql}.{qident(c)}" for m in self.coalesce[(ai, c.lower())]]
                            sql_c = f"COALESCE({sql_c}, {', '.join(others)})"
                        if sql_c.startswith("COALESCE("):
                            out.append((sql_c, sql_c, "expr"))
                        else:
                            out.append((sql_c, f"{a}.{c}", "col"))
                if not hit:
                    raise Degrade(f"unknown table alias '{it.star_alias}'")
                ordmap[idx + 1] = None
            else:
                txt = S.txt(it.lo, it.expr_hi)
                kind = "expr"
                if S.agg_calls(it.lo, it.expr_hi):
                    kind = "agg"
                if S.window_calls(it.lo, it.expr_hi):
                    kind = "window"
                elif kind == "expr" and re.fullmatch(r"(?:[A-Za-z_]\w*\.)?[A-Za-z_]\w*|\"[^\"]+\"(?:\.\"[^\"]+\")?", txt.strip()):
                    kind = "col"
                full = S.txt(it.lo, it.hi)
                out.append((full, txt, kind))
        return out, ordmap

    def agg_in_all(self):
        S, core = self.S, self.core
        calls = []
        for it in core.items:
            if not it.star:
                calls += S.agg_calls(it.lo, it.expr_hi)
        if core.having:
            calls += S.agg_calls(*core.having)
        if self.body is not None and self.body.order:
            calls += S.agg_calls(*self.body.order)
        return calls

    def win_calls(self):
        S, core = self.S, self.core
        out = []
        for it in core.items:
            if not it.star:
                out += S.window_calls(it.lo, it.expr_hi)
        return out

    def resolve_group_expr(self, a, b):
        S, core = self.S, self.core
        txt = S.txt(a, b)
        if b - a == 1 and S.toks[a].k == "num" and re.fullmatch(r"\d+", S.toks[a].x):
            n = int(S.toks[a].x)
            if 1 <= n <= len(core.items) and not core.items[n - 1].star:
                it = core.items[n - 1]
                return S.txt(it.lo, it.expr_hi)
            raise Degrade("GROUP BY position refers to a * item")
        if b - a == 1 and S.toks[a].k in ("id", "qid"):
            nm = unquote(S.toks[a].x).lower()
            is_col = any(nm == c.lower() for cols in self.scols for c in cols)
            if not is_col:
                for it in core.items:
                    if it.alias and it.alias.lower() == nm:
                        return S.txt(it.lo, it.expr_hi)
        return txt

    # ---- GROUP BY / HAVING ---------------------------------------------------------------
    def group_stages(self):
        S, core, tr = self.S, self.core, self.tr
        n = self.n
        gexprs = []
        if core.group:
            for a, b in S.split_commas(*core.group):
                gexprs.append(self.resolve_group_expr(a, b))
        has_window = bool(self.win_calls())
        calls, seen = [], {}
        ranges = [(it.lo, it.expr_hi) for it in core.items if not it.star]
        if core.having:
            ranges.append(core.having)
        if self.body is not None and self.body.order:
            ranges.append(self.body.order)
        for lo, hi in ranges:
            for c in S.agg_calls(lo, hi):
                txt = S.txt(c["lo"], c["hi"])
                key = re.sub(r"\s+", "", txt).lower()
                if key in seen:
                    continue
                seen[key] = len(calls)
                alo, ahi = c["args"]
                distinct = S.kw(alo, "DISTINCT")
                if distinct:
                    alo += 1
                star = ahi - alo == 1 and S.op(alo, "*")
                arg = None
                if ahi > alo and not star:
                    parts = S.split_commas(alo, ahi)
                    arg = S.txt(*parts[0])
                calls.append({"text": txt, "name": c["name"], "arg": arg, "star": star or (ahi == alo),
                              "distinct": distinct, "extra": len(S.split_commas(alo, ahi)) > 1 if ahi > alo else False})
        self.agg_list = calls
        wh = self.where_text()
        argexprs = [c["arg"] for c in calls if c["arg"]]
        # per-row group keys and aggregate arguments (rows that passed WHERE)
        sel = self.tag_cols(n) + [f"({g})" for g in gexprs] + [f"({a})" for a in argexprs]
        _, mrows = tr.q(f"{self.prefix}SELECT {', '.join(sel) or '1'} FROM {self.chain(n)}{wh} ORDER BY {self.order_by_tags(n)}")
        nt, ng = n + 1, len(gexprs)
        passed = [self.tag2idx[tuple(r[:nt])] for r in mrows]
        self.passed_idx = passed
        row_keys = [list(r[nt:nt + ng]) for r in mrows]
        row_args = [list(r[nt + ng:]) for r in mrows]
        gb = (" GROUP BY " + ", ".join(gexprs)) if gexprs else ""
        agg_sel = [f"({g})" for g in gexprs] + [c["text"] for c in calls]
        having_txt = S.txt(*core.having) if core.having else None
        hv_col = verdict_sql(having_txt) if having_txt else "NULL"
        having_fallback = False
        alias_sel = [S.txt(it.lo, it.hi) for it in core.items if it.alias and not it.star] if having_txt else []
        na = len(agg_sel)
        try:
            _, grows = tr.q(f"{self.prefix}SELECT {', '.join(agg_sel + [hv_col] + alias_sel)} FROM {self.chain(n)}{wh}{gb}")
            hv = [r[na] for r in grows]
            grows = [r[:na] for r in grows]
        except QueryError:
            if not having_txt:
                raise
            _, grows = tr.q(f"{self.prefix}SELECT {', '.join(agg_sel)} FROM {self.chain(n)}{wh}{gb}")
            _, kept = tr.q(f"{self.prefix}SELECT {', '.join(agg_sel + alias_sel)} FROM {self.chain(n)}{wh}{gb} HAVING {having_txt}")
            kset = {nkey(r[:na]) for r in kept}
            hv = [1 if nkey(r) in kset else 0 for r in grows]
        groups, gmap = [], {}
        for gi, r in enumerate(grows):
            key = nkey(r[:ng])
            gmap[key] = gi
            groups.append({"key": list(r[:ng]), "members": [], "aggs": list(r[ng:])})
        for pi, rk in enumerate(row_keys):
            gi = gmap.get(nkey(rk))
            if gi is None:
                raise Degrade("group membership mismatch")
            groups[gi]["members"].append(pi)
        self.groups = groups
        self.group_keys = gexprs
        self.gkeep = [v == 1 or having_txt is None for v in hv] if having_txt else [True] * len(groups)
        self.hv = hv
        input_rows = [self.base[i] for i in passed]
        implicit = not gexprs
        title = ("GROUP BY " + ", ".join(gexprs)) if gexprs else "Aggregate (no GROUP BY: all rows form one group)"
        gins = self.group_insights(calls, groups, input_rows, row_args, implicit)
        stages = [{
            "type": "group", "phase": "group", "title": title, "keys": gexprs, "cols": self.base_cols, "rows": input_rows,
            "rowKeys": row_keys, "rowArgs": row_args, "aggs": [{k: c[k] for k in ("text", "name", "arg", "star", "distinct")} for c in calls],
            "groups": groups, "implicit": implicit,
            "span": S.span(core.kw["GROUP"], core.group[1]) if core.group else S.span(core.lo, core.items[-1].hi),
            "rowsIn": len(input_rows), "rowsOut": len(groups), "insights": gins}]
        if having_txt:
            kept = sum(1 for k in self.gkeep if k)
            hins = [("info", f"HAVING filters whole groups after aggregation (WHERE filters rows before it): {kept} of {len(groups)} group(s) survive.")]
            if any(v is None for v in hv):
                hins.append(("warn", "Some groups evaluated to NULL (unknown) and were dropped."))
            stages.append({"type": "having", "phase": "having", "title": "HAVING", "text": having_txt, "keys": gexprs,
                           "aggs": stages[0]["aggs"], "groups": groups, "verdicts": hv, "keep": self.gkeep,
                           "span": S.span(core.kw["HAVING"], core.having[1]), "rowsIn": len(groups), "rowsOut": kept,
                           "insights": hins})
        return stages

    def group_insights(self, calls, groups, rows, row_args, implicit):
        ins = []
        if implicit:
            ins.append(("info", "Aggregate functions without GROUP BY collapse all input rows into a single row."))
        else:
            ins.append(("info", f"Rows with the same GROUP BY key are collapsed into one row per group: {len(rows)} rows -> {len(groups)} group(s)."))
        arg_pos = {}
        j = 0
        for ci, c in enumerate(calls):
            if c["arg"]:
                arg_pos[ci] = j
                j += 1
        for ci, c in enumerate(calls):
            if c["star"] and c["name"] == "COUNT":
                continue
            if ci not in arg_pos:
                continue
            p = arg_pos[ci]
            for g in groups:
                vals = [row_args[m][p] for m in g["members"]]
                nn = sum(1 for v in vals if v is None)
                if nn and c["name"] == "COUNT":
                    ins.append(("tip", f"{c['text']} counts only non-NULL values: a group with {len(vals)} row(s) but {nn} NULL(s) counts "
                                       f"{len(vals) - nn}. COUNT(*) would count all {len(vals)}."))
                    break
                if nn and c["name"] in ("SUM", "AVG", "MIN", "MAX", "TOTAL"):
                    ins.append(("tip", f"{c['name']} ignores NULLs ({nn} NULL value(s) skipped in one group)."
                                       + (" An all-NULL group gives NULL (TOTAL gives 0.0)." if c['name'] != "TOTAL" else "")))
                    break
        return ins

    # ---- windows ---------------------------------------------------------------------------
    def tiebreak_col(self):
        """A primary-key column of one of the FROM tables (a natural ORDER BY tiebreaker), or None."""
        for ai, src in enumerate(self.srcs):
            pk = self.tr.pks.get((src.name or "").lower(), [])
            if len(pk) == 1 and pk[0] in self.scols[ai]:
                col = pk[0] if re.fullmatch(r"[A-Za-z_]\w*", pk[0]) else qident(pk[0])
                return f"{src.alias_sql}.{col}"
        return None

    def window_stages(self, grouped=False):
        """Window functions per row (default) or over the rows left after GROUP BY / HAVING (grouped=True)."""
        S, core, tr = self.S, self.core, self.tr
        n = self.n
        named = frames.split_named_windows(S, core.windows)
        funcs, seen = [], set()
        for it in core.items:
            if it.star:
                continue
            for c in S.window_calls(it.lo, it.expr_hi):
                txt = S.txt(c["lo"], c["hi"])
                if txt in seen:
                    continue
                seen.add(txt)
                funcs.append((c, txt))
        wh, wc = self.where_text(), self.win_clause()
        if grouped:
            gb = (" GROUP BY " + ", ".join(self.group_keys)) if self.group_keys else ""
            hv = (" HAVING " + S.txt(*core.having)) if core.having else ""
            from_sql = f"{self.chain(n)}{wh}{gb}{hv}{wc}"
            ident = [f"({g})" for g in self.group_keys]
            kept = [gi for gi, k in enumerate(self.gkeep) if k]
            gpos = {nkey(self.groups[gi]["key"]): pos for pos, gi in enumerate(kept)}
            nid = len(ident)
            order_tail = ident
            in_cols = [{"alias": None, "name": g} for g in self.group_keys] + [{"alias": None, "name": a["text"]} for a in self.agg_list]
            in_rows = [list(self.groups[gi]["key"]) + list(self.groups[gi]["aggs"]) for gi in kept]
            rid = lambda r: gpos[nkey(r[:nid])]
        else:
            from_sql = f"{self.chain(n)}{wh}{wc}"
            ident = self.tag_cols(n)
            nid = n + 1
            order_tail = [f"{t} IS NULL, {t}" for t in ident]
            pset = {self.tags[i]: pi for pi, i in enumerate(self.passed_idx)}
            in_cols, in_rows = self.base_cols, [self.base[i] for i in self.passed_idx]
            rid = lambda r: pset[tuple(r[:nid])]
        tie_col = None if grouped else self.tiebreak_col()
        res = []
        for c, txt in funcs[:6]:
            if c["over"][0] == "spec":
                spec = frames.parse_spec(S, c["over"][1], c["over"][2], named)
            else:
                nm = c["over"][1].lower()
                if nm not in named:
                    raise Degrade(f"unknown window '{c['over'][1]}'")
                spec = frames.parse_spec(S, named[nm][0], named[nm][1], named)
            if spec["base"] and spec["base"] not in named:
                raise Degrade(f"unknown window '{spec['base']}'")
            alo, ahi = c["args"]
            arg_parts = S.split_commas(alo, ahi) if ahi > alo else []
            argexpr = S.txt(*arg_parts[0]) if arg_parts and not (ahi - alo == 1 and S.op(alo, "*")) else None
            sel = (ident + [f"({p})" for p in spec["partition"]] + [f"({o['expr']})" for o in spec["order"]]
                   + ([f"({argexpr})"] if argexpr else []) + [txt])
            order = ", ".join([f"({p})" for p in spec["partition"]] + [o["text"] for o in spec["order"]] + order_tail)
            _, rows = tr.q(f"{self.prefix}SELECT {', '.join(sel)} FROM {from_sql}" + (f" ORDER BY {order}" if order else ""))
            npk, nok = len(spec["partition"]), len(spec["order"])
            entries, parts = [], []
            cur_key, cur = object(), None
            for r in rows:
                pk = nkey(r[nid:nid + npk])
                if pk != cur_key:
                    cur_key = pk
                    cur = {"key": list(r[nid:nid + npk]), "rows": []}
                    parts.append(cur)
                entries.append({"row": rid(r), "part": len(parts) - 1, "pos": len(cur["rows"]),
                                "value": r[-1], "arg": r[nid + npk + nok] if argexpr else None,
                                "ok": list(r[nid + npk:nid + npk + nok])})
                cur["rows"].append(len(entries) - 1)
            name = c["name"]
            cat = ("frame" if name in AGG_NAMES or name in ("FIRST_VALUE", "LAST_VALUE", "NTH_VALUE")
                   else "offset" if name in ("LAG", "LEAD") else "rank")
            ftxt, explicit = frames.describe_frame(spec)
            has_ties = False
            for p in parts:
                oks = [tuple(entries[e]["ok"]) for e in p["rows"]]
                fr, gid = frames.compute_frames(spec, oks)
                if spec["order"] and len(set(gid)) < len(gid):
                    has_ties = True
                for pos, e in enumerate(p["rows"]):
                    ent = entries[e]
                    ent["peer"] = gid[pos]
                    s_, e_, ex = fr[pos]
                    if cat == "frame":
                        ent["frame"] = [s_, e_]
                        if ex:
                            ent["excl"] = ex
                if cat == "offset":
                    off = 1
                    if len(arg_parts) > 1:
                        try:
                            off = int(S.txt(*arg_parts[1]))
                        except ValueError:
                            off = 1
                    for pos, e in enumerate(p["rows"]):
                        tgt = pos - off if name == "LAG" else pos + off
                        entries[e]["src"] = tgt if 0 <= tgt < len(p["rows"]) else None
                        entries[e]["off"] = off
            sp = spec["spans"]
            fr_ = spec["frame"]
            range_offset = bool(fr_ and fr_["mode"] == "RANGE" and any(b and isinstance(b[0], (int, float)) for b in (fr_["start"], fr_["end"])))
            tb = tie_col if (tie_col and spec["order"] and not range_offset and tie_col.split(".")[-1].strip('"').lower() not in
                             " ".join(o["expr"].lower() for o in spec["order"])) else None
            wins = {"text": txt, "name": name, "cat": cat, "arg": argexpr,
                    "partitionBy": spec["partition"], "orderBy": [{"text": o["text"], "desc": o["desc"]} for o in spec["order"]],
                    "frame": ftxt, "frameExplicit": explicit, "partitions": parts, "entries": entries,
                    "namedWindow": spec["base"], "hasTies": has_ties, "tiebreak": tb,
                    "spans": {"frame": sp.get("frame"), "order": sp.get("order"), "inner": sp.get("inner"), "fn": S.span(c["lo"], c["lo"] + 1)}}
            wins["insights"] = self.window_insights(wins)
            res.append(wins)
        ins = [("info", "Window functions add a computed column without collapsing rows: every input row survives, "
                        "each looks at its own partition/frame." if not grouped else
                        "Window functions run after GROUP BY and HAVING, so each input row here is one group; they can even rank aggregates.")]
        if len(funcs) > 6:
            ins.append(("info", f"Showing the first 6 of {len(funcs)} window functions."))
        return [{"type": "window", "phase": "window", "title": "WINDOW", "cols": in_cols, "rows": in_rows,
                 "funcs": res, "span": S.span(funcs[0][0]["lo"], funcs[-1][0]["hi"]) if funcs else None,
                 "rowsIn": len(in_rows), "rowsOut": len(in_rows), "insights": ins, "grouped": grouped}]

    def window_insights(self, w):
        ins = []
        name, ents = w["name"], w["entries"]
        peers = False
        for p in w["partitions"]:
            gids = [ents[e]["peer"] for e in p["rows"]]
            if len(set(gids)) < len(gids):
                peers = True
        if w["orderBy"] and peers and name in ("RANK", "DENSE_RANK", "ROW_NUMBER"):
            if name == "RANK":
                ins.append(("tip", "Ties get the same RANK and the next rank is skipped (1,1,3). DENSE_RANK would give 1,1,2; ROW_NUMBER never ties (1,2,3)."))
            elif name == "DENSE_RANK":
                ins.append(("tip", "Ties get the same DENSE_RANK and no ranks are skipped (1,1,2). RANK would give 1,1,3."))
            else:
                ins.append(("tip", "ROW_NUMBER numbers rows 1,2,3... even when they tie on the ORDER BY key - add a tiebreaker column to make it deterministic."))
        if w["cat"] == "frame" and w["orderBy"] and not w["frameExplicit"] and peers and name in AGG_NAMES:
            ins.append(("warn", "Default frame is RANGE ... CURRENT ROW, which includes all peers (rows tied on the ORDER BY key). "
                                "Tied rows therefore share the same running value. Use ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW for a strict row-by-row running total."))
        if w["cat"] == "frame" and not w["orderBy"] and not w["partitionBy"]:
            ins.append(("info", "OVER () with no PARTITION BY / ORDER BY: the frame is the whole result, so every row sees the same aggregate."))
        elif w["cat"] == "frame" and not w["orderBy"]:
            ins.append(("info", "No ORDER BY: the frame is the entire partition, so every row in a partition gets the same value."))
        if name in ("LAST_VALUE",) and w["orderBy"] and not w["frameExplicit"]:
            ins.append(("warn", "LAST_VALUE with the default frame returns the current row's value (the frame ends at CURRENT ROW). Use ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING for the partition's last value."))
        if name in ("LAG", "LEAD"):
            ins.append(("info", f"{name} reads a value from a neighbouring row in the partition (NULL when there is none)."))
        return ins

    # ---- SELECT / DISTINCT / ORDER BY / LIMIT ------------------------------------------------
    def select_stages(self):
        S, core, tr = self.S, self.core, self.tr
        n = self.n
        exprs, ordmap = self.items_expanded()
        self.exprs = exprs
        sel_txt = ", ".join(e[0] for e in exprs)
        wh, wc = self.where_text(), self.win_clause()
        stages = []
        if self.has_agg:
            gb = (" GROUP BY " + ", ".join(self.group_keys)) if self.group_keys else ""
            hv = (" HAVING " + S.txt(*core.having)) if core.having else ""
            base_q = f"FROM {self.chain(n)}{wh}{gb}{hv}{wc}"
            nk = len(self.group_keys)
            keys = ", ".join(f"({g})" for g in self.group_keys)
            cols_all, rows_all = tr.q(f"{self.prefix}SELECT {keys + ', ' if keys else ''}{sel_txt} {base_q}")
            cols, rows = cols_all[nk:], [r[nk:] for r in rows_all]
            gmap = {nkey(g["key"]): gi for gi, g in enumerate(self.groups)}
            src = [gmap.get(nkey(r[:nk])) for r in rows_all]
            srcdesc = "group"
        else:
            base_q = f"FROM {self.chain(n)}{wh}{wc}"
            cols_all, rows_all = tr.q(f"{self.prefix}SELECT {', '.join(self.tag_cols(n))}, {sel_txt} {base_q} ORDER BY {self.order_by_tags(n)}")
            nt = n + 1
            cols, rows = cols_all[nt:], [r[nt:] for r in rows_all]
            pmap = {bi: pi for pi, bi in enumerate(self.passed_idx)}      # chain-row index -> index among rows that passed WHERE
            src = [pmap[self.tag2idx[tuple(r[:nt])]] for r in rows_all]
            srcdesc = "row"
        if len(rows) > MAXR:
            raise Degrade(f"more than {MAXR} rows - too many to animate")
        span = S.span(core.lo, core.items[-1].hi)
        in_cnt = sum(self.gkeep) if self.has_agg else len(self.passed_idx)
        stages.append({"type": "select", "phase": "select", "title": "SELECT", "exprs": [{"text": e[1], "kind": e[2]} for e in exprs],
                       "cols": cols, "rows": rows, "src": src, "srcKind": srcdesc, "agg": self.has_agg,
                       "inCols": self.base_cols if not self.has_agg else None,
                       "inRows": [self.base[i] for i in self.passed_idx] if not self.has_agg else None,
                       "span": span, "rowsIn": in_cnt, "rowsOut": len(rows),
                       "insights": self.select_insights(exprs, cols) + self.select_extra_insights(exprs, rows, base_q)})
        if not self.has_agg:
            subs = self.sub_panel("SELECT", self.passed_idx)
            if subs:
                stages[-1]["subs"] = subs
        cur_rows = rows
        # DISTINCT
        if core.distinct:
            dcols, drows = tr.q(f"{self.prefix}SELECT DISTINCT {sel_txt} {base_q}")
            removed = self.map_rows(cur_rows, drows)
            stages.append({"type": "distinct", "phase": "distinct", "title": "DISTINCT", "cols": cols, "rows": cur_rows,
                           "keep": [i in removed["kept"] for i in range(len(cur_rows))], "out": drows,
                           "from": self.order_mapping(cur_rows, drows),
                           "span": S.span(core.lo + 1, core.lo + 2),
                           "rowsIn": len(cur_rows), "rowsOut": len(drows),
                           "insights": [("info", f"DISTINCT removes duplicate output rows: {len(cur_rows)} -> {len(drows)}. It compares whole rows (all selected columns), after SELECT.")]})
            cur_rows = drows
        # ORDER BY / LIMIT
        stages += self.order_limit(base_q, sel_txt, cols, cur_rows, ordmap)
        self.final = (cols, self.final_rows)
        return stages

    def map_rows(self, before, after):
        """Greedy multiset match: which indices of `before` survive into `after` (by value, in order)."""
        pool = {}
        for i, r in enumerate(after):
            pool.setdefault(nkey(r), []).append(i)
        kept, used = set(), set()
        for i, r in enumerate(before):
            lst = pool.get(nkey(r))
            if lst:
                lst.pop(0)
                kept.add(i)
        return {"kept": kept}

    def order_text(self, ordmap):
        # ORDER BY ordinals index the result columns; our expanded select list is exactly those columns
        return self.S.txt(*self.body.order)

    def order_limit(self, base_q, sel_txt, cols, cur_rows, ordmap):
        S, core, tr, body = self.S, self.core, self.tr, self.body
        stages = []
        rows = cur_rows
        dist = "DISTINCT " if core.distinct else ""
        if body is not None and body.order:
            ot = self.order_text(ordmap)
            _, orows = tr.q(f"{self.prefix}SELECT {dist}{sel_txt} {base_q} ORDER BY {ot}")
            mapping = self.order_mapping(rows, orows)
            keys = [S.txt(a, b) for a, b in S.split_commas(*body.order)]
            stages.append({"type": "order", "phase": "order", "title": "ORDER BY " + ", ".join(keys), "keys": keys,
                           "cols": cols, "rows": orows, "from": mapping, "before": rows,
                           "span": S.span(body.tail_kw["ORDER"], body.order[1]),
                           "rowsIn": len(rows), "rowsOut": len(orows),
                           "insights": [("info", "ORDER BY is applied after SELECT, so it can use output aliases; without it, row order is not guaranteed.")]})
            rows = orows
        if body is not None and body.limit:
            lt = S.txt(*body.limit)
            ot = (" OFFSET " + S.txt(*body.offset)) if body.offset else ""
            oby = (" ORDER BY " + self.order_text(ordmap)) if body.order else ""
            _, lrows = tr.q(f"{self.prefix}SELECT {dist}{sel_txt} {base_q}{oby} LIMIT {lt}{ot}")
            ins = [("info", f"LIMIT is the very last step: it keeps {len(lrows)} of {len(rows)} row(s).")]
            if not body.order:
                ins.append(("warn", "LIMIT without ORDER BY: which rows you get is arbitrary. Add ORDER BY to make it deterministic."))
            off = 0
            if body.offset:
                try:
                    off = int(S.txt(*body.offset))
                except ValueError:
                    off = 0
            if body.order and not core.distinct and lrows and len(rows) > off + len(lrows):
                try:
                    if self.cut_in_tie(base_q, sel_txt, ordmap, cols, off + len(lrows)):
                        ins.append(("warn", "The LIMIT cut falls inside a tie: the next row has the same sort key as the last row kept, so which of "
                                            "them you get is arbitrary. Add a tiebreaker column to ORDER BY to make it deterministic."))
                except QueryError:
                    pass
            stages.append({"type": "limit", "phase": "limit", "title": f"LIMIT {lt}" + (f" OFFSET {S.txt(*body.offset)}" if body.offset else ""),
                           "cols": cols, "rows": rows, "offset": off, "count": len(lrows), "out": lrows,
                           "span": S.span(body.tail_kw["LIMIT"], (body.offset or body.limit)[1]),
                           "rowsIn": len(rows), "rowsOut": len(lrows), "insights": ins})
            rows = lrows
        self.final_rows = rows
        return stages

    def cut_in_tie(self, base_q, sel_txt, ordmap, cols, cut):
        """True when rows [cut-1] and [cut] of the sorted result have identical sort keys."""
        S, body = self.S, self.body
        exprs = self.exprs
        keys = []
        for a, b in S.split_commas(*body.order):
            e = b
            if e - a >= 3 and S.kw(e - 2, "NULLS") and S.kw(e - 1, "FIRST", "LAST"):
                e -= 2
            if e - a >= 2 and S.kw(e - 1, "ASC", "DESC"):
                e -= 1
            if e - a == 1 and S.toks[a].k == "num" and re.fullmatch(r"\d+", S.toks[a].x):
                nn = int(S.toks[a].x)
                keys.append(exprs[nn - 1][1] if 1 <= nn <= len(exprs) else None)
            elif e - a == 1 and S.toks[a].k in ("id", "qid"):
                nm = unquote(S.toks[a].x).lower()
                item = next((it for it in self.core.items if it.alias and it.alias.lower() == nm and not it.star), None)
                keys.append(S.txt(item.lo, item.expr_hi) if item else S.txt(a, e))
            else:
                keys.append(S.txt(a, e))
        if not keys or any(k is None for k in keys):
            return False
        _, rws = self.tr.q(f"{self.prefix}SELECT {sel_txt}, {', '.join('(' + k + ')' for k in keys)} {base_q} ORDER BY {self.order_text(ordmap)}")
        nsel = len(cols)
        if cut >= len(rws):
            return False
        return nkey(rws[cut - 1][nsel:]) == nkey(rws[cut][nsel:])

    def order_mapping(self, before, after):
        pool = {}
        for i, r in enumerate(before):
            pool.setdefault(nkey(r), []).append(i)
        out = []
        for r in after:
            lst = pool.get(nkey(r))
            out.append(lst.pop(0) if lst else None)
        return out

    def tail_stages(self, cols, rows, compound_hi=None):
        """ORDER BY / LIMIT applied to an already-computed (compound) result."""
        S, tr, body = self.S, self.tr, self.body
        stages = []
        if body is None:
            return stages
        base = self.prefix + S.txt(body.lo, compound_hi) if compound_hi else None
        cur = rows
        if body.order:
            keys = [S.txt(a, b) for a, b in S.split_commas(*body.order)]
            _, orows = tr.q(f"{base} ORDER BY {S.txt(*body.order)}")
            stages.append({"type": "order", "phase": "order", "title": "ORDER BY " + ", ".join(keys), "keys": keys, "cols": cols,
                           "rows": orows[:MAXR], "from": self.order_mapping(cur, orows)[:MAXR], "before": cur[:MAXR],
                           "span": S.span(body.tail_kw["ORDER"], body.order[1]), "rowsIn": len(cur), "rowsOut": len(orows),
                           "insights": [("info", "ORDER BY applies to the combined result of the set operation.")]})
            cur = orows
        if body.limit:
            oby = (" ORDER BY " + S.txt(*body.order)) if body.order else ""
            off = (" OFFSET " + S.txt(*body.offset)) if body.offset else ""
            _, lrows = tr.q(f"{base}{oby} LIMIT {S.txt(*body.limit)}{off}")
            try:
                off_n = int(S.txt(*body.offset)) if body.offset else 0
            except ValueError:
                off_n = 0
            stages.append({"type": "limit", "phase": "limit",
                           "title": f"LIMIT {S.txt(*body.limit)}" + (f" OFFSET {S.txt(*body.offset)}" if body.offset else ""), "cols": cols,
                           "rows": cur[:MAXR], "offset": off_n, "count": len(lrows), "out": lrows[:MAXR],
                           "span": S.span(body.tail_kw["LIMIT"], (body.offset or body.limit)[1]),
                           "rowsIn": len(cur), "rowsOut": len(lrows), "insights": []})
            cur = lrows
        if stages:
            stages.append({"type": "result", "phase": "result", "title": "Result", "cols": cols, "rows": cur[:MAXR],
                           "rowsIn": len(cur), "rowsOut": len(cur), "insights": []})
        return stages

    def select_insights(self, exprs, cols):
        ins = []
        kinds = {e[2] for e in exprs}
        if "agg" in kinds:
            ins.append(("info", "SELECT runs after grouping, so aggregate results are simply read from each group."))
        if "window" in kinds:
            ins.append(("info", "Window function results were computed before SELECT; here they are just projected as columns."))
        if not ins:
            ins.append(("info", "SELECT is evaluated late: it picks and computes output columns from the rows that survived all earlier steps."))
        if self.correlated.get("SELECT"):
            ins.append(("info", "A correlated sub-query in the SELECT list is re-evaluated for each row."))
        return ins

    @staticmethod
    def _norm_col(x):
        return re.sub(r"\s+", "", x).lower().split(".")[-1].strip('"')

    def select_extra_insights(self, exprs, rows, base_q):
        ins = []
        if self.has_agg:
            gk = {self._norm_col(g) for g in self.group_keys}
            single_minmax = len(self.agg_list) == 1 and self.agg_list[0]["name"] in ("MIN", "MAX")
            fd = set()                                    # aliases whose primary key is a GROUP BY key (functional dependence)
            for ai, src in enumerate(self.srcs):
                pk = [c.lower() for c in self.tr.pks.get((src.name or "").lower(), [])]
                if pk and all(c in gk for c in pk):
                    fd.add(ai)

            def owner(txt):
                t = re.sub(r"\s+", "", txt).strip('"')
                if "." in t:
                    a = t.split(".")[0].strip('"').lower()
                    return next((i for i, al in enumerate(self.aliases) if al.lower() == a), None)
                return next((i for i, cols in enumerate(self.scols) if t.lower() in {c.lower() for c in cols}), None)
            bare = [e[1] for e in exprs if e[2] == "col" and self._norm_col(e[1]) not in gk and owner(e[1]) not in fd]
            if bare and not single_minmax:
                ins.append(("warn", f"{', '.join(bare[:3])} is neither in GROUP BY nor aggregated: SQLite returns it from an arbitrary row of each group "
                                    "(PostgreSQL and MySQL would reject the query). Add it to GROUP BY or wrap it in an aggregate."))
        idx = [i for i, e in enumerate(exprs) if e[2] in ("expr", "agg") and "/" in e[1] and not re.search(r"['\"]|--|/\*", e[1])]
        if idx:
            try:
                alt = ", ".join(re.sub(r"/", "* 1.0 /", exprs[i][1]) for i in idx)
                _, arows = self.tr.q(f"{self.prefix}SELECT {alt} {base_q}")
                for j, i in enumerate(idx):
                    orig = [r[i] for r in rows]
                    new = [r[j] for r in arows]
                    if orig and all(isinstance(v, int) or v is None for v in orig) and any(isinstance(v, float) and v != int(v) for v in new):
                        o_s, n_s = sorted(orig, key=repr), sorted(new, key=repr)
                        ex = next(((a, b) for a, b in zip(o_s, n_s) if a != b), None)
                        eg = f" (e.g. {ex[0]} instead of {round(ex[1], 3)})" if ex else ""
                        ins.append(("warn", f"Integer division: {exprs[i][1]} divides two integers, so SQLite truncates the result{eg}. "
                                            "Multiply one side by 1.0 (or use AVG) to keep the fraction."))
            except QueryError:
                pass
        return ins

    def result_insights(self, rows):
        return []


def trace_query(db, text, strict=False, pks=None):
    tr = Tracer(db, text, strict=strict)
    tr.pks = pks or {}
    blocks = tr.run()
    return blocks, tr.notes


def sanitize(obj):
    return clean(obj)
