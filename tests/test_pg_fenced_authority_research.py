"""Static deny-only checks for native disposable PostgreSQL fence experiment.

These checks do not connect to any database and do not enable serving.
Native concurrency proof requires manual --run in the research harness.
"""
from pathlib import Path
import unittest

ROOT=Path(__file__).resolve().parents[1]/"research/fenced_authority"


class ResearchFenceContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.schema=(ROOT/"schema.sql").read_text()
        cls.runner=(ROOT/"qualify_pg.py").read_text()

    def test_research_not_production_danger_label(self):
        self.assertTrue(self.schema.startswith("-- EXPERIMENT ONLY"))
        self.assertIn("DO NOT apply on a production",self.schema)
        self.assertIn("PLAN_ONLY",self.runner)

    def test_default_run_has_no_side_effects(self):
        self.assertIn('if not args.run:',self.runner)
        self.assertIn('parser.add_argument("--run",action="store_true")',self.runner)

    def test_no_host_ports_or_networking(self):
        self.assertIn('"--network","none"',self.runner)
        self.assertNotIn('"--publish"',self.runner)
        self.assertNotIn('"--network","host"',self.runner)
        self.assertIn('["docker","rm","-f",CONTAINER]',self.runner)

    def test_private_schema_functions_not_public_execute(self):
        self.assertIn("REVOKE ALL ON SCHEMA fence_research FROM PUBLIC;",self.schema)
        self.assertIn("REVOKE EXECUTE ON ALL FUNCTIONS IN SCHEMA fence_research FROM PUBLIC;",self.schema)
        self.assertIn("untrusted_sql_role_execute",self.runner)

    def test_shared_postgres_transactions_fence_revocation(self):
        self.assertIn("FOR UPDATE",self.schema)
        self.assertIn("WHERE scope_hash=p_scope AND epoch=p_epoch",self.schema)
        self.assertIn("terminal rejected by stale/revoked/expired fence",self.schema)

    def test_token_jti_cannot_cross_epochs(self):
        self.assertIn("current_attempt.epoch<>p_epoch",self.schema)
        self.assertIn("jti_hash,scope_hash,epoch,owner_hash",self.schema)
        self.assertIn("unspent_old_epoch_jti_reuse",self.runner)

    def test_transaction_uses_request_and_receipt_uniqueness(self):
        self.assertIn("request_hash text PRIMARY KEY",self.schema)
        self.assertIn("CREATE UNIQUE INDEX unique_verified_receipt_digest",self.schema)
        self.assertIn("conflicting immutable terminal receipt",self.schema)

    def test_safe_terminal_states_have_uncertain_option(self):
        self.assertIn("'possibly_accepted'",self.schema)
        self.assertIn("'revoked'",self.schema)
        self.assertIn("trusted receipt verifier",self.schema)

    def test_single_primary_not_cross_host_consensus(self):
        self.assertIn("no_cross_host_consensus_proven",self.runner)
        self.assertIn("PASS_SINGLE_PRIMARY_MECHANISM_NOT_PRODUCTION",self.runner)

if __name__=="__main__":unittest.main()
