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

-- 国联集团组织级知识底座演示数据。全部记录均为确定性合成数据，
-- 不代表国联集团真实经营数据。
CREATE TABLE org_units (
  id BIGINT PRIMARY KEY,
  unit_code VARCHAR(40) NOT NULL UNIQUE,
  unit_name VARCHAR(120) NOT NULL,
  unit_type VARCHAR(40) NOT NULL,
  parent_id BIGINT,
  active BOOLEAN NOT NULL DEFAULT TRUE,
  metadata JSON,
  CONSTRAINT fk_org_parent FOREIGN KEY (parent_id) REFERENCES org_units(id)
);

INSERT INTO companies VALUES
  (10, '国联集团（演示）', '华东', '演示集团主体，不代表真实经营数据', '2026-01-01 08:00:00'),
  (11, '集团本部（演示）', '华东', '演示集团本部', '2026-01-01 08:00:00'),
  (12, '数字科技公司（演示）', '华东', '演示数字化建设单位', '2026-01-01 08:00:00'),
  (13, '能源服务公司（演示）', '华东', '演示能源服务单位', '2026-01-01 08:00:00'),
  (14, '金融科技公司（演示）', '华东', '演示金融科技单位', '2026-01-01 08:00:00');
INSERT INTO org_units VALUES
  (1, 'GL-DEMO', '国联集团', 'group', NULL, TRUE, JSON_OBJECT('demo',true)),
  (2, 'GL-HQ-DEMO', '集团本部', 'headquarters', 1, TRUE, JSON_OBJECT('demo',true)),
  (3, 'GL-DIGITAL-DEMO', '数字科技公司', 'subsidiary', 1, TRUE, JSON_OBJECT('demo',true,'alias','国联数科')),
  (4, 'GL-ENERGY-DEMO', '能源服务公司', 'subsidiary', 1, TRUE, JSON_OBJECT('demo',true)),
  (5, 'GL-FINTECH-DEMO', '金融科技公司', 'subsidiary', 1, TRUE, JSON_OBJECT('demo',true));
INSERT INTO departments VALUES
  (101, 11, 'GL-DEMO-PROCURE', '集团采购管理部（演示）', NULL, 'DEMO-PC-001'),
  (102, 12, 'GL-DEMO-DIGITAL', '数字化管理部（演示）', NULL, 'DEMO-DT-001'),
  (103, 10, 'GL-DEMO-RISK', '风险管理部（演示）', NULL, 'DEMO-RM-001'),
  (104, 12, 'GL-DEMO-PMO', '项目管理办公室（演示）', NULL, 'DEMO-PM-001');
INSERT INTO suppliers VALUES
  (101, 11, '东方智造', 'high', '13800000001', 'demo-never-return-token'),
  (102, 11, '江南信息', 'medium', '13800000002', NULL),
  (103, 11, '太湖云科', 'low', '13800000003', NULL),
  (104, 11, '华东系统集成', 'medium', '13800000004', NULL),
  (105, 11, '新城数据服务', 'critical', '13800000005', NULL);
INSERT INTO products VALUES
  (101, 'DEMO-NX1', 'NexusOne', '智慧流程组件', 120000.00, JSON_OBJECT('demo',true,'edition','enterprise'), '智慧流程中枢核心产品'),
  (102, 'DEMO-WF', '智慧流程引擎', '流程平台', 180000.00, JSON_OBJECT('demo',true), '集团流程编排引擎'),
  (103, 'DEMO-DEX', '集团数据交换平台', '数据平台', 420000.00, JSON_OBJECT('demo',true), '集团系统间数据交换能力'),
  (104, 'DEMO-IAM', '统一身份组件', '基础组件', 300000.00, JSON_OBJECT('demo',true), '统一身份认证组件');
INSERT INTO projects VALUES
  (101, 'DEMO-WF-HUB', '智慧流程中枢项目', 12, 104, '2026-01-01', '2026-12-31', 2200000.00, 'running'),
  (102, 'DEMO-KB', '集团知识底座项目', 12, 102, '2026-02-01', '2026-12-31', 1800000.00, 'running'),
  (103, 'DEMO-PROC-UP', '采购协同平台升级项目', 11, 101, '2026-03-01', '2026-11-30', 1200000.00, 'running');

CREATE TABLE project_products (
  project_id BIGINT NOT NULL,
  product_id BIGINT NOT NULL,
  usage_role VARCHAR(80) NOT NULL,
  enabled_at DATE NOT NULL,
  PRIMARY KEY (project_id, product_id),
  CONSTRAINT fk_demo_pp_project FOREIGN KEY (project_id) REFERENCES projects(id),
  CONSTRAINT fk_demo_pp_product FOREIGN KEY (product_id) REFERENCES products(id)
);
INSERT INTO project_products VALUES
  (101,101,'核心流程能力','2026-01-10'),(101,102,'流程编排','2026-01-10'),
  (101,103,'数据交换','2026-01-10'),(101,104,'统一身份','2026-01-10'),
  (102,101,'知识服务接入','2026-02-10'),(102,103,'数据交换','2026-02-10'),
  (103,102,'采购流程编排','2026-03-10');

CREATE TABLE purchase_orders (
  id BIGINT PRIMARY KEY,
  order_no VARCHAR(40) NOT NULL UNIQUE,
  org_unit_id BIGINT NOT NULL,
  supplier_id BIGINT NOT NULL,
  project_id BIGINT,
  order_date DATE NOT NULL,
  status VARCHAR(30) NOT NULL,
  amount DECIMAL(16,2) NOT NULL,
  currency VARCHAR(10) NOT NULL DEFAULT 'CNY',
  approved_at DATETIME,
  executed_at DATETIME,
  remark TEXT,
  metadata JSON,
  CONSTRAINT fk_demo_po_org FOREIGN KEY (org_unit_id) REFERENCES org_units(id),
  CONSTRAINT fk_demo_po_supplier FOREIGN KEY (supplier_id) REFERENCES suppliers(id),
  CONSTRAINT fk_demo_po_project FOREIGN KEY (project_id) REFERENCES projects(id)
);
INSERT INTO purchase_orders VALUES
  (1001,'PO-DEMO-2026-001',3,101,101,'2026-01-15','signed',360000.00,'CNY','2026-01-14 10:00:00',NULL,'智慧流程中枢首批组件',JSON_OBJECT('demo',true,'source','contract')),
  (1002,'PO-DEMO-2026-002',3,101,101,'2026-02-15','executing',240000.00,'CNY','2026-02-14 15:00:00','2026-02-16 09:00:00','NexusOne 第二批组件',JSON_OBJECT('demo',true)),
  (1003,'PO-DEMO-2026-003',2,102,103,'2026-03-11','signed',420000.00,'CNY','2026-03-10 15:00:00',NULL,'集团数据交换平台采购',JSON_OBJECT('demo',true)),
  (1004,'PO-DEMO-2026-004',4,103,102,'2026-04-09','accepted',380000.00,'CNY','2026-04-08 10:00:00','2026-04-10 08:00:00','知识底座与流程服务采购',JSON_OBJECT('demo',true)),
  (1005,'PO-DEMO-2026-005',5,104,101,'2026-05-20','accepted',300000.00,'CNY','2026-05-19 10:00:00','2026-05-21 08:00:00','统一身份组件采购',JSON_OBJECT('demo',true)),
  (1006,'PO-DEMO-2026-006',2,101,101,'2026-05-28','cancelled',120000.00,'CNY',NULL,NULL,'因供应风险取消',JSON_OBJECT('demo',true,'cancel_reason','交付延期')),
  (1007,'PO-DEMO-2026-007',3,105,102,'2026-06-05','executing',90000.00,'CNY',NULL,'2026-06-06 08:00:00','已执行但审批待补充',JSON_OBJECT('demo',true,'exception',true)),
  (1009,'PO-DEMO-2025-001',3,101,101,'2025-06-10','accepted',200000.00,'CNY','2025-06-09 10:00:00','2025-06-11 08:00:00','同比基期订单',JSON_OBJECT('demo',true)),
  (1011,'PO-DEMO-2027-001',3,103,102,'2027-01-01','accepted',60000.00,'CNY','2026-12-31 17:00:00','2027-01-02 08:00:00','下一年度订单',JSON_OBJECT('demo',true));

CREATE TABLE purchase_order_items (
  id BIGINT PRIMARY KEY,
  purchase_order_id BIGINT NOT NULL,
  product_id BIGINT NOT NULL,
  quantity INT NOT NULL,
  unit_price DECIMAL(14,2) NOT NULL,
  line_amount DECIMAL(16,2) NOT NULL,
  UNIQUE KEY uq_demo_po_product (purchase_order_id, product_id),
  CONSTRAINT fk_demo_item_order FOREIGN KEY (purchase_order_id) REFERENCES purchase_orders(id),
  CONSTRAINT fk_demo_item_product FOREIGN KEY (product_id) REFERENCES products(id)
);
INSERT INTO purchase_order_items VALUES
  (1,1001,101,2,120000.00,240000.00),(2,1001,102,1,120000.00,120000.00),
  (3,1002,101,2,120000.00,240000.00),(4,1003,103,1,420000.00,420000.00),
  (5,1004,102,1,380000.00,380000.00),(6,1005,104,1,300000.00,300000.00),
  (7,1006,101,1,120000.00,120000.00),(8,1007,102,1,90000.00,90000.00),
  (10,1009,101,1,200000.00,200000.00),(12,1011,103,1,60000.00,60000.00);

CREATE TABLE approval_records (
  id BIGINT PRIMARY KEY,
  purchase_order_id BIGINT NOT NULL,
  approval_stage VARCHAR(80) NOT NULL,
  approver_role VARCHAR(80) NOT NULL,
  decision VARCHAR(30) NOT NULL,
  decided_at DATETIME,
  comment TEXT,
  CONSTRAINT fk_demo_approval_order FOREIGN KEY (purchase_order_id) REFERENCES purchase_orders(id)
);
INSERT INTO approval_records VALUES
  (1,1001,'采购审批','集团采购管理部','approved','2026-01-14 10:00:00','审批通过'),
  (2,1002,'采购审批','数字化管理部','approved','2026-02-14 15:00:00','审批通过'),
  (3,1003,'采购审批','集团采购管理部','approved','2026-03-10 15:00:00','审批通过'),
  (4,1004,'采购审批','能源服务公司','approved','2026-04-08 10:00:00','审批通过'),
  (5,1005,'采购审批','金融科技公司','approved','2026-05-19 10:00:00','审批通过'),
  (6,1007,'采购审批','集团采购管理部','pending',NULL,'执行后待补审批');
INSERT INTO contracts VALUES
  (101,'DEMO-CT-2026-001',11,101,'NexusOne 采购合同（演示）','2026-01-10','2026-12-31',600000.00,'active'),
  (102,'DEMO-CT-2026-002',12,102,'智慧流程引擎服务合同（演示）','2026-02-10','2026-12-31',180000.00,'active');
INSERT INTO risk_events VALUES
  (101,101,'2026-04-28','交付延期',5,'东方智造关键组件预计延期十个工作日；演示数据'),
  (102,101,'2026-05-12','质量异常',4,'东方智造批次质量异常；演示数据'),
  (103,102,'2026-06-03','一般质量提示',2,NULL);

CREATE TABLE procurement_targets (
  id BIGINT PRIMARY KEY,
  org_unit_id BIGINT NOT NULL,
  target_year INT NOT NULL,
  target_amount DECIMAL(16,2) NOT NULL,
  UNIQUE KEY uq_demo_target (org_unit_id, target_year),
  CONSTRAINT fk_demo_target_org FOREIGN KEY (org_unit_id) REFERENCES org_units(id)
);
INSERT INTO procurement_targets VALUES
  (1,2,2026,500000.00),(2,3,2026,800000.00),(3,4,2026,450000.00),(4,5,2026,350000.00);

CREATE TABLE system_dependencies (
  id BIGINT PRIMARY KEY,
  system_name VARCHAR(160) NOT NULL,
  depends_on_system VARCHAR(160) NOT NULL,
  component_name VARCHAR(160),
  dependency_type VARCHAR(80) NOT NULL,
  owner_department VARCHAR(120) NOT NULL,
  status VARCHAR(30) NOT NULL,
  metadata JSON
);
INSERT INTO system_dependencies VALUES
  (1,'智慧流程中枢','集团数据交换平台','数据交换服务','data','数字化管理部','active',JSON_OBJECT('demo',true)),
  (2,'集团数据交换平台','统一身份组件','统一身份组件','identity','数字化管理部','maintenance',JSON_OBJECT('demo',true,'maintenance_window','2026-09-18 22:00')),
  (3,'智慧流程中枢','NexusOne','语义建模与知识服务','knowledge','项目管理办公室','active',JSON_OBJECT('demo',true));

CREATE TABLE policy_applicability (
  id BIGINT PRIMARY KEY,
  policy_code VARCHAR(60) NOT NULL,
  policy_name VARCHAR(200) NOT NULL,
  org_unit_id BIGINT NOT NULL,
  effective_date DATE NOT NULL,
  expires_at DATE,
  current_version BOOLEAN NOT NULL DEFAULT TRUE,
  UNIQUE KEY uq_demo_policy_app (policy_code, org_unit_id, effective_date),
  CONSTRAINT fk_demo_policy_org FOREIGN KEY (org_unit_id) REFERENCES org_units(id)
);
INSERT INTO policy_applicability VALUES
  (1,'GL-DEMO-PROC-2025','集团本部采购实施细则（2025 修订演示版）',1,'2025-07-01',NULL,TRUE),
  (2,'GL-DEMO-PROC-2025','集团本部采购实施细则（2025 修订演示版）',2,'2025-07-01',NULL,TRUE);

CREATE VIEW procurement_order_overview AS
SELECT po.order_no, ou.unit_name, s.supplier_name, p.project_name, po.order_date,
       po.status, po.amount, po.remark
FROM purchase_orders po
JOIN org_units ou ON ou.id = po.org_unit_id
JOIN suppliers s ON s.id = po.supplier_id
LEFT JOIN projects p ON p.id = po.project_id;

REVOKE ALL PRIVILEGES, GRANT OPTION FROM 'structured_reader'@'%';
GRANT SELECT ON structured_fixture.* TO 'structured_reader'@'%';
GRANT SHOW VIEW ON structured_fixture.* TO 'structured_reader'@'%';
FLUSH PRIVILEGES;
