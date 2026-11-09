# LFM3 independent signer enrollment: offline Ed25519 key-control challenge

**READ FIRST — LOCAL CRYPTO IS NOT HUMAN IDENTITY PROOF.** This research-only procedure adds a real proof-of-key-control check to the existing author / gold-reviewer / independent-witness key roster. A caller-provided public-key roster, an Ed25519 signature, and a freshly randomized nonce **cannot establish that a person is independent**, authorized, trustworthy, or a different human controlling the other keys. The software never grants inference, model-value or production authorization. External case sourcing and the chronology of the new sealed data remain issue #157 HOLD.

## What a real external enrollment should look like

1. A human/organizational reviewer **independent of model and rule development** verifies the three prospective roles separately: **new-case author, independent gold reviewer, and timestamp witness**. Use an established trusted identity channel, obtain independent explicit consent, verify that each party is separate, and check their conflict-of-interest and data-access boundaries **outside this CLI**. Keep private identity records, consent and audit logs in approved restricted external custody, **not public GitHub or the model operator's tool logs**.
2. Require each individual to register their distinct, self-controlled **public Ed25519 key** out of band. The custodian's `lfm3.custody.trusted-keys.v1` JSON roster has the exact three-role key format already required by PR #156. The verifier independently records the public key's SHA-256 fingerprint and binds the identity/role to that fingerprint through a trusted human enrollment procedure. Neither a public key in a JSON file nor a plausible `key_id` name proves ownership or independence.
3. The **independent verifier**, not the model developer, issues a fresh nonce challenge for each role, and records the challenge file's SHA-256, public-key fingerprint, role, timestamp and intended one-time recipient in a trusted audit log. This CLI creates a cryptographically random local 32-byte nonce, but **does not authenticate the party issuing it**, timestamp its issuance or maintain a stateful spent-nonce ledger. Freshness and single use therefore remain separate **HOLD** until verified out of band.
4. Deliver the challenge plus the **exact detached signing bytes** to the intended person over an authenticated private channel. They inspect the request's purpose, role, key fingerprint and trusted issuing party **on their own machine** before signing with their own Ed25519 private key. Their private key **never leaves their machine** and must never be passed into Fleet Commander, GitHub, ChatGPT, an LFM3 inference provider, or the custodian's verification CLI.
5. The independent verifier receives only the 64-byte raw detached signature, checks the original immutable issued challenge against their own challenge log, confirms nonce was not used for an earlier enrollment, verifies Ed25519 signature structure with the independently enrolled public key, and **then separately records a human-attested identity/role proof**. The CLI checks only the mathematical signature and a strict role/commitment format; do not turn its PASS into a human-verification assertion.
6. Only after real independent enrollment, separate case authoring and gold review, the human roles may use the PR #169 prepare/verify/assemble signing payloads and PR #168 pre-witness timestamp-query workflow. The witness must obtain an independently trusted third-party timestamp on the exact new corpus commitment **before** the first authorized model request. The public contract-only FreeTSA token from PR #160 does not count.

## Offline key-control CLI

Place the independently checked **public** key roster in an owner-owned mode-0700 custodian folder with a mode-0600 or 0400 JSON file. Create separate owner-private destination files. The CLI uses existing no-symlink ancestor walking, bounded single-FD source reads, exclusive file creation and inode-checked rollback.

**Independent verifier prepares and records a role-specific random challenge:**

```bash
PYTHONPATH=. python3 -m scripts.lfm3_key_possession_challenge prepare \
  --key-roster /verified/private/three-role-public-key-roster.json \
  --role author \
  --challenge-output /verified/private/author-challenge.json \
  --payload-output /verified/private/author-challenge-bytes.bin
```

Repeat with `--role reviewer` and `--role witness` on independently chosen fresh challenges. A challenge includes the exact designated `key_id`, SHA-256 fingerprint of that role's Ed25519 public key, 32-byte random nonce, frozen prereg and model checkpoint commitments, explicit *not verified* identity and false inference/production claims. The detached signing payload is `b"lfm3:custody:key-possession:v1\\n" + canonical(challenge).encode("utf-8")`. Both files are exclusive 0600 and neither contains private keys, case notes, gold labels or model responses. The output command only reports an unsigned challenge digest and retains all trusted-identity/freshness flags as false.

**Person controlling that role's private key signs on their own machine** (illustrative OpenSSL syntax; never run on a machine holding other participants' private keys):

```bash
openssl pkeyutl -sign -rawin \
  -inkey /signer-only/ed25519-private.pem \
  -in /signer-reviewed/author-challenge-bytes.bin \
  -out /signer-only/author-challenge.sig
```

**Independent verifier verifies the raw detached signature on the original issued challenge:**

```bash
PYTHONPATH=. python3 -m scripts.lfm3_key_possession_challenge verify \
  --key-roster /verified/private/three-role-public-key-roster.json \
  --role author \
  --challenge /verified/private/author-challenge.json \
  --signature /verified/private/returned-author-challenge.sig
```

On success, the CLI returns `PASS_OFFLINE_KEY_SIGNATURE_STRUCTURE_ONLY` with `signature_matches_supplied_public_key=true` and a challenge digest. It **always** returns `independent_challenge_issuance_verified=false`, `challenge_freshness_and_single_use_verified=false`, `independent_human_identity_verified=false`, `signer_organizational_independence_verified=false`, `independent_case_custody_verified=false`, `pre_inference_seal_verified=false`, `permitted_to_infer=false` and `production_authorized=false`. The human auditor separately decides whether authenticated identities, freshness, consent, independent data access and external custody meet policy. A self-issued challenge with a signature is not a registration approval.

## Limitations and exact test evidence

- The challenge includes the *existing* frozen prereg SHA and model checkpoint SHA to prevent casual cross-study purpose confusion, but it does **not** attest a specific new case corpus because the author and reviewer may not have independently prepared or reviewed that corpus yet. Their later exact case-corpus signing payload is a **separate** stronger binding and remains mandatory.
- The challenge's role, key ID and SHA-256 public-key fingerprint are checked against a caller-supplied roster; the software requires distinct role keys but cannot know whether a single person controls several keys. A signature can also be replayed with the **same** old challenge; rejecting replay requires an authentic verifier-maintained issuance/spent-nonce log that is not supplied or faked here. A signature from one role cannot be transferred to another role, another nonce or an altered frozen contract.
- The prepare command uses a secure OS random 32-byte nonce; verification does **not** claim that a nonce was issued by the independent verifier, not colluding with the signer, or was unused. Neither command connects to any website, TSA, repository, model endpoint, production system or protected credential store.
- The synthetic test suite exercises all three role signatures with temporary in-memory Ed25519 keys, actual `openssl pkeyutl -sign -rawin` subprocesses, strict 64-byte responses and real private 0600 local files. Tests demonstrate role/nonce replay rejection against changed challenges, stale-challenge replay acceptance **with false freshness flags**, changed frozen source, malformed signatures, duplicate public keys, unsafe paths/modes and output collisions. They do **not** enroll any real individual.
- **Never merge/deploy** this draft research-only tooling as justification for LFM3 execution or public chat cutover. Independent author/reviewer/witness identity, sealed-before-inference new cases, trusted external TSA issuer, true model value, GPU custody and production all remain HOLD under #157.
