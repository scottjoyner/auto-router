"""Benchmark-only qualification for local node/model pairs.

This module answers a question that endpoint reachability cannot: *is this
model actually useful, and for what?* The latest fleet campaign made the gap
concrete. OptiPlex, Lenovo, Destroyer and Xwing were variously degraded,
degraded, unavailable and high-load. Several endpoints were reachable. None of
them cleared a quality gate, so none earned a coding role.

Reachability, visibility, load and quality are four different claims. Collapsing
them into "the endpoint answered" is what lets a fast, confidently wrong model
look like a good one. This module keeps them separate.

Two invariants matter more than anything else here:

1. **Throughput never buys quality.** A high tokens/sec figure cannot lift a
   model past a failed quality floor. It is recorded, and it may break ties
   *within* a role, but it can never create a role.
2. **Qualification is benchmark-only.** It can feed the benchmark planner, the
   benchmark routing policy, loadout reports and operator dashboards. It can
   never create provider eligibility, admit a node, or change routing. Signed
   production admission stays exactly where it is.

Every result carries ``advisory_only`` and an explicit ``authority`` block, so
"this is benchmark evidence" survives being written to a report.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

QUALIFICATION_SCHEMA = "auto-router-benchmark-qualification-v1"

#: Minimum quality score before a level counts as passed. A level that failed its
#: quality floor is failed, whatever its throughput.
QUALITY_FLOOR = 0.5

#: Minimum sample count before a derived role is considered confident enough to
#: recommend. Below this the model is reported as unqualified *for role purposes*
#: rather than being guessed at.
ROLE_CONFIDENCE_FLOOR = 0.5

#: Tasks are sampled in this order; a failure stops the climb.
LADDER_ORDER: tuple[str, ...] = (
    "l0_protocol",
    "l1_exact_grounding",
    "l2_source_grounding",
    "l3_diagnosis",
    "l4_patch",
)


class LadderLevel(StrEnum):
    """A rung of the qualification ladder, in increasing difficulty."""

    l0_protocol = "l0_protocol"
    l1_exact_grounding = "l1_exact_grounding"
    l2_source_grounding = "l2_source_grounding"
    l3_diagnosis = "l3_diagnosis"
    l4_patch = "l4_patch"


class QualificationRole(StrEnum):
    """Benchmark-only role a node/model pair has earned on the evidence."""

    code_qualified = "code_qualified"
    review_qualified = "review_qualified"
    scout_qualified = "scout_qualified"
    summary_only = "summary_only"
    unqualified = "unqualified"


class EndpointStatus(StrEnum):
    """How far a node/model pair got before quality was even reachable.

    These are distinct capabilities, not one health signal. A model can be
    loaded and still have an unusable protocol; conflating the two is exactly the
    "the endpoint answered, so it must be fine" mistake this pipeline exists to
    prevent. ``protocol_unusable`` is therefore a rung of its own, a fully
    healthy transport reports ``protocol_usable``, and a loaded model whose
    protocol was never measured stays at ``model_loaded``.
    """

    unreachable = "unreachable"
    reachable = "reachable"
    model_visible = "model_visible"
    model_loaded = "model_loaded"
    protocol_unusable = "protocol_unusable"
    protocol_usable = "protocol_usable"


class TestOutcome(StrEnum):
    passed = "passed"
    failed = "failed"
    not_applicable = "not_applicable"


class QualificationFailure(StrEnum):
    """Why an attempt did not clear its ladder level.

    These are deliberately distinct. Collapsing them into a generic
    ``model_failed`` destroys the operator's ability to tell a broken
    endpoint from a broken model from a broken prompt, and those need
    completely different responses.
    """

    #: Nothing usable came back in the expected content field.
    empty_usable_content = "empty_usable_content"
    #: Content was empty but a reasoning/thinking field was populated. The model
    #: reasoned and never emitted an answer.
    reasoning_only_output = "reasoning_only_output"
    #: Generation hit the length cap mid-answer.
    length_truncated = "length_truncated"
    #: The request did not complete inside its deadline.
    timeout = "timeout"
    #: The answer is not derivable from the supplied context.
    context_not_grounded = "context_not_grounded"
    #: The model answered, but not in the field the protocol expected.
    wrong_response_field = "wrong_response_field"
    #: Output was syntactically present but did not apply.
    invalid_patch = "invalid_patch"
    #: Patch applied but the test suite rejected it.
    test_failure = "test_failure"
    #: Transport worked but the protocol itself is unusable.
    protocol_unusable = "protocol_unusable"
    #: No evidence collected at all.
    not_attempted = "not_attempted"


#: Failures that mean "this is a harness or transport problem", not "this model
#: cannot do the task". Reported separately so a flaky node is not misfiled as a
#: weak model.
TRANSPORT_FAILURES = frozenset(
    {
        QualificationFailure.timeout,
        QualificationFailure.protocol_unusable,
        QualificationFailure.empty_usable_content,
        QualificationFailure.reasoning_only_output,
        QualificationFailure.length_truncated,
    }
)


class QualificationEvidence(BaseModel):
    """One ladder attempt. Benchmark evidence only.

    Prompts and sources are recorded as SHA-256 digests, never as text. A
    benchmark corpus can contain proprietary source, so the record is designed
    so that raw sensitive prompt persistence is not merely discouraged but
    unrepresentable: there is no field to put a prompt in.
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: str = QUALIFICATION_SCHEMA

    node_id: str
    runtime_instance_id: str = ""
    runtime_kind: str = ""
    model_id: str
    quantization: str | None = None
    context_tokens: int = Field(default=0, ge=0)

    task_family: str = "general"
    test_level: LadderLevel

    prompt_sha256: str = Field(min_length=64, max_length=64)
    source_sha256: str | None = Field(default=None, min_length=64, max_length=64)

    outcome: TestOutcome
    failure: QualificationFailure | None = None

    quality_score: float | None = Field(default=None, ge=0.0, le=1.0)
    grounding_correct: bool | None = None
    patch_sha256: str | None = Field(default=None, min_length=64, max_length=64)
    test_result: TestOutcome = TestOutcome.not_applicable

    ttft_ms: float | None = Field(default=None, ge=0.0)
    tokens_per_second: float | None = Field(default=None, ge=0.0)
    finish_reason: str | None = None

    observed_at: str
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)

    @property
    def qualification_id(self) -> str:
        return f"{self.node_id}:{self.model_id}:{self.task_family}:{self.test_level}"

    @property
    def is_transport_failure(self) -> bool:
        return self.failure in TRANSPORT_FAILURES


def evidence_from_response(
    *,
    node_id: str,
    model_id: str,
    test_level: LadderLevel,
    prompt_sha256: str,
    content: str | None,
    reasoning: str | None = None,
    finish_reason: str | None = None,
    expected_field: str = "content",
    response_field_used: str | None = None,
    timed_out: bool = False,
    grounding_correct: bool | None = None,
    quality_score: float | None = None,
    test_result: TestOutcome = TestOutcome.not_applicable,
    patch_sha256: str | None = None,
    task_family: str = "general",
    context_tokens: int = 0,
    ttft_ms: float | None = None,
    tokens_per_second: float | None = None,
    runtime_instance_id: str = "",
    runtime_kind: str = "",
    quantization: str | None = None,
    observed_at: str | None = None,
) -> QualificationEvidence:
    """Classify one raw response into evidence, without guessing.

    The order of these checks is the whole point. "Content is empty" has two
    very different causes and they must not collapse together:

    * the model returned nothing at all -> ``empty_usable_content``
    * the model filled a reasoning/thinking field and left content empty ->
      ``reasoning_only_output``

    The second is a distinct pathology: the endpoint is healthy, the model is
    loaded, and the transport worked. Folding it into "empty" would send an
    operator looking at the wrong subsystem.
    """
    failure: QualificationFailure | None = None

    if timed_out:
        failure = QualificationFailure.timeout
    elif finish_reason == "length":
        failure = QualificationFailure.length_truncated
    elif response_field_used is not None and response_field_used != expected_field:
        failure = QualificationFailure.wrong_response_field
    elif not (content or "").strip():
        if (reasoning or "").strip():
            failure = QualificationFailure.reasoning_only_output
        else:
            failure = QualificationFailure.empty_usable_content
    elif test_result is TestOutcome.failed:
        failure = QualificationFailure.test_failure
    elif grounding_correct is False:
        failure = QualificationFailure.context_not_grounded
    elif patch_sha256 is not None and test_result is TestOutcome.not_applicable:
        failure = QualificationFailure.invalid_patch

    passed = failure is None and (
        quality_score is None or quality_score >= QUALITY_FLOOR
    )

    return QualificationEvidence(
        node_id=node_id,
        runtime_instance_id=runtime_instance_id,
        runtime_kind=runtime_kind,
        model_id=model_id,
        quantization=quantization,
        context_tokens=context_tokens,
        task_family=task_family,
        test_level=test_level,
        prompt_sha256=prompt_sha256,
        outcome=TestOutcome.passed if passed else TestOutcome.failed,
        failure=failure,
        quality_score=quality_score,
        grounding_correct=grounding_correct,
        patch_sha256=patch_sha256,
        test_result=test_result,
        ttft_ms=ttft_ms,
        tokens_per_second=tokens_per_second,
        finish_reason=finish_reason,
        observed_at=observed_at or datetime.now(UTC).isoformat(),
        confidence=1.0 if passed else 0.0,
    )


def classify_observation(observation: dict[str, Any]) -> EndpointStatus:
    """Separate transport reachability from protocol usability.

    Each rung is checked and returned distinctly. In particular a loaded model
    with a broken protocol reports ``protocol_unusable`` rather than being
    rounded up to ``model_loaded``: a node that answers HTTP but cannot complete
    a usable exchange has not passed the protocol rung, and reporting it as
    healthy is how a broken runtime keeps looking available.

    The status is a *progress* ladder: you stop at the last rung actually
    demonstrated. A loaded model whose protocol usability was never measured
    stays at ``model_loaded``. Defaulting an unmeasured field to "usable" would
    report a capability nobody verified, which is the same mistake as reporting
    an unexercised model as useful.
    """
    if not bool(observation.get("reachable")):
        return EndpointStatus.unreachable
    if not bool(observation.get("model_visible")):
        return EndpointStatus.reachable
    if not bool(observation.get("model_loaded")):
        return EndpointStatus.model_visible
    if "protocol_usable" not in observation:
        return EndpointStatus.model_loaded
    if not bool(observation["protocol_usable"]):
        return EndpointStatus.protocol_unusable
    return EndpointStatus.protocol_usable


def highest_passed_level(
    evidence: Iterable[QualificationEvidence],
) -> LadderLevel | None:
    """Return the highest ladder level actually passed, or None.

    Absence of evidence is a failure. A model that was never asked, or whose
    only attempts timed out, has demonstrated nothing and is not qualified at
    any level. This is fail-closed on purpose: the campaign under test was one
    where nodes looked fine right up until they were actually used.
    """
    passed = {record.test_level for record in evidence if record.outcome is TestOutcome.passed}
    for level in reversed(LADDER_ORDER):
        if LadderLevel(level) in passed:
            return LadderLevel(level)
    return None


#: Level -> the best role that level alone justifies. L0 is deliberately absent:
#: a deterministic tiny response proves the transport works, not that the model
#: is useful. Reaching only L0 earns no lane.
_ROLE_BY_LEVEL: dict[str, QualificationRole] = {
    LadderLevel.l4_patch: QualificationRole.code_qualified,
    LadderLevel.l3_diagnosis: QualificationRole.review_qualified,
    LadderLevel.l2_source_grounding: QualificationRole.scout_qualified,
    LadderLevel.l1_exact_grounding: QualificationRole.summary_only,
}


def derive_role(
    evidence: Sequence[QualificationEvidence],
    *,
    max_confidence_observed: float | None = None,
) -> dict[str, Any]:
    """Derive a benchmark-only role from ladder evidence.

    ``highest_passed_level`` alone is not sufficient. Two extra conditions:

    * **Quality floor.** A level only counts as passed if its quality score
      cleared ``QUALITY_FLOOR``. This is what stops throughput from buying a
      role: ``tokens_per_second`` is deliberately not an input here.
    * **Confidence floor.** Evidence that is too thin to be confident about
      yields ``unqualified`` for role purposes rather than a guess. Below the
      floor we would rather report nothing than advise a lane on one sample.
    """
    usable = [
        record
        for record in evidence
        if record.outcome is TestOutcome.passed
        and (
            record.quality_score is None
            or record.quality_score >= QUALITY_FLOOR
        )
    ]
    level = highest_passed_level(usable)

    reasons: list[str] = []
    for record in evidence:
        if record.outcome is TestOutcome.failed and record.failure is not None:
            reasons.append(f"{record.test_level}: {record.failure}")

    if level is None:
        if not evidence:
            return _role_result(
                QualificationRole.unqualified,
                None,
                confidence=0.0,
                reasons=["no benchmark evidence collected"],
            )
        return _role_result(
            QualificationRole.unqualified,
            None,
            confidence=0.0,
            reasons=reasons or ["no ladder level cleared its quality floor"],
        )

    confidence = max_confidence_observed
    if confidence is None:
        confidence = min(
            (record.confidence for record in usable),
            default=0.0,
        )

    if confidence < ROLE_CONFIDENCE_FLOOR:
        return _role_result(
            QualificationRole.unqualified,
            level,
            confidence=confidence,
            reasons=reasons
            + [
                "ladder level reached but evidence confidence is below the "
                "role confidence floor"
            ],
        )

    return _role_result(
        _ROLE_BY_LEVEL.get(level, QualificationRole.unqualified),
        level,
        confidence=confidence,
        reasons=reasons,
    )


def _role_result(
    role: QualificationRole,
    level: LadderLevel | None,
    *,
    confidence: float,
    reasons: list[str],
) -> dict[str, Any]:
    return {
        "role": role,
        "highest_passed_level": level,
        "confidence": round(confidence, 3),
        "reasons": reasons,
    }


def build_qualification_report(
    observations: Sequence[dict[str, Any]],
    evidence: Sequence[QualificationEvidence],
) -> dict[str, Any]:
    """Aggregate node/model observations and ladder evidence, advisory-only.

    The ``authority`` block is the load-bearing part of this return value. It
    restates, in the shape a consumer will actually read, that benchmark
    qualification does not admit nodes, does not create provider eligibility,
    and does not change routing. Signed production admission remains a separate,
    deterministic path.
    """
    grouped: dict[tuple[str, str], list[QualificationEvidence]] = {}
    for record in evidence:
        grouped.setdefault((record.node_id, record.model_id), []).append(record)

    observations_by_key = {
        (str(item.get("node_id", "")), str(item.get("model_id", ""))): item
        for item in observations
    }

    entries: list[dict[str, Any]] = []
    for (node_id, model_id), records in sorted(grouped.items()):
        observation = observations_by_key.get((node_id, model_id), {})
        role = derive_role(records)
        entries.append(
            {
                "node_id": node_id,
                "model_id": model_id,
                "endpoint_status": classify_observation(observation).value,
                "quantization": next(
                    (record.quantization for record in records if record.quantization),
                    None,
                ),
                "runtime_instance_id": next(
                    (record.runtime_instance_id for record in records), ""
                ),
                "task_families": sorted({record.task_family for record in records}),
                "failures": sorted(
                    {record.failure.value for record in records if record.failure}
                ),
                "max_tokens_per_second": max(
                    (
                        record.tokens_per_second
                        for record in records
                        if record.tokens_per_second is not None
                    ),
                    default=None,
                ),
                "quality_floor": QUALITY_FLOOR,
                "role_confidence_floor": ROLE_CONFIDENCE_FLOOR,
                "advisory_only": True,
                **role,
            }
        )

    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "schema_version": QUALIFICATION_SCHEMA,
        "advisory_only": True,
        "auto_load_allowed": False,
        "authority": {
            "advisory_only": True,
            "creates_provider_eligibility": False,
            "changes_production_admission": False,
            "changes_routing_authority": False,
            "changes_signed_runtime_projection": False,
        },
        "method": {
            "ladder": list(LADDER_ORDER),
            "quality_floor": QUALITY_FLOOR,
            "role_confidence_floor": ROLE_CONFIDENCE_FLOOR,
            "raw_prompts_persisted": False,
            "metadata_only": True,
        },
        "summary": {
            "entries": len(entries),
            "code_qualified": sum(
                1 for e in entries if e["role"] is QualificationRole.code_qualified
            ),
            "review_qualified": sum(
                1 for e in entries if e["role"] is QualificationRole.review_qualified
            ),
            "scout_qualified": sum(
                1 for e in entries if e["role"] is QualificationRole.scout_qualified
            ),
            "summary_only": sum(
                1 for e in entries if e["role"] is QualificationRole.summary_only
            ),
            "unqualified": sum(
                1 for e in entries if e["role"] is QualificationRole.unqualified
            ),
        },
        "entries": entries,
    }


def apply_qualification_to_benchmark_plan(
    plan: dict[str, Any],
    report: dict[str, Any],
) -> dict[str, Any]:
    """Annotate an existing benchmark plan with qualification outcomes.

    The plan is left structurally intact: no request is added, removed,
    reordered or promoted, and the plan's own ``advisory_only`` and
    ``auto_load_allowed`` flags are preserved. This only attaches the roles so a
    planner or dashboard can see which of its requests are pointed at models
    that actually earned the lane.

    It deliberately does not touch provider eligibility, admission, or routing:
    benchmark evidence must never be able to make an unadmitted node routable.
    """
    roles = {
        (str(entry["node_id"]), str(entry["model_id"])): entry
        for entry in report.get("entries", [])
    }

    annotated = dict(plan)
    requests: list[dict[str, Any]] = []
    for request in plan.get("requests", []):
        if not isinstance(request, dict):
            requests.append(request)
            continue
        entry = roles.get((str(request.get("node_id", "")), str(request.get("model_id", ""))))
        updated = dict(request)
        if entry is not None:
            updated["benchmark_role"] = entry["role"]
            updated["benchmark_role_confidence"] = entry["confidence"]
        requests.append(updated)

    annotated["requests"] = requests
    annotated["advisory_only"] = True
    annotated["auto_load_allowed"] = False
    return annotated