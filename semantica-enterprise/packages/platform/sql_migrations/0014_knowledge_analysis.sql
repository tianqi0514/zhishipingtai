CREATE INDEX IF NOT EXISTS ix_analysis_rule_sets_tenant_enabled ON analysis_rule_sets (tenant_id, enabled);
CREATE INDEX IF NOT EXISTS ix_analysis_rules_set_enabled ON analysis_rules (rule_set_id, enabled, priority);
CREATE INDEX IF NOT EXISTS ix_analysis_rule_versions_rule_version ON analysis_rule_versions (rule_id, version);
CREATE INDEX IF NOT EXISTS ix_analysis_scenarios_tenant_enabled ON analysis_scenarios (tenant_id, enabled);
CREATE INDEX IF NOT EXISTS ix_inference_runs_tenant_recent ON inference_runs (tenant_id, created_at);
CREATE INDEX IF NOT EXISTS ix_inferred_facts_space_status ON inferred_facts (space_id, status, predicate);
CREATE INDEX IF NOT EXISTS ix_inference_evidence_fact ON inference_evidence (inferred_fact_id, ordinal);
CREATE INDEX IF NOT EXISTS ix_saved_graph_queries_owner ON saved_graph_queries (tenant_id, user_id, updated_at);
