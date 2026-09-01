CREATE TABLE companies (
  id BIGINT PRIMARY KEY,
  name VARCHAR(120) NOT NULL UNIQUE,
  region VARCHAR(40) NOT NULL,
  description TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
COMMENT ON TABLE companies IS '集团经营主体';
COMMENT ON COLUMN companies.name IS '企业名称';

CREATE TABLE suppliers (
  id BIGINT PRIMARY KEY,
  company_id BIGINT NOT NULL REFERENCES companies(id),
  supplier_name VARCHAR(120) NOT NULL UNIQUE,
  risk_level VARCHAR(20) NOT NULL,
  contact_phone VARCHAR(30),
  api_token VARCHAR(120)
);

CREATE TABLE products (
  id BIGINT PRIMARY KEY,
  product_code VARCHAR(40) NOT NULL UNIQUE,
  product_name VARCHAR(120) NOT NULL,
  category VARCHAR(80) NOT NULL,
  unit_price NUMERIC(14,2) NOT NULL,
  metadata JSONB,
  description TEXT
);

CREATE TABLE customers (
  id BIGINT PRIMARY KEY,
  customer_code VARCHAR(40) NOT NULL UNIQUE,
  customer_name VARCHAR(120) NOT NULL,
  region VARCHAR(40) NOT NULL,
  email VARCHAR(160),
  mobile VARCHAR(30),
  password VARCHAR(120),
  registered_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE orders (
  id BIGINT PRIMARY KEY,
  order_no VARCHAR(40) NOT NULL UNIQUE,
  customer_id BIGINT NOT NULL REFERENCES customers(id),
  order_date DATE NOT NULL,
  status VARCHAR(30) NOT NULL,
  region VARCHAR(40) NOT NULL,
  sales_amount NUMERIC(16,2) NOT NULL
);

CREATE TABLE order_items (
  id BIGINT PRIMARY KEY,
  order_id BIGINT NOT NULL REFERENCES orders(id),
  product_id BIGINT NOT NULL REFERENCES products(id),
  quantity INTEGER NOT NULL,
  unit_price NUMERIC(14,2) NOT NULL,
  discount_rate NUMERIC(6,4),
  UNIQUE(order_id, product_id)
);

CREATE TABLE sales_targets (
  id BIGINT PRIMARY KEY,
  target_year INTEGER NOT NULL,
  target_month INTEGER NOT NULL,
  region VARCHAR(40) NOT NULL,
  target_amount NUMERIC(16,2) NOT NULL,
  UNIQUE(target_year, target_month, region)
);

CREATE TABLE risk_events (
  id BIGINT PRIMARY KEY,
  supplier_id BIGINT NOT NULL REFERENCES suppliers(id),
  event_date DATE NOT NULL,
  risk_type VARCHAR(80) NOT NULL,
  severity INTEGER NOT NULL,
  detail TEXT
);

CREATE TABLE activity_log (
  event_time TIMESTAMPTZ NOT NULL,
  event_type VARCHAR(80) NOT NULL,
  payload JSONB
);

INSERT INTO companies VALUES
  (1, '国联数字科技有限公司', '华东', '负责集团数字化与人工智能产品', '2025-01-01T08:00:00+08:00'),
  (2, '国联供应链有限公司', '华东', '负责集团供应链业务', '2025-02-01T08:00:00+08:00');
INSERT INTO suppliers VALUES
  (1, 2, '华星核心器件', 'high', '13800138000', 'fixture-secret-token'),
  (2, 2, '江南云服务', 'low', '13900139000', NULL);
INSERT INTO products VALUES
  (1, 'NX1', 'NexusOne', '企业智能一体机', 100000.00, '{"edition":"enterprise","language":"zh-CN"}', '面向企业知识管理与智能问答'),
  (2, 'CS-KB', '传神智库', '知识平台', 180000.00, '{"edition":"group"}', '组织级知识底座'),
  (3, 'CS-AGENT', '传神智能体', '智能体平台', 150000.00, NULL, '面向业务应用场景');
INSERT INTO customers VALUES
  (1, 'C001', '华东制造集团', '华东', 'buyer@example.com', '13811112222', 'never-return-this', '2025-01-15T09:00:00+08:00'),
  (2, 'C002', '江北能源集团', '华北', 'energy@example.com', '13933334444', 'never-return-this', '2025-03-20T10:00:00+08:00'),
  (3, 'C003', '南方交通集团', '华南', NULL, NULL, 'never-return-this', '2026-01-08T11:00:00+08:00'),
  (4, 'C004', '尚未成交客户', '华东', 'lead@example.com', '13755556666', 'never-return-this', '2026-06-01T11:00:00+08:00');
INSERT INTO orders VALUES
  (1, 'O20250101', 1, '2025-03-10', 'completed', '华东', 200000.00),
  (2, 'O20260101', 1, '2026-01-12', 'completed', '华东', 300000.00),
  (3, 'O20260201', 2, '2026-02-18', 'completed', '华北', 360000.00),
  (4, 'O20260301', 3, '2026-03-09', 'completed', '华南', 250000.00),
  (5, 'O20260401', 1, '2026-04-22', 'cancelled', '华东', 150000.00);
INSERT INTO order_items VALUES
  (1, 1, 1, 2, 100000.00, 0),
  (2, 2, 1, 3, 100000.00, 0),
  (3, 3, 2, 2, 180000.00, 0),
  (4, 4, 1, 1, 100000.00, 0),
  (5, 4, 3, 1, 150000.00, 0),
  (6, 5, 3, 1, 150000.00, 0);
INSERT INTO sales_targets VALUES
  (1, 2026, 1, '华东', 400000.00),
  (2, 2026, 2, '华北', 400000.00),
  (3, 2026, 3, '华南', 300000.00),
  (4, 2026, 4, '华东', 300000.00);
INSERT INTO risk_events VALUES
  (1, 1, '2026-01-20', '交付延期', 4, '关键器件交付延期三天'),
  (2, 1, '2026-04-10', '质量异常', 5, '抽检发现批次质量异常'),
  (3, 2, '2026-05-01', '服务波动', 2, NULL);
INSERT INTO activity_log VALUES
  ('2026-08-01T12:00:00+08:00', 'login', '{"user":"fixture"}'),
  ('2026-08-01T12:05:00+08:00', 'query', '{"module":"knowledge"}');

CREATE VIEW completed_orders AS
SELECT id, order_no, customer_id, order_date, region, sales_amount
FROM orders WHERE status = 'completed';

CREATE ROLE structured_reader LOGIN PASSWORD 'structured_fixture_password';
GRANT CONNECT ON DATABASE structured_fixture TO structured_reader;
GRANT USAGE ON SCHEMA public TO structured_reader;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO structured_reader;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO structured_reader;
