SET NAMES utf8mb4 COLLATE utf8mb4_0900_ai_ci;

CREATE TABLE companies (
  id BIGINT PRIMARY KEY,
  name VARCHAR(120) NOT NULL UNIQUE COMMENT '企业名称',
  region VARCHAR(40) NOT NULL,
  description TEXT,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
) COMMENT='集团经营主体';

CREATE TABLE departments (
  id BIGINT PRIMARY KEY,
  company_id BIGINT NOT NULL,
  department_code VARCHAR(40) NOT NULL UNIQUE,
  department_name VARCHAR(120) NOT NULL,
  parent_id BIGINT,
  cost_center VARCHAR(40),
  CONSTRAINT fk_department_company FOREIGN KEY (company_id) REFERENCES companies(id),
  CONSTRAINT fk_department_parent FOREIGN KEY (parent_id) REFERENCES departments(id)
);

CREATE TABLE suppliers (
  id BIGINT PRIMARY KEY,
  company_id BIGINT NOT NULL,
  supplier_name VARCHAR(120) NOT NULL UNIQUE,
  risk_level VARCHAR(20) NOT NULL,
  contact_phone VARCHAR(30),
  api_token VARCHAR(120),
  CONSTRAINT fk_supplier_company FOREIGN KEY (company_id) REFERENCES companies(id)
);

CREATE TABLE products (
  id BIGINT PRIMARY KEY,
  product_code VARCHAR(40) NOT NULL UNIQUE,
  product_name VARCHAR(120) NOT NULL,
  category VARCHAR(80) NOT NULL,
  unit_price DECIMAL(14,2) NOT NULL,
  metadata JSON,
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
  registered_at DATETIME NOT NULL
);

CREATE TABLE contracts (
  id BIGINT PRIMARY KEY,
  contract_no VARCHAR(40) NOT NULL UNIQUE,
  company_id BIGINT NOT NULL,
  supplier_id BIGINT NOT NULL,
  subject VARCHAR(200) NOT NULL,
  signed_at DATE NOT NULL,
  expires_at DATE NOT NULL,
  amount DECIMAL(16,2) NOT NULL,
  status VARCHAR(30) NOT NULL,
  CONSTRAINT fk_contract_company FOREIGN KEY (company_id) REFERENCES companies(id),
  CONSTRAINT fk_contract_supplier FOREIGN KEY (supplier_id) REFERENCES suppliers(id)
);

CREATE TABLE projects (
  id BIGINT PRIMARY KEY,
  project_code VARCHAR(40) NOT NULL UNIQUE,
  project_name VARCHAR(160) NOT NULL,
  company_id BIGINT NOT NULL,
  owner_department_id BIGINT NOT NULL,
  start_date DATE NOT NULL,
  end_date DATE,
  budget DECIMAL(16,2) NOT NULL,
  status VARCHAR(30) NOT NULL,
  CONSTRAINT fk_project_company FOREIGN KEY (company_id) REFERENCES companies(id),
  CONSTRAINT fk_project_department FOREIGN KEY (owner_department_id) REFERENCES departments(id)
);

CREATE TABLE project_members (
  project_id BIGINT NOT NULL,
  department_id BIGINT NOT NULL,
  member_name VARCHAR(80) NOT NULL,
  member_role VARCHAR(80) NOT NULL,
  joined_at DATE NOT NULL,
  PRIMARY KEY (project_id, department_id, member_name),
  CONSTRAINT fk_member_project FOREIGN KEY (project_id) REFERENCES projects(id),
  CONSTRAINT fk_member_department FOREIGN KEY (department_id) REFERENCES departments(id)
);

CREATE TABLE indicator_definitions (
  id BIGINT PRIMARY KEY,
  indicator_code VARCHAR(60) NOT NULL UNIQUE,
  indicator_name VARCHAR(120) NOT NULL,
  definition TEXT NOT NULL,
  formula TEXT NOT NULL,
  owner_department VARCHAR(120) NOT NULL,
  effective_date DATE NOT NULL
);

CREATE TABLE orders (
  id BIGINT PRIMARY KEY,
  order_no VARCHAR(40) NOT NULL UNIQUE,
  customer_id BIGINT NOT NULL,
  order_date DATE NOT NULL,
  status VARCHAR(30) NOT NULL,
  region VARCHAR(40) NOT NULL,
  sales_amount DECIMAL(16,2) NOT NULL,
  CONSTRAINT fk_order_customer FOREIGN KEY (customer_id) REFERENCES customers(id)
);

CREATE TABLE order_items (
  id BIGINT PRIMARY KEY,
  order_id BIGINT NOT NULL,
  product_id BIGINT NOT NULL,
  quantity INT NOT NULL,
  unit_price DECIMAL(14,2) NOT NULL,
  discount_rate DECIMAL(6,4),
  UNIQUE KEY uq_order_product (order_id, product_id),
  CONSTRAINT fk_item_order FOREIGN KEY (order_id) REFERENCES orders(id),
  CONSTRAINT fk_item_product FOREIGN KEY (product_id) REFERENCES products(id)
);

CREATE TABLE sales_targets (
  id BIGINT PRIMARY KEY,
  target_year INT NOT NULL,
  target_month INT NOT NULL,
  region VARCHAR(40) NOT NULL,
  target_amount DECIMAL(16,2) NOT NULL,
  UNIQUE KEY uq_target_period_region (target_year, target_month, region)
);

CREATE TABLE risk_events (
  id BIGINT PRIMARY KEY,
  supplier_id BIGINT NOT NULL,
  event_date DATE NOT NULL,
  risk_type VARCHAR(80) NOT NULL,
  severity INT NOT NULL,
  detail TEXT,
  CONSTRAINT fk_risk_supplier FOREIGN KEY (supplier_id) REFERENCES suppliers(id)
);

CREATE TABLE activity_log (
  event_time DATETIME NOT NULL,
  event_type VARCHAR(80) NOT NULL,
  payload JSON
);

INSERT INTO companies VALUES
  (1, '国联数字科技有限公司', '华东', '负责集团数字化与人工智能产品', '2025-01-01 08:00:00'),
  (2, '国联供应链有限公司', '华东', '负责集团供应链业务', '2025-02-01 08:00:00');
INSERT INTO departments VALUES
  (1, 1, 'GLDT-AI', '人工智能中心', NULL, 'CC-AI-001'),
  (2, 1, 'GLDT-PRODUCT', '产品研发部', NULL, 'CC-PD-001'),
  (3, 2, 'GLSC-PROCURE', '采购管理部', NULL, 'CC-PC-001'),
  (4, 2, 'GLSC-RISK', '风险管理部', NULL, 'CC-RM-001');
INSERT INTO suppliers VALUES
  (1, 2, '华星核心器件', 'high', '13800138000', 'fixture-secret-token'),
  (2, 2, '江南云服务', 'low', '13900139000', NULL);
INSERT INTO products VALUES
  (1, 'NX1', 'NexusOne', '企业智能一体机', 100000.00, JSON_OBJECT('edition','enterprise','language','zh-CN'), '面向企业知识管理与智能问答'),
  (2, 'CS-KB', '传神智库', '知识平台', 180000.00, JSON_OBJECT('edition','group'), '组织级知识底座'),
  (3, 'CS-AGENT', '传神智能体', '智能体平台', 150000.00, NULL, '面向业务应用场景');
INSERT INTO customers VALUES
  (1, 'C001', '华东制造集团', '华东', 'buyer@example.com', '13811112222', 'never-return-this', '2025-01-15 09:00:00'),
  (2, 'C002', '江北能源集团', '华北', 'energy@example.com', '13933334444', 'never-return-this', '2025-03-20 10:00:00'),
  (3, 'C003', '南方交通集团', '华南', NULL, NULL, 'never-return-this', '2026-01-08 11:00:00'),
  (4, 'C004', '尚未成交客户', '华东', 'lead@example.com', '13755556666', 'never-return-this', '2026-06-01 11:00:00');
INSERT INTO contracts VALUES
  (1, 'GL-SC-2026-008', 2, 1, 'NexusOne 关键器件采购框架协议', '2026-01-01', '2026-12-31', 680000.00, 'active'),
  (2, 'GL-IT-2026-003', 1, 2, '集团云资源服务协议', '2026-02-01', '2027-01-31', 360000.00, 'active');
INSERT INTO projects VALUES
  (1, 'GL-AI-15FIVE', '集团人工智能十五五知识底座项目', 1, 1, '2026-01-01', '2026-12-31', 3000000.00, 'running'),
  (2, 'GL-NX1-R2', 'NexusOne 企业版二期', 1, 2, '2026-03-01', '2026-10-31', 1800000.00, 'running');
INSERT INTO project_members VALUES
  (1, 1, '张明', '项目负责人', '2026-01-01'),
  (1, 2, '李晓', '产品负责人', '2026-01-01'),
  (1, 4, '王宁', '风险顾问', '2026-02-01'),
  (2, 2, '陈宇', '技术负责人', '2026-03-01');
INSERT INTO indicator_definitions VALUES
  (1, 'SALES_AMOUNT', '销售额', '仅统计状态为 completed 的订单金额。', 'SUM(orders.sales_amount) WHERE status = completed', '经营管理部', '2026-01-01'),
  (2, 'YOY_RATE', '同比增长率', '本期相对上年同期的增长百分比。', '(本期销售额-上年同期销售额)/上年同期销售额*100%', '经营管理部', '2026-01-01'),
  (3, 'TARGET_COMPLETION', '目标完成率', '已完成销售额占同期销售目标的百分比。', '已完成销售额/销售目标*100%', '经营管理部', '2026-01-01');
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
  ('2026-08-01 12:00:00', 'login', JSON_OBJECT('user','fixture')),
  ('2026-08-01 12:05:00', 'query', JSON_OBJECT('module','knowledge'));

CREATE VIEW completed_orders AS
SELECT id, order_no, customer_id, order_date, region, sales_amount
FROM orders WHERE status = 'completed';

REVOKE ALL PRIVILEGES, GRANT OPTION FROM 'structured_reader'@'%';
GRANT SELECT ON structured_fixture.* TO 'structured_reader'@'%';
GRANT SHOW VIEW ON structured_fixture.* TO 'structured_reader'@'%';
FLUSH PRIVILEGES;
