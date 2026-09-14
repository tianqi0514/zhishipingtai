-- dialect: postgresql
ALTER TABLE writing_projects
  ALTER COLUMN knowledge_product_release_id DROP NOT NULL;

-- dialect: postgresql
ALTER TABLE writing_document_versions
  ALTER COLUMN scenario_package_version_id DROP NOT NULL,
  ALTER COLUMN knowledge_product_release_id DROP NOT NULL;
