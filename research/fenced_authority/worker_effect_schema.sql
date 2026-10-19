-- EXPERIMENT ONLY; disposable PostgreSQL extension for physical worker output fencing.
-- A SQL row is an ATOMIC ACCEPTED CHUNK witness, not actual remote client delivery.
-- Calls must only originate from trusted validated broker; never expose to workers.
CREATE TABLE fence_research.accepted_chunks (
  request_hash text NOT NULL REFERENCES fence_research.executions(request_hash),
  sequence integer NOT NULL CHECK (sequence BETWEEN 1 AND 100000),
  scope_hash text NOT NULL REFERENCES fence_research.scopes(scope_hash),
  epoch bigint NOT NULL,
  content_sha256 text NOT NULL CHECK (content_sha256 ~ '^[0-9a-f]{64}$'),
  committed_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  PRIMARY KEY (request_hash, sequence)
);

CREATE OR REPLACE FUNCTION fence_research.accept_chunk(
 p_scope text,p_epoch bigint,p_owner text,p_claim text,p_projection bigint,
 p_request text,p_sequence integer,p_content_sha256 text
) RETURNS integer LANGUAGE plpgsql AS $$
DECLARE current_scope fence_research.scopes%ROWTYPE;
        execution fence_research.executions%ROWTYPE;
        last_seq integer;
BEGIN
 SELECT * INTO current_scope FROM fence_research.scopes
 WHERE scope_hash=p_scope FOR UPDATE;
 IF NOT FOUND OR current_scope.state<>'active' OR current_scope.epoch<>p_epoch
    OR current_scope.owner_hash<>p_owner OR current_scope.claim_hash<>p_claim
    OR current_scope.projection_generation<>p_projection
    OR current_scope.expires_at<=clock_timestamp() THEN
    RAISE EXCEPTION 'effect fenced: authority missing/revoked/expired';
 END IF;
 SELECT * INTO execution FROM fence_research.executions
 WHERE request_hash=p_request FOR UPDATE;
 IF NOT FOUND OR execution.scope_hash<>p_scope OR execution.epoch<>p_epoch
    OR execution.owner_hash<>p_owner OR execution.claim_hash<>p_claim
    OR execution.projection_generation<>p_projection OR execution.status<>'reserved' THEN
    RAISE EXCEPTION 'effect fenced: request no longer active';
 END IF;
 SELECT COALESCE(MAX(sequence),0) INTO last_seq
 FROM fence_research.accepted_chunks WHERE request_hash=p_request;
 IF p_sequence<>last_seq+1 THEN
    RAISE EXCEPTION 'effect fenced: chunk sequence replay or gap';
 END IF;
 INSERT INTO fence_research.accepted_chunks
 (request_hash,sequence,scope_hash,epoch,content_sha256)
 VALUES (p_request,p_sequence,p_scope,p_epoch,p_content_sha256);
 RETURN p_sequence;
END $$;

REVOKE ALL ON TABLE fence_research.accepted_chunks FROM PUBLIC;
REVOKE EXECUTE ON FUNCTION fence_research.accept_chunk(
text,bigint,text,text,bigint,text,integer,text) FROM PUBLIC;
