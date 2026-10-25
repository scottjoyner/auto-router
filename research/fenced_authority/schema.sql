-- EXPERIMENT ONLY. DO NOT apply on a production database.
-- This schema is a shared-transaction *mechanism*, NOT an authentication,
-- receipt signing, admission issuer or cross-datacenter consensus system.
-- All call arguments MUST be checked by a trusted owner-bound broker.
CREATE SCHEMA IF NOT EXISTS fence_research;

CREATE TABLE fence_research.scopes (
 scope_hash text PRIMARY KEY CHECK (scope_hash ~ '^[0-9a-f]{64}$'),
 epoch bigint NOT NULL CHECK (epoch BETWEEN 1 AND 9223372036854775806),
 owner_hash text NOT NULL CHECK (owner_hash ~ '^[0-9a-f]{64}$'),
 claim_hash text NOT NULL CHECK (claim_hash ~ '^[0-9a-f]{64}$'),
 projection_generation bigint NOT NULL CHECK (projection_generation > 0),
 expires_at timestamptz NOT NULL,
 state text NOT NULL CHECK (state IN ('active','revoked')),
 changed_at timestamptz NOT NULL DEFAULT clock_timestamp()
);

CREATE TABLE fence_research.attempts (
 jti_hash text PRIMARY KEY CHECK (jti_hash ~ '^[0-9a-f]{64}$'),
 scope_hash text NOT NULL REFERENCES fence_research.scopes(scope_hash),
 epoch bigint NOT NULL CHECK (epoch > 0),
 owner_hash text NOT NULL CHECK (owner_hash ~ '^[0-9a-f]{64}$'),
 claim_hash text NOT NULL CHECK (claim_hash ~ '^[0-9a-f]{64}$'),
 projection_generation bigint NOT NULL CHECK (projection_generation > 0),
 expires_at timestamptz NOT NULL,
 max_attempts integer NOT NULL CHECK (max_attempts BETWEEN 1 AND 100),
 consumed integer NOT NULL CHECK (consumed BETWEEN 0 AND 100)
);

CREATE TABLE fence_research.executions (
 request_hash text PRIMARY KEY CHECK (request_hash ~ '^[0-9a-f]{64}$'),
 scope_hash text NOT NULL REFERENCES fence_research.scopes(scope_hash),
 epoch bigint NOT NULL,
 owner_hash text NOT NULL CHECK (owner_hash ~ '^[0-9a-f]{64}$'),
 claim_hash text NOT NULL CHECK (claim_hash ~ '^[0-9a-f]{64}$'),
 projection_generation bigint NOT NULL,
 jti_hash text NOT NULL REFERENCES fence_research.attempts(jti_hash),
 attempt integer NOT NULL,
 model_hash text NOT NULL CHECK (model_hash ~ '^[0-9a-f]{64}$'),
 runtime_hash text NOT NULL CHECK (runtime_hash ~ '^[0-9a-f]{64}$'),
 status text NOT NULL CHECK (status IN ('reserved','completed','failed','cancelled','possibly_accepted')),
 terminal_receipt_hash text CHECK (terminal_receipt_hash IS NULL OR terminal_receipt_hash ~ '^[0-9a-f]{64}$'),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 finished_at timestamptz,
 UNIQUE(jti_hash,attempt),
 CONSTRAINT final_only_receipted CHECK (
     (status='reserved' AND terminal_receipt_hash IS NULL AND finished_at IS NULL)
     OR
     (status <> 'reserved' AND terminal_receipt_hash IS NOT NULL AND finished_at IS NOT NULL)
 )
);

CREATE UNIQUE INDEX unique_verified_receipt_digest
ON fence_research.executions (terminal_receipt_hash)
WHERE terminal_receipt_hash IS NOT NULL;

-- A broker must verify a live authorized owner/claim before calling this.
-- After revocation the next grant obtains a strictly higher database epoch;
-- two concurrent grants cannot both win. Active grants cannot be stolen.
CREATE OR REPLACE FUNCTION fence_research.grant_scope(
 p_scope text,p_owner text,p_claim text,p_projection bigint,p_expires timestamptz
) RETURNS bigint LANGUAGE plpgsql AS $$
DECLARE new_epoch bigint;
BEGIN
 IF p_expires <= clock_timestamp() OR p_expires > clock_timestamp()+interval '60 seconds'
    OR p_projection < 1 THEN
   RAISE EXCEPTION 'grant validity or projection rejected';
 END IF;
 INSERT INTO fence_research.scopes AS s (
   scope_hash,epoch,owner_hash,claim_hash,projection_generation,expires_at,state
 ) VALUES (p_scope,1,p_owner,p_claim,p_projection,p_expires,'active')
 ON CONFLICT (scope_hash) DO UPDATE SET
   epoch=s.epoch+1, owner_hash=EXCLUDED.owner_hash,
   claim_hash=EXCLUDED.claim_hash,projection_generation=EXCLUDED.projection_generation,
   expires_at=EXCLUDED.expires_at,state='active',changed_at=clock_timestamp()
 WHERE s.state='revoked' AND s.epoch<9223372036854775806
 RETURNING epoch INTO new_epoch;
 IF new_epoch IS NULL THEN RAISE EXCEPTION 'scope already active: deny competing grant'; END IF;
 RETURN new_epoch;
END $$;

CREATE OR REPLACE FUNCTION fence_research.revoke_scope(
 p_scope text,p_epoch bigint,p_owner text,p_claim text
) RETURNS boolean LANGUAGE plpgsql AS $$
DECLARE changed integer;
BEGIN
 UPDATE fence_research.scopes SET state='revoked',changed_at=clock_timestamp()
 WHERE scope_hash=p_scope AND epoch=p_epoch AND owner_hash=p_owner AND claim_hash=p_claim
 AND state='active';
 GET DIAGNOSTICS changed=ROW_COUNT;
 IF changed<>1 THEN RAISE EXCEPTION 'wrong epoch/owner/claim or already revoked'; END IF;
 RETURN true;
END $$;

-- Row-level FOR UPDATE locks serialize all requests on a single scope
-- and prevent concurrent revoke/grant changing the authority mid-reserve.
-- Signed JWT validation, owner identity and model admission must precede
-- this function at a trusted broker; SQL parameters alone are not proof.
CREATE OR REPLACE FUNCTION fence_research.reserve(
 p_scope text,p_epoch bigint,p_owner text,p_claim text,p_projection bigint,
 p_jti text,p_expires timestamptz,p_max integer,p_request text,
 p_model text,p_runtime text
) RETURNS integer LANGUAGE plpgsql AS $$
DECLARE current_scope fence_research.scopes%ROWTYPE;
        current_attempt fence_research.attempts%ROWTYPE;
        next_attempt integer;
BEGIN
 SELECT * INTO current_scope FROM fence_research.scopes
 WHERE scope_hash=p_scope FOR UPDATE;
 IF NOT FOUND OR current_scope.state<>'active' OR current_scope.epoch<>p_epoch
    OR current_scope.owner_hash<>p_owner OR current_scope.claim_hash<>p_claim
    OR current_scope.projection_generation<>p_projection
    OR current_scope.expires_at<=clock_timestamp() THEN
    RAISE EXCEPTION 'fence stale, revoked, expired or wrong owner/claim';
 END IF;
 IF p_expires <= clock_timestamp() OR p_expires > clock_timestamp()+interval '60 seconds'
    OR p_max NOT BETWEEN 1 AND 100 THEN
    RAISE EXCEPTION 'invalid signed token validity or attempt budget';
 END IF;
 SELECT * INTO current_attempt FROM fence_research.attempts
 WHERE jti_hash=p_jti FOR UPDATE;
 IF FOUND THEN
   IF current_attempt.scope_hash<>p_scope OR current_attempt.epoch<>p_epoch
      OR current_attempt.owner_hash<>p_owner
      OR current_attempt.claim_hash<>p_claim
      OR current_attempt.projection_generation<>p_projection
      OR current_attempt.max_attempts<>p_max
      OR current_attempt.expires_at<>p_expires THEN
     RAISE EXCEPTION 'signed JTI reused under another binding';
   END IF;
   IF current_attempt.consumed>=p_max THEN
     RAISE EXCEPTION 'durable shared attempt budget exhausted';
   END IF;
   next_attempt:=current_attempt.consumed+1;
   UPDATE fence_research.attempts SET consumed=next_attempt WHERE jti_hash=p_jti;
 ELSE
   next_attempt:=1;
   INSERT INTO fence_research.attempts (
     jti_hash,scope_hash,epoch,owner_hash,claim_hash,projection_generation,
     expires_at,max_attempts,consumed
   ) VALUES (p_jti,p_scope,p_epoch,p_owner,p_claim,p_projection,p_expires,p_max,1);
 END IF;
 INSERT INTO fence_research.executions (
    request_hash,scope_hash,epoch,owner_hash,claim_hash,projection_generation,
    jti_hash,attempt,model_hash,runtime_hash,status
 ) VALUES (p_request,p_scope,p_epoch,p_owner,p_claim,p_projection,
           p_jti,next_attempt,p_model,p_runtime,'reserved');
 RETURN next_attempt;
END $$;

-- A trusted receipt verifier must validate the Ed25519 terminal signature,
-- source-bound owner/model/runtime and live revocation BEFORE calling this.
-- Passing a hash without external signature verification is not admissible.
CREATE OR REPLACE FUNCTION fence_research.commit_terminal(
 p_scope text,p_epoch bigint,p_owner text,p_claim text,p_request text,
 p_model text,p_runtime text,p_outcome text,p_receipt text
) RETURNS text LANGUAGE plpgsql AS $$
DECLARE current_scope fence_research.scopes%ROWTYPE;
        execution fence_research.executions%ROWTYPE;
BEGIN
 SELECT * INTO current_scope FROM fence_research.scopes
 WHERE scope_hash=p_scope FOR UPDATE;
 IF NOT FOUND OR current_scope.state<>'active' OR current_scope.epoch<>p_epoch
    OR current_scope.owner_hash<>p_owner OR current_scope.claim_hash<>p_claim
    OR current_scope.expires_at<=clock_timestamp() THEN
    RAISE EXCEPTION 'terminal rejected by stale/revoked/expired fence';
 END IF;
 IF p_outcome NOT IN ('completed','failed','cancelled','possibly_accepted') THEN
    RAISE EXCEPTION 'not a terminal result';
 END IF;
 SELECT * INTO execution FROM fence_research.executions
 WHERE request_hash=p_request FOR UPDATE;
 IF NOT FOUND OR execution.scope_hash<>p_scope OR execution.epoch<>p_epoch
    OR execution.owner_hash<>p_owner OR execution.claim_hash<>p_claim
    OR execution.projection_generation<>current_scope.projection_generation
    OR execution.model_hash<>p_model OR execution.runtime_hash<>p_runtime THEN
    RAISE EXCEPTION 'terminal request owner/claim/model binding differs';
 END IF;
 IF execution.status<>'reserved' THEN
    IF execution.status=p_outcome AND execution.terminal_receipt_hash=p_receipt THEN
      RETURN 'duplicate';
    END IF;
    RAISE EXCEPTION 'conflicting immutable terminal receipt';
 END IF;
 UPDATE fence_research.executions
 SET status=p_outcome,terminal_receipt_hash=p_receipt,finished_at=clock_timestamp()
 WHERE request_hash=p_request;
 RETURN 'recorded';
END $$;


-- CRITICAL: PostgreSQL grants EXECUTE on new functions to PUBLIC by default.
-- A deployment must create and grant only to an authenticated broker role
-- with separately enforced identity/receipt verification, never a model
-- worker or web client. No broker role is created here.
REVOKE ALL ON SCHEMA fence_research FROM PUBLIC;
REVOKE ALL ON ALL TABLES IN SCHEMA fence_research FROM PUBLIC;
REVOKE EXECUTE ON ALL FUNCTIONS IN SCHEMA fence_research FROM PUBLIC;

-- Existing-object REVOKE alone is NOT a forward-looking privilege policy.
-- PostgreSQL grants PUBLIC EXECUTE to newly created functions by default.
-- Only a *global* default REVOKE for the CURRENT CREATING ROLE removes that
-- built-in grant. "IN SCHEMA fence_research" REVOKE cannot override it.
-- The disposable research migration uses one creation role; production
-- migration roles must each be configured and independently audited.
-- This alters creation defaults for this role in the disposable DATABASE,
-- not just this schema. NEVER run the research migration in production.
ALTER DEFAULT PRIVILEGES REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC;
