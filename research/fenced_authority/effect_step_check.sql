-- EXPERIMENT ONLY: synthetic resource step check against ONE disposable PG primary.
-- This does not authorize a model call or make its effects atomic with SQL.
-- Current revocation is checked on *each separate invocation*, not cached.
-- Both tables and account-scoped quota must be controlled by another role.
CREATE OR REPLACE FUNCTION fence_research.assert_live_effect_step(
 p_scope text, p_epoch bigint, p_owner text, p_claim text,
 p_projection bigint, p_request text, p_model text, p_runtime text
) RETURNS boolean LANGUAGE plpgsql AS $$
DECLARE
  active_scope fence_research.scopes%ROWTYPE;
  active_request fence_research.executions%ROWTYPE;
BEGIN
  -- SHARE-lock serializes with a concurrent revoke/update ONLY for this SQL
  -- transaction. It cannot lock a real HTTP/model effect after returning.
  SELECT * INTO active_scope FROM fence_research.scopes
  WHERE scope_hash=p_scope FOR SHARE;
  IF NOT FOUND OR active_scope.state <> 'active'
    OR active_scope.epoch <> p_epoch
    OR active_scope.owner_hash <> p_owner
    OR active_scope.claim_hash <> p_claim
    OR active_scope.projection_generation <> p_projection
    OR active_scope.expires_at <= clock_timestamp() THEN
    RAISE EXCEPTION 'effect_check_denied: scope revoked, stale, expired or misbound';
  END IF;

  SELECT * INTO active_request FROM fence_research.executions
  WHERE request_hash=p_request FOR SHARE;
  IF NOT FOUND OR active_request.status <> 'reserved'
    OR active_request.scope_hash <> p_scope
    OR active_request.epoch <> p_epoch
    OR active_request.owner_hash <> p_owner
    OR active_request.claim_hash <> p_claim
    OR active_request.projection_generation <> p_projection
    OR active_request.model_hash <> p_model
    OR active_request.runtime_hash <> p_runtime THEN
    RAISE EXCEPTION 'effect_check_denied: no live matching reserved request';
  END IF;
  RETURN true;
END $$;

-- Newly introduced functions are executable by PUBLIC by PostgreSQL default
-- unless the migration creator has changed global default privileges.
REVOKE EXECUTE ON FUNCTION fence_research.assert_live_effect_step(
 text,bigint,text,text,bigint,text,text,text
) FROM PUBLIC;
