"""Run isolated control-versus-graph-memory model comparisons."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import logging
import math
import os
import re
import time

from src.core.risk import DecisionValidationError, validate_decision_response
from src.core.strategy import render_system_prompt
from src.knowledge_graph import (
    KnowledgeGraph,
    KnowledgeMemorySnapshot,
    render_knowledge_memory,
)


logger = logging.getLogger(__name__)

_PROVENANCE = "neo4j:candidate-outcome-aggregate-v1"
_TICKER_PATTERN = re.compile(r"^[A-Z0-9][A-Z0-9.-]{0,19}$")
_ACTIONS = frozenset({"BUY", "SELL", "HOLD", "ABSTAIN"})
_EMPTY_CHECKSUM = hashlib.sha256(b"").hexdigest()


@dataclass(frozen=True)
class ShadowCycleResult:
    evaluated_at: str
    processed: int
    succeeded: int
    skipped: int
    failed: int

    def to_dict(self) -> dict:
        return {
            "evaluated_at": self.evaluated_at,
            "processed": self.processed,
            "succeeded": self.succeeded,
            "skipped": self.skipped,
            "failed": self.failed,
        }


def _normalized_tickers(values) -> list[str]:
    if isinstance(values, (str, bytes)) or values is None:
        return []
    return sorted({
        ticker
        for value in values
        if isinstance(value, str)
        if (ticker := value.strip().upper())
        if _TICKER_PATTERN.fullmatch(ticker)
    })[:50]


def _decision_map(response: dict) -> dict[str, tuple[str, float | None]]:
    result: dict[str, tuple[str, float | None]] = {}
    if not isinstance(response, dict):
        return result
    decisions = response.get("decisions")
    if not isinstance(decisions, list):
        return result
    for decision in decisions[:50]:
        if not isinstance(decision, dict):
            continue
        ticker = str(decision.get("ticker") or "").strip().upper()
        action = str(decision.get("action") or "").strip().upper()
        if (
            ticker in result
            or not _TICKER_PATTERN.fullmatch(ticker)
            or action not in _ACTIONS
        ):
            continue
        confidence_value = decision.get("confidence")
        confidence = None
        if (
            not isinstance(confidence_value, bool)
            and isinstance(confidence_value, (int, float))
            and math.isfinite(float(confidence_value))
            and 0 <= float(confidence_value) <= 100
        ):
            confidence = float(confidence_value)
        result[ticker] = (action, confidence)
    return result


def compare_shadow_decisions(
    control: dict,
    memory: dict,
    *,
    tickers,
) -> list[dict]:
    """Return a bounded comparison without model reasoning or raw text."""
    control_map = _decision_map(control)
    memory_map = _decision_map(memory)
    compared_tickers = _normalized_tickers(
        [
            *_normalized_tickers(tickers),
            *control_map.keys(),
            *memory_map.keys(),
        ]
    )
    comparisons = []
    for ticker in compared_tickers:
        control_action, control_confidence = control_map.get(
            ticker,
            ("ABSTAIN", None),
        )
        memory_action, memory_confidence = memory_map.get(
            ticker,
            ("ABSTAIN", None),
        )
        comparisons.append({
            "ticker": ticker,
            "control_action": control_action,
            "memory_action": memory_action,
            "control_confidence": control_confidence,
            "memory_confidence": memory_confidence,
            "changed": control_action != memory_action,
        })
    return comparisons


def _parse_model_response(raw_text: str) -> dict:
    if not isinstance(raw_text, str) or len(raw_text) > 100_000:
        raise DecisionValidationError("model response must be bounded text")
    json_text = raw_text.strip()
    if json_text.startswith("```"):
        json_text = "\n".join(
            line
            for line in json_text.splitlines()
            if not line.strip().startswith("```")
        )
    return validate_decision_response(json.loads(json_text))


def _prompt(
    *,
    decision_at: datetime,
    market_context: str,
    memory: KnowledgeMemorySnapshot | None,
) -> str:
    parts = [
        f"Datum: {decision_at.strftime('%Y-%m-%d %H:%M')} UTC",
        market_context,
    ]
    if memory is not None:
        parts.extend([
            "## GRAFMEMINNE I SKUGGLÄGE",
            render_knowledge_memory(memory),
        ])
    parts.append("Analysera all data och ge dina trading-beslut som JSON.")
    return "\n\n".join(parts)[:20_000]


def _model_matches_source(model, source: dict) -> bool:
    return (
        str(getattr(model, "backend", "") or "")
        == str(source.get("model_backend") or "")
        and str(getattr(model, "model", "") or "")
        == str(source.get("model_name") or "")
        and (
            getattr(model, "model_provider", None)
            == source.get("model_provider")
        )
        and (
            getattr(model, "reasoning_effort", None)
            == source.get("reasoning_effort")
        )
    )


def _checked_source(source: dict) -> tuple[int, datetime, str, str, list[str]]:
    source_id = source.get("source_decision_id")
    decision_at = source.get("decision_at")
    market_context = source.get("market_context")
    strategy_version = source.get("strategy_version")
    tickers = _normalized_tickers(source.get("tickers"))
    if (
        isinstance(source_id, bool)
        or not isinstance(source_id, int)
        or source_id <= 0
        or not isinstance(decision_at, datetime)
        or decision_at.tzinfo is None
        or decision_at.utcoffset() is None
        or not isinstance(market_context, str)
        or not 1 <= len(market_context) <= 10_000
        or not isinstance(strategy_version, str)
        or not strategy_version
    ):
        raise ValueError("invalid knowledge shadow source")
    return (
        source_id,
        decision_at.astimezone(timezone.utc),
        market_context,
        strategy_version,
        tickers,
    )


def _record(
    database,
    *,
    source_decision_id: int,
    evaluated_at: datetime,
    status: str,
    reason_code: str,
    call_order: str,
    market_context: str,
    snapshot: KnowledgeMemorySnapshot | None,
    candidate_count: int,
    comparisons: list[dict],
    control_tokens: tuple[int, int] = (0, 0),
    memory_tokens: tuple[int, int] = (0, 0),
    model,
) -> int:
    return database.record_knowledge_shadow_run(
        source_decision_id=source_decision_id,
        evaluated_at=evaluated_at,
        status=status,
        reason_code=reason_code,
        call_order=call_order,
        context_checksum_sha256=hashlib.sha256(
            market_context.encode("utf-8")
        ).hexdigest(),
        evidence_provenance=_PROVENANCE,
        evidence_as_of=(
            snapshot.as_of if snapshot is not None else evaluated_at
        ),
        evidence_checksum_sha256=(
            snapshot.checksum_sha256
            if snapshot is not None
            else _EMPTY_CHECKSUM
        ),
        evidence_fact_count=(
            len(snapshot.facts) if snapshot is not None else 0
        ),
        candidate_count=candidate_count,
        comparison_count=len(comparisons),
        changed_count=sum(
            1 for comparison in comparisons if comparison["changed"]
        ),
        control_prompt_tokens=control_tokens[0],
        control_response_tokens=control_tokens[1],
        memory_prompt_tokens=memory_tokens[0],
        memory_response_tokens=memory_tokens[1],
        model_backend=str(getattr(model, "backend", "") or "unknown"),
        model_name=str(getattr(model, "model", "") or "unknown"),
        model_provider=getattr(model, "model_provider", None),
        reasoning_effort=getattr(model, "reasoning_effort", None),
        comparisons=comparisons,
    )


def run_shadow_cycle(
    database,
    graph,
    model,
    *,
    evaluated_at: datetime,
    max_runs: int = 2,
) -> ShadowCycleResult:
    """Compare two model calls; never validate or execute an order."""
    if (
        not isinstance(evaluated_at, datetime)
        or evaluated_at.tzinfo is None
        or evaluated_at.utcoffset() is None
    ):
        raise ValueError("evaluated_at must be timezone-aware")
    if (
        isinstance(max_runs, bool)
        or not isinstance(max_runs, int)
        or not 1 <= max_runs <= 10
    ):
        raise ValueError("max_runs must be between 1 and 10")
    checked_at = evaluated_at.astimezone(timezone.utc)
    sources = database.get_pending_knowledge_shadow_inputs(
        now=checked_at,
        limit=max_runs,
    )
    counters = {"succeeded": 0, "skipped": 0, "failed": 0}

    for source in sources:
        try:
            (
                source_id,
                decision_at,
                market_context,
                strategy_version,
                tickers,
            ) = _checked_source(source)
        except ValueError:
            logger.warning("knowledge_shadow_source_invalid")
            counters["failed"] += 1
            continue

        if not _model_matches_source(model, source):
            _record(
                database,
                source_decision_id=source_id,
                evaluated_at=checked_at,
                status="SKIPPED",
                reason_code="MODEL_CONFIG_MISMATCH",
                call_order="NOT_RUN",
                market_context=market_context,
                snapshot=None,
                candidate_count=len(tickers),
                comparisons=[],
                model=model,
            )
            counters["skipped"] += 1
            continue
        if not tickers:
            _record(
                database,
                source_decision_id=source_id,
                evaluated_at=checked_at,
                status="SKIPPED",
                reason_code="NO_CANDIDATES",
                call_order="NOT_RUN",
                market_context=market_context,
                snapshot=None,
                candidate_count=0,
                comparisons=[],
                model=model,
            )
            counters["skipped"] += 1
            continue

        try:
            snapshot = graph.get_decision_memory(
                tickers=tickers,
                as_of=decision_at,
            )
        except Exception:
            logger.warning(
                "knowledge_shadow_graph_unavailable source_decision_id=%d",
                source_id,
            )
            _record(
                database,
                source_decision_id=source_id,
                evaluated_at=checked_at,
                status="FAILED",
                reason_code="GRAPH_UNAVAILABLE",
                call_order="NOT_RUN",
                market_context=market_context,
                snapshot=None,
                candidate_count=len(tickers),
                comparisons=[],
                model=model,
            )
            counters["failed"] += 1
            continue
        if not snapshot.facts:
            _record(
                database,
                source_decision_id=source_id,
                evaluated_at=checked_at,
                status="SKIPPED",
                reason_code="NO_ELIGIBLE_EVIDENCE",
                call_order="NOT_RUN",
                market_context=market_context,
                snapshot=snapshot,
                candidate_count=len(tickers),
                comparisons=[],
                model=model,
            )
            counters["skipped"] += 1
            continue

        try:
            strategy = database.get_strategy_version(strategy_version)
        except Exception:
            logger.warning(
                "knowledge_shadow_strategy_unavailable source_decision_id=%d",
                source_id,
            )
            _record(
                database,
                source_decision_id=source_id,
                evaluated_at=checked_at,
                status="SKIPPED",
                reason_code="STRATEGY_UNAVAILABLE",
                call_order="NOT_RUN",
                market_context=market_context,
                snapshot=snapshot,
                candidate_count=len(tickers),
                comparisons=[],
                model=model,
            )
            counters["skipped"] += 1
            continue

        system = render_system_prompt(strategy)
        prompts = {
            "control": _prompt(
                decision_at=decision_at,
                market_context=market_context,
                memory=None,
            ),
            "memory": _prompt(
                decision_at=decision_at,
                market_context=market_context,
                memory=snapshot,
            ),
        }
        call_order = (
            ("control", "memory")
            if source_id % 2 == 0
            else ("memory", "control")
        )
        responses: dict[str, dict] = {}
        tokens: dict[str, tuple[int, int]] = {
            "control": (0, 0),
            "memory": (0, 0),
        }
        try:
            for variant in call_order:
                raw_text, prompt_tokens, response_tokens = model._call_llm(
                    system=system,
                    user_msg=prompts[variant],
                    max_tokens=2_000,
                )
                responses[variant] = _parse_model_response(raw_text)
                tokens[variant] = (
                    int(prompt_tokens or 0),
                    int(response_tokens or 0),
                )
        except (json.JSONDecodeError, DecisionValidationError, ValueError):
            logger.warning(
                "knowledge_shadow_response_invalid source_decision_id=%d",
                source_id,
            )
            _record(
                database,
                source_decision_id=source_id,
                evaluated_at=checked_at,
                status="FAILED",
                reason_code="INVALID_MODEL_RESPONSE",
                call_order=(
                    "CONTROL_FIRST"
                    if call_order[0] == "control"
                    else "MEMORY_FIRST"
                ),
                market_context=market_context,
                snapshot=snapshot,
                candidate_count=len(tickers),
                comparisons=[],
                control_tokens=tokens["control"],
                memory_tokens=tokens["memory"],
                model=model,
            )
            counters["failed"] += 1
            continue
        except Exception:
            logger.warning(
                "knowledge_shadow_model_unavailable source_decision_id=%d",
                source_id,
            )
            _record(
                database,
                source_decision_id=source_id,
                evaluated_at=checked_at,
                status="FAILED",
                reason_code="MODEL_UNAVAILABLE",
                call_order=(
                    "CONTROL_FIRST"
                    if call_order[0] == "control"
                    else "MEMORY_FIRST"
                ),
                market_context=market_context,
                snapshot=snapshot,
                candidate_count=len(tickers),
                comparisons=[],
                control_tokens=tokens["control"],
                memory_tokens=tokens["memory"],
                model=model,
            )
            counters["failed"] += 1
            continue

        comparisons = compare_shadow_decisions(
            responses["control"],
            responses["memory"],
            tickers=tickers,
        )
        _record(
            database,
            source_decision_id=source_id,
            evaluated_at=checked_at,
            status="SUCCEEDED",
            reason_code="COMPARED",
            call_order=(
                "CONTROL_FIRST"
                if call_order[0] == "control"
                else "MEMORY_FIRST"
            ),
            market_context=market_context,
            snapshot=snapshot,
            candidate_count=len(tickers),
            comparisons=comparisons,
            control_tokens=tokens["control"],
            memory_tokens=tokens["memory"],
            model=model,
        )
        counters["succeeded"] += 1

    return ShadowCycleResult(
        evaluated_at=checked_at.isoformat(),
        processed=len(sources),
        **counters,
    )


def _bounded_integer(
    environ,
    key: str,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    try:
        value = int(environ.get(key, str(default)))
    except (TypeError, ValueError) as error:
        raise ValueError(f"{key} must be an integer") from error
    if not minimum <= value <= maximum:
        raise ValueError(
            f"{key} must be between {minimum} and {maximum}"
        )
    return value


def main(
    argv: list[str] | None = None,
    *,
    database_factory=None,
    graph_factory=KnowledgeGraph.from_environment,
    model_factory=None,
    now_factory=lambda: datetime.now(timezone.utc),
    sleeper=time.sleep,
    environ=None,
) -> int:
    parser = argparse.ArgumentParser(
        description="Run isolated knowledge-memory shadow comparisons.",
    )
    parser.add_argument(
        "mode",
        choices=("once", "daemon", "health"),
        nargs="?",
        default="once",
    )
    arguments = parser.parse_args(argv)
    environment = os.environ if environ is None else environ
    if database_factory is None:
        from src.data.database import Database

        database_factory = Database
    if model_factory is None:
        from src.core.brain import TradingBrain

        model_factory = TradingBrain

    graph = graph_factory(environment)
    try:
        if arguments.mode == "health":
            graph.verify_connectivity()
            database_factory()
            return 0

        database = database_factory()
        model = model_factory(database)
        max_runs = _bounded_integer(
            environment,
            "KNOWLEDGE_SHADOW_MAX_RUNS_PER_CYCLE",
            2,
            1,
            10,
        )
        if arguments.mode == "once":
            result = run_shadow_cycle(
                database,
                graph,
                model,
                evaluated_at=now_factory(),
                max_runs=max_runs,
            )
            print(json.dumps(result.to_dict(), ensure_ascii=False))
            return 0 if result.failed == 0 else 1

        interval = _bounded_integer(
            environment,
            "KNOWLEDGE_SHADOW_INTERVAL_SECONDS",
            300,
            60,
            3_600,
        )
        while True:
            result = run_shadow_cycle(
                database,
                graph,
                model,
                evaluated_at=now_factory(),
                max_runs=max_runs,
            )
            logger.info(
                "knowledge_shadow_cycle processed=%d succeeded=%d "
                "skipped=%d failed=%d",
                result.processed,
                result.succeeded,
                result.skipped,
                result.failed,
            )
            sleeper(interval)
    finally:
        graph.close()


if __name__ == "__main__":
    raise SystemExit(main())
