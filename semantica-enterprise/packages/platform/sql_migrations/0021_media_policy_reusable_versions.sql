-- dialect: postgresql
-- A policy may intentionally return to an earlier configuration.  The
-- immutable version number remains unique, while equal configuration hashes
-- are useful for identifying the rollback/reuse and must not reject the edit.
ALTER TABLE media_parsing_policy_versions
  DROP CONSTRAINT IF EXISTS uq_media_policy_config_hash;

CREATE INDEX IF NOT EXISTS ix_media_policy_config_hash
  ON media_parsing_policy_versions(policy_id, config_hash);
