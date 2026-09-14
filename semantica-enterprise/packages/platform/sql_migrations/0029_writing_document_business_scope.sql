-- dialect: postgresql
ALTER TABLE writing_documents
  ADD COLUMN IF NOT EXISTS purpose TEXT NOT NULL DEFAULT '',
  ADD COLUMN IF NOT EXISTS audience VARCHAR(300) NOT NULL DEFAULT '',
  ADD COLUMN IF NOT EXISTS applicability JSON NOT NULL DEFAULT '{}',
  ADD COLUMN IF NOT EXISTS writing_requirements TEXT NOT NULL DEFAULT '',
  ADD COLUMN IF NOT EXISTS adopted_material_ids JSON,
  ADD COLUMN IF NOT EXISTS scenario_package_version_id VARCHAR(36) REFERENCES scenario_package_versions(id),
  ADD COLUMN IF NOT EXISTS knowledge_product_release_id VARCHAR(36) REFERENCES knowledge_product_releases(id);

CREATE INDEX IF NOT EXISTS ix_writing_document_scenario_version
  ON writing_documents(scenario_package_version_id);
CREATE INDEX IF NOT EXISTS ix_writing_document_knowledge_release
  ON writing_documents(knowledge_product_release_id);

-- dialect: postgresql
UPDATE writing_documents AS document
SET scenario_package_version_id = project.scenario_package_version_id,
    knowledge_product_release_id = project.knowledge_product_release_id
FROM writing_projects AS project
WHERE document.project_id = project.id
  AND (document.scenario_package_version_id IS NULL OR document.knowledge_product_release_id IS NULL);
