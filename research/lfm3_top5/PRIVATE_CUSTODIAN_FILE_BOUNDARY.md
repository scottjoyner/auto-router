# LFM3 offline custodian: private file boundary hardening

**Research-only.** This change hardens the file *input* boundary on both opt-in offline custodian CLIs: `scripts.lfm3_prepare_blind_packet` (gold-free packet export) and `research.lfm3_top5.blind_capture_reconciliation` (private scoring reconciliation). Neither script starts model inference, contacts a provider, changes production, or authenticates independent evidence. No frozen #152 scorer, #153 session rule, #156 three-role signer contract, #158 primary selector, #159/#160 timestamp logic, #161 model–rule gate, #162/#163 packet format or #164 static schema policy is changed.

## Concrete defect corrected

The earlier reconciliation CLI passed **arbitrary file paths** to an unbounded `json.load()`, allowing an oversized, potentially attacker-controlled four-session capture to consume excessive memory or delay evaluation. The earlier packet exporter had size caps but still followed source-file symlinks and accepted permissively readable source files. Since these files can contain entire fictional source cases, gold answers, private alias maps, or raw model completions, the local offline custodian must fail closed at the filesystem boundary **before** parsing and scoring.

Both CLIs now use a single `scripts/lfm3_private_json_reader.py` with a read-only file-descriptor interface:

- Requires a **single-linked, ordinary, nonempty regular file**, owned by the executing custodian UID and readable by that owner. Source input mode must have **no group or other access**, nor any execute bit (owner-only `0400` or `0600` are acceptable). An existing world/group-readable or executable input is **HOLD**, never auto-chmodded.
- Uses `os.open(O_RDONLY|O_NOFOLLOW|O_NONBLOCK)` so a final-component symlink cannot be followed and a named pipe cannot hang the process. The helper requires these OS capabilities; it **does not silently downgrade** if they are absent. Filesystem device, directory, hard link, empty file or missing file => HOLD.
- Checks `fstat()` metadata before and after the bounded read for owner, file type, size, inode, device, mtime and ctime stability. This is a bounded local consistency test, **not** protection from an attacker who controls the same UID or process memory. Private parent directory ACLs, mount permissions, symlinked parent path components, backup handling, secure deletion, and external signer/custody policy remain explicit operator responsibilities.
- Applies the same JSON decoder semantics as before: strict UTF-8, **duplicate-key rejection** and nonfinite-constant rejection. No error path prints sensitive source text or file content; CLI exits with code 2 and a redacted HOLD on invalid input.
- Enforces the following maximum input bytes (read-only; no truncation accepted):

| File | Limit |
| --- | ---: |
| Frozen prereg manifest | 64 KiB |
| Original signed dataset with gold | 2 MiB |
| Three-role signature bundle | 64 KiB |
| Public signer roster | 64 KiB |
| Gold-free model-facing packet | 2 MiB |
| Private opaque ID receipt | 256 KiB |
| Four-stage blinded capture | 16 MiB |

The exporter only uses the **dataset, bundle and roster** limits. The reconciler uses all seven. These caps exceed the frozen study's stated 250-case/four-session upper bounds, including reasonable JSON spacing, but a future larger workload must be approved explicitly rather than silently raising the limits or bypassing checks.

## Reproduction and evidence

`tests/test_lfm3_blind_case_intake.py` and `tests/test_lfm3_blind_capture_reconciliation.py` exercise the two actual offline Python CLI subprocesses using fabricated fictional cases and ephemeral in-memory signing keys. They assert genuine private `0600` input and output file permissions, successful normal projection/reconciliation, strict no-gold stdout, and HOLD on 0644/0640/0604/0700 file modes, source symlink/hardlink/FIFO/directory paths, oversized **otherwise valid JSON**, missing/empty file, duplicate keys and nonfinite JSON constants. They check that invalid exporter sources produce **no packet or receipt**, and incorrect reconciliation never returns the original IDs or notes.

This adds only code, tests, research protocol and no-deploy workflow coverage. It **does not** establish signer ownership, third-party TSA approval, external source custody, accurate model classification, real GPU execution or production admission. The frozen dev cases were already seen, independent gold review remains missing, and a previous proposed generation call was blocked before any execution. Independent custody #157, model-value and production **HOLD**.
