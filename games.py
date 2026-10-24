"""Stateless games: no accounts, no storage. Questions are rebuilt deterministically from (id, seed)."""
import random

from engine.db import QueryError
from engine.sqlparse import Sql

# kind 'rows': "how many rows?"   kind 'cell': value at ask = {key, val, col, text}
# alts: SQL that yields tempting wrong answers (same ask applied)   extra: literal extra distractors
PREDICT = [
 dict(id="j-inner", topic="JOIN", kind="rows", sql="""SELECT e.name, d.dept_name
FROM employees e
JOIN departments d ON e.dept_id = d.dept_id""",
  explain="INNER JOIN keeps only matching pairs. Kiran has dept_id NULL (NULL never equals anything) and Legal has no employees, so both vanish."),
 dict(id="j-left", topic="JOIN", kind="rows", sql="""SELECT e.name, d.dept_name
FROM employees e
LEFT JOIN departments d ON e.dept_id = d.dept_id""",
  explain="LEFT JOIN keeps every employee. Kiran has no department, so appears once with NULL; Legal (a department, not an employee) is not in the output."),
 dict(id="j-right", topic="JOIN", kind="rows", sql="""SELECT d.dept_name, e.name
FROM employees e
RIGHT JOIN departments d ON e.dept_id = d.dept_id""",
  explain="RIGHT JOIN keeps every department: 10 matched employee rows plus Legal with a NULL employee. Kiran (no department) is dropped."),
 dict(id="j-full", topic="JOIN", kind="rows", sql="""SELECT d.dept_name, e.name
FROM employees e
FULL OUTER JOIN departments d ON e.dept_id = d.dept_id""",
  explain="FULL JOIN = INNER matches + unmatched left rows (Kiran) + unmatched right rows (Legal)."),
 dict(id="j-cross", topic="JOIN", kind="rows", sql="""SELECT d.dept_name, p.name
FROM departments d
CROSS JOIN products p""",
  explain="A CROSS JOIN pairs every left row with every right row: 4 departments x 6 products."),
 dict(id="j-fanout", topic="JOIN", kind="rows", sql="""SELECT o.order_id, oi.product_id
FROM orders o
JOIN order_items oi ON oi.order_id = o.order_id""",
  explain="One-to-many fan-out: each order repeats once per line item, so the row count follows order_items, not orders."),
 dict(id="j-orphan", topic="JOIN", kind="rows", sql="""SELECT o.order_id, c.name
FROM orders o
JOIN customers c ON c.customer_id = o.customer_id""",
  explain="Order 11 points at customer 99, who does not exist. An INNER JOIN silently drops orphan rows."),
 dict(id="j-anti", topic="JOIN", kind="rows", sql="""SELECT d.dept_name
FROM departments d
LEFT JOIN employees e ON e.dept_id = d.dept_id
WHERE e.emp_id IS NULL""",
  explain="Anti-join: LEFT JOIN pads unmatched departments with NULLs, then WHERE keeps exactly those. Only Legal has no employees."),
 dict(id="j-trap", topic="JOIN", kind="rows", sql="""SELECT d.dept_name, e.name
FROM departments d
LEFT JOIN employees e ON e.dept_id = d.dept_id
WHERE e.salary > 100000""",
  explain="The WHERE tests e.salary, which is NULL for the NULL-padded Legal row, so that row is filtered out: the LEFT JOIN behaves like an INNER JOIN."),
 dict(id="j-self", topic="JOIN", kind="rows", sql="""SELECT e.name, m.name
FROM employees e
JOIN employees m ON e.manager_id = m.emp_id""",
  explain="Self join. Asha (the CEO) has manager_id NULL, so she has no match and an INNER JOIN drops her."),
 dict(id="j-m2m", topic="JOIN", kind="rows", sql="""SELECT s.name, c.title
FROM students s
JOIN enrollments e ON e.student_id = s.student_id
JOIN courses c ON c.course_id = e.course_id""",
  explain="Many-to-many through enrollments. One enrollment belongs to student 9, who does not exist, so that row drops out. Ivy (no enrollments) and Poetry (no students) never appear."),
 dict(id="j-nonequi", topic="JOIN", kind="rows", sql="""SELECT a.name, b.name
FROM employees a
JOIN employees b ON a.salary < b.salary AND a.dept_id = b.dept_id""",
  explain="A non-equi join: every pair (a, b) in the same department where a earns less than b. Ties on salary do not pair up."),
 dict(id="g-null", topic="GROUP BY", kind="rows", sql="""SELECT dept_id, COUNT(*)
FROM employees
GROUP BY dept_id""",
  explain="NULLs form their own group. Departments 1, 2, 3 plus the NULL group (Kiran) make 4 groups."),
 dict(id="g-count-col", topic="GROUP BY", kind="cell", ask=dict(key="name", val="Elena", col=1, text="What number does Elena get?"),
  sql="""SELECT c.name, COUNT(o.order_id)
FROM customers c
LEFT JOIN orders o ON o.customer_id = c.customer_id
GROUP BY c.customer_id""",
  alts=["SELECT c.name, COUNT(*) FROM customers c LEFT JOIN orders o ON o.customer_id = c.customer_id GROUP BY c.customer_id"],
  explain="COUNT(col) skips NULLs. Elena has no orders, so the padded o.order_id is NULL and her count is 0. COUNT(*) would say 1."),
 dict(id="g-count-star", topic="GROUP BY", kind="cell", ask=dict(key="name", val="Elena", col=1, text="What number does Elena get?"),
  sql="""SELECT c.name, COUNT(*)
FROM customers c
LEFT JOIN orders o ON o.customer_id = c.customer_id
GROUP BY c.customer_id""",
  alts=["SELECT c.name, COUNT(o.order_id) FROM customers c LEFT JOIN orders o ON o.customer_id = c.customer_id GROUP BY c.customer_id"],
  explain="COUNT(*) counts rows, and Elena's group has one row (the NULL-padded one), so she gets 1 even though she has no orders."),
 dict(id="g-having", topic="GROUP BY", kind="rows", sql="""SELECT dept_id
FROM employees
GROUP BY dept_id
HAVING COUNT(*) > 2""",
  explain="HAVING filters whole groups after aggregation. Departments 1 and 2 have 4 employees each; 3 has 2 and the NULL group has 1."),
 dict(id="n-neq", topic="NULL", kind="cell", ask=dict(col=0, text="What single number does this return?"),
  sql="""SELECT COUNT(*)
FROM employees
WHERE dept_id <> 1""",
  alts=["SELECT COUNT(*) FROM employees", "SELECT COUNT(*) FROM employees WHERE dept_id IS NOT 1", "SELECT COUNT(*) FROM employees WHERE dept_id = 1"],
  explain="dept_id <> 1 is NULL (not TRUE) for Kiran, and WHERE keeps only TRUE. So Kiran is excluded: 6, not 7."),
 dict(id="n-student", topic="NULL", kind="rows", sql="""SELECT name
FROM students
WHERE major <> 'CS'""",
  explain="Ivy's major is NULL, and NULL <> 'CS' is NULL, so she is filtered out. Only the 4 Math/Physics students remain."),
 dict(id="n-avg", topic="NULL", kind="cell", ask=dict(col=0, text="What does AVG(grade) return?"),
  sql="""SELECT ROUND(AVG(grade), 2)
FROM enrollments""",
  alts=["SELECT ROUND(SUM(grade) * 1.0 / COUNT(*), 2) FROM enrollments", "SELECT ROUND(AVG(COALESCE(grade, 0)), 2) FROM enrollments", "SELECT ROUND(SUM(grade) * 1.0 / (COUNT(*) - 1), 2) FROM enrollments"],
  explain="Aggregates ignore NULL grades: AVG divides by the number of graded rows (15), not all 17."),
 dict(id="n-counts", topic="NULL", kind="cell", ask=dict(col=1, text="What is the second number (COUNT(dept_id))?"),
  sql="""SELECT COUNT(DISTINCT dept_id), COUNT(dept_id), COUNT(*)
FROM employees""",
  alts=["SELECT COUNT(*), 0, 0 FROM employees", "SELECT COUNT(DISTINCT dept_id), 0, 0 FROM employees"],
  explain="COUNT(dept_id) counts non-NULL values (10 of 11 rows). DISTINCT gives 3 and COUNT(*) gives 11."),
 dict(id="s-union", topic="SET", kind="rows", sql="""SELECT dept_id FROM employees
UNION
SELECT dept_id FROM departments""",
  explain="UNION removes duplicates, so you get the distinct values {1, 2, 3, 4, NULL}. UNION ALL would keep all 15."),
 dict(id="s-except", topic="SET", kind="rows", sql="""SELECT dept_id FROM departments
EXCEPT
SELECT dept_id FROM employees""",
  explain="EXCEPT keeps departments whose id never appears among employees: just Legal (4)."),
 dict(id="w-rank", topic="WINDOW", kind="cell", ask=dict(key="name", val="Dev", col=1, text="What RANK does Dev get?"),
  sql="""SELECT name, RANK() OVER (ORDER BY salary DESC) AS rnk
FROM employees
WHERE dept_id = 1""",
  alts=["SELECT name, DENSE_RANK() OVER (ORDER BY salary DESC) FROM employees WHERE dept_id = 1",
        "SELECT name, ROW_NUMBER() OVER (ORDER BY salary DESC, emp_id) FROM employees WHERE dept_id = 1"],
  explain="Ben and Chitra tie at rank 2, so RANK skips 3 and Dev is 4. DENSE_RANK would give 3."),
 dict(id="w-dense", topic="WINDOW", kind="cell", ask=dict(key="name", val="Dev", col=1, text="What DENSE_RANK does Dev get?"),
  sql="""SELECT name, DENSE_RANK() OVER (ORDER BY salary DESC) AS dr
FROM employees
WHERE dept_id = 1""",
  alts=["SELECT name, RANK() OVER (ORDER BY salary DESC) FROM employees WHERE dept_id = 1"],
  explain="DENSE_RANK never skips: Asha 1, Ben and Chitra 2, Dev 3."),
 dict(id="w-running", topic="WINDOW", kind="cell", ask=dict(key="txn_id", val=2, col=1, text="What balance does transaction 2 show?"),
  sql="""SELECT txn_id, SUM(amount) OVER (ORDER BY txn_date) AS bal
FROM transactions
WHERE account_id = 1""",
  alts=["SELECT txn_id, SUM(amount) OVER (ORDER BY txn_date ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) FROM transactions WHERE account_id = 1"],
  explain="The default frame is RANGE ... CURRENT ROW, which includes peers: transactions 2 and 3 share a date, so both see 500 - 120 - 30 = 350. A ROWS frame would give 380."),
 dict(id="w-lag", topic="WINDOW", kind="cell", ask=dict(key="sale_date", val="2024-03-01", col=1, text="What does LAG return on the first day?"),
  sql="""SELECT sale_date, LAG(amount) OVER (ORDER BY sale_date) AS prev
FROM daily_sales
WHERE region = 'North'""",
  extra=["0"],
  explain="There is no previous row for the first day, so LAG returns NULL (not 0) unless you pass a default."),
 dict(id="c-rec", topic="CTE", kind="rows", sql="""WITH RECURSIVE t(n) AS (
  SELECT 1
  UNION ALL
  SELECT n + 1 FROM t WHERE n < 5
)
SELECT * FROM t""",
  explain="The anchor yields 1; each step adds n+1 while n < 5, so 1..5: five rows."),
 dict(id="c-tree", topic="CTE", kind="rows", sql="""WITH RECURSIVE tree(id, depth) AS (
  SELECT category_id, 0 FROM categories WHERE parent_id IS NULL
  UNION ALL
  SELECT c.category_id, t.depth + 1
  FROM categories c JOIN tree t ON c.parent_id = t.id
)
SELECT * FROM tree WHERE depth = 2""",
  explain="Depth 0 is the root (Store), depth 1 has Electronics and Home, and depth 2 has their children: Phones, Laptops, Kitchen and Garden."),
 dict(id="m-unrated", topic="JOIN", kind="rows", sql="""SELECT m.title, AVG(r.stars)
FROM movies m
LEFT JOIN ratings r ON r.movie_id = m.movie_id
GROUP BY m.movie_id""",
  explain="One row per movie. Movies with no ratings still appear, with a NULL average, because of the LEFT JOIN."),
 dict(id="m-nevermet", topic="JOIN", kind="rows", sql="""SELECT u.handle
FROM users u
LEFT JOIN web_events e ON e.user_id = u.user_id
WHERE e.event_id IS NULL""",
  explain="Anti-join again: only pia never triggered an event."),

 dict(id="n-notin", topic="NULL", kind="rows", sql="""SELECT dept_name
FROM departments
WHERE dept_id NOT IN (SELECT dept_id FROM employees)""",
  alts=["SELECT dept_name FROM departments WHERE dept_id NOT IN (SELECT dept_id FROM employees WHERE dept_id IS NOT NULL)",
        "SELECT d.dept_name FROM departments d WHERE NOT EXISTS (SELECT 1 FROM employees e WHERE e.dept_id = d.dept_id)"],
  explain="Kiran's dept_id is NULL, so the sub-query's list contains NULL. x NOT IN (..., NULL) is never TRUE (it is FALSE or NULL), so Legal is not returned either. NOT EXISTS would return Legal."),
 dict(id="n-notexists", topic="NULL", kind="rows", sql="""SELECT c.name
FROM customers c
WHERE NOT EXISTS (
  SELECT 1 FROM orders o WHERE o.customer_id = c.customer_id
)""",
  alts=["SELECT c.name FROM customers c WHERE EXISTS (SELECT 1 FROM orders o WHERE o.customer_id = c.customer_id)"],
  explain="NOT EXISTS keeps customers with no order rows (Elena and Farah). It is the NULL-safe way to write an anti-join."),
 dict(id="sub-scalar-legal", topic="SUBQUERY", kind="cell", ask=dict(key="dept_name", val="Legal", col=1, text="What does Legal get in the `top` column?"),
  sql="""SELECT d.dept_name,
  (SELECT MAX(salary) FROM employees e WHERE e.dept_id = d.dept_id) AS top
FROM departments d""",
  extra=["0", "60000"],
  explain="A correlated scalar sub-query over zero rows yields NULL (MAX of nothing), not 0. It is re-run for every department."),
 dict(id="c-case-count", topic="CASE", kind="cell", ask=dict(col=0, text="What is not_dept1?"),
  sql="""SELECT COUNT(CASE WHEN dept_id <> 1 THEN 1 END) AS not_dept1,
       COUNT(CASE WHEN dept_id = 1 THEN 1 END) AS dept1
FROM employees""",
  alts=["SELECT COUNT(*) - COUNT(CASE WHEN dept_id = 1 THEN 1 END) FROM employees"],
  explain="CASE without ELSE gives NULL, and COUNT skips NULL. Kiran's dept_id is NULL, so he matches neither CASE: 6 + 4 = 10, not 11."),
 dict(id="n-nullif", topic="NULL", kind="cell", ask=dict(col=0, text="What does it return?"),
  sql="""SELECT ROUND(AVG(NULLIF(qty, 0)), 2)
FROM order_items""",
  alts=["SELECT ROUND(AVG(qty), 2) FROM order_items"],
  explain="NULLIF(qty, 0) turns the zero-quantity line into NULL, and AVG ignores NULL, so it divides by 14 lines instead of 15."),
 dict(id="d-modifier", topic="DATES", kind="cell", ask=dict(col=0, text="How many orders does it count?"),
  sql="""SELECT COUNT(*)
FROM orders
WHERE order_date >= date('2024-03-01', '-1 month')""",
  alts=["SELECT COUNT(*) FROM orders WHERE order_date >= '2024-03-01'"],
  explain="date('2024-03-01', '-1 month') is '2024-02-01', so both February and March orders count. Dates are ISO text, so comparing as strings works."),
 dict(id="s-like-glob", topic="TEXT", kind="cell", ask=dict(col=0, text="How many names match?"),
  sql="""SELECT COUNT(*)
FROM employees
WHERE name LIKE 'a%'""",
  alts=["SELECT COUNT(*) FROM employees WHERE name GLOB 'a*'"], extra=["11"],
  explain="In SQLite, LIKE is case-insensitive for ASCII, so 'a%' matches Asha. GLOB is case-sensitive and would match nothing."),
 dict(id="g-distinct-pairs", topic="DISTINCT", kind="rows", sql="""SELECT DISTINCT dept_id, manager_id
FROM employees""",
  alts=["SELECT dept_id, manager_id FROM employees"],
  explain="DISTINCT compares whole rows, and treats NULLs as equal to each other (unlike =). Employees sharing the same (dept, manager) pair collapse into one row."),
 dict(id="g-having-bool", topic="GROUP BY", kind="rows", sql="""SELECT customer_id
FROM orders
GROUP BY customer_id
HAVING SUM(status = 'cancelled') = 0""",
  explain="A comparison yields 1 or 0, so SUM counts cancelled orders per customer. HAVING keeps customers with none; it may use aggregates that are not in the SELECT list."),
 dict(id="o-null-multi", topic="ORDER BY", kind="cell", ask=dict(col=0, text="Which name comes back?"),
  sql="""SELECT name
FROM employees
ORDER BY dept_id DESC, salary ASC
LIMIT 1""",
  alts=["SELECT name FROM employees ORDER BY dept_id DESC NULLS FIRST, salary ASC LIMIT 1"], extra=["Isha", "Asha"],
  explain="NULL sorts lowest in SQLite, so DESC puts Kiran (NULL dept) last. Department 3 comes first, and salary ASC picks the lower paid of Isha and Jai: Jai."),
 dict(id="w-ntile", topic="WINDOW", kind="cell", ask=dict(key="name", val="Hari", col=1, text="Which bucket is Hari in?"),
  sql="""SELECT name, NTILE(3) OVER (ORDER BY emp_id) AS bucket
FROM employees""",
  extra=["3", "1"],
  explain="11 rows split into 3 buckets: sizes 4, 4, 3 (the larger buckets come first). Hari is row 8, the last row of bucket 2."),
 dict(id="w-percent-rank", topic="WINDOW", kind="cell", ask=dict(key="name", val="Gita", col=2, text="What PERCENT_RANK does Gita get?"),
  sql="""SELECT name, salary, PERCENT_RANK() OVER (ORDER BY salary) AS pr
FROM employees
WHERE dept_id = 2""",
  alts=["SELECT name, salary, CUME_DIST() OVER (ORDER BY salary) FROM employees WHERE dept_id = 2"], extra=["0.5"],
  explain="PERCENT_RANK = (rank - 1) / (rows - 1). Farid and Hari tie at rank 1, so Gita is rank 3: (3 - 1) / 3 = 0.67. CUME_DIST would give 0.75."),
 dict(id="w-lead-default", topic="WINDOW", kind="cell", ask=dict(key="txn_id", val=9, col=1, text="What does LEAD return for transaction 9?"),
  sql="""SELECT txn_id,
  LEAD(amount, 1, 0) OVER (PARTITION BY account_id ORDER BY txn_date, txn_id) AS nxt
FROM transactions""",
  extra=["NULL", "-400"],
  explain="Transaction 9 is the last row of account 1, so there is no next row and LEAD falls back to its third argument, 0 (NULL if you omit it)."),
 dict(id="w-ma-gap", topic="WINDOW", kind="cell", ask=dict(key="sale_date", val="2024-03-06", col=2, text="What is ma3 on 2024-03-06?"),
  sql="""SELECT sale_date, amount,
  ROUND(AVG(amount) OVER (ORDER BY sale_date ROWS BETWEEN 2 PRECEDING AND CURRENT ROW), 1) AS ma3
FROM daily_sales
WHERE region = 'South'""",
  alts=["SELECT sale_date, amount, ROUND(AVG(amount) OVER (ORDER BY sale_date ROWS BETWEEN 1 PRECEDING AND CURRENT ROW), 1) FROM daily_sales WHERE region = 'South'",
        "SELECT sale_date, amount, ROUND(AVG(amount) OVER (ORDER BY sale_date), 1) FROM daily_sales WHERE region = 'South'"],
  explain="ROWS counts rows, not days. South has no sales on 03-04 and 03-05, so the '3-day' window spans 03-02 to 03-06 (three rows)."),
 dict(id="c-path", topic="CTE", kind="cell", ask=dict(col=1, text="What path does category 10 get?"),
  sql="""WITH RECURSIVE p(id, path) AS (
  SELECT category_id, name FROM categories WHERE parent_id IS NULL
  UNION ALL
  SELECT c.category_id, p.path || ' > ' || c.name
  FROM categories c JOIN p ON c.parent_id = p.id
)
SELECT id, path FROM p WHERE id = 10""",
  extra=["Store > Kitchen > Cookware", "Home > Kitchen > Cookware", "Cookware"],
  explain="Each recursion level appends one more name to the path of its parent: Store, then Home, then Kitchen, then Cookware."),
 dict(id="s-intersect", topic="SET", kind="rows", sql="""SELECT customer_id FROM orders WHERE status = 'shipped'
INTERSECT
SELECT customer_id FROM orders WHERE status = 'cancelled'""",
  explain="INTERSECT keeps values present in both results. Only Bob (2) has both shipped and cancelled orders."),
]

BUGS = [
 dict(id="vanishing", title="The vanishing customers", topic="JOIN",
  story="List every customer with how many SHIPPED orders they have (0 if none). Right now customers with no shipped orders are missing.",
  broken="""SELECT c.name, COUNT(o.order_id) AS shipped
FROM customers c
LEFT JOIN orders o ON o.customer_id = c.customer_id
WHERE o.status = 'shipped'
GROUP BY c.customer_id""",
  solution="""SELECT c.name, COUNT(o.order_id) AS shipped
FROM customers c
LEFT JOIN orders o ON o.customer_id = c.customer_id AND o.status = 'shipped'
GROUP BY c.customer_id""", ordered=False,
  hint="Step through the WHERE stage: what happens to the NULL-padded rows?",
  lesson="A WHERE on the right table of a LEFT JOIN discards the NULL-padded rows. Put the filter in the ON clause to keep unmatched left rows."),
 dict(id="count-star", title="COUNT(*) lies", topic="GROUP BY",
  story="Show each department with its headcount. Legal has nobody, but it shows 1.",
  broken="""SELECT d.dept_name, COUNT(*) AS headcount
FROM departments d
LEFT JOIN employees e ON e.dept_id = d.dept_id
GROUP BY d.dept_id""",
  solution="""SELECT d.dept_name, COUNT(e.emp_id) AS headcount
FROM departments d
LEFT JOIN employees e ON e.dept_id = d.dept_id
GROUP BY d.dept_id""", ordered=False,
  hint="Look at Legal's group in the GROUP BY stage. How many rows does it contain, and how many are real?",
  lesson="COUNT(*) counts rows, including the NULL-padded one. COUNT(column) skips NULLs, so use a column from the optional side."),
 dict(id="fanout", title="Items, not orders", topic="JOIN",
  story="For each customer, count their non-cancelled orders. The numbers look too big.",
  broken="""SELECT c.name, COUNT(*) AS orders
FROM customers c
JOIN orders o ON o.customer_id = c.customer_id
JOIN order_items oi ON oi.order_id = o.order_id
WHERE o.status <> 'cancelled'
GROUP BY c.customer_id""",
  solution="""SELECT c.name, COUNT(DISTINCT o.order_id) AS orders
FROM customers c
JOIN orders o ON o.customer_id = c.customer_id
JOIN order_items oi ON oi.order_id = o.order_id
WHERE o.status <> 'cancelled'
GROUP BY c.customer_id""", ordered=False,
  hint="Check the second JOIN: do orders repeat in the output?",
  lesson="Joining to a one-to-many table repeats each order once per item (fan-out). COUNT(DISTINCT order_id) counts each order once."),
 dict(id="null-neq", title="NULLs hide an employee", topic="NULL",
  story="List employees who are NOT in department 1. Employees without a department count too, but one is missing.",
  broken="""SELECT name
FROM employees
WHERE dept_id <> 1""",
  solution="""SELECT name
FROM employees
WHERE dept_id IS NOT 1""", ordered=False,
  hint="What does dept_id <> 1 evaluate to for a NULL dept_id? Check the verdict column.",
  lesson="NULL <> 1 is NULL, not TRUE, so WHERE drops the row. IS NOT (or OR dept_id IS NULL) handles NULLs explicitly."),
 dict(id="agg-where", title="Aggregates can't live in WHERE", topic="GROUP BY",
  story="List the departments with more than 2 employees. The query errors out.",
  broken="""SELECT dept_id, COUNT(*)
FROM employees
WHERE COUNT(*) > 2
GROUP BY dept_id""",
  solution="""SELECT dept_id, COUNT(*)
FROM employees
GROUP BY dept_id
HAVING COUNT(*) > 2""", ordered=False,
  hint="WHERE runs before GROUP BY, so aggregates do not exist yet. Which clause runs after?",
  lesson="WHERE filters rows before grouping; HAVING filters groups after aggregation."),
 dict(id="tie-lost", title="The tie that wasn't", topic="WINDOW",
  story="Show the best grade in each course. If students tie for best, list them all. Course 101 is missing a student.",
  broken="""WITH r AS (
  SELECT course_id, student_id, grade,
    ROW_NUMBER() OVER (PARTITION BY course_id ORDER BY grade DESC) AS rn
  FROM enrollments
  WHERE grade IS NOT NULL
)
SELECT course_id, student_id, grade FROM r WHERE rn = 1""",
  solution="""WITH r AS (
  SELECT course_id, student_id, grade,
    RANK() OVER (PARTITION BY course_id ORDER BY grade DESC) AS rn
  FROM enrollments
  WHERE grade IS NOT NULL
)
SELECT course_id, student_id, grade FROM r WHERE rn = 1""", ordered=False,
  hint="Look at course 101 in the window stage. Do two rows share the top grade?",
  lesson="ROW_NUMBER never ties, so it arbitrarily keeps one. RANK gives tied rows the same rank, so rank = 1 keeps all of them."),
 dict(id="same-day", title="Same-day balance", topic="WINDOW",
  story="Show a running balance per account in the order transactions happened (txn_id). Two transactions on the same day show the same balance.",
  broken="""SELECT txn_id, account_id, txn_date, amount,
  SUM(amount) OVER (PARTITION BY account_id ORDER BY txn_date) AS balance
FROM transactions""",
  solution="""SELECT txn_id, account_id, txn_date, amount,
  SUM(amount) OVER (PARTITION BY account_id ORDER BY txn_date, txn_id) AS balance
FROM transactions""", ordered=False,
  hint="Step through the window: which rows are in the frame when two rows share a date?",
  lesson="With ORDER BY alone the default frame is RANGE, which includes all peers (rows tied on the sort key). Add a tiebreaker like txn_id."),
 dict(id="last-value", title="Last value isn't last", topic="WINDOW",
  story="Next to every employee, show the lowest-paid person in their department. It just repeats each employee's own name.",
  broken="""SELECT name, dept_id,
  LAST_VALUE(name) OVER (PARTITION BY dept_id ORDER BY salary DESC, emp_id) AS lowest
FROM employees
WHERE dept_id IS NOT NULL""",
  solution="""SELECT name, dept_id,
  LAST_VALUE(name) OVER (PARTITION BY dept_id ORDER BY salary DESC, emp_id
    ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING) AS lowest
FROM employees
WHERE dept_id IS NOT NULL""", ordered=False,
  hint="Watch the frame for each row: where does it end?",
  lesson="The default frame ends at CURRENT ROW, so LAST_VALUE sees only up to the current row. Extend the frame to UNBOUNDED FOLLOWING."),
 dict(id="union-eats", title="UNION ate my rows", topic="SET",
  story="List every dept_id from employees and from departments, keeping repeats (to count how often each appears).",
  broken="""SELECT dept_id FROM employees
UNION
SELECT dept_id FROM departments""",
  solution="""SELECT dept_id FROM employees
UNION ALL
SELECT dept_id FROM departments""", ordered=False,
  hint="Compare how many rows go in with how many come out.",
  lesson="UNION removes duplicates; UNION ALL keeps every row (and is cheaper)."),
 dict(id="root-gone", title="The root went missing", topic="JOIN",
  story="List each category with its parent's name. The root category (no parent) must still appear.",
  broken="""SELECT c.name, p.name AS parent
FROM categories c
JOIN categories p ON c.parent_id = p.category_id""",
  solution="""SELECT c.name, p.name AS parent
FROM categories c
LEFT JOIN categories p ON c.parent_id = p.category_id""", ordered=False,
  hint="Which row finds no partner in the right table?",
  lesson="The root's parent_id is NULL, which matches nothing, so an INNER JOIN drops it. LEFT JOIN keeps it with a NULL parent."),
 dict(id="avg-null", title="Ungraded counts as zero", topic="NULL",
  story="The class average should treat ungraded (NULL) enrollments as 0. The number is too high.",
  broken="""SELECT ROUND(AVG(grade), 2) AS class_avg
FROM enrollments""",
  solution="""SELECT ROUND(AVG(COALESCE(grade, 0)), 2) AS class_avg
FROM enrollments""", ordered=False,
  hint="AVG ignores NULLs. What should replace them?",
  lesson="Aggregates skip NULLs, so AVG divides by fewer rows. COALESCE(grade, 0) turns NULL into 0 before averaging."),
 dict(id="orphans", title="Which orders are orphans?", topic="JOIN",
  story="Find orders whose customer does not exist. The query returns customers instead.",
  broken="""SELECT o.order_id
FROM customers c
LEFT JOIN orders o ON o.customer_id = c.customer_id
WHERE o.order_id IS NULL""",
  solution="""SELECT o.order_id
FROM orders o
LEFT JOIN customers c ON c.customer_id = o.customer_id
WHERE c.customer_id IS NULL""", ordered=False,
  hint="Which side of the LEFT JOIN is preserved? You want to preserve the orders.",
  lesson="An anti-join keeps the preserved side's unmatched rows. Preserve the table you want to find orphans in."),

 dict(id="notin-null", title="NOT IN finds nothing", topic="NULL",
  story="List the departments that have no employees. The query returns no rows at all.",
  broken="""SELECT dept_name
FROM departments
WHERE dept_id NOT IN (SELECT dept_id FROM employees)""",
  solution="""SELECT d.dept_name
FROM departments d
WHERE NOT EXISTS (SELECT 1 FROM employees e WHERE e.dept_id = d.dept_id)""", ordered=False,
  hint="Look at the sub-query's list of dept_id values. Is everything in it a real number?",
  lesson="If the NOT IN list contains a NULL, x NOT IN (...) can never be TRUE. Use NOT EXISTS, or filter NULLs out of the sub-query."),
 dict(id="int-div", title="Averages that round down", topic="TYPES",
  story="Show the average quantity per order line. It says 2 but it should be about 2.93.",
  broken="""SELECT SUM(qty) / COUNT(*) AS avg_qty
FROM order_items""",
  solution="""SELECT SUM(qty) * 1.0 / COUNT(*) AS avg_qty
FROM order_items""", ordered=False,
  hint="What type does an integer divided by an integer have in SQLite?",
  lesson="Integer / integer is integer division in SQLite. Multiply by 1.0 (or CAST to REAL) before dividing, or just use AVG()."),
 dict(id="text-date", title="Dates are just text", topic="DATES",
  story="List orders placed after January 15, 2024. The query returns nothing.",
  broken="""SELECT order_id
FROM orders
WHERE order_date > '2024-1-15'""",
  solution="""SELECT order_id
FROM orders
WHERE order_date > '2024-01-15'""", ordered=False,
  hint="SQLite compares text character by character. Compare '2024-02-02' with '2024-1-15'.",
  lesson="Dates are TEXT compared character by character. Always zero-pad ISO dates (YYYY-MM-DD) so text order equals date order."),
 dict(id="page-two", title="Page two starts at the wrong row", topic="LIMIT",
  story="Page 2 with 3 rows per page (ordered by emp_id) should start at Dev, the 4th employee. It starts one row too early.",
  broken="""SELECT name
FROM employees
ORDER BY emp_id
LIMIT 3 OFFSET 2""",
  solution="""SELECT name
FROM employees
ORDER BY emp_id
LIMIT 3 OFFSET 3""", ordered=True,
  hint="OFFSET is the number of rows to skip, not the page number.",
  lesson="OFFSET = (page - 1) x page size. Page 2 of size 3 skips 3 rows."),
]

# ---- Predict kind 'join': show the OUTPUT, ask which join produced it ({J} is the join keyword)
JOINQ = [
 dict(id="jk-emp-dept", topic="JOIN", t="""SELECT e.name, d.dept_name
FROM employees e
{J} departments d ON e.dept_id = d.dept_id"""),
 dict(id="jk-dept-emp", topic="JOIN", t="""SELECT d.dept_name, e.name
FROM departments d
{J} employees e ON e.dept_id = d.dept_id"""),
 dict(id="jk-cust-orders", topic="JOIN", t="""SELECT c.name, o.order_id
FROM customers c
{J} orders o ON o.customer_id = c.customer_id"""),
 dict(id="jk-students", topic="JOIN", t="""SELECT s.name, e.course_id
FROM students s
{J} enrollments e ON e.student_id = s.student_id"""),
]
JOIN_KINDS = ["INNER", "LEFT", "RIGHT", "FULL"]

# ---- Predict kind 'blank': fill the ___ ; the right fragment reproduces the stored result, wrong ones are checked to differ
BLANKS = [
 dict(id="b-anti", topic="JOIN", kind="blank", ask="Fill the blank so the query lists departments that have NO employees.",
  sql="""SELECT d.dept_name
FROM departments d
LEFT JOIN employees e ON e.dept_id = d.dept_id
WHERE e.emp_id ___""", fill="IS NULL", wrong=["= NULL", "IS NOT NULL", "= 0"],
  explain="After a LEFT JOIN, unmatched departments carry NULL in every e column. `= NULL` is never true (it is NULL); only IS NULL tests for it."),
 dict(id="b-count", topic="GROUP BY", kind="blank", ask="Customers with no orders must show 0, not 1.",
  sql="""SELECT c.name, COUNT(___) AS orders
FROM customers c
LEFT JOIN orders o ON o.customer_id = c.customer_id
GROUP BY c.customer_id""", fill="o.order_id", wrong=["*", "c.customer_id", "1"],
  explain="COUNT(col) skips NULLs, so a customer whose only joined row is NULL-padded counts 0. COUNT(*), a constant, or the customer's own id count the padded row."),
 dict(id="b-unionall", topic="SET", kind="blank", ask="Keep every row, including duplicates.",
  sql="""SELECT dept_id FROM employees
___
SELECT dept_id FROM departments""", fill="UNION ALL", wrong=["UNION", "INTERSECT", "EXCEPT"],
  explain="UNION ALL just concatenates. UNION removes duplicates, INTERSECT keeps common values, EXCEPT subtracts."),
 dict(id="b-dense", topic="WINDOW", kind="blank", ask="Ties share a rank and no rank numbers are skipped.",
  sql="""SELECT name, salary,
  ___() OVER (ORDER BY salary DESC) AS rnk
FROM employees
WHERE dept_id = 1""", fill="DENSE_RANK", wrong=["RANK", "ROW_NUMBER", "PERCENT_RANK"],
  explain="RANK leaves gaps after ties (1,2,2,4), ROW_NUMBER never ties, and DENSE_RANK gives 1,2,2,3."),
 dict(id="b-having", topic="GROUP BY", kind="blank", ask="Keep only departments with more than 2 employees.",
  sql="""SELECT dept_id, COUNT(*)
FROM employees
GROUP BY dept_id
___ COUNT(*) > 2""", fill="HAVING", wrong=["WHERE", "AND", "ORDER BY"],
  explain="Aggregates exist only after GROUP BY, so the filter must be HAVING. WHERE runs earlier and cannot see COUNT(*)."),
 dict(id="b-frame", topic="WINDOW", kind="blank", ask="A strict running balance: each transaction adds only itself, even when dates tie.",
  sql="""SELECT txn_id, SUM(amount) OVER (ORDER BY txn_date ___) AS bal
FROM transactions
WHERE account_id = 1""", fill="ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW",
  wrong=["RANGE BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW", "ROWS BETWEEN CURRENT ROW AND UNBOUNDED FOLLOWING", "ROWS BETWEEN 1 PRECEDING AND CURRENT ROW"],
  explain="ROWS counts physical rows. RANGE (the default) also includes peers tied on the sort key, so same-day transactions would share a balance."),
 dict(id="b-isnot", topic="NULL", kind="blank", ask="Everyone NOT in department 1, including people with no department.",
  sql="""SELECT name
FROM employees
WHERE dept_id ___ 1""", fill="IS NOT", wrong=["<>", "!=", "IS"],
  explain="<> and != yield NULL when dept_id is NULL, so Kiran is filtered out. IS NOT compares NULL-safely."),
 dict(id="b-coalesce", topic="NULL", kind="blank", ask="Show 0 instead of NULL for ungraded enrollments.",
  sql="""SELECT student_id, ___(grade, 0) AS g
FROM enrollments""", fill="COALESCE", wrong=["IFNULL", "NULLIF", "ABS", "MAX"],
  explain="COALESCE returns its first non-NULL argument. (IFNULL does the same with two arguments, so the quiz drops it as an option; NULLIF does the opposite.)"),
]

# ---- Match the output: only the target table is shown; write any query that produces it
MATCH = [
 dict(id="boss", title="Who reports to whom", topic="JOIN", ordered=False,
  solution="""SELECT e.name, m.name AS boss
FROM employees e
LEFT JOIN employees m ON e.manager_id = m.emp_id""",
  hint="Two copies of the same table. Who has no boss, and how is that row kept?",
  lesson="A self join with LEFT JOIN keeps the top of the hierarchy (NULL boss)."),
 dict(id="busy", title="The busy departments", topic="GROUP BY", ordered=True,
  solution="""SELECT d.dept_name, COUNT(*) AS headcount
FROM departments d
JOIN employees e ON e.dept_id = d.dept_id
GROUP BY d.dept_id
HAVING COUNT(*) >= 3
ORDER BY headcount DESC, d.dept_name""",
  hint="Group, filter the groups, then sort by two keys. Order matters here.",
  lesson="JOIN -> GROUP BY -> HAVING -> ORDER BY: the HAVING filters groups, the ORDER BY needs a tiebreaker."),
 dict(id="top-earners", title="Top earner per department", topic="SUBQUERY", ordered=False,
  solution="""SELECT d.dept_name, e.name, e.salary
FROM employees e
JOIN departments d ON d.dept_id = e.dept_id
WHERE e.salary = (SELECT MAX(x.salary) FROM employees x WHERE x.dept_id = e.dept_id)""",
  hint="Each employee is compared with the maximum of their own department. A correlated sub-query or a window function both work.",
  lesson="A correlated sub-query re-runs per row; RANK() OVER (PARTITION BY dept_id ORDER BY salary DESC) = 1 is the window-function alternative."),
 dict(id="never-ordered", title="Customers who never ordered", topic="JOIN", ordered=False,
  solution="""SELECT c.name
FROM customers c
LEFT JOIN orders o ON o.customer_id = c.customer_id
WHERE o.order_id IS NULL""",
  hint="Keep all customers, then keep only the ones that found no order.",
  lesson="Anti-join: LEFT JOIN + IS NULL on the other side's key (or NOT EXISTS)."),
 dict(id="price-bands", title="Price bands", topic="CASE", ordered=False,
  solution="""SELECT name, price,
  CASE WHEN price < 10 THEN 'cheap' WHEN price < 500 THEN 'mid' ELSE 'premium' END AS band
FROM products""",
  hint="Look at the prices: where are the cut-offs between the three labels?",
  lesson="CASE tests conditions top to bottom and returns the first match, so order the ranges from low to high."),
 dict(id="payroll", title="Running payroll", topic="WINDOW", ordered=False,
  solution="""SELECT name, salary, SUM(salary) OVER (ORDER BY emp_id) AS running
FROM employees""",
  hint="A column that accumulates down the table, in employee order.",
  lesson="SUM(...) OVER (ORDER BY ...) is a running total; every input row survives."),
 dict(id="both-statuses", title="Shipped and cancelled", topic="SET", ordered=False,
  solution="""SELECT customer_id FROM orders WHERE status = 'shipped'
INTERSECT
SELECT customer_id FROM orders WHERE status = 'cancelled'""",
  hint="Customers present in two different result sets at once.",
  lesson="INTERSECT keeps only values found in both queries (duplicates removed)."),
 dict(id="months", title="Orders per month", topic="GROUP BY", ordered=False,
  solution="""SELECT substr(order_date, 1, 7) AS month, COUNT(*) AS orders
FROM orders
GROUP BY month""",
  hint="Dates are text like 2024-03-18. How do you cut out the year-month part?",
  lesson="substr() on ISO dates gives the month; grouping by the alias is allowed in SQLite."),
 dict(id="org-paths", title="The org chart paths", topic="CTE", ordered=False,
  solution="""WITH RECURSIVE t(emp_id, name, depth, path) AS (
  SELECT emp_id, name, 0, name FROM employees WHERE manager_id IS NULL
  UNION ALL
  SELECT e.emp_id, e.name, t.depth + 1, t.path || ' > ' || e.name
  FROM employees e JOIN t ON e.manager_id = t.emp_id
)
SELECT name, depth, path FROM t""",
  hint="Start at the person with no manager and walk down, building the path string as you go.",
  lesson="A recursive CTE: the anchor is the root, the recursive step appends one level per iteration."),
]
MATCH_BY_ID = {m["id"]: m for m in MATCH}

BUG_BY_ID = {b["id"]: b for b in BUGS}
PRED_BY_ID = {p["id"]: p for p in PREDICT}
PRED_BY_ID.update({b["id"]: b for b in BLANKS})
PRED_BY_ID.update({j["id"]: dict(j, kind="join") for j in JOINQ})
ASK_ROWS = "How many rows does this query return?"


def _fmt(v):
    if v is None:
        return "NULL"
    if isinstance(v, float):
        return str(int(v)) if v == int(v) else f"{v:.2f}".rstrip("0").rstrip(".")
    return str(v)


def _pick(rows, cols, ask):
    if not rows:
        return None
    if ask.get("key") is not None:
        ki = next((i for i, c in enumerate(cols) if c.lower() == ask["key"].lower()), 0)
        for r in rows:
            if r[ki] == ask["val"]:
                return r[ask["col"]]
        return None
    return rows[0][ask["col"]]


def _variants(sql):
    """Plausible-but-wrong versions of a query (other join kinds, no WHERE/HAVING, UNION <-> UNION ALL)."""
    out = []
    try:
        S = Sql(sql)
        st = S.parse()
        for core in st.body.parts:
            if core.opaque or not core.from_:
                continue
            for j in core.from_.joins:
                if j.comma or j.natural:
                    continue
                for k in ("INNER", "LEFT", "RIGHT", "FULL"):
                    if k != (j.kw or "INNER"):
                        a, b = S.toks[j.kw_lo].a, S.toks[j.kw_hi - 1].b
                        out.append(sql[:a] + f"{k} JOIN" + sql[b:])
            for key, rng in (("WHERE", core.where), ("HAVING", core.having)):
                if rng:
                    a = S.toks[core.kw[key]].a
                    b = S.toks[rng[1] - 1].b
                    out.append(sql[:a] + sql[b:])
        for opname, ti in st.body.ops:
            a = S.toks[ti].a
            b = S.toks[ti + (1 if opname == "UNION ALL" else 0)].b
            if opname == "UNION":
                out.append(sql[:a] + "UNION ALL" + sql[b:])
            elif opname == "UNION ALL":
                out.append(sql[:a] + "UNION" + sql[b:])
    except Exception:
        pass
    return out


def _rowset(rows):
    return sorted(repr(tuple(round(v, 6) if isinstance(v, float) else v for v in r)) for r in rows)


def _build_blank(db, entry, seed):
    rng = random.Random(f"{entry['id']}:{seed}")
    full = entry["sql"].replace("___", entry["fill"])
    _, good = db.q(full)
    want = _rowset(good)
    keep = []
    for w in entry["wrong"]:
        try:
            _, r = db.q(entry["sql"].replace("___", w))
            if _rowset(r) == want:
                continue                          # accidentally equivalent: not a fair distractor
        except QueryError:
            pass                                  # an error is certainly different
        keep.append(w)
    options = keep[:3] + [entry["fill"]]
    rng.shuffle(options)
    return dict(id=entry["id"], topic=entry["topic"], kind="blank", sql=entry["sql"], ask=entry["ask"], options=options,
                answer=entry["fill"], explain=entry["explain"], fullSql=full, seed=seed)


def _build_join(db, entry, seed):
    rng = random.Random(f"{entry['id']}:{seed}")
    kind = rng.choice(JOIN_KINDS)
    counts = {}
    for k in JOIN_KINDS:
        counts[k] = len(db.q(entry["t"].replace("{J}", k + " JOIN"))[1])
    cols, rows = db.q(entry["t"].replace("{J}", kind + " JOIN"))
    clue = {"INNER": "no NULL padding anywhere: only matching pairs survive",
            "LEFT": "NULLs appear only on the right side (unmatched left rows were kept)",
            "RIGHT": "NULLs appear only on the left side (unmatched right rows were kept)",
            "FULL": "NULLs appear on both sides (unmatched rows from each side were kept)"}[kind]
    explain = (f"{kind} JOIN: {clue}. For these tables INNER gives {counts['INNER']} rows, LEFT {counts['LEFT']}, "
               f"RIGHT {counts['RIGHT']}, FULL {counts['FULL']}; this output has {len(rows)}.")
    options = [k + " JOIN" for k in JOIN_KINDS]
    rng.shuffle(options)
    return dict(id=entry["id"], topic=entry["topic"], kind="join", sql=entry["t"].replace("{J}", "___ JOIN"),
                ask="Which join produced this output?", options=options, answer=kind + " JOIN", explain=explain,
                table={"cols": cols, "rows": [list(r) for r in rows[:14]], "total": len(rows)},
                fullSql=entry["t"].replace("{J}", kind + " JOIN"), seed=seed)


def build_question(db, entry, seed):
    if entry.get("kind") == "blank":
        return _build_blank(db, entry, seed)
    if entry.get("kind") == "join":
        return _build_join(db, entry, seed)
    rng = random.Random(f"{entry['id']}:{seed}")
    cols, rows = db.q(entry["sql"])
    if entry["kind"] == "rows":
        correct = len(rows)
        ask = ASK_ROWS
        cands = []
        for v in _variants(entry["sql"]):
            try:
                cands.append(len(db.q(v)[1]))
            except QueryError:
                pass
        for alt in entry.get("alts", []):
            try:
                cands.append(len(db.q(alt)[1]))
            except QueryError:
                pass
        nums = [c for c in dict.fromkeys(cands) if c != correct]
        for d in (1, -1, 2, -2, 3):
            if len(nums) < 5 and correct + d >= 0 and correct + d not in nums:
                nums.append(correct + d)
        distract = [str(n) for n in nums]
        answer = str(correct)
    else:
        a = entry["ask"]
        ask = a["text"]
        correct = _pick(rows, cols, a)
        answer = _fmt(correct)
        pool = []
        for alt in entry.get("alts", []):
            try:
                c2, r2 = db.q(alt)
                pool.append(_fmt(_pick(r2, c2, a)))
            except QueryError:
                pass
        pool += list(entry.get("extra", []))
        if a.get("key") is not None:
            ci = a["col"]
            pool += [_fmt(r[ci]) for r in rows]
        if isinstance(correct, (int, float)):
            for d in (1, -1, 2):
                pool.append(_fmt(correct + d))
        distract = [p for p in dict.fromkeys(pool) if p != answer]
    distract = distract[:3]                      # keep the most pedagogical distractors (they come first)
    options = distract + [answer]
    rng.shuffle(options)
    return dict(id=entry["id"], topic=entry["topic"], kind=entry["kind"], sql=entry["sql"], ask=ask, options=options,
                answer=answer, explain=entry["explain"], seed=seed)


def new_question(db, seed, exclude=()):
    rng = random.Random(seed)
    ids = [i for i in PRED_BY_ID if i not in set(exclude)] or list(PRED_BY_ID)
    q = build_question(db, PRED_BY_ID[rng.choice(ids)], seed)
    for k in ("answer", "explain", "fullSql"):
        q.pop(k, None)
    return q


def answer_question(db, qid, seed, choice):
    q = build_question(db, PRED_BY_ID[qid], int(seed))
    return {"correct": choice == q["answer"], "answer": q["answer"], "explain": q["explain"], "fullSql": q.get("fullSql")}
