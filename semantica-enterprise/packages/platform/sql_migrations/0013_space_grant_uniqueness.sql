DELETE FROM space_grants
WHERE id IN (
  SELECT id
  FROM (
    SELECT
      id,
      ROW_NUMBER() OVER (
        PARTITION BY tenant_id, space_id, subject_type, subject_id, permission, effect
        ORDER BY created_at, id
      ) AS duplicate_rank
    FROM space_grants
    WHERE deleted_at IS NULL
  ) ranked
  WHERE duplicate_rank > 1
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_space_grants_active
ON space_grants (tenant_id, space_id, subject_type, subject_id, permission, effect)
WHERE deleted_at IS NULL;
