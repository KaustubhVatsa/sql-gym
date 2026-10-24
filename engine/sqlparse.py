"""Tokenizer and a structure-only parser for SELECT statements.

Expressions are never parsed; we only locate clause boundaries (by token index, at paren depth 0)
so the tracer can re-run each logical phase as real SQLite SQL and report intermediate tables.
Anything we don't understand raises Unsupported and the tracer degrades to "just show the result".
"""
import re
from dataclasses import dataclass, field
from typing import Optional


class ParseError(Exception):
    pass


class Unsupported(Exception):
    pass


TOKEN_RE = re.compile(r"""
  (?P<ws>\s+)
 |(?P<lc>--[^\n]*)
 |(?P<bc>/\*.*?(?:\*/|\Z))
 |(?P<str>'(?:[^']|'')*')
 |(?P<qid>"(?:[^"]|"")*"|`(?:[^`]|``)*`|\[[^\]]*\])
 |(?P<num>0[xX][0-9a-fA-F]+|(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?)
 |(?P<id>[A-Za-z_][A-Za-z_0-9$]*)
 |(?P<param>\?\d*|[:@$][A-Za-z_][A-Za-z_0-9]*)
 |(?P<op><>|!=|==|<=|>=|<<|>>|\|\||->>|->|[-+*/%<>=&|~^.,;()])
""", re.X | re.S)


class Tok:
    __slots__ = ("k", "x", "a", "b", "u")

    def __init__(self, k, x, a, b):
        self.k, self.x, self.a, self.b = k, x, a, b
        self.u = x.upper() if k == "id" else x

    def __repr__(self):
        return f"Tok({self.k},{self.x!r})"


def tokenize(text):
    toks, pos, n = [], 0, len(text)
    while pos < n:
        m = TOKEN_RE.match(text, pos)
        if not m:
            ch = text[pos]
            if ch == "'":
                raise ParseError("Unterminated string literal")
            if ch == '"':
                raise ParseError("Unterminated quoted identifier")
            raise ParseError(f"Unexpected character {ch!r}")
        kind = m.lastgroup
        if kind not in ("ws", "lc", "bc"):
            toks.append(Tok(kind, m.group(), m.start(), m.end()))
        pos = m.end()
    return toks


def unquote(x):
    if x[:1] == '"':
        return x[1:-1].replace('""', '"')
    if x[:1] == "`":
        return x[1:-1].replace("``", "`")
    if x[:1] == "[":
        return x[1:-1]
    return x


def qident(name):
    return '"' + name.replace('"', '""') + '"'


AGG_NAMES = {"COUNT", "SUM", "AVG", "MIN", "MAX", "TOTAL", "GROUP_CONCAT", "STRING_AGG",
             "JSON_GROUP_ARRAY", "JSON_GROUP_OBJECT"}
WINDOW_ONLY = {"ROW_NUMBER", "RANK", "DENSE_RANK", "PERCENT_RANK", "CUME_DIST", "NTILE",
               "LAG", "LEAD", "FIRST_VALUE", "LAST_VALUE", "NTH_VALUE"}
JOIN_WORDS = {"NATURAL", "LEFT", "RIGHT", "FULL", "INNER", "CROSS", "JOIN", "OUTER"}
NOT_ALIAS = JOIN_WORDS | {"ON", "USING", "WHERE", "GROUP", "HAVING", "WINDOW", "ORDER", "LIMIT",
                          "UNION", "INTERSECT", "EXCEPT", "INDEXED", "NOT", "OFFSET"}
NOT_ITEM_ALIAS = {"END", "NULL", "TRUE", "FALSE", "CURRENT_DATE", "CURRENT_TIME", "CURRENT_TIMESTAMP",
                  "ISNULL", "NOTNULL", "ASC", "DESC", "FROM", "AND", "OR", "NOT", "IS", "IN", "LIKE",
                  "GLOB", "BETWEEN", "THEN", "ELSE", "WHEN", "CASE", "COLLATE", "DISTINCT", "ALL", "OVER"}


@dataclass
class Source:
    kind: str                       # table | subquery | func
    lo: int
    hi: int
    name: Optional[str]
    alias: str
    alias_sql: str
    inner_sql: str                  # goes after FROM inside the tagged wrapper
    sub: Optional[tuple] = None     # token range inside the parens (subquery)
    explicit_alias: bool = False


@dataclass
class Join:
    kw: str                         # INNER | LEFT | RIGHT | FULL | CROSS
    natural: bool
    comma: bool
    kw_lo: int
    kw_hi: int
    on: Optional[tuple]
    using: Optional[list]
    source: Source
    hi: int = 0


@dataclass
class FromClause:
    lo: int
    hi: int
    first: Source
    joins: list = field(default_factory=list)


@dataclass
class Item:
    lo: int
    hi: int
    expr_hi: int
    alias: Optional[str]
    star: bool = False
    star_alias: Optional[str] = None


@dataclass
class Core:
    lo: int
    hi: int
    distinct: bool
    items: list
    from_: Optional[FromClause]
    where: Optional[tuple]
    group: Optional[tuple]
    having: Optional[tuple]
    windows: Optional[tuple]
    kw: dict                         # clause keyword -> token index
    opaque: bool = False


@dataclass
class Body:
    lo: int
    hi: int
    parts: list
    ops: list                        # [(text, tok_idx)]
    order: Optional[tuple]
    limit: Optional[tuple]
    offset: Optional[tuple]
    tail_kw: dict


@dataclass
class CTE:
    name: str
    cols: Optional[list]
    body: tuple
    lo: int
    hi: int                          # token range of the whole definition `name(cols) AS (...)`


@dataclass
class Statement:
    ctes: list
    recursive: bool
    body: Body
    lo: int
    hi: int


class Sql:
    def __init__(self, text):
        self.text = text
        self.toks = tokenize(text)
        self.match = {}
        stack = []
        for i, t in enumerate(self.toks):
            if t.k == "op" and t.x == "(":
                stack.append(i)
            elif t.k == "op" and t.x == ")":
                if not stack:
                    raise ParseError("Unmatched ')'")
                self.match[stack.pop()] = i
        if stack:
            raise ParseError("Unmatched '('")

    # ---- helpers --------------------------------------------------------------------------
    def txt(self, lo, hi):
        if hi <= lo:
            return ""
        return self.text[self.toks[lo].a:self.toks[hi - 1].b]

    def span(self, lo, hi):
        if hi <= lo:
            return None
        return [self.toks[lo].a, self.toks[hi - 1].b]

    def kw(self, i, *words):
        if i < 0 or i >= len(self.toks):
            return False
        t = self.toks[i]
        return t.k == "id" and (not words or t.u in words)

    def op(self, i, x):
        return 0 <= i < len(self.toks) and self.toks[i].k == "op" and self.toks[i].x == x

    def top(self, lo, hi):
        """Yield token indices at paren depth 0 inside [lo, hi) (paren groups are skipped, '(' is yielded)."""
        i = lo
        while i < hi:
            yield i
            i = self.match[i] + 1 if i in self.match else i + 1

    def split_commas(self, lo, hi):
        out, cur = [], lo
        for i in self.top(lo, hi):
            if self.op(i, ","):
                out.append((cur, i))
                cur = i + 1
        if cur < hi or not out:
            out.append((cur, hi))
        return out

    def is_subquery_paren(self, i):
        return i in self.match and self.kw(i + 1, "SELECT", "WITH", "VALUES")

    # ---- statement ------------------------------------------------------------------------
    def parse(self):
        lo, hi = 0, len(self.toks)
        while hi > lo and self.op(hi - 1, ";"):
            hi -= 1
        if hi <= lo:
            raise ParseError("Empty query")
        for i in range(lo, hi):
            if self.op(i, ";"):
                raise ParseError("Only one statement at a time, please")
        return self.statement(lo, hi)

    def statement(self, lo, hi):
        i, ctes, recursive = lo, [], False
        if self.kw(i, "WITH"):
            i += 1
            if self.kw(i, "RECURSIVE"):
                recursive = True
                i += 1
            while True:
                start = i
                if i >= hi or self.toks[i].k not in ("id", "qid"):
                    raise ParseError("Expected a CTE name after WITH")
                name = unquote(self.toks[i].x)
                i += 1
                cols = None
                if self.op(i, "("):
                    cols = [unquote(self.toks[j].x) for j in range(i + 1, self.match[i])
                            if self.toks[j].k in ("id", "qid")]
                    i = self.match[i] + 1
                if not self.kw(i, "AS"):
                    raise ParseError(f"Expected AS after CTE name '{name}'")
                i += 1
                if self.kw(i, "NOT"):
                    i += 1
                if self.kw(i, "MATERIALIZED"):
                    i += 1
                if not self.op(i, "("):
                    raise ParseError(f"Expected '(' to start the body of CTE '{name}'")
                ctes.append(CTE(name, cols, (i + 1, self.match[i]), start, self.match[i] + 1))
                i = self.match[i] + 1
                if self.op(i, ","):
                    i += 1
                    continue
                break
        body = self.body(i, hi)
        return Statement(ctes, recursive, body, lo, hi)

    def body(self, lo, hi):
        while self.op(lo, "(") and self.match.get(lo) == hi - 1 and self.kw(lo + 1, "SELECT", "WITH", "VALUES"):
            lo, hi = lo + 1, hi - 1          # ( select ... )
            if self.kw(lo, "WITH"):
                raise Unsupported("WITH inside parentheses")
        parts, ops, cur = [], [], lo
        for i in self.top(lo, hi):
            if self.kw(i, "UNION", "INTERSECT", "EXCEPT"):
                word, j = self.toks[i].u, i + 1
                if word == "UNION" and self.kw(j, "ALL"):
                    word, j = "UNION ALL", j + 1
                parts.append((cur, i))
                ops.append((word, i))
                cur = j
        parts.append((cur, hi))
        last_lo, last_hi = parts[-1]
        tail = None
        for i in self.top(last_lo, last_hi):
            if (self.kw(i, "ORDER") and self.kw(i + 1, "BY")) or self.kw(i, "LIMIT"):
                tail = i
                break
        order = limit = offset = None
        tail_kw = {}
        if tail is not None:
            parts[-1] = (last_lo, tail)
            i, end = tail, last_hi
            if self.kw(i, "ORDER"):
                tail_kw["ORDER"] = i
                j = last_hi
                for k in self.top(i + 2, last_hi):
                    if self.kw(k, "LIMIT"):
                        j = k
                        break
                order = (i + 2, j)
                i = j
            if i < end and self.kw(i, "LIMIT"):
                tail_kw["LIMIT"] = i
                j, off = end, None
                for k in self.top(i + 1, end):
                    if self.kw(k, "OFFSET") or self.op(k, ","):
                        j, off = k, k
                        break
                limit = (i + 1, j)
                if off is not None:
                    if self.op(off, ","):
                        # LIMIT offset, count  (SQLite legacy form)
                        limit, offset = (off + 1, end), (i + 1, off)
                    else:
                        offset = (off + 1, end)
        cores = []
        for (a, b) in parts:
            cores.append(self.core(a, b))
        return Body(lo, hi, cores, ops, order, limit, offset, tail_kw)

    # ---- core select ----------------------------------------------------------------------
    def core(self, lo, hi):
        if self.kw(lo, "VALUES"):
            return Core(lo, hi, False, [], None, None, None, None, None, {}, opaque=True)
        if not self.kw(lo, "SELECT"):
            raise ParseError("Expected SELECT" + (f" but found '{self.toks[lo].x}'" if lo < len(self.toks) else ""))
        i, distinct = lo + 1, False
        if self.kw(i, "DISTINCT"):
            distinct, i = True, i + 1
        elif self.kw(i, "ALL"):
            i += 1
        order = ["FROM", "WHERE", "GROUP", "HAVING", "WINDOW"]
        marks, last = {}, -1
        for j in self.top(i, hi):
            if not self.kw(j):
                continue
            w = self.toks[j].u
            if w in order and order.index(w) > last:
                if w == "FROM" and self.kw(j - 1, "DISTINCT") and self.kw(j - 2, "IS", "NOT"):
                    continue
                if w == "GROUP" and not self.kw(j + 1, "BY"):
                    continue
                marks[w] = j
                last = order.index(w)
        pos = sorted(marks.items(), key=lambda kv: kv[1])
        first_mark = pos[0][1] if pos else hi
        items = self.items(i, first_mark)

        def rng(w):
            if w not in marks:
                return None
            start = marks[w] + (2 if w == "GROUP" else 1)
            nxt = [p for (_, p) in pos if p > marks[w]]
            return (start, nxt[0] if nxt else hi)

        frm = None
        if "FROM" in marks:
            f = rng("FROM")
            frm = self.from_clause(f[0], f[1])
        return Core(lo, hi, distinct, items, frm, rng("WHERE"), rng("GROUP"), rng("HAVING"), rng("WINDOW"), marks)

    def items(self, lo, hi):
        out = []
        for (a, b) in self.split_commas(lo, hi):
            if b <= a:
                raise ParseError("Empty column in SELECT list")
            if b - a == 1 and self.op(a, "*"):
                out.append(Item(a, b, b, None, star=True))
                continue
            if b - a == 3 and self.toks[a].k in ("id", "qid") and self.op(a + 1, ".") and self.op(a + 2, "*"):
                out.append(Item(a, b, b, None, star=True, star_alias=unquote(self.toks[a].x)))
                continue
            alias, expr_hi = None, b
            if b - a >= 3 and self.kw(b - 2, "AS") and self.toks[b - 1].k in ("id", "qid", "str"):
                alias, expr_hi = unquote(self.toks[b - 1].x).strip("'") if self.toks[b - 1].k == "str" else unquote(self.toks[b - 1].x), b - 2
            elif b - a >= 2 and self.toks[b - 1].k in ("id", "qid") and not (
                    self.toks[b - 1].k == "id" and self.toks[b - 1].u in NOT_ITEM_ALIAS):
                prev = self.toks[b - 2]
                if not (prev.k == "op" and prev.x in ("+", "-", "*", "/", "%", "||", ".", "(", ",", "=", "<", ">",
                                                       "<=", ">=", "<>", "!=", "&", "|", "<<", ">>", "~")) \
                        and not (prev.k == "id" and prev.u in NOT_ITEM_ALIAS):
                    alias, expr_hi = unquote(self.toks[b - 1].x), b - 1
            out.append(Item(a, b, expr_hi, alias))
        return out

    # ---- FROM -----------------------------------------------------------------------------
    def source(self, i, hi):
        t = self.toks[i]
        kind, name, sub, inner, j = None, None, None, None, i
        if self.op(i, "("):
            if self.is_subquery_paren(i):
                kind, sub = "subquery", (i + 1, self.match[i])
                inner = "(" + self.txt(i + 1, self.match[i]) + ")"
                j = self.match[i] + 1
            else:
                raise Unsupported("parenthesized join in FROM")
        elif t.k in ("id", "qid"):
            j = i + 1
            if self.op(j, "."):
                j += 1
                if j >= hi or self.toks[j].k not in ("id", "qid"):
                    raise ParseError("Expected a table name after '.'")
                j += 1
            if self.op(j, "("):
                kind, j = "func", self.match[j] + 1
                inner = self.txt(i, j)
                name = None
            else:
                kind = "table"
                name = unquote(self.toks[j - 1].x)
                inner = self.txt(i, j)
        else:
            raise ParseError(f"Unexpected '{t.x}' in FROM clause")
        alias, alias_sql, explicit = None, None, False
        if self.kw(j, "AS"):
            if j + 1 >= hi or self.toks[j + 1].k not in ("id", "qid"):
                raise ParseError("Expected an alias after AS")
            alias, alias_sql, explicit = unquote(self.toks[j + 1].x), self.toks[j + 1].x, True
            j += 2
        elif j < hi and self.toks[j].k in ("id", "qid") and not (self.toks[j].k == "id" and self.toks[j].u in NOT_ALIAS):
            alias, alias_sql, explicit = unquote(self.toks[j].x), self.toks[j].x, True
            j += 1
        if alias is None:
            if kind == "table":
                alias, alias_sql = name, self.toks[j - 1].x
            else:
                alias = f"t{i}"
                alias_sql = qident(alias)
        return Source(kind, i, j, name, alias, alias_sql, inner, sub, explicit), j

    def from_clause(self, lo, hi):
        if lo >= hi:
            raise ParseError("Expected a table after FROM")
        first, i = self.source(lo, hi)
        fc = FromClause(lo, hi, first)
        while i < hi:
            if self.op(i, ","):
                src, j = self.source(i + 1, hi)
                fc.joins.append(Join("CROSS", False, True, i, i + 1, None, None, src, j))
                i = j
                continue
            if not self.kw(i, "NATURAL", "LEFT", "RIGHT", "FULL", "INNER", "CROSS", "JOIN"):
                raise ParseError(f"Unexpected '{self.toks[i].x}' in FROM clause")
            kw_lo, natural, kind = i, False, "INNER"
            if self.kw(i, "NATURAL"):
                natural, i = True, i + 1
            if self.kw(i, "LEFT", "RIGHT", "FULL"):
                kind, i = self.toks[i].u, i + 1
                if self.kw(i, "OUTER"):
                    i += 1
            elif self.kw(i, "INNER"):
                i += 1
            elif self.kw(i, "CROSS"):
                kind, i = "CROSS", i + 1
            if not self.kw(i, "JOIN"):
                raise ParseError("Expected JOIN")
            i += 1
            kw_hi = i
            src, i = self.source(i, hi)
            on = using = None
            if self.kw(i, "ON"):
                j = i + 1
                for k in self.top(j, hi):
                    if self.kw(k, *JOIN_WORDS) and not self.op(k + 1, "(") or self.op(k, ","):
                        break
                else:
                    k = hi
                on = (i + 1, k)
                i = k
            elif self.kw(i, "USING"):
                if not self.op(i + 1, "("):
                    raise ParseError("Expected '(' after USING")
                using = [unquote(self.toks[j].x) for j in range(i + 2, self.match[i + 1]) if self.toks[j].k in ("id", "qid")]
                i = self.match[i + 1] + 1
            fc.joins.append(Join(kind, natural, False, kw_lo, kw_hi, on, using, src, i))
        return fc

    # ---- expression scanners ----------------------------------------------------------------
    def conjuncts(self, lo, hi):
        """Split a predicate at top-level ANDs (only when there is no top-level OR)."""
        case_depth, between, cuts, has_or = 0, 0, [], False
        for i in self.top(lo, hi):
            if not self.kw(i):
                continue
            u = self.toks[i].u
            if u == "CASE":
                case_depth += 1
            elif u == "END":
                case_depth = max(0, case_depth - 1)
            elif case_depth:
                continue
            elif u == "BETWEEN":
                between += 1
            elif u == "AND":
                if between:
                    between -= 1
                else:
                    cuts.append(i)
            elif u == "OR":
                has_or = True
        if has_or or not cuts:
            return [(lo, hi)]
        out, cur = [], lo
        for c in cuts:
            out.append((cur, c))
            cur = c + 1
        out.append((cur, hi))
        return out

    def predicate_parts(self, lo, hi):
        """Split a predicate for display: ('OR', terms) at top-level ORs (AND binds tighter, so each term may hold ANDs),
        else ('AND', conjuncts) at top-level ANDs, else (None, [whole])."""
        case_depth, between, ands, ors = 0, 0, [], []
        for i in self.top(lo, hi):
            if not self.kw(i):
                continue
            u = self.toks[i].u
            if u == "CASE":
                case_depth += 1
            elif u == "END":
                case_depth = max(0, case_depth - 1)
            elif case_depth:
                continue
            elif u == "BETWEEN":
                between += 1
            elif u == "AND":
                if between:
                    between -= 1
                else:
                    ands.append(i)
            elif u == "OR":
                ors.append(i)
        cuts, kind = (ors, "OR") if ors else (ands, "AND")
        if not cuts:
            return None, [(lo, hi)]
        out, cur = [], lo
        for c in cuts:
            out.append((cur, c))
            cur = c + 1
        out.append((cur, hi))
        return kind, out

    def calls(self, lo, hi):
        """All function calls in [lo,hi), skipping nested subqueries.
        Returns dicts: name, lo, hi (end incl. FILTER/OVER), args (lo,hi), over (None | ('spec',lo,hi) | ('name',str))."""
        out, i = [], lo
        while i < hi:
            if self.is_subquery_paren(i):
                i = self.match[i] + 1
                continue
            t = self.toks[i]
            if t.k == "id" and self.op(i + 1, "(") and not (i > lo and self.op(i - 1, ".")):
                close = self.match[i + 1]
                end, over, k = close + 1, None, close + 1
                if self.kw(k, "FILTER") and self.op(k + 1, "("):
                    k = self.match[k + 1] + 1
                    end = k
                if self.kw(k, "OVER"):
                    if self.op(k + 1, "("):
                        over = ("spec", k + 2, self.match[k + 1])
                        end = self.match[k + 1] + 1
                    elif k + 1 < hi and self.toks[k + 1].k in ("id", "qid"):
                        over = ("name", unquote(self.toks[k + 1].x))
                        end = k + 2
                out.append({"name": t.u, "lo": i, "hi": end, "args": (i + 2, close), "over": over})
            i += 1
        return out

    def agg_calls(self, lo, hi):
        res = []
        for c in self.calls(lo, hi):
            if c["over"] is not None or c["name"] not in AGG_NAMES:
                continue
            if c["name"] in ("MIN", "MAX") and len(self.split_commas(*c["args"])) > 1:
                continue
            res.append(c)
        return res

    def window_calls(self, lo, hi):
        return [c for c in self.calls(lo, hi) if c["over"] is not None]

    def subqueries(self, lo, hi):
        """Parenthesised sub-selects in [lo,hi) (outermost only)."""
        out, i = [], lo
        while i < hi:
            if self.is_subquery_paren(i):
                out.append((i + 1, self.match[i]))
                i = self.match[i] + 1
            else:
                i += 1
        return out

    def idents_in(self, lo, hi):
        return [self.toks[i] for i in range(lo, hi) if self.toks[i].k in ("id", "qid")]
