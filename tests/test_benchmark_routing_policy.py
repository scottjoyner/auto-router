from __future__ import annotations

from auto_router.benchmark_routing_policy import benchmark_order, normalize_task_family
from auto_router.models import (
    ExecutionStage,
    ModelConfig,
    ProviderCandidate,
    ProviderConfig,
    RouterRequest,
    StagePurpose,
)


def _candidate(
    node: str,
    model: str,
    *,
    roles: set[str],
    family: str,
    utility: float | None,
    quality: float = 0.8,
    passed: bool = True,
    code: bool = False,
) -> ProviderCandidate:
    scores = {}
    if utility is not None:
        scores[family] = {
            "utility_score": utility,
            "quality_score": quality,
            "quality_confidence": 1.0,
            "quality_floor_passed": passed,
            "tokens_per_second": 12.0,
        }
    provider = ProviderConfig(
        name=f"provider-{node}",
        type="lmstudio",
        node_id=node,
        runtime_instance_id=f"runtime-{node}",
        runtime_kind="lmstudio",
        runtime_version="1",
        parallel_slots=1,
        base_url="http://127.0.0.1:1234/v1",
        models=[],
        routing_roles=roles,
        worker_mode="agent" if "full_agent" in roles else "auxiliary",
        allow_code_execution=code,
    )
    model_config = ModelConfig(
        alias=model,
        provider_model=model,
        capabilities={"chat"},
        task_family_scores=scores,
        routing_roles=roles,
        worker_mode=provider.worker_mode,
        allow_code_execution=code,
    )
    return ProviderCandidate(provider=provider, model=model_config, score=100.0)


def _request(model: str, family: str | None = None) -> RouterRequest:
    metadata = {"task_family": family} if family else {}
    return RouterRequest(
        request_id="request-1",
        route="chat_completions",
        model=model,
        metadata=metadata,
    )


def test_auxiliary_summary_model_can_beat_full_agent_by_family_evidence() -> None:
    stage = ExecutionStage(
        purpose=StagePurpose.final,
        candidates=[
            _candidate(
                "x1-370",
                "agent-model",
                roles={"full_agent"},
                family="summarization",
                utility=0.62,
                code=True,
            ),
            _candidate(
                "optiplex",
                "summary-model",
                roles={"auxiliary_llm", "summarization"},
                family="summarization",
                utility=0.91,
            ),
        ],
    )

    ordered = benchmark_order(stage, _request("auto/summarize"))

    assert ordered.candidates[0].provider.node_id == "optiplex"
    assert "summarization benchmark utility=0.910" in ordered.candidates[0].reason


def test_auxiliary_node_is_not_eligible_for_coding() -> None:
    stage = ExecutionStage(
        purpose=StagePurpose.final,
        candidates=[
            _candidate(
                "optiplex",
                "summary-model",
                roles={"auxiliary_llm", "summarization"},
                family="coding",
                utility=0.99,
            ),
            _candidate(
                "xwing",
                "code-model",
                roles={"full_agent", "code_agent"},
                family="coding",
                utility=0.75,
                code=True,
            ),
        ],
    )

    ordered = benchmark_order(stage, _request("auto/code", "coding"))

    assert [item.provider.node_id for item in ordered.candidates] == ["xwing"]


def test_quality_floor_failure_sorts_after_unmeasured_candidate() -> None:
    stage = ExecutionStage(
        purpose=StagePurpose.final,
        candidates=[
            _candidate(
                "fast-bad",
                "fast-model",
                roles={"auxiliary_llm", "compression"},
                family="compression",
                utility=0.99,
                quality=0.2,
                passed=False,
            ),
            _candidate(
                "unknown",
                "new-model",
                roles={"auxiliary_llm", "compression"},
                family="compression",
                utility=None,
            ),
        ],
    )

    ordered = benchmark_order(stage, _request("auto/compress"))

    assert [item.provider.node_id for item in ordered.candidates] == [
        "unknown",
        "fast-bad",
    ]


def test_aliases_and_metadata_classify_task_family() -> None:
    assert normalize_task_family(_request("auto/summarize")) == "summarization"
    assert normalize_task_family(_request("auto/compact")) == "compression"
    assert normalize_task_family(_request("auto/parse")) == "extraction"
    assert normalize_task_family(_request("auto/fast", "research")) == "reasoning"
