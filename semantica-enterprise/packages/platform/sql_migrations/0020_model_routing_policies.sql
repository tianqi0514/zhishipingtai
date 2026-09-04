CREATE TABLE IF NOT EXISTS model_routing_policies (
  id VARCHAR(36) PRIMARY KEY,
  tenant_id VARCHAR(36) NOT NULL REFERENCES tenants(id),
  name VARCHAR(200) NOT NULL,
  description TEXT NOT NULL,
  routes JSON NOT NULL,
  enabled BOOLEAN NOT NULL,
  is_default BOOLEAN NOT NULL,
  created_at TIMESTAMP NOT NULL,
  updated_at TIMESTAMP NOT NULL,
  deleted_at TIMESTAMP,
  CONSTRAINT uq_model_routing_policy_tenant_name UNIQUE (tenant_id, name)
);
CREATE INDEX IF NOT EXISTS ix_model_routing_policy_tenant ON model_routing_policies(tenant_id);
