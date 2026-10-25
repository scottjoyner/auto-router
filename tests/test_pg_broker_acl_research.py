"""Offline broker ACL safeguards; no Docker, DB or inference used in tests."""
from pathlib import Path
import ast

ROOT = Path(__file__).resolve().parents[1] / "research" / "fenced_authority"
SRC = (ROOT / "qualify_broker_acl.py").read_text()


def test_default_plan_only_and_explicit_native_run():
    assert "PLAN_ONLY: --run required" in SRC
    assert 'parser.add_argument("--run", action="store_true")' in SRC
    ast.parse(SRC)


def test_network_volume_free_ephemeral_postgres_container():
    assert '"--network", "none"' in SRC
    assert '"--memory", "1024m"' in SRC
    assert '"--pids-limit", "128"' in SRC
    assert '"docker", "rm", "-f", CONTAINER' in SRC
    docker_run = SRC.split('["docker", "run",', 1)[1].split("],", 1)[0]
    assert '"--publish"' not in docker_run
    assert '"--mount"' not in docker_run
    assert '"-v"' not in docker_run
    assert 'IMAGE = "postgres:17-bookworm"' in SRC


def test_research_schema_unmodified_non_superuser_owner():
    assert 'schema = (ROOT / "schema.sql").read_bytes()' in SRC
    assert 'psql("SET ROLE " + OWNER_ROLE + ";\\n" + schema.decode()' in SRC
    assert "NOSUPERUSER NOCREATEDB NOCREATEROLE" in SRC


def test_function_definer_search_path_and_broker_only_privileges():
    assert '" SECURITY DEFINER;"' in SRC
    assert '" SET search_path TO pg_catalog, pg_temp;"' in SRC
    assert '"GRANT EXECUTE ON FUNCTION fence_research."' in SRC
    assert '"GRANT USAGE ON SCHEMA fence_research TO "' in SRC
    assert 'has_table_privilege(' in SRC
    assert 'has_function_privilege(' in SRC
    assert 'flags == "true:true"' in SRC


def test_deny_direct_dml_and_worker_invocation():
    assert "permission denied for table" in SRC
    assert "permission denied for schema" in SRC
    assert "revoked_attempt_denied" in SRC


def test_explicit_broker_cannot_prove_real_identity():
    assert "broker_can_fabricate_owner_claim_without_external_verifier" in SRC
    assert '"shared_epoch_admission_authorized": False' in SRC
    assert '"provider_calls": 0' in SRC


def test_research_role_fixture_not_installed_as_live_schema():
    schema = (ROOT / "schema.sql").read_text()
    for token in ("fence_acl_owner", "fence_acl_broker", "fence_acl_worker"):
        assert token not in schema
    assert "ALTER DEFAULT PRIVILEGES REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC;" in schema
