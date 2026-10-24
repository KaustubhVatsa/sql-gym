"""Window specs and frame membership: which rows does each window function see for each row?"""
from .sqlparse import unquote

FRAME_MODES = ("ROWS", "RANGE", "GROUPS")


def split_named_windows(sql, rng):
    """`WINDOW w AS (spec), w2 AS (spec)` -> {name: (spec_lo, spec_hi)}"""
    out = {}
    if not rng:
        return out
    for (a, b) in sql.split_commas(*rng):
        if b - a >= 4 and sql.kw(a + 1, "AS") and sql.op(a + 2, "("):
            out[unquote(sql.toks[a].x).lower()] = (a + 3, sql.match[a + 2])
    return out


def _bound(sql, lo, hi):
    """Parse one frame bound from tokens [lo,hi)."""
    if hi - lo >= 2 and sql.kw(lo, "UNBOUNDED"):
        return ("UNBOUNDED", sql.toks[lo + 1].u)
    if hi - lo >= 2 and sql.kw(lo, "CURRENT"):
        return ("CURRENT", "ROW")
    if hi - lo >= 2 and sql.kw(hi - 1, "PRECEDING", "FOLLOWING"):
        t = sql.txt(lo, hi - 1)
        try:
            n = float(t)
            n = int(n) if n == int(n) else n
        except ValueError:
            n = None
        return (n, sql.toks[hi - 1].u)
    return None


def parse_spec(sql, lo, hi, named, depth=0):
    """Parse the inside of OVER ( ... ) into a dict."""
    spec = {"partition": [], "order": [], "frame": None, "base": None, "spans": {}}
    inner_at = sql.toks[hi].a if hi < len(sql.toks) else None          # offset of the closing paren (insert point)
    spec["spans"]["inner"] = inner_at
    i = lo
    if i < hi and sql.toks[i].k in ("id", "qid") and not sql.kw(i, "PARTITION", "ORDER", "ROWS", "RANGE", "GROUPS"):
        base = unquote(sql.toks[i].x).lower()
        spec["base"] = base
        if base in named and depth < 5:
            b = parse_spec(sql, named[base][0], named[base][1], named, depth + 1)
            spec["partition"], spec["order"], spec["frame"] = b["partition"], b["order"], b["frame"]
            spec["spans"] = {**b["spans"]}
        i += 1
    marks = {}
    for j in sql.top(i, hi):
        if sql.kw(j, "PARTITION") and sql.kw(j + 1, "BY"):
            marks["P"] = j
        elif sql.kw(j, "ORDER") and sql.kw(j + 1, "BY"):
            marks["O"] = j
        elif sql.kw(j, *FRAME_MODES) and "F" not in marks:
            marks["F"] = j
    pos = sorted(marks.items(), key=lambda kv: kv[1])

    def rng(k):
        start = marks[k] + (2 if k in "PO" else 0)
        nxt = [p for (_, p) in pos if p > marks[k]]
        return start, (nxt[0] if nxt else hi)

    if "P" in marks:
        a, b = rng("P")
        spec["partition"] = [sql.txt(x, y) for (x, y) in sql.split_commas(a, b)]
    if "O" in marks:
        a, b = rng("O")
        terms = []
        for (x, y) in sql.split_commas(a, b):
            desc, nulls, e = False, None, y
            if e - x >= 3 and sql.kw(e - 2, "NULLS") and sql.kw(e - 1, "FIRST", "LAST"):
                nulls, e = sql.toks[e - 1].u, e - 2
            if e - x >= 2 and sql.kw(e - 1, "ASC", "DESC"):
                desc, e = sql.toks[e - 1].u == "DESC", e - 1
            terms.append({"text": sql.txt(x, y), "expr": sql.txt(x, e), "desc": desc, "nulls": nulls})
        spec["order"] = terms
        spec["spans"]["order"] = sql.span(a, b)
    if "F" in marks:
        a, b = rng("F")
        mode = sql.toks[a].u
        spec["spans"]["frame"] = sql.span(a, b)
        j, excl = a + 1, None
        for k in range(j, b):
            if sql.kw(k, "EXCLUDE"):
                excl = sql.txt(k + 1, b).upper()
                b = k
                break
        start = end = None
        if sql.kw(j, "BETWEEN"):
            and_i = next((k for k in range(j + 1, b) if sql.kw(k, "AND")), None)
            if and_i is not None:
                start, end = _bound(sql, j + 1, and_i), _bound(sql, and_i + 1, b)
        else:
            start, end = _bound(sql, j, b), ("CURRENT", "ROW")
        spec["frame"] = {"mode": mode, "start": start, "end": end, "exclude": excl,
                         "text": sql.txt(a, b) + (f" EXCLUDE {excl}" if excl else "")}
    return spec


def _num(v):
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def compute_frames(spec, okeys):
    """For one ordered partition: per row -> (start, end, excluded_positions); start>end means empty frame.
    okeys: list of order-key tuples in partition order."""
    n = len(okeys)
    has_order = bool(spec["order"])
    gid, groups = [0] * n, []
    for i in range(n):
        if i > 0 and (not has_order or okeys[i] == okeys[i - 1]):
            groups[-1][1] = i
            gid[i] = len(groups) - 1
        else:
            groups.append([i, i])
            gid[i] = len(groups) - 1
    fr = spec["frame"]
    mode = fr["mode"] if fr else "RANGE"
    start = (fr["start"] if fr else None) or ("UNBOUNDED", "PRECEDING")
    end = (fr["end"] if fr else None) or ("CURRENT", "ROW")
    excl = fr["exclude"] if fr else None
    desc = spec["order"][0]["desc"] if spec["order"] else False
    sign = -1 if desc else 1
    out = []
    ng = len(groups)

    def rows_pos(b, i, is_start):
        k, d = b
        if k == "UNBOUNDED":
            return 0 if d == "PRECEDING" else n - 1
        if k == "CURRENT":
            return i
        return i - k if d == "PRECEDING" else i + k

    def groups_pos(b, i, is_start):
        g = gid[i]
        k, d = b
        if k == "UNBOUNDED":
            return 0 if d == "PRECEDING" else n - 1
        if k == "CURRENT":
            return groups[g][0] if is_start else groups[g][1]
        t = g - k if d == "PRECEDING" else g + k
        if t < 0:
            return -1 if not is_start else 0
        if t >= ng:
            return n if is_start else n - 1
        return groups[t][0] if is_start else groups[t][1]

    def range_pos(b, i, is_start):
        k, d = b
        if k == "UNBOUNDED":
            return 0 if d == "PRECEDING" else n - 1
        if k == "CURRENT":
            return groups[gid[i]][0] if is_start else groups[gid[i]][1]
        v = _num(okeys[i][0]) if okeys[i] else None
        if v is None:                       # NULL or non-numeric key: only its peers
            return groups[gid[i]][0] if is_start else groups[gid[i]][1]
        target = v - sign * k if d == "PRECEDING" else v + sign * k
        keys = [_num(o[0]) if o else None for o in okeys]
        if is_start:
            for j in range(n):
                if keys[j] is not None and sign * keys[j] >= sign * target:
                    return j
            return n
        last = -1
        for j in range(n):
            if keys[j] is not None and sign * keys[j] <= sign * target:
                last = j
        return last

    pos = {"ROWS": rows_pos, "GROUPS": groups_pos, "RANGE": range_pos}[mode]
    for i in range(n):
        s, e = pos(start, i, True), pos(end, i, False)
        s, e = max(s, 0), min(e, n - 1)
        ex = []
        if excl and s <= e:
            g0, g1 = groups[gid[i]]
            if excl == "CURRENT ROW":
                ex = [i]
            elif excl == "GROUP":
                ex = list(range(g0, g1 + 1))
            elif excl == "TIES":
                ex = [j for j in range(g0, g1 + 1) if j != i]
            ex = [j for j in ex if s <= j <= e]
        out.append((s, e, ex))
    return out, gid


def describe_frame(spec):
    fr = spec["frame"]
    if fr:
        return fr["text"], True
    if spec["order"]:
        return "RANGE BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW", False
    return "RANGE BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING", False
