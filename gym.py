#!/usr/bin/env python3
"""SQL gym. Usage: gym.py list | show N | check N | hint N | solution N | reset | ui
Write your query in answers/N.sql, then run `python3 gym.py check N`  (or use the visual UI: `python3 gym.py ui`).
Rows are compared by value (column names ignored). Ordered=True means row order must match too.
The sample database (shop.db) is built automatically from seed.sql + presets.sql the first time it is needed."""
import sqlite3, sys, os

# (id, stage, prompt, hint, solution, ordered)
EX = [
# ---- Stage 1: JOINS ----
(1,"JOIN","List each employee's name with their department name. (Employees with no department should NOT appear.)",
 "INNER JOIN on dept_id. Kiran has NULL dept_id, and NULL never equals anything.",
 "SELECT e.name, d.dept_name FROM employees e JOIN departments d ON e.dept_id=d.dept_id",False),
(2,"JOIN","List ALL employees with their department name; those without a department show NULL.",
 "Which side's rows must be preserved? LEFT JOIN keeps the left table whole.",
 "SELECT e.name, d.dept_name FROM employees e LEFT JOIN departments d ON e.dept_id=d.dept_id",False),
(3,"JOIN","List departments that have NO employees.",
 "LEFT JOIN from departments, then filter for the unmatched side: the right table's key IS NULL (anti-join).",
 "SELECT d.dept_name FROM departments d LEFT JOIN employees e ON d.dept_id=e.dept_id WHERE e.emp_id IS NULL",False),
(4,"JOIN","Each customer's name and number of orders (include customers with 0 orders).",
 "LEFT JOIN + GROUP BY. COUNT(*) vs COUNT(o.order_id): which one gives 0 for no orders?",
 "SELECT c.name, COUNT(o.order_id) FROM customers c LEFT JOIN orders o ON c.customer_id=o.customer_id GROUP BY c.customer_id",False),
(5,"JOIN","Find orders whose customer does not exist in customers.",
 "Same anti-join pattern, but flipped: preserve orders, find missing customers.",
 "SELECT o.order_id FROM orders o LEFT JOIN customers c ON o.customer_id=c.customer_id WHERE c.customer_id IS NULL",False),
(6,"JOIN","Each employee's name alongside their manager's name (NULL manager for the CEO).",
 "Self join: the same table twice with different aliases.",
 "SELECT e.name, m.name FROM employees e LEFT JOIN employees m ON e.manager_id=m.emp_id",False),
(7,"JOIN","Total revenue (qty*price) per product category, only for non-cancelled orders. Categories with no sales still show 0.",
 "Chain 3-4 tables. Careful: a WHERE on a LEFT-joined table silently turns it into an INNER JOIN. Put the status filter in the ON clause. Use COALESCE for 0.",
 """SELECT p.category, COALESCE(SUM(oi.qty*p.price),0)
    FROM products p
    LEFT JOIN order_items oi ON oi.product_id=p.product_id
    LEFT JOIN orders o ON o.order_id=oi.order_id AND o.status<>'cancelled'
    WHERE o.order_id IS NOT NULL OR oi.order_id IS NULL
    GROUP BY p.category""",False),
(8,"JOIN","Pair every department (only dept 1 and 2) with every employee NOT in that department (NULL-dept employees count as not in it). Return just the row count.",
 "CROSS JOIN, then filter. Think: how many rows before filtering?",
 "SELECT COUNT(*) FROM departments d CROSS JOIN employees e WHERE d.dept_id IN (1,2) AND e.dept_id IS NOT d.dept_id",False),
# ---- Stage 2: CTEs ----
(9,"CTE","Using a CTE, find customers whose total spend (non-cancelled) is above the average customer spend (among customers with spend).",
 "CTE 1: spend per customer. Then compare to (SELECT AVG(spend) FROM cte).",
 """WITH spend AS (SELECT o.customer_id, SUM(oi.qty*p.price) s FROM orders o
      JOIN order_items oi USING(order_id) JOIN products p USING(product_id)
      WHERE o.status<>'cancelled' GROUP BY o.customer_id)
    SELECT customer_id, s FROM spend WHERE s > (SELECT AVG(s) FROM spend)""",False),
(10,"CTE","Two CTEs: avg salary per department, then list employees earning more than their own department's average.",
 "CTE then JOIN back to the base table. Same shape as a subquery, but named and reusable.",
 """WITH da AS (SELECT dept_id, AVG(salary) a FROM employees GROUP BY dept_id)
    SELECT e.name FROM employees e JOIN da USING(dept_id) WHERE e.salary > da.a""",False),
(11,"CTE","Recursive CTE: list every employee under Asha (emp 1) with their depth (direct reports = 1).",
 "WITH RECURSIVE: anchor row UNION ALL step that joins the CTE to employees on manager_id. Columns: name, depth. Exclude Asha.",
 """WITH RECURSIVE t(emp_id,name,depth) AS (
      SELECT emp_id,name,0 FROM employees WHERE emp_id=1
      UNION ALL SELECT e.emp_id,e.name,t.depth+1 FROM employees e JOIN t ON e.manager_id=t.emp_id)
    SELECT name, depth FROM t WHERE depth>0""",False),
(12,"CTE","Recursive CTE: generate dates from 2024-01-01 to 2024-01-07 (one column).",
 "No table needed. Anchor = a literal date; step = date(d,'+1 day') with a stop condition.",
 """WITH RECURSIVE d(x) AS (SELECT '2024-01-01' UNION ALL SELECT date(x,'+1 day') FROM d WHERE x<'2024-01-07')
    SELECT x FROM d""",True),
# ---- Stage 3: WINDOW functions ----
(13,"WINDOW","For every employee: name, salary, and the average salary of their department (one row per employee, no GROUP BY collapse).",
 "AVG(salary) OVER (PARTITION BY dept_id). Windows add columns; they don't collapse rows.",
 "SELECT name, salary, AVG(salary) OVER (PARTITION BY dept_id) FROM employees",False),
(14,"WINDOW","Rank employees by salary within each department using ROW_NUMBER (break ties by emp_id). Return name, dept_id, rn. Exclude employees with no dept.",
 "ROW_NUMBER() OVER (PARTITION BY ... ORDER BY salary DESC, emp_id).",
 """SELECT name, dept_id, ROW_NUMBER() OVER (PARTITION BY dept_id ORDER BY salary DESC, emp_id)
    FROM employees WHERE dept_id IS NOT NULL""",False),
(15,"WINDOW","Same ranking by salary desc within department, but with RANK and DENSE_RANK both. Return name, dept_id, rank, dense_rank. Look at Engineering and Sales ties!",
 "Ties share a rank. RANK leaves gaps after ties, DENSE_RANK doesn't.",
 """SELECT name, dept_id, RANK() OVER w, DENSE_RANK() OVER w FROM employees
    WHERE dept_id IS NOT NULL WINDOW w AS (PARTITION BY dept_id ORDER BY salary DESC)""",False),
(16,"WINDOW","Top 2 earners per department (ties: include all tied at rank<=2 using RANK). Return name, dept_id.",
 "You can't filter on a window function in WHERE. Wrap in a CTE/subquery, then filter.",
 """WITH r AS (SELECT name, dept_id, RANK() OVER (PARTITION BY dept_id ORDER BY salary DESC) rk
      FROM employees WHERE dept_id IS NOT NULL)
    SELECT name, dept_id FROM r WHERE rk<=2""",False),
(17,"WINDOW","Monthly revenue (non-cancelled, YYYY-MM) with a running total ordered by month. Return month, revenue, running_total.",
 "CTE to aggregate by month, then SUM(revenue) OVER (ORDER BY month). Note the default frame!",
 """WITH m AS (SELECT substr(o.order_date,1,7) mo, SUM(oi.qty*p.price) rev FROM orders o
      JOIN order_items oi USING(order_id) JOIN products p USING(product_id)
      WHERE o.status<>'cancelled' GROUP BY mo)
    SELECT mo, rev, SUM(rev) OVER (ORDER BY mo) FROM m ORDER BY mo""",True),
(18,"WINDOW","Same monthly revenue with the change vs previous month (NULL for the first). Return month, revenue, diff.",
 "LAG(rev) OVER (ORDER BY mo). Then subtract.",
 """WITH m AS (SELECT substr(o.order_date,1,7) mo, SUM(oi.qty*p.price) rev FROM orders o
      JOIN order_items oi USING(order_id) JOIN products p USING(product_id)
      WHERE o.status<>'cancelled' GROUP BY mo)
    SELECT mo, rev, rev - LAG(rev) OVER (ORDER BY mo) FROM m ORDER BY mo""",True),
(19,"WINDOW","For each customer's orders (all statuses, existing customers only), show order_id, customer_id, and days since that customer's previous order (NULL for first).",
 "LAG(order_date) OVER (PARTITION BY customer_id ORDER BY order_date), then julianday() math.",
 """SELECT o.order_id, o.customer_id,
      julianday(o.order_date) - julianday(LAG(o.order_date) OVER (PARTITION BY o.customer_id ORDER BY o.order_date))
    FROM orders o JOIN customers c USING(customer_id)""",False),
(20,"WINDOW","For each month, count non-cancelled orders, then show month, orders, and the average of the current + previous month's counts (frame: ROWS BETWEEN 1 PRECEDING AND CURRENT ROW).",
 "Explicit frame clause: ROWS BETWEEN 1 PRECEDING AND CURRENT ROW.",
 """WITH m AS (SELECT substr(order_date,1,7) mo, COUNT(*) n FROM orders WHERE status<>'cancelled' GROUP BY mo)
    SELECT mo, n, AVG(n) OVER (ORDER BY mo ROWS BETWEEN 1 PRECEDING AND CURRENT ROW) FROM m ORDER BY mo""",True),
(21,"WINDOW","Split employees (with a dept) into salary quartiles across the whole company: name, NTILE(4) by salary desc, emp_id.",
 "NTILE(4) OVER (ORDER BY salary DESC, emp_id).",
 "SELECT name, NTILE(4) OVER (ORDER BY salary DESC, emp_id) FROM employees WHERE dept_id IS NOT NULL",False),
# ---- Stage 4: COMBINED / interview-style ----
(22,"MIX","Each customer's most recent non-cancelled order (customer name, order_id, order_date). One row per customer who has one.",
 "ROW_NUMBER() OVER (PARTITION BY customer ORDER BY order_date DESC) in a CTE, filter rn=1.",
 """WITH r AS (SELECT c.name, o.order_id, o.order_date,
      ROW_NUMBER() OVER (PARTITION BY c.customer_id ORDER BY o.order_date DESC) rn
      FROM customers c JOIN orders o USING(customer_id) WHERE o.status<>'cancelled')
    SELECT name, order_id, order_date FROM r WHERE rn=1""",False),
(23,"MIX","Second-highest distinct salary in each department (dept_id, salary). Departments with only one distinct salary are omitted.",
 "DENSE_RANK = 2, in a CTE.",
 """WITH r AS (SELECT dept_id, salary, DENSE_RANK() OVER (PARTITION BY dept_id ORDER BY salary DESC) dr
      FROM employees WHERE dept_id IS NOT NULL)
    SELECT DISTINCT dept_id, salary FROM r WHERE dr=2""",False),
(24,"MIX","Each product category's share of total non-cancelled revenue as a percentage (rounded to 1 decimal): category, pct.",
 "CTE for category revenue, then rev*100.0/SUM(rev) OVER (). An empty OVER () = the whole result set.",
 """WITH c AS (SELECT p.category, SUM(oi.qty*p.price) rev FROM orders o
      JOIN order_items oi USING(order_id) JOIN products p USING(product_id)
      WHERE o.status<>'cancelled' GROUP BY p.category)
    SELECT category, ROUND(rev*100.0/SUM(rev) OVER (),1) FROM c""",False),
(25,"MIX","List customers who placed a non-cancelled order in all 3 months (Jan, Feb, Mar 2024). Return customer_id.",
 "Distinct (customer, month) pairs in a CTE, then count per customer = 3. Or use LAG twice.",
 """WITH cm AS (SELECT DISTINCT customer_id, substr(order_date,1,7) mo FROM orders WHERE status<>'cancelled')
    SELECT customer_id FROM cm GROUP BY customer_id HAVING COUNT(*)=3""",False),
]
BYID = {e[0]: e for e in EX}
HERE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(HERE, "shop.db")
ANS = os.path.join(HERE, "answers")
SEED_FILES = ("seed.sql", "presets.sql")


def build_db(path=DB):
    """(Re)create the sample database from the seed scripts using only the standard library."""
    tmp = path + ".tmp"
    if os.path.exists(tmp):
        os.remove(tmp)
    con = sqlite3.connect(tmp)
    try:
        for f in SEED_FILES:
            with open(os.path.join(HERE, f)) as fh:
                con.executescript(fh.read())
        con.commit()
    finally:
        con.close()
    os.replace(tmp, path)


def ensure_db():
    os.makedirs(ANS, exist_ok=True)
    if not os.path.exists(DB):
        build_db()


ensure_db()

def run(sql):
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    try: return con.execute(sql).fetchall()
    finally: con.close()

def norm(rows):  # round floats so 3.0000001 == 3.0
    return [tuple(round(v, 4) if isinstance(v, float) else v for v in r) for r in rows]

def main():
    a = sys.argv[1:]
    if not a or a[0] == "list":
        stage = None
        for e in EX:
            if e[1] != stage: stage = e[1]; print(f"\n== {stage} ==")
            done = "x" if os.path.exists(os.path.join(ANS, f"{e[0]}.sql")) else " "
            print(f" [{done}] {e[0]:>2}. {e[2][:90]}")
        return
    if a[0] == "reset":
        build_db(); print("db reset"); return
    if a[0] == "ui":
        import server; sys.argv = [sys.argv[0]] + a[1:]; server.main(); return
    n = int(a[1]); e = BYID[n]
    if a[0] == "show":
        print(f"[{e[1]}] #{n}\n{e[2]}\n\nWrite your SQL in answers/{n}.sql, then: python3 gym.py check {n}")
    elif a[0] == "hint": print(e[3])
    elif a[0] == "check":
        p = os.path.join(ANS, f"{n}.sql")
        if not os.path.exists(p): print(f"No answers/{n}.sql yet."); return
        try: mine = norm(run(open(p).read()))
        except Exception as ex: print("SQL error:", ex); return
        want = norm(run(e[4]))
        ok = (mine == want) if e[5] else (sorted(mine, key=repr) == sorted(want, key=repr))
        if ok: print(f"PASS  ({len(mine)} rows)")
        else:
            print(f"FAIL  you: {len(mine)} rows, expected: {len(want)} rows")
            print("your first rows:", mine[:5]); print("hint: python3 gym.py hint", n)
            if len(want) <= 12 and "--peek" in a: print("expected:", want)
    elif a[0] == "solution": print(e[4])
if __name__ == "__main__":
    main()
