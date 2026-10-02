"""Scope analysis for the S1 stale-context-cache defect.

``_project_live_models`` calls ``snapshot.model_for(...)`` and then replaces
``snapshot.models`` / ``provider.models`` **in place**. ``ContextSnapshot`` memoises
``all_models()`` and ``model_for()`` in ``PrivateAttr`` caches that nothing
invalidates, and ``signal_registry.hydrate_context`` propagates them onward via
``model_copy`` (pydantic copies ``__pydantic_private__`` by value).

This module pins **exactly** what that does and does not affect, so the fix can be
scoped correctly. The conclusion after measurement:

* **Affected** — the memoised accessors (``all_models``, ``local_models``,
  ``free_api_models``, ``model_for``) return the *pre-projection* model set. Every
  consumer is operator visibility: ``ops_dashboard_routes``. Model *selection* is
  unaffected because ``PolicyEngine`` reads ``state.providers[].models`` from the
  provider registry, not the context caches.
* **Not affected** — signal-based blocking. ``ContextSnapshot.canonical_model_id``
  falls back to ``str(model_id).strip().lower()`` on an index miss, and
  ``ContextModel.model_id`` is always produced lowercase by
  ``config._provider_scoped_model_id``. So a model that exists only after live
  projection still canonicalises to the same string, and model-scoped blocking
  signals resolve. ``_signal_index_cache`` is additionally never populated before
  ``hydrate_context`` runs (``save_snapshot`` iterates ``snapshot.signals``
  directly), so the signal index is built fresh from the hydrated signal list.
* **Not affected** — ``graph_objects()`` and ``all_services()``, which iterate the
  fields directly rather than through the caches.

These are characterisation tests, not regression detectors: they assert today's
behaviour so that fixing S1 is a deliberate, reviewable change rather than an
accident. They are written to FAIL once the staleness is fixed, at which point they
should be replaced by the fix's own regression tests.
"""

from __future__ import annotations

from auto_router.config import (
    AgentWorkerRegistry,
    ProviderRegistry,
    _project_live_models,
    load_context_snapshot,
)
from auto_router.context import ContextSignal
from auto_router.live_models import LiveModelSnapshot
from auto_router.models import ModelConfig, PolicyStage, ProviderConfig, StagePurpose
from auto_router.policy import PolicyEngine

LIVE_MODEL_ID = "new-live-model"
CANONICAL_ID = f"lmstudio-local.{LIVE_MODEL_ID}"


def _providers() -> ProviderRegistry:
    return ProviderRegistry(
        providers=[
            ProviderConfig(
                name="lmstudio-local",
                type="lmstudio",
                node_id="local-node",
                enabled=True,
                base_url="http://127.0.0.1:1234/v1",
                quota_class="local",
                models=[
                    ModelConfig(
                        alias="local/default",
                        provider_model="local/default",
                        capabilities={"chat"},
                    )
                ],
            )
        ]
    )


def build_projected_context():
    """Mimic main.load_state(): load -> project live models -> hydrate."""

    providers = _providers()
    context = load_context_snapshot(
        "config/context.example.yaml", providers, AgentWorkerRegistry()
    )
    context = _project_live_models(
        context,
        providers,
        [
            LiveModelSnapshot(
                provider="lmstudio-local",
                ok=True,
                fetched_at=100,
                expires_at=10**12,
                models=[{"id": LIVE_MODEL_ID, "owned_by": "lmstudio"}],
            )
        ],
    )
    return providers, context


def build_hydrated_context():
    """Fold through the model_copy that hydrate_context performs."""

    providers, context = build_projected_context()
    blocked = ContextSignal(
        signal_id=f"blocked::{CANONICAL_ID}",
        target_type="model",
        target_id=CANONICAL_ID,
        signal_type="blocked",
        source="assistx",
        strength=1.0,
        active=True,
    )
    hydrated = context.model_copy(update={"signals": [blocked]})
    return providers, hydrated


def test_memoised_model_accessors_return_the_pre_projection_set() -> None:
    """The defect: the live-discovered model is present in data but not in cache."""

    _, context = build_hydrated_context()

    # Present in the underlying field...
    assert any(m.model_id == CANONICAL_ID for m in context.models), context.models
    assert any(m.provider_model == LIVE_MODEL_ID for m in context.models)

    # ... absent from the memoised index and the memoised list.
    assert context.model_for(CANONICAL_ID) is None
    memoised = {m.model_id for m in context.all_models()}
    assert CANONICAL_ID not in memoised

    # local_models()/free_api_models() inherit the same staleness.
    assert all(m.model_id != CANONICAL_ID for m in context.local_models())
    assert all(m.model_id != CANONICAL_ID for m in context.free_api_models())


def test_unmemoised_accessors_are_correct() -> None:
    """graph_objects()/all_services() iterate fields directly, so they are accurate."""

    _, context = build_hydrated_context()

    graph_model_ids = {
        obj["id"] for obj in context.graph_objects() if obj["kind"] == "model"
    }
    assert CANONICAL_ID in graph_model_ids


def test_model_scoped_blocking_signal_still_resolves() -> None:
    """The staleness does NOT open a fail-open path for signal-based blocking.

    ``canonical_model_id`` falls back to a lowercased echo on an index miss, and
    ``ContextModel.model_id`` is always lowercase, so the fallback equals the real
    canonical id and the blocking signal is found. ``PolicyEngine`` therefore still
    sees the block.
    """

    providers, context = build_hydrated_context()

    assert context.canonical_model_id(CANONICAL_ID) == CANONICAL_ID
    assert len(context.signals_for_model(CANONICAL_ID)) == 1

    engine = PolicyEngine(providers, None, "local_only", context)
    model = ModelConfig(alias=LIVE_MODEL_ID, provider_model=LIVE_MODEL_ID, capabilities={"chat"})
    provider = providers.providers[0]

    assert engine._provider_signal_blocked(provider, model) is True
    adjustment = engine._signal_score_adjustment(
        engine._context_signals(provider, model),
        PolicyStage(purpose=StagePurpose.final),
    )
    assert adjustment == 500.0, "blocking signal must still add the block penalty"


def test_signal_index_is_not_populated_before_hydration() -> None:
    """save_snapshot() iterates snapshot.signals directly, so the signal cache
    stays None across the model_copy and is rebuilt from the hydrated list."""

    _, context = build_projected_context()
    assert context._signal_index_cache is None
    assert context._model_index_cache is not None, "the model index IS the stale one"
    assert context._all_models_cache is not None


def test_config_project_live_models_never_touches_the_provider_registry() -> None:
    """Confirms the routing blast radius is zero.

    ``config._project_live_models`` writes only to the ContextSnapshot. The provider
    registry that routing actually reads is mutated by a different function,
    ``live_model_routes.sync_live_models_to_providers``. So no amount of cache
    staleness inside the context can change model selection.
    """

    providers, context = build_projected_context()

    registry_models = {
        model.provider_model for provider in providers.providers for model in provider.models
    }
    assert LIVE_MODEL_ID not in registry_models, "the context projector must not write here"
    assert LIVE_MODEL_ID in {m.provider_model for m in context.models}
    assert LIVE_MODEL_ID not in {m.provider_model for m in context.all_models()}
