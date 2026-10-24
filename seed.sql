DROP TABLE IF EXISTS order_items; DROP TABLE IF EXISTS orders; DROP TABLE IF EXISTS products;
DROP TABLE IF EXISTS customers; DROP TABLE IF EXISTS employees; DROP TABLE IF EXISTS departments;

CREATE TABLE departments (dept_id INTEGER PRIMARY KEY, dept_name TEXT);
CREATE TABLE employees (emp_id INTEGER PRIMARY KEY, name TEXT, dept_id INTEGER, manager_id INTEGER, salary INTEGER, hire_date TEXT);
CREATE TABLE customers (customer_id INTEGER PRIMARY KEY, name TEXT, country TEXT, signup_date TEXT);
CREATE TABLE products (product_id INTEGER PRIMARY KEY, name TEXT, category TEXT, price REAL);
CREATE TABLE orders (order_id INTEGER PRIMARY KEY, customer_id INTEGER, order_date TEXT, status TEXT);
CREATE TABLE order_items (order_id INTEGER, product_id INTEGER, qty INTEGER);

INSERT INTO departments VALUES (1,'Engineering'),(2,'Sales'),(3,'Marketing'),(4,'Legal'); -- Legal has no employees

INSERT INTO employees VALUES
(1,'Asha',1,NULL,200000,'2019-01-15'),
(2,'Ben',1,1,150000,'2020-03-01'),
(3,'Chitra',1,1,150000,'2021-07-20'),   -- tie with Ben
(4,'Dev',1,2,120000,'2022-02-10'),
(5,'Esha',2,1,130000,'2019-11-05'),
(6,'Farid',2,5,90000,'2021-01-12'),
(7,'Gita',2,5,95000,'2022-06-30'),
(8,'Hari',2,5,90000,'2023-04-18'),
(9,'Isha',3,1,110000,'2020-09-09'),
(10,'Jai',3,9,70000,'2023-01-02'),
(11,'Kiran',NULL,1,60000,'2024-02-14');  -- no department

INSERT INTO customers VALUES
(1,'Alice','US','2023-01-05'),(2,'Bob','UK','2023-02-11'),(3,'Carla','US','2023-03-20'),
(4,'Dmitri','DE','2023-05-02'),(5,'Elena','DE','2023-06-15'),(6,'Farah','IN','2023-07-01'); -- 5,6 may have no orders

INSERT INTO products VALUES
(1,'Laptop','Electronics',1200),(2,'Phone','Electronics',800),(3,'Desk','Furniture',300),
(4,'Chair','Furniture',150),(5,'Notebook','Stationery',5),(6,'Pen','Stationery',2); -- Pen never sold

INSERT INTO orders VALUES
(1,1,'2024-01-03','shipped'),(2,1,'2024-01-20','shipped'),(3,2,'2024-01-22','cancelled'),
(4,3,'2024-02-02','shipped'),(5,1,'2024-02-14','shipped'),(6,2,'2024-02-20','shipped'),
(7,4,'2024-03-01','shipped'),(8,3,'2024-03-05','shipped'),(9,1,'2024-03-18','shipped'),
(10,2,'2024-03-25','shipped'),(11,99,'2024-03-28','shipped'); -- customer 99 doesn't exist (orphan)

INSERT INTO order_items VALUES
(1,1,1),(1,5,3),(2,3,1),(2,4,2),(3,2,1),(4,2,1),(4,6,0),(5,5,10),(6,1,1),
(7,4,4),(8,3,2),(9,2,2),(9,5,5),(10,4,1),(11,6,10);
