# Independent author and gold reviewer: exact detached Ed25519 signing handoff

**Research-only workflow.** This fills a practical blocker before draft #168's prewitness RFC3161 query: independently enrolled author and gold reviewer must receive the **exact role-domain-separated bytes** to sign with their *own* offline Ed25519 keys, and the custodian needs to verify/assemble their detached responses into the previously frozen prewitness bundle. The verifier already existed; these inputs and outputs did not have a safe operator-facing command. **No real independent signer, gold dataset or external timestamp has been supplied.** Nothing here enrolls key owners or makes a proof of their identities.

## Prerequisites — external humans, not software claims

A case author and separate gold reviewer must have **already** independently completed and adjudicated a *new, confidential* 36–250-note fictional/sanitized dataset under frozen `lfm3.prospective.cases.v1`. Never reuse the 36 developer-created observed notes as a held-out corpus. An independent human custodian must verify real identities and separately authenticate Ed25519 public keys from **three distinct owners**: author, reviewer and later independent witness. The `trusted-keys.v1` roster contains public keys only, but being present in a file does **not** prove independent key ownership. Keep corpus, gold, signer private keys and any real identifiers **off GitHub** and away from inference operators until an actual independent seal.

For this offline CLI, place the full dataset and public-key roster under a private mode-0700 custodian directory with owner-only mode-0600 or 0400 regular input files. `scripts.lfm3_signer_payload_handoff` uses the existing #165/#166 no-symlink directory-FD and bounded file reader. The CLI never reads or generates any private signing key.

## Phase A — export two role-specific payloads (no signatures yet)

```bash
PYTHONPATH=. python3 -m scripts.lfm3_signer_payload_handoff prepare \
  --dataset /approved/private/sealed-cases-and-gold.json \
  --key-roster /approved/private/externally-authenticated-public-roster.json \
  --author-payload /approved/private/author-role-payload.bin \
  --reviewer-payload /approved/private/reviewer-role-payload.bin
```

The pure verifier checks the frozen prereg SHA, source dataset balance, canonical dataset and gold checksum, unchanged LFM3 checkpoint and three **distinct** caller-provided public keys. The CLI writes two different, **exact UTF-8 role-domain-separated signing payloads**, both mode-0600, each into an owner-private mode-0700 directory, using the exclusive two-output writer from #166. It prints only an unsigned role-payload checksum per role; it does **not** print notes, gold, original case IDs or the full dataset commitment. A SHA is a *commitment*, not confidentiality for guessable private content: distribute these payload files only through approved signer-to-custodian channels.

Each signer should separately confirm the dataset commitment they are authorizing against the **independently authenticated** original dataset and frozen research contract, not blindly sign bytes supplied by a developer. The payload has no raw case narratives, but contains the full canonical dataset hash, prereg hash, expected checkpoint and role. Both role payloads are exact existing #156 `role_payload` values, so the signatures remain valid when #168 later constructs the final three-role handoff.

## Phase B — sign on the *signer's own trusted machine*, not in this tool

For illustration only, a signer who independently controls an appropriately secured Ed25519 key may use OpenSSL:

```bash
openssl pkeyutl -sign -rawin \
  -inkey /signer-only/author-ed25519-private-key.pem \
  -in /signer-approved/author-role-payload.bin \
  -out /signer-only/author.sig
```

The reviewer uses their **different** key and `reviewer-role-payload.bin`. Each detached result is exactly **64 raw bytes**, not base64/ASCII, a PEM certificate, a JSON Web Signature or a package that includes private-key data. A human should never copy their private key into the model prompt, GitHub, Fleet Commander, the custodian server or the CLI; only the two *detached signatures* return to the private custodian by an independently authenticated channel. This script neither creates nor imports the signers' private keys; any synthetic Ed25519 signing done inside unit tests uses ephemeral keys in memory and proves **zero** real-world independence.

## Phase C — verify returned detached signatures and assemble prewitness package

After authenticating the two signing responses separately, place each raw 64-byte signature in a mode-0600 private regular file. The custodian then runs:

```bash
PYTHONPATH=. python3 -m scripts.lfm3_signer_payload_handoff assemble \
  --dataset /approved/private/sealed-cases-and-gold.json \
  --key-roster /approved/private/externally-authenticated-public-roster.json \
  --author-signature /approved/private/author.sig \
  --reviewer-signature /approved/private/reviewer.sig \
  --prewitness-output /approved/private/author-reviewer-prewitness.json
```

The CLI independently recomputes the exact role payloads, cryptographically verifies each detached signature with the specified role's distinct public key, verifies the previously frozen `inspect_prewitness` contract, and writes a single **new, exclusive mode-0600** prewitness JSON file. The output is only the original #168 `lfm3.custody.prewitness-author-review.v1` bundle with canonical commitment and two signature records. It has **no witness signature, no claimed TSA issuance, no inference or production authority**. The stdout is just a redacted status and bundle SHA-256; it contains no case text or private keys. Wrong role, changed data, malformed signature, duplicate keys, reused roster, symlink and unsafe/private input paths or pre-existing destination must HOLD without overwrite.

## Phase D — existing offline nonce query and later genuine external witness

Once independent humans approve the local prewitness structure, run #168's existing:

```bash
PYTHONPATH=. python3 -m scripts.lfm3_prepare_prewitness_tsq \
  --dataset /approved/private/sealed-cases-and-gold.json \
  --prewitness /approved/private/author-reviewer-prewitness.json \
  --key-roster /approved/private/externally-authenticated-public-roster.json \
  --tsq-output /approved/private/nonce-bearing-request.tsq
```

An independently enrolled witness must then obtain a real TSR from a separately approved TSA, confirm authentic issuer/nonce/time/source custody *before any inference*, sign the returned token hash using their own key, and run the existing #156/#159 validators. The public-contract FreeTSA timestamp in #160 is **not** a case-corpus seal. A mathematical crypto PASS with self-generated keys cannot prove independent identity or chronology.

## Explicit safeguards and nonclaims

- The `prepare` command creates signing bytes **but never signs**; the `assemble` command verifies caller-supplied detached signatures **but never generates/reads signing keys**. Both use local private files only; they do not call a network, TSA, model, provider or production tool.
- Both outputs are exclusive/no-overwrite, use private mode-0700 parent directories and mode-0600 payload/assembly files, and retain the earlier non-atomic crash caveat. The private signature input decoder is the same single-FD bounded regular-file reader as the existing private JSON decoder. Full dataset and gold remain private and unuploaded.
- Neither program identifies the humans controlling the keys, establishes a trusted key registry, seals a corpus externally, or authorizes a model call. Every PASS explicitly keeps `independent_author_verified=false`, `key_ownership_independently_authenticated=false`, `external_case_custody_verified=false`, `pre_inference_seal_verified=false`, `permitted_to_infer=false`, `model_value_verified=false` and `production_authorized=false`.
- Only a real independent human/org can enroll signers, create and adjudicate a novel gold set, prove external timestamp chronology, and authorize an evaluation under the existing controls in [issue #157](https://github.com/scottjoyner/auto-router/issues/157). All draft research PRs remain **unmerged**, all blocked model-inference work remains **HOLD**, and production remains **HOLD**.
