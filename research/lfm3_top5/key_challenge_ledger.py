"""Research-only verifier-controlled local issue-and-spend nonce ledger.

This is a LOCAL duplicate-claim control, never independent human identity,
proof of external issuer trust, sealed corpus custody, or inference permission.
The directory must be verifier-owned, private mode 0700, on a trusted filesystem.
"""
from __future__ import annotations

import os
import fcntl

from .key_possession_contract import (
    hold as base_hold,
    issue_local_challenge,
    challenge_signing_bytes,
    verify_detached_key_possession,
)
from .prospective_holdout_gate import canonical, sha
from scripts.lfm3_private_json_reader import read_private_json
from scripts.lfm3_private_output_writer import (
    _target_dirfd, _owned_create, _rollback,
    write_private_projection_pair,
)

ISSUE_SCHEMA = "lfm3.custody.local-challenge-issuance.v1"
SPEND_SCHEMA = "lfm3.custody.local-challenge-spend.v1"


def hold(reason: str) -> dict:
    result = base_hold(reason)
    result["local_issuance_record_matched"] = False
    result["local_one_time_claim_recorded"] = False
    return result


def _ledger_fd(directory: str) -> int:
    # _target_dirfd walks absolute ancestry via pinned no-follow FDs, then
    # verifies final directory belongs to the current custodian and is 0700.
    if type(directory) is not str or not directory or directory == "/":
        raise ValueError("invalid_verifier_private_ledger_directory")
    fd, _ = _target_dirfd(directory.rstrip("/") + "/nonce-record")
    return fd


def _lock_verifier_ledger(fd: int) -> None:
    # A complete issued record must not be consumed before the issuer's
    # directory fsync returns. Use one bounded, cooperative directory lock
    # around publication and consumption; contention always fails closed.
    # This does NOT protect against noncooperating same-UID adversaries,
    # filesystem rollback or a host with broken advisory locking.
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)


def _issue_record(challenge: dict) -> dict:
    return {
        "schema": ISSUE_SCHEMA,
        "challenge_sha256": sha(challenge),
        "role": challenge["role"],
        "key_id": challenge["key_id"],
        "ed25519_public_key_sha256": challenge["ed25519_public_key_sha256"],
    }


def _file_name(prefix: str, digest: str) -> str:
    # sha() is the lower-case 64-hex canonical digest of validated challenge.
    if (type(digest) is not str or len(digest) != 64 or
            any(c not in "0123456789abcdef" for c in digest)):
        raise ValueError("invalid_challenge_digest")
    return prefix + digest + ".json"


def issue_to_private_ledger(roster: object, role: object, ledger_dir: str,
                            challenge_output: str, payload_output: str) -> dict:
    """Issue new random nonce; store immutable verifier-local record LAST.

    If writing either file or record fails, fail closed. Partial output files
    may remain after a crash; an unrecorded challenge is NOT redeemable.
    """
    result, challenge = issue_local_challenge(roster, role)
    if challenge is None:
        return hold("cannot_issue_challenge_" + result["reason"])
    checked, payload = challenge_signing_bytes(roster, role, challenge)
    if payload is None:
        return hold("cannot_derive_challenge_signing_bytes")
    fd = None
    created = []
    try:
        # Pin/validate the verifier-controlled private store before publishing.
        fd = _ledger_fd(ledger_dir)
        _lock_verifier_ledger(fd)
        record = _issue_record(challenge)
        name = _file_name("issued-", record["challenge_sha256"])
        # Refuse accidental reuse and pre-existing final symlinks without
        # exposing any private metadata.
        try:
            os.stat(name, dir_fd=fd, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            return hold("challenge_already_in_local_ledger")
        write_private_projection_pair(
            challenge_output, canonical(challenge) + "\n",
            payload_output, payload.decode("utf-8"),
        )
        # Complete both private output writes before publishing the issuance.
        _owned_create(fd, name, (canonical(record) + "\n").encode(), created)
        os.fsync(fd)
    except (OSError, ValueError, TypeError, OverflowError, RecursionError):
        # On ordinary failure, remove only *our own* issuance inode.
        # Crash-before-rollback and same-UID tampering remain external risks.
        if fd is not None:
            _rollback(created)
        return hold("local_issue_output_or_commit_failed")
    finally:
        if fd is not None:
            os.close(fd)
    return {
        **hold("local issuance record written; independent verifier identity not proven"),
        "decision": "PASS_OFFLINE_LOCAL_CHALLENGE_ISSUED_ONLY",
        "challenge_shape_verified": True,
        "local_issuance_record_matched": True,
        "role": role,
        "challenge_sha256": sha(challenge),
    }


def consume_once(roster: object, role: object, challenge: object,
                 signature: object, ledger_dir: str) -> dict:
    """A successful claim atomically creates an exclusive spent marker.

    Concurrent attempts for one valid issued challenge: at most one PASS.
    A failed signature does not spend the challenge. Any uncertain write
    leaves a spent marker behind when possible and returns HOLD, never PASS.
    """
    checked = verify_detached_key_possession(roster, role, challenge, signature)
    if checked["decision"] != "PASS_OFFLINE_KEY_SIGNATURE_STRUCTURE_ONLY":
        return hold("unverifiable_key_control_signature_" + checked["reason"])
    fd = None
    try:
        fd = _ledger_fd(ledger_dir)
        _lock_verifier_ledger(fd)
        record = _issue_record(challenge)
        digest = record["challenge_sha256"]
        issued = read_private_json(_file_name("issued-", digest),
                                   limit=4096, dir_fd=fd)
        # Reconstructed typed record includes role and pinned key fingerprint.
        if type(issued) is not dict or issued != record or set(issued) != set(record):
            return hold("local_issuance_record_mismatch")
        spent = {
            "schema": SPEND_SCHEMA,
            "challenge_sha256": digest,
            "role": role,
            "signature_sha256": __import__("hashlib").sha256(signature).hexdigest(),
        }
        # Deliberately do NOT rollback a spent marker after an uncertain
        # write or fsync error; uncertainty must fail closed against replay.
        _owned_create(fd, _file_name("spent-", digest),
                      (canonical(spent) + "\n").encode(), [])
        os.fsync(fd)
        return {
            **hold("local one-time claim recorded; external identity still unverified"),
            "decision": "PASS_OFFLINE_LOCAL_ONE_TIME_KEY_CLAIM_ONLY",
            "challenge_shape_verified": True,
            "signature_matches_supplied_public_key": True,
            "local_issuance_record_matched": True,
            "local_one_time_claim_recorded": True,
            "role": role,
            "challenge_sha256": digest,
        }
    except FileExistsError:
        return hold("local_challenge_already_spent")
    except (OSError, ValueError, TypeError, KeyError,
            OverflowError, RecursionError, UnicodeError):
        return hold("missing_unsafe_or_untrusted_local_challenge_ledger")
    finally:
        if fd is not None:
            os.close(fd)
