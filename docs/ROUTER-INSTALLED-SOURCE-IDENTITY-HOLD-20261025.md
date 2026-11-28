# Router installed-source identity acceptance (P0 #102)

Scope: [deployed router drift #102](https://github.com/scottjoyner/auto-router/issues/102). 
Independent Xwing auth #198 and archive custody #173 remain HOLD.

## Why a source merge and Docker restart were insufficient

October 25 x1-370 physical readback showed `git-router-1` running an image
whose actual **imported** `site-packages/auto_router/providers.py` and
`event_dispatcher.py` still contained the upstream-body reflection fixed in
merged PR #101. Its *effective bind-mounted provider configuration* was also
missing merged PR #100's optional Xwing token-file variable. The source
checkout remained 22 commits behind and had untracked configuration backups.

The reviewed source from `bd2bc73e...` passed 36 synthetic offline tests
per node. An isolated local OCI image was built, but that did not update or
attest the running Compose router. This procedure must distinguish image,
effective config, and real deployed module identities.

## The read-only gate

`scripts/router_installed_source_identity.py` runs **inside the target
Python environment**, without starting the router's ASGI lifespan. The
operator must supply three exact SHA-256 pins obtained from a trusted reviewed
build and effective-config manifest:

- installed `auto_router/providers.py` hash;
- installed `auto_router/event_dispatcher.py` hash;
- *effective* `/app/config/providers.yaml` hash (not an unrelated checkout).

It resolves `auto_router` through the Python import loader; uses bounded,
nonblocking `O_NOFOLLOW` reads; rejects symlinks including ancestors, unsafe
group/other writes, FIFOs, changed files and ambiguous paths; refuses the
known `response.text[:500]` reflection marker even if a supplied hash
matches, and requires the stable dispatch error string and optional Xwing
token-file field. It returns compact reason codes without printing actual
config contents, headers, tokens, environment values, model requests or
raw error bodies.

An exact match returns `SOURCE_IDENTITY_MATCH_ONLY` (exit 0), but
**`deployment_authorized=false`, `deployed_image_provenance_verified=false`,
and `asgi_lifespan_started=false`**. Any mismatch returns HOLD (exit 2).
These are *identity*, not functionality or independent attestation claims.
A privileged operator could provide hashes from unreviewed code: pin
custody and code-signing remain separate human/automation controls.

The Dockerfile now normalizes the baked `/app/config/providers.yaml` to
mode `0644`. This is necessary because a permissive checkout umask led to
a 0664 file in the original scratch-built image; the gate properly refused
it. Neither this source change nor the checker changes any live bind mount.

## Safe verification approach

On a trusted host with an independently reviewed exact checkout, source
the gate over standard input to a **one-shot** candidate container with
`--network none`, `--read-only`, dropped capabilities, no mounts/secrets
and an unprivileged UID. Supply three reviewed SHA256 values as CLI arguments
using `--providers-sha256`, `--dispatcher-sha256`, and `--config-sha256`.
The code is invoked with `python3 -B -` and defaults to
`/app/config/providers.yaml` for the third path. Do **not** use the host's
mutable `git-router:latest` or mount a host source checkout that shadows
the image-installed `auto_router` package.

The same gate can be streamed read-only through
`docker exec -i git-router-1 python3 -B - ...` to inspect the installed
running package and mounted configuration without restarting it. A HOLD
is not permission to `docker compose up`, hot-patch `site-packages`,
enable model dispatch, apply new token files or overwrite host backup files.

The code's synthetic tests:
```sh
python3 -B -m unittest discover -s tests -p test_router_installed_source_identity.py -q
python3 -B -m pytest -q tests/test_xwing_native_auth_token_optin.py \
    tests/test_provider_error_payload_redaction.py \
    tests/test_event_dispatcher_error_custody.py
```

## Remaining P0 promotion checks

Build from an immutable source commit, produce reviewed OCI image provenance,
retain rollback image/config snapshots, reconcile host checkout and effective
Compose mounts without overwriting private files, and run the candidate's
installed-package synthetic failure tests in strict isolation. **A normal
secure ASGI lifespan launches polling, outbox and backlog tasks**; do not
start even an apparently healthy staging server without separate worker,
quota and network containment. An import-only synthetic staging canary can
use an **in-memory** request-idempotency database and no network; no
production secrets, inference endpoints, or data mounts are used.

Only in an authorized maintenance window: perform an atomic, reversible
single-instance deployment; read back actual installed package, active
mounted config, state, identities, and bounded authenticated client checks;
monitor for unwanted behavior. Release remains HOLD until issue #102 has a
recorded production readback, #198's native Xwing auth path remains separate,
and no free-provider or historical archive gates have been bypassed.
