# Restricted broker SQL ACL — physical research checkpoint, 2026-10-24

**DRAFT / NO-GO. Not a service, not an authenticated broker, not an execution grant. No production PostgreSQL changes.**

## Source and fixture

The experiment uses the existing research-only schema.sql in Auto-Router PR #75. That original schema is not changed. The qualify_broker_acl.py entry point is PLAN_ONLY unless explicitly given --run. It uses one disposable cached postgres:17-bookworm container, network disabled, zero published ports, no mounts, no credentials, and exclusively synthetic 64-hex identifiers.

The disposable bootstrap creates three non-login, non-superuser PostgreSQL roles: fence_acl_owner, fence_acl_broker, and fence_acl_worker. The non-superuser owner role creates the schema and its functions; only that owner gets CREATE on this ephemeral database.

In the disposable test only, the four named grant, revoke, reserve, and terminal routines become SECURITY DEFINER under the non-superuser owner, with fixed execution search_path of pg_catalog, pg_temp and fully schema-qualified table access. The broker has schema USAGE and EXECUTE on only the four routines, with no direct table SELECT, INSERT, UPDATE or DELETE. The worker has neither schema usage nor function execution. Newly created functions remain unexecutable to both broker and worker.

**Important trust limit:** The fixture connects as the disposable database superuser and uses SET ROLE to exercise broker/worker PostgreSQL privileges. This verifies role ACLs, not independently authenticated broker connections, credential custody, network security, or isolated administration. This is not deployable broker code.

## Physical evidence — Xwing, PostgreSQL 17 Bookworm

From exact PR #75 source, the native result was PASS_RESTRICTED_SQL_ACL_ONLY_NOT_ADMISSION. The experiment proved that restricted broker calls to the approved four functions work, direct table reads and writes fail, untrusted worker function calls fail, fixed SECURITY DEFINER settings and the non-superuser owner are visible in PostgreSQL catalogs, and revocation rejects a stale reservation. Future routine creation does not reopen broker/worker function execution. Container networking was disabled; no ports or mounts were published, and a separate Docker readback confirmed disposable container removal.

## Reproduced owner/claim authority bypass — NOT SAFE TO PROMOTE

A restricted broker can still invoke grant_scope using invented scope, owner, and claim hashes. PostgreSQL confirms that the caller holds broker EXECUTE permissions, but **does not establish that those values represent an authenticated, current owner or approved Task**. The physical regression intentionally reports:

- broker_can_fabricate_owner_claim_without_external_verifier=true
- shared_epoch_admission_authorized=false
- provider_calls=0

Therefore these SQL functions must not be available directly to model workers, web clients, or untrusted callers who choose their own parameter values.

## Next independent acceptance required

A separately trusted broker must verify signature, issuer, audience, active owner-bound Task, current claim and revocation, projection generation, exact model/JTI and quota policy before ANY SQL call. A separately controlled resource-side observer must witness and cancel actual model effects and sign terminal receipts. Independent high-water and rollback protection, two-owner cross-node isolation, and in-flight revocation remain necessary.

Keep Auto-Router PR #75 DRAFT, knowledge issue #57 OPEN, and autonomous model/provider dispatch on HOLD.
