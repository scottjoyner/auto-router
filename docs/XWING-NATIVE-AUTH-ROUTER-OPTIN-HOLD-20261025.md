# Xwing native LM Studio authentication — router-side opt-in (HOLD)

Owner: [knowledge security #198](https://github.com/scottjoyner/knowledge/issues/198).
Related: [fleet-llm-profiles draft #27](https://github.com/scottjoyner/fleet-llm-profiles/pull/27).

## Exact change

The existing `OpenAICompatibleProvider` already supports `api_key_file` and
reads a file-backed bearer token into process memory with owner/mode/type
checks. Missing or unsafe configured token files refuse the provider instead
of silently falling back to unauthenticated HTTP.

The `lmstudio-xwing` entry in `config/providers.yaml` now additionally
supports:

```yaml
api_key_file: ${LMSTUDIO_XWING_API_KEY_FILE:-}
```

**Default is blank.** Nothing changes for the running router until an
operator sets the **path variable**, mounts the corresponding owner-readable,
private file into the router process, and explicitly rolls out the reviewed
configuration. The variable carries a *filename only*, never the token.

Only `lmstudio-xwing` :1234 is changed. The separate Xwing Bonsai2
server on :18270 retains its existing `TERNARY_BONSAI2_API_KEY` handling.

## Local test evidence and boundaries

`tests/test_xwing_native_auth_token_optin.py` runs without importing the
router application bootstrap: it loads actual `models.py`, `providers.py`
and `provider_credential_file.py` in a temporary Python package namespace,
and extracts the real `_expand_env` AST from `config.py`. It exercises:

- Unset variable: enabled Xwing provider and tokenless header unchanged.
- Set private 0600 file: existing provider produces an in-memory bearer
  header; its serialized configuration does not contain the token.
- Missing, world-readable, symlink and directory file: token-required
  mode fails closed and cannot select anonymous fallback.
- Token-file + API-key-env conflict: rejects selection.
- Other provider definitions: do not inherit this Xwing path.
- No HTTP or model generation in any test.

The temporary-package import avoids installing dependencies into a running
node or initializing Auto-Router's global benchmark policy; it is not an
end-to-end router launch. Verified 9/9 tests on x1-370 and Xwing from the
same exact GitHub branch, in automatically removed scratch checkouts.

## Required future physical roll-out (not performed)

1. Identify every Xwing consumer (actual router process, x1 AssistX,
   Hermes/agent workers, Xwing own health, mobile and browser access).
   Catalogue which clients need native /api/v1 versus /v1 support.
2. Authorized owner issues individual scoped LM Studio API tokens inside
   Xwing's native management UI. No plaintext token in Git, GitHub, chat,
   environment values, process argv or unattended logs.
3. Securely mount a single chosen token into **the router container's**
   identity/mount namespace: absolute path, regular file, owned by the
   effective UID, no group/other permission bits, mode 0400/0600.
   Set `LMSTUDIO_XWING_API_KEY_FILE` to the *in-container path* only.
   Verify no accidental environment-based competing bearer header.
4. Before changing LM Studio's authentication setting, verify the
   container's file access/headers and all other identified clients in
   isolated non-generation probes. Merely returning HTTP 200 does not
   prove token authentication while the server accepts anonymous GETs.
5. Enable native `Require Authentication` only in a reviewed
   change window with a client-by-client migration and tested rollback.
   Confirm no-token/wrong-token/revoked-token **denials** and legitimate
   token **positive** catalog access from required Tailnet/loopback
   client identities, with preserved model residency and tool access.
6. Verify credential revocation, fleet gateway error traces, client
   recovery, loaded model catalog, and dependency health before release.

## Non-authority

This draft does NOT provision any token, mount files, restart containers,
alter LM Studio, change Tailnet ACLs/firewall, generate models or grant
quota/agent execution. Native Xwing anonymous/invalid-token catalogs
were still HTTP 200 at the last physical verification. Correct client
header construction alone is not proof that the server enforces it.

**Keep draft / HOLD.** Do not merge/deploy or enable native Xwing auth based
only on 9/9 synthetic tests. Hosted exact-head CI and full positive/negative
server authentication acceptance remain separate release gates.
