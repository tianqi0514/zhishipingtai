-- dialect: postgresql
ALTER TABLE knowledge_releases
  ALTER COLUMN graph_release_id DROP NOT NULL;
