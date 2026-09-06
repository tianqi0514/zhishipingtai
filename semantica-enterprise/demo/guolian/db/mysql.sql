-- 传神智库 · 国联集团组织级知识底座独立演示库（MySQL）
-- 演示数据，不代表国联集团真实经营数据。
-- 本文件只包含确定性合成数据，不包含连接凭据或真实个人信息。

SET NAMES utf8mb4 COLLATE utf8mb4_0900_ai_ci;

CREATE TABLE org_units (
  id BIGINT PRIMARY KEY,
  unit_code VARCHAR(40) NOT NULL UNIQUE,
  unit_name VARCHAR(120) NOT NULL,
  unit_type VARCHAR(40) NOT NULL,
  parent_id BIGINT,
  active BOOLEAN NOT NULL DEFAULT TRUE,
  metadata JSON,
  CONSTRAINT fk_org_parent FOREIGN KEY (parent_id) REFERENCES org_units(id)
) COMMENT='演示组织单位，不代表真实组织数据';

CREATE TABLE departments (
  id BIGINT PRIMARY KEY,
  org_unit_id BIGINT NOT NULL,
  department_code VARCHAR(40) NOT NULL UNIQUE,
  department_name VARCHAR(120) NOT NULL,
  responsibility TEXT,
  CONSTRAINT fk_department_org FOREIGN KEY (org_unit_id) REFERENCES org_units(id)
);

CREATE TABLE suppliers (
  id BIGINT PRIMARY KEY,
  supplier_code VARCHAR(40) NOT NULL UNIQUE,
  supplier_name VARCHAR(120) NOT NULL,
  unified_demo_code VARCHAR(40) NOT NULL UNIQUE,
  risk_level VARCHAR(20) NOT NULL,
  status VARCHAR(30) NOT NULL,
  metadata JSON
);

CREATE TABLE supplier_contacts (
  id BIGINT PRIMARY KEY,
  supplier_id BIGINT NOT NULL,
  contact_role VARCHAR(80) NOT NULL,
  demo_mobile VARCHAR(30),
  demo_email VARCHAR(160),
  demo_api_token VARCHAR(160),
  CONSTRAINT fk_contact_supplier FOREIGN KEY (supplier_id) REFERENCES suppliers(id)
) COMMENT='用于验证服务端脱敏的虚构联系方式';

CREATE TABLE products (
  id BIGINT PRIMARY KEY,
  product_code VARCHAR(40) NOT NULL UNIQUE,
  product_name VARCHAR(120) NOT NULL,
  category VARCHAR(80) NOT NULL,
  description TEXT,
  metadata JSON
);

CREATE TABLE projects (
  id BIGINT PRIMARY KEY,
  project_code VARCHAR(40) NOT NULL UNIQUE,
  project_name VARCHAR(160) NOT NULL,
  org_unit_id BIGINT NOT NULL,
  owner_department_id BIGINT NOT NULL,
  start_date DATE NOT NULL,
  end_date DATE,
  budget DECIMAL(16,2) NOT NULL,
  status VARCHAR(30) NOT NULL,
  CONSTRAINT fk_project_org FOREIGN KEY (org_unit_id) REFERENCES org_units(id),
  CONSTRAINT fk_project_department FOREIGN KEY (owner_department_id) REFERENCES departments(id)
);

CREATE TABLE project_products (
  project_id BIGINT NOT NULL,
  product_id BIGINT NOT NULL,
  usage_role VARCHAR(80) NOT NULL,
  enabled_at DATE NOT NULL,
  active BOOLEAN NOT NULL DEFAULT TRUE,
  PRIMARY KEY (project_id, product_id),
  CONSTRAINT fk_project_product_project FOREIGN KEY (project_id) REFERENCES projects(id),
  CONSTRAINT fk_project_product_product FOREIGN KEY (product_id) REFERENCES products(id)
);

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
  CONSTRAINT fk_purchase_order_org FOREIGN KEY (org_unit_id) REFERENCES org_units(id),
  CONSTRAINT fk_purchase_order_supplier FOREIGN KEY (supplier_id) REFERENCES suppliers(id),
  CONSTRAINT fk_purchase_order_project FOREIGN KEY (project_id) REFERENCES projects(id)
);

CREATE TABLE purchase_order_items (
  id BIGINT PRIMARY KEY,
  purchase_order_id BIGINT NOT NULL,
  product_id BIGINT NOT NULL,
  quantity INT NOT NULL,
  unit_price DECIMAL(14,2) NOT NULL,
  line_amount DECIMAL(16,2) NOT NULL,
  UNIQUE KEY uq_purchase_order_product (purchase_order_id, product_id),
  CONSTRAINT fk_order_item_order FOREIGN KEY (purchase_order_id) REFERENCES purchase_orders(id),
  CONSTRAINT fk_order_item_product FOREIGN KEY (product_id) REFERENCES products(id),
  CONSTRAINT chk_order_item_quantity CHECK (quantity > 0)
);

CREATE TABLE approval_records (
  id BIGINT PRIMARY KEY,
  purchase_order_id BIGINT NOT NULL,
  approval_stage VARCHAR(80) NOT NULL,
  approver_role VARCHAR(80) NOT NULL,
  decision VARCHAR(30) NOT NULL,
  decided_at DATETIME,
  comment TEXT,
  CONSTRAINT fk_approval_order FOREIGN KEY (purchase_order_id) REFERENCES purchase_orders(id)
);

CREATE TABLE contracts (
  id BIGINT PRIMARY KEY,
  contract_no VARCHAR(40) NOT NULL UNIQUE,
  supplier_id BIGINT NOT NULL,
  project_id BIGINT,
  subject VARCHAR(200) NOT NULL,
  signed_at DATE NOT NULL,
  expires_at DATE NOT NULL,
  amount DECIMAL(16,2) NOT NULL,
  status VARCHAR(30) NOT NULL,
  CONSTRAINT fk_contract_supplier FOREIGN KEY (supplier_id) REFERENCES suppliers(id),
  CONSTRAINT fk_contract_project FOREIGN KEY (project_id) REFERENCES projects(id)
);

CREATE TABLE risk_events (
  id BIGINT PRIMARY KEY,
  supplier_id BIGINT NOT NULL,
  affected_product_id BIGINT,
  event_date DATE NOT NULL,
  risk_type VARCHAR(80) NOT NULL,
  risk_level VARCHAR(20) NOT NULL,
  severity INT NOT NULL,
  detail TEXT,
  CONSTRAINT fk_risk_supplier FOREIGN KEY (supplier_id) REFERENCES suppliers(id),
  CONSTRAINT fk_risk_product FOREIGN KEY (affected_product_id) REFERENCES products(id),
  CONSTRAINT chk_risk_severity CHECK (severity BETWEEN 1 AND 5)
);

CREATE TABLE procurement_targets (
  id BIGINT PRIMARY KEY,
  org_unit_id BIGINT NOT NULL,
  target_year INT NOT NULL,
  target_amount DECIMAL(16,2) NOT NULL,
  UNIQUE KEY uq_target_org_year (org_unit_id, target_year),
  CONSTRAINT fk_target_org FOREIGN KEY (org_unit_id) REFERENCES org_units(id)
);

CREATE TABLE system_dependencies (
  id BIGINT PRIMARY KEY,
  system_name VARCHAR(160) NOT NULL,
  depends_on_system VARCHAR(160) NOT NULL,
  dependency_type VARCHAR(80) NOT NULL,
  active BOOLEAN NOT NULL DEFAULT TRUE,
  source_note TEXT
);

CREATE TABLE policy_applicability (
  id BIGINT PRIMARY KEY,
  policy_name VARCHAR(200) NOT NULL,
  org_unit_id BIGINT NOT NULL,
  effective_date DATE NOT NULL,
  expires_at DATE,
  current_version BOOLEAN NOT NULL DEFAULT TRUE,
  CONSTRAINT fk_policy_org FOREIGN KEY (org_unit_id) REFERENCES org_units(id)
);

CREATE TABLE archived_projects (
  id BIGINT PRIMARY KEY,
  project_code VARCHAR(40) NOT NULL,
  archive_reason TEXT
) COMMENT='故意保留为空，用于验证空表预览';

INSERT INTO org_units (id, unit_code, unit_name, unit_type, parent_id, active, metadata) VALUES
  (1, 'GL-DEMO', '国联集团', 'group', NULL, TRUE, JSON_OBJECT('demo', TRUE)),
  (2, 'GL-HQ-DEMO', '集团本部', 'headquarters', 1, TRUE, JSON_OBJECT('demo', TRUE)),
  (3, 'GL-DIGITAL-DEMO', '数字科技公司', 'subsidiary', 1, TRUE, JSON_OBJECT('demo', TRUE, 'alias', '国联数科')),
  (4, 'GL-ENERGY-DEMO', '能源服务公司', 'subsidiary', 1, TRUE, JSON_OBJECT('demo', TRUE)),
  (5, 'GL-FINTECH-DEMO', '金融科技公司', 'subsidiary', 1, TRUE, JSON_OBJECT('demo', TRUE));

INSERT INTO departments (id, org_unit_id, department_code, department_name, responsibility) VALUES
  (1, 2, 'GL-DEMO-PROCURE', '集团采购管理部', '制定采购制度与管理采购执行'),
  (2, 3, 'GL-DEMO-DIGITAL', '数字化管理部', '集团数字化统筹与平台建设'),
  (3, 1, 'GL-DEMO-RISK', '风险管理部', '供应商风险识别与跟踪'),
  (4, 3, 'GL-DEMO-PMO', '项目管理办公室', '项目计划与里程碑管理');

INSERT INTO suppliers (id, supplier_code, supplier_name, unified_demo_code, risk_level, status, metadata) VALUES
  (1, 'SUP-DEMO-001', '东方智造', '91320200DEMO000001', 'high', 'active', JSON_OBJECT('demo', TRUE, 'alias', '东方智造有限公司')),
  (2, 'SUP-DEMO-002', '江南信息', '91320200DEMO000002', 'medium', 'active', JSON_OBJECT('demo', TRUE)),
  (3, 'SUP-DEMO-003', '太湖云科', '91320200DEMO000003', 'low', 'active', JSON_OBJECT('demo', TRUE)),
  (4, 'SUP-DEMO-004', '华东系统集成', '91320200DEMO000004', 'medium', 'active', JSON_OBJECT('demo', TRUE)),
  (5, 'SUP-DEMO-005', '新城数据服务', '91320200DEMO000005', 'critical', 'candidate', JSON_OBJECT('demo', TRUE));

INSERT INTO supplier_contacts (id, supplier_id, contact_role, demo_mobile, demo_email, demo_api_token) VALUES
  (1, 1, '项目接口人', '13800000001', 'demo-contact-1@example.invalid', 'demo-token-never-return-001'),
  (2, 2, '商务接口人', '13800000002', 'demo-contact-2@example.invalid', NULL);

INSERT INTO products (id, product_code, product_name, category, description, metadata) VALUES
  (1, 'DEMO-NX1', 'NexusOne', '智慧流程组件', '智慧流程中枢核心产品；演示数据', JSON_OBJECT('demo', TRUE, 'edition', 'enterprise')),
  (2, 'DEMO-WF', '智慧流程引擎', '流程平台', '集团流程编排引擎；演示数据', JSON_OBJECT('demo', TRUE)),
  (3, 'DEMO-DEX', '集团数据交换平台', '数据平台', '集团系统间数据交换能力；演示数据', JSON_OBJECT('demo', TRUE)),
  (4, 'DEMO-IAM', '统一身份组件', '基础组件', '统一身份认证组件；演示数据', JSON_OBJECT('demo', TRUE));

INSERT INTO projects (id, project_code, project_name, org_unit_id, owner_department_id, start_date, end_date, budget, status) VALUES
  (1, 'DEMO-WF-HUB', '智慧流程中枢项目', 3, 4, '2026-01-01', '2026-12-31', 2200000.00, 'running'),
  (2, 'DEMO-KB', '集团知识底座项目', 3, 2, '2026-02-01', '2026-12-31', 1800000.00, 'running'),
  (3, 'DEMO-PROC-UP', '采购协同平台升级项目', 2, 1, '2026-03-01', '2026-11-30', 1200000.00, 'running');

INSERT INTO project_products (project_id, product_id, usage_role, enabled_at, active) VALUES
  (1, 1, '核心流程能力', '2026-01-10', TRUE),
  (1, 2, '流程编排', '2026-01-10', TRUE),
  (1, 3, '数据交换', '2026-01-10', TRUE),
  (1, 4, '统一身份', '2026-01-10', TRUE),
  (2, 3, '知识数据交换', '2026-02-10', TRUE),
  (3, 2, '采购流程编排', '2026-03-10', TRUE);

INSERT INTO purchase_orders (
  id, order_no, org_unit_id, supplier_id, project_id, order_date, status, amount,
  currency, approved_at, executed_at, remark, metadata
) VALUES
  (1001, 'PO-DEMO-2026-001', 3, 1, 1, '2026-01-15', 'signed', 360000.00, 'CNY', '2026-01-14 10:00:00', NULL, '智慧流程中枢首批组件', JSON_OBJECT('demo', TRUE, 'source', 'contract')),
  (1002, 'PO-DEMO-2026-002', 3, 1, 1, '2026-02-15', 'executing', 240000.00, 'CNY', '2026-02-14 15:00:00', '2026-02-16 09:00:00', 'NexusOne 第二批组件', JSON_OBJECT('demo', TRUE)),
  (1003, 'PO-DEMO-2026-003', 2, 2, 3, '2026-03-11', 'signed', 420000.00, 'CNY', '2026-03-10 15:00:00', NULL, '集团数据交换平台采购', JSON_OBJECT('demo', TRUE)),
  (1004, 'PO-DEMO-2026-004', 4, 3, 2, '2026-04-09', 'accepted', 380000.00, 'CNY', '2026-04-08 10:00:00', '2026-04-10 08:00:00', '知识底座与流程服务采购', JSON_OBJECT('demo', TRUE)),
  (1005, 'PO-DEMO-2026-005', 5, 4, 1, '2026-05-20', 'accepted', 300000.00, 'CNY', '2026-05-19 10:00:00', '2026-05-21 08:00:00', '统一身份组件采购', JSON_OBJECT('demo', TRUE)),
  (1006, 'PO-DEMO-2026-006', 2, 1, 1, '2026-05-28', 'cancelled', 120000.00, 'CNY', NULL, NULL, '因供应风险取消', JSON_OBJECT('demo', TRUE, 'cancel_reason', '交付延期')),
  (1007, 'PO-DEMO-2026-007', 3, 5, 2, '2026-06-05', 'executing', 90000.00, 'CNY', NULL, '2026-06-06 08:00:00', '已执行但审批待补充', JSON_OBJECT('demo', TRUE, 'exception', TRUE)),
  (1009, 'PO-DEMO-2025-001', 3, 1, 1, '2025-06-10', 'accepted', 200000.00, 'CNY', '2025-06-09 10:00:00', '2025-06-11 08:00:00', '同比基期订单', JSON_OBJECT('demo', TRUE)),
  (1011, 'PO-DEMO-2027-001', 3, 3, 2, '2027-01-01', 'accepted', 60000.00, 'CNY', '2026-12-31 17:00:00', '2027-01-02 08:00:00', '下一年度订单', JSON_OBJECT('demo', TRUE));

INSERT INTO purchase_order_items (id, purchase_order_id, product_id, quantity, unit_price, line_amount) VALUES
  (1, 1001, 1, 2, 120000.00, 240000.00),
  (2, 1001, 2, 1, 120000.00, 120000.00),
  (3, 1002, 1, 2, 120000.00, 240000.00),
  (4, 1003, 3, 1, 420000.00, 420000.00),
  (5, 1004, 2, 1, 380000.00, 380000.00),
  (6, 1005, 4, 1, 300000.00, 300000.00),
  (7, 1006, 1, 1, 120000.00, 120000.00),
  (8, 1007, 2, 1, 90000.00, 90000.00),
  (9, 1009, 1, 1, 200000.00, 200000.00),
  (10, 1011, 3, 1, 60000.00, 60000.00);

INSERT INTO approval_records (id, purchase_order_id, approval_stage, approver_role, decision, decided_at, comment) VALUES
  (1, 1001, '采购审批', '集团采购管理部', 'approved', '2026-01-14 10:00:00', '审批通过'),
  (2, 1002, '采购审批', '数字化管理部', 'approved', '2026-02-14 15:00:00', '审批通过'),
  (3, 1003, '采购审批', '集团采购管理部', 'approved', '2026-03-10 15:00:00', '审批通过'),
  (4, 1004, '采购审批', '能源服务公司', 'approved', '2026-04-08 10:00:00', '审批通过'),
  (5, 1005, '采购审批', '金融科技公司', 'approved', '2026-05-19 10:00:00', '审批通过'),
  (6, 1007, '采购审批', '集团采购管理部', 'pending', NULL, '执行后待补审批');

INSERT INTO contracts (id, contract_no, supplier_id, project_id, subject, signed_at, expires_at, amount, status) VALUES
  (1, 'DEMO-CT-2026-001', 1, 1, 'NexusOne 采购合同（演示）', '2026-01-10', '2026-12-31', 600000.00, 'active'),
  (2, 'DEMO-CT-2026-002', 2, 3, '数据交换平台采购合同（演示）', '2026-03-01', '2026-12-31', 420000.00, 'active');

INSERT INTO risk_events (id, supplier_id, affected_product_id, event_date, risk_type, risk_level, severity, detail) VALUES
  (1, 1, 1, '2026-04-28', '交付延期', 'high', 5, '关键组件预计延期十个工作日；演示数据'),
  (2, 1, 1, '2026-05-12', '质量异常', 'high', 4, '批次质量异常；演示数据'),
  (3, 2, 3, '2026-06-03', '一般质量提示', 'low', 2, NULL),
  (4, 1, 1, '2025-12-20', '历史交付提示', 'high', 3, '时间边界外历史事件；演示数据');

INSERT INTO procurement_targets (id, org_unit_id, target_year, target_amount) VALUES
  (1, 2, 2026, 500000.00),
  (2, 3, 2026, 800000.00),
  (3, 4, 2026, 450000.00),
  (4, 5, 2026, 350000.00);

INSERT INTO system_dependencies (id, system_name, depends_on_system, dependency_type, active, source_note) VALUES
  (1, '智慧流程中枢', '集团数据交换平台', 'data_exchange', TRUE, '演示架构材料'),
  (2, '集团数据交换平台', '统一身份组件', 'identity', TRUE, '演示架构材料');

INSERT INTO policy_applicability (id, policy_name, org_unit_id, effective_date, expires_at, current_version) VALUES
  (1, '集团本部采购实施细则（2025演示现行版）', 1, '2025-01-01', NULL, TRUE),
  (2, '集团本部采购实施细则（2023演示旧版）', 1, '2023-07-01', '2024-12-31', FALSE);

CREATE VIEW procurement_order_overview AS
SELECT
  po.order_no,
  ou.unit_name,
  s.supplier_name,
  p.project_name,
  po.order_date,
  po.status,
  po.amount,
  ar.decision AS approval_decision
FROM purchase_orders po
JOIN org_units ou ON ou.id = po.org_unit_id
JOIN suppliers s ON s.id = po.supplier_id
LEFT JOIN projects p ON p.id = po.project_id
LEFT JOIN approval_records ar ON ar.purchase_order_id = po.id;
