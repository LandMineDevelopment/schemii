CREATE TABLE fixture_version (version integer PRIMARY KEY CHECK (version = 1));
INSERT INTO fixture_version VALUES (1);
CREATE TABLE customers (id integer PRIMARY KEY, name text NOT NULL, region text NOT NULL CHECK (region IN ('east','west')));
INSERT INTO customers SELECT n, 'Customer ' || lpad(n::text, 3, '0'), CASE WHEN n % 2 = 0 THEN 'east' ELSE 'west' END FROM generate_series(1,32) n;
CREATE TABLE orders (id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY, customer_id integer NOT NULL REFERENCES customers(id), ordered_on date NOT NULL, status text NOT NULL CHECK(status IN ('pending','paid','cancelled')), amount numeric(10,2) NOT NULL CHECK(amount >= 0), notes text);
INSERT INTO orders(customer_id,ordered_on,status,amount,notes)
SELECT (n % 32)+1, DATE '2025-01-01' + (n % 90), (ARRAY['pending','paid','cancelled'])[(n % 3)+1], ((n * 137) % 100000)::numeric / 100, CASE WHEN n % 7 = 0 THEN NULL ELSE 'Fixture order ' || n END FROM generate_series(1,513) n;
CREATE VIEW customer_totals AS SELECT c.id,c.name,c.region,count(o.id) AS order_count,coalesce(sum(o.amount),0) AS total FROM customers c LEFT JOIN orders o ON o.customer_id=c.id GROUP BY c.id,c.name,c.region;
COMMENT ON TABLE fixture_version IS 'Schemii portable QA baseline v1';
