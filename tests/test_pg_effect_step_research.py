"""Read-only static tests for the isolated per-step revocation trial."""
from pathlib import Path
import ast

ROOT = Path(__file__).resolve().parents[1] / "research" / "fenced_authority"
SQL = (ROOT / "effect_step_check.sql").read_text()
RUNNER = (ROOT / "qualify_effect_step.py").read_text()


def test_explicit_disposable_opt_in_and_no_public_network():
    assert "PLAN_ONLY" in RUNNER
    assert 'parser.add_argument("--run", action="store_true")' in RUNNER
    assert '"--network", "none"' in RUNNER
    assert '"-v"' not in RUNNER
    assert '"--publish"' not in RUNNER
    assert '"docker", "rm", "-f", CONTAINER' in RUNNER
    ast.parse(RUNNER)


def test_check_locks_current_scope_and_exact_request():
    assert "FOR SHARE" in SQL
    assert "scope_hash=p_scope FOR SHARE" in SQL
    assert "request_hash=p_request FOR SHARE" in SQL
    for s in ("active_scope.state <> 'active'", "active_scope.epoch <> p_epoch",
              "active_scope.expires_at <= clock_timestamp()",
              "active_request.status <> 'reserved'",
              "active_request.model_hash <> p_model",
              "active_request.runtime_hash <> p_runtime"):
        assert s in SQL


def test_public_not_granted_any_effect_check():
    assert "REVOKE EXECUTE ON FUNCTION fence_research.assert_live_effect_step" in SQL
    assert "worker_sql_exec_denied" in RUNNER


def test_revocation_terminal_disconnect_and_claims_fail_closed():
    for phrase in ("revoked_stream_step_denied", "old_epoch_after_regrant_denied",
                   "terminal_stream_step_denied", "primary_unreachable_fails_closed",
                   "wrong_identity_model_request_denied"):
        assert phrase in RUNNER


def test_no_actual_effect_authority_claimed():
    assert '"provider_calls": 0' in RUNNER
    assert '"worker_models_started": 0' in RUNNER
    assert '"live_resource_effect_atomicity_proven": False' in RUNNER
    assert '"cross_primary_highwater_proven": False' in RUNNER
    assert '"execution_authorized": False' in RUNNER
    assert "It cannot lock a real HTTP/model effect" in SQL
