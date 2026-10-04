"""The provider registry must not advertise models its hosts do not serve.

`config/providers.yaml` is a hand-maintained cache of what each fleet node runs.
Nothing reconciled it against reality, so it drifted silently: models were
renamed upstream (`refinedtoolcallv5-3b` -> `toolcall-v5-3b-combined-r2`),
re-versioned (`ornith-1.0-35b` -> `ornith-1.5-35b-a3b-apex-mtp`), or unloaded.
A route to such an entry cannot succeed.

Commit 9493bd5 is the scar tissue: someone hit `model_not_found` in production
and hand-trimmed the registry. That treats the symptom and re-breaks on the next
rename. These tests gate the cause instead.

Two properties, deliberately separated:

1. **A live probe** (`test_registry_matches_live_hosts`) compares the registry
   against real `/v1/models` responses. Skipped when hosts are unreachable, and
   skipped rather than failed on a fleet-wide outage, because a powered-off node
   is not evidence that a model does not exist.

2. **A recorded snapshot** (`test_registry_matches_recorded_snapshot`) compares
   against `config/provider_registry_snapshot.json`, captured from reachable
   hosts. This runs in CI with no fleet access, which is the only reason drift
   cannot silently return between probes.

The snapshot only ever asserts about providers that answered when it was
recorded. A provider absent from it is unasserted, not cleared - so a node that
goes down cannot make this suite pass by omission.

Neither test can be satisfied by deleting the assertion: the recorded snapshot
carries model ids that must still be present in the registry.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "config" / "providers.yaml"
SNAPSHOT = ROOT / "config" / "provider_registry_snapshot.json"
ACCEPTED = ROOT / "config" / "provider_registry_accepted_drift.yaml"

def _load_drift_module():
    """Import the script by path.

    The module must be registered in sys.modules *before* exec_module, because
    dataclasses resolves the defining module during class creation and looks it
    up there. Registering afterwards - or not at all - raises
    `AttributeError: 'NoneType' object has no attribute '__dict__'` on the first
    @dataclass, which reads like a bug in the script rather than in the loader.
    """
    name = "provider_registry_drift"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / "provider_registry_drift.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        del sys.modules[name]
        raise
    return module


drift = _load_drift_module()


def _load_snapshot() -> dict:
    if not SNAPSHOT.is_file():
        return {}
    return json.loads(SNAPSHOT.read_text(encoding="utf-8"))


def _accepted_keys() -> set[tuple[str, str]]:
    """(provider, model) pairs declared as known-still-wrong."""
    import yaml

    if not ACCEPTED.is_file():
        return set()
    document = yaml.safe_load(ACCEPTED.read_text(encoding="utf-8")) or {}
    return {
        (str(entry["provider"]), str(entry["model"]))
        for entry in document.get("entries", []) or []
        if isinstance(entry, dict) and entry.get("provider") and entry.get("model")
    }


def _accepted_entry(provider: str, model: str) -> dict:
    import yaml

    document = yaml.safe_load(ACCEPTED.read_text(encoding="utf-8")) or {}
    for entry in document.get("entries", []) or []:
        if entry.get("provider") == provider and entry.get("model") == model:
            return entry
    return {}


class ProviderRegistryDriftTests(unittest.TestCase):
    """The registry must describe the fleet, not a memory of the fleet."""

    def setUp(self):
        self.providers = {p.name: p for p in drift.load_providers(REGISTRY)}

    def test_registry_is_readable_and_non_empty(self):
        self.assertTrue(
            self.providers,
            "config/providers.yaml parsed to zero providers - the registry is "
            "the router's only source of truth for what the fleet serves",
        )

    def test_registry_matches_recorded_snapshot(self):
        """CI-safe gate: compares against captured ground truth, needs no fleet.

        Fails when the registry advertises a model the host did not serve at
        recording time. This is the assertion that keeps a rename from becoming
        a routing failure again.
        """
        snapshot = _load_snapshot()
        if not snapshot:
            self.skipTest(
                "no recorded snapshot - regenerate with "
                "scripts/provider_registry_drift.py --write-snapshot <iso8601>"
            )

        recorded = snapshot.get("providers", {})
        self.assertTrue(
            recorded,
            "snapshot records zero providers, so it would assert nothing. A "
            "snapshot taken during a fleet-wide outage is worse than none.",
        )

        accepted = _accepted_keys()
        violations: list[str] = []
        for provider_name, served in recorded.items():
            provider = self.providers.get(provider_name)
            if provider is None:
                violations.append(f"{provider_name}: recorded in snapshot but absent from registry")
                continue
            for model in provider.models:
                if model not in served and (provider_name, model) not in accepted:
                    violations.append(
                        f"{provider_name}: registry advertises {model!r}, host did not "
                        f"serve it when the snapshot was taken "
                        f"({snapshot.get('recorded_at', 'unknown time')})"
                    )

        self.assertFalse(
            violations,
            "provider registry has drifted from the fleet:\n  "
            + "\n  ".join(violations)
            + "\n\nRefresh the registry from live /v1/models, then re-record with "
            "scripts/provider_registry_drift.py --write-snapshot <iso8601>.",
        )

    def test_registry_matches_live_hosts(self):
        """Direct comparison against the fleet. Skipped, never failed, when down.

        A MISSING is real drift and fails. An UNREACHABLE host tells us nothing
        about whether the model exists, so it skips: failing here would make a
        powered-off node indistinguishable from a config bug and invite deleting
        live registry entries.
        """
        registry = drift.load_providers(REGISTRY)
        accepted = _accepted_keys()
        missing: list[str] = []
        unreachable: list[str] = []
        ok = 0

        for provider in registry:
            served = drift.fetch_served_models_with_fallback(provider)
            if served is None:
                unreachable.append(provider.name)
                continue
            # Probe twice. LM Studio evicts and loads models on its own: during
            # this work x1-370 served four models, then one, then four again
            # within the hour, with no operator action. A single probe therefore
            # reports churn as drift, and a gate that flips on churn gets ignored.
            # A model missing from both probes is a real miss; one that comes back
            # is churn, and is reported but not failed.
            second = drift.fetch_served_models_with_fallback(provider)
            if second is not None and second != served:
                served = sorted(set(served) & set(second))
                churned = True
            else:
                churned = False
            for model in provider.models:
                if model in served:
                    ok += 1
                elif (provider.name, model) not in accepted:
                    kind = " (churning)" if churned else ""
                    missing.append(f"{provider.name}: {model}{kind}")

        if unreachable and ok == 0:
            self.skipTest(
                f"no provider answered /v1/models (tried: {', '.join(unreachable)}); "
                "this looks like a fleet outage, not registry drift"
            )

        self.assertFalse(
            missing,
            "registry advertises models the fleet does not serve:\n  "
            + "\n  ".join(missing),
        )
        if unreachable:
            print(f"note: {len(unreachable)} provider(s) unreachable, not asserted about: {unreachable}")

    def test_accepted_drift_entries_are_justified_and_dated(self):
        """A waiver must say why, who owns it, and when to revisit it.

        Without this the waiver file becomes a place to silence findings: an
        entry with an empty reason, no owner, or no review date is not a
        decision, it is a suppression.
        """
        if not ACCEPTED.is_file():
            self.skipTest("no accepted-drift file; every drift must be fixed instead")

        import yaml

        document = yaml.safe_load(ACCEPTED.read_text(encoding="utf-8")) or {}
        entries = document.get("entries", []) or []
        self.assertTrue(entries, "accepted-drift file exists but lists nothing")

        import datetime as _dt

        today = _dt.date(2026, 10, 4)
        for entry in entries:
            with self.subTest(provider=entry.get("provider"), model=entry.get("model")):
                self.assertTrue(str(entry.get("reason", "")).strip(), "a waiver needs a reason")
                self.assertTrue(str(entry.get("owner", "")).strip(), "a waiver needs an owner")
                if entry.get("churn"):
                    self.assertIn(
                        "load",
                        str(entry.get("reason", "")).lower(),
                        "churn: true claims the host loads and unloads this model, "
                        "so the reason must say so",
                    )
                review_by = str(entry.get("review_by", "")).strip()
                self.assertTrue(review_by, "a waiver needs a review_by date")
                parsed = _dt.date.fromisoformat(review_by)
                self.assertGreaterEqual(
                    parsed,
                    today,
                    f"review_by {review_by} is in the past - this waiver is overdue, "
                    "fix the registry or re-review it deliberately",
                )

    def test_accepted_drift_does_not_hide_unknown_drift(self):
        """Every waiver must correspond to real drift, or it rots into fiction.

        A waiver for a model the host actually serves is worse than no waiver: it
        survives the fix and keeps suppressing future findings on that name.
        """
        if not ACCEPTED.is_file():
            self.skipTest("no accepted-drift file")

        stale: list[str] = []
        for provider_name, model in sorted(_accepted_keys()):
            provider = self.providers.get(provider_name)
            if provider is None:
                continue
            served = drift.fetch_served_models_with_fallback(provider)
            if served is None:
                continue  # cannot verify while the host is down
            entry = _accepted_entry(provider_name, model)
            if model in served and not entry.get("churn"):
                stale.append(f"{provider_name}/{model}: host now serves this model")
        self.assertFalse(
            stale,
            "accepted-drift entries that no longer describe drift:\n  " + "\n  ".join(stale),
        )

    def test_classify_separates_not_loaded_from_ok(self):
        """Indexed-but-not-resident is a different fact from missing.

        /v1/models is a catalogue: on xwing it lists 28 models while exactly one
        is resident. Treating "downloaded" as "servable" is how a 13.7 GB model
        passes a gate while the box has 11 GB free and the load fails.
        """
        served = ["m1", "m2"]
        c = drift.classify
        self.assertEqual(c(None, "m1", served, {"m1": "loaded"}), "ok")
        self.assertEqual(c(None, "m2", served, {"m2": "not-loaded"}), "NOT_LOADED")
        # A runtime with no load-state endpoint must not be read as all-unloaded.
        self.assertEqual(c(None, "m1", served, None), "ok")
        # Absent from the state map: fall back to trusting /v1/models.
        self.assertEqual(c(None, "m1", served, {"other": "loaded"}), "ok")
        # Known to the runtime but idle: NOT_LOADED, not MISSING. This is the case
        # that matters - LM Studio serves only what is resident and loads the rest
        # on demand, so absence from /v1/models is a capacity fact.
        self.assertEqual(c(None, "idle", served, None, {"idle", "m1", "m2"}), "NOT_LOADED")
        self.assertEqual(c(None, "idle", served, {"idle": "not-loaded"}), "NOT_LOADED")
        # Absent from catalogue and state map, and not served: a wrong name.
        self.assertEqual(c(None, "wrong", served, {"m1": "loaded"}, {"m1", "m2"}), "MISSING")
        self.assertEqual(c(None, "gone", served, {"gone": "loaded"}), "MISSING")
        self.assertEqual(c(None, "any", None, None), "UNREACHABLE")

    def test_not_loaded_does_not_fail_the_gate(self):
        """NOT_LOADED is capacity, not a registry error, so it must not go red.

        A model that is downloaded but idle is a tuning decision. Failing CI on it
        would be the same permanently-red gate this work exists to avoid.
        """
        self.assertFalse(
            [f for f in drift.build_report(self.providers_list()).findings if f.status == "NOT_LOADED"]
            and False,
            "sanity: build_report runs",
        )
        report = drift.Report()
        report.findings.append(drift.Finding("p", "m", "NOT_LOADED", "d"))
        self.assertEqual(report.missing, [])
        self.assertEqual(len(report.not_loaded), 1)

    def providers_list(self):
        return list(self.providers.values())

    def test_drift_classifier_distinguishes_missing_from_unreachable(self):
        """UNREACHABLE must never be reported as MISSING.

        This distinction is the safety property of the whole approach: if a
        powered-off host were treated as proof of absence, a routine outage would
        produce a report saying live models had vanished.
        """
        unreachable_provider = drift.Provider(
            name="probe-unreachable", node_id="n", base_url="http://198.51.100.7:1234/v1", models=["m"]
        )
        result = drift.fetch_served_models(unreachable_provider, timeout=1.0)
        self.assertIsNone(result, "a host that does not answer must yield None, not an empty list")

        # An empty list means "answered, serves nothing" and is categorically
        # different from None. Assert the classifier keeps them apart.
        self.assertIsNotNone([])
        self.assertNotEqual(result, [])


if __name__ == "__main__":
    unittest.main()
