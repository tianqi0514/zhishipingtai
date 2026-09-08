-- Reconcile P4 project lifecycle after every required business decision has
-- been confirmed.  This is intentionally a status-only, idempotent backfill;
-- immutable document versions, decisions, and audit rows are untouched.
UPDATE writing_projects AS project
SET status = 'ready', updated_at = CURRENT_TIMESTAMP
WHERE project.deleted_at IS NULL
  AND project.status IN ('draft', 'preparing')
  AND EXISTS (
    SELECT 1
    FROM writing_documents AS document
    WHERE document.project_id = project.id
      AND document.deleted_at IS NULL
  )
  AND NOT EXISTS (
    SELECT 1
    FROM writing_decision_gates AS gate
    WHERE gate.project_id = project.id
      AND gate.deleted_at IS NULL
      AND gate.required = TRUE
      AND gate.status <> 'confirmed'
  );
