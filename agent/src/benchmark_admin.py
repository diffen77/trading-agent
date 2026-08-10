"""Transactional operator API for forward paper benchmark governance."""

import argparse
from datetime import date, datetime
import json
import math
from pathlib import Path
import re
import sys
from typing import Any

import psycopg2
from psycopg2.extras import Json, RealDictCursor

from .runtime_secrets import read_runtime_secret
from .core.forward_benchmark import (
    BenchmarkCriteria,
    BenchmarkIncident,
    BenchmarkRegistration,
    BenchmarkStartEvidence,
)


_KEY_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{2,99}$")
_OPERATOR_PATTERN = re.compile(r"^operator:[A-Za-z0-9._-]{1,80}$")


class BenchmarkAdmin:
    """Manage lifecycle transitions without bypassing database triggers."""

    def __init__(self, database_url: str | None = None):
        self.database_url = database_url or read_runtime_secret(
            "DATABASE_URL",
            required=True,
        )

    def create_paper_benchmark_draft(
        self,
        registration: BenchmarkRegistration,
    ) -> int:
        if not isinstance(registration, BenchmarkRegistration):
            raise ValueError("registration must be BenchmarkRegistration")
        payload = registration.to_payload()
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO paper_benchmark_experiments (
                        experiment_key,
                        strategy_version_id,
                        strategy_config_hash,
                        release_sha,
                        release_manifest_sha256,
                        agent_image_digest_sha256,
                        model_backend,
                        model_name,
                        model_evidence_sha256,
                        reference_snapshot_id,
                        universe_checksum_sha256,
                        quote_provider_contract_id,
                        execution_price_source,
                        execution_provider_contract_id,
                        benchmark_provider_contract_id,
                        benchmark_source_url,
                        benchmark_terms_url,
                        fee_bps,
                        spread_bps,
                        slippage_bps,
                        min_trading_sessions,
                        min_closed_trades,
                        max_drawdown_pct,
                        min_data_coverage_pct,
                        preregistration_payload,
                        preregistration_canonical_json,
                        preregistration_hash,
                        proposed_by
                    )
                    SELECT
                        %s,
                        strategy.id,
                        strategy.config_hash,
                        %s,
                        %s,
                        %s,
                        %s,
                        %s,
                        %s,
                        snapshot.id,
                        snapshot.universe_checksum_sha256,
                        quote_contract.id,
                        %s,
                        execution_contract.id,
                        benchmark_contract.id,
                        %s,
                        %s,
                        %s,
                        %s,
                        %s,
                        %s,
                        %s,
                        %s,
                        %s,
                        %s,
                        %s,
                        %s,
                        %s
                    FROM strategy_versions strategy
                    JOIN reference_data_snapshots snapshot
                      ON snapshot.id = %s
                     AND snapshot.mic = 'XSTO'
                     AND snapshot.universe_checksum_sha256 = %s
                    JOIN market_data_provider_contracts quote_contract
                      ON quote_contract.contract_key = %s
                    JOIN market_data_provider_contracts execution_contract
                      ON execution_contract.contract_key = %s
                    JOIN market_data_provider_contracts benchmark_contract
                      ON benchmark_contract.contract_key = %s
                    WHERE strategy.version = %s
                      AND strategy.config_hash = %s
                    RETURNING id
                    """,
                    (
                        registration.experiment_key,
                        registration.release_sha,
                        registration.release_manifest_sha256,
                        registration.agent_image_digest_sha256,
                        registration.model_backend,
                        registration.model_name,
                        registration.model_evidence_sha256,
                        registration.execution_price_source,
                        registration.benchmark_source_url,
                        registration.benchmark_terms_url,
                        registration.fee_bps,
                        registration.spread_bps,
                        registration.slippage_bps,
                        registration.criteria.min_trading_sessions,
                        registration.criteria.min_closed_trades,
                        registration.criteria.max_drawdown_pct,
                        registration.criteria.min_data_coverage_pct,
                        Json(payload),
                        registration.canonical_json,
                        registration.payload_hash,
                        registration.proposed_by,
                        registration.reference_snapshot_id,
                        registration.universe_checksum_sha256,
                        registration.quote_provider_contract_key,
                        registration.execution_provider_contract_key,
                        registration.benchmark_provider_contract_key,
                        registration.strategy_version,
                        registration.strategy_config_hash,
                    ),
                )
                row = cursor.fetchone()
                if row is None:
                    raise ValueError(
                        "strategy, XSTO snapshot, or provider contract is missing"
                    )
                return int(row[0])

    def approve_paper_benchmark(
        self,
        experiment_key: str,
        *,
        approved_by: str,
    ) -> None:
        key = _experiment_key(experiment_key)
        operator = _operator_identity(approved_by)
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE paper_benchmark_experiments
                    SET
                        status = 'APPROVED',
                        approved_by = %s,
                        approved_at = NOW()
                    WHERE experiment_key = %s
                      AND status = 'DRAFT'
                    RETURNING id
                    """,
                    (operator, key),
                )
                if cursor.fetchone() is None:
                    raise ValueError(
                        "paper benchmark draft does not exist"
                    )

    def start_paper_benchmark(
        self,
        experiment_key: str,
        *,
        evidence: BenchmarkStartEvidence,
        started_by: str,
    ) -> None:
        key = _experiment_key(experiment_key)
        operator = _operator_identity(started_by)
        if not isinstance(evidence, BenchmarkStartEvidence):
            raise ValueError("evidence must be BenchmarkStartEvidence")
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE paper_benchmark_experiments
                    SET
                        status = 'RUNNING',
                        started_by = %s,
                        started_at = NOW(),
                        benchmark_start_level_id = %s,
                        benchmark_start_level = %s,
                        benchmark_start_event_time = %s,
                        benchmark_start_available_at = %s,
                        benchmark_start_checksum_sha256 = %s
                    WHERE experiment_key = %s
                      AND status = 'APPROVED'
                    RETURNING id
                    """,
                    (
                        operator,
                        evidence.level_id,
                        evidence.benchmark_level,
                        evidence.event_time,
                        evidence.available_at,
                        evidence.source_checksum_sha256,
                        key,
                    ),
                )
                if cursor.fetchone() is None:
                    raise ValueError(
                        "approved paper benchmark does not exist"
                    )

    def pause_paper_benchmark(
        self,
        experiment_key: str,
        *,
        paused_by: str,
    ) -> None:
        self._transition(
            experiment_key,
            from_status="RUNNING",
            to_status="PAUSED",
            actor=paused_by,
            reason="PAUSED_BY_OPERATOR",
        )

    def resume_paper_benchmark(
        self,
        experiment_key: str,
        *,
        resumed_by: str,
    ) -> None:
        self._transition(
            experiment_key,
            from_status="PAUSED",
            to_status="RUNNING",
            actor=resumed_by,
            reason="RESUMED_BY_OPERATOR",
        )

    def invalidate_paper_benchmark(
        self,
        experiment_key: str,
        *,
        invalidated_by: str,
        reason: str,
    ) -> None:
        key = _experiment_key(experiment_key)
        operator = _operator_identity(invalidated_by)
        normalized_reason = _bounded_reason(reason)
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE paper_benchmark_experiments
                    SET
                        status = 'INVALIDATED',
                        invalidated_by = %s,
                        invalidated_at = NOW(),
                        invalidation_reason = %s
                    WHERE experiment_key = %s
                      AND status IN (
                          'DRAFT',
                          'APPROVED',
                          'RUNNING',
                          'PAUSED'
                      )
                    RETURNING id
                    """,
                    (operator, normalized_reason, key),
                )
                if cursor.fetchone() is None:
                    raise ValueError(
                        "active paper benchmark does not exist"
                    )

    def record_incident(
        self,
        experiment_key: str,
        incident: BenchmarkIncident,
        *,
        recorded_by: str,
    ) -> int:
        key = _experiment_key(experiment_key)
        operator = _operator_identity(recorded_by)
        if not isinstance(incident, BenchmarkIncident):
            raise ValueError("incident must be BenchmarkIncident")
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO paper_benchmark_incidents (
                        experiment_id,
                        incident_key,
                        session_date,
                        severity,
                        description,
                        detected_at,
                        source_checksum_sha256,
                        recorded_by
                    )
                    SELECT
                        experiment.id,
                        %s,
                        %s,
                        %s,
                        %s,
                        %s,
                        %s,
                        %s
                    FROM paper_benchmark_experiments experiment
                    WHERE experiment.experiment_key = %s
                    RETURNING id
                    """,
                    (
                        incident.incident_key,
                        incident.session_date,
                        incident.severity,
                        incident.description,
                        incident.detected_at,
                        incident.source_checksum_sha256,
                        operator,
                        key,
                    ),
                )
                row = cursor.fetchone()
                if row is None:
                    raise ValueError(
                        "paper benchmark experiment does not exist"
                    )
                return int(row[0])

    def evaluate_paper_benchmark(
        self,
        experiment_key: str,
        *,
        evaluated_by: str,
    ) -> dict[str, Any]:
        key = _experiment_key(experiment_key)
        operator = _operator_identity(evaluated_by)
        with self._connect() as connection:
            with connection.cursor(
                cursor_factory=RealDictCursor,
            ) as cursor:
                cursor.execute(
                    """
                    INSERT INTO paper_benchmark_evaluations (
                        experiment_id,
                        evaluated_by
                    )
                    SELECT id, %s
                    FROM paper_benchmark_experiments
                    WHERE experiment_key = %s
                      AND status IN ('RUNNING', 'PAUSED')
                    RETURNING *
                    """,
                    (operator, key),
                )
                row = cursor.fetchone()
                if row is None:
                    raise ValueError(
                        "running paper benchmark does not exist"
                    )
                return dict(row)

    def get_paper_benchmark(
        self,
        experiment_key: str,
    ) -> dict[str, Any]:
        key = _experiment_key(experiment_key)
        with self._connect() as connection:
            with connection.cursor(
                cursor_factory=RealDictCursor,
            ) as cursor:
                cursor.execute(
                    """
                    SELECT
                        experiment.id,
                        experiment.experiment_key,
                        experiment.status,
                        strategy.version AS strategy_version,
                        experiment.release_sha,
                        experiment.model_backend,
                        experiment.model_name,
                        experiment.benchmark_symbol,
                        experiment.benchmark_kind,
                        experiment.preregistration_hash,
                        experiment.proposed_at,
                        experiment.approved_at,
                        experiment.started_at,
                        experiment.benchmark_start_level_id,
                        experiment.benchmark_start_level,
                        experiment.benchmark_start_event_time,
                        experiment.benchmark_start_available_at,
                        experiment.benchmark_start_checksum_sha256,
                        experiment.completed_at,
                        experiment.invalidated_at,
                        evaluation.passed,
                        evaluation.reason_codes
                    FROM paper_benchmark_experiments experiment
                    JOIN strategy_versions strategy
                      ON strategy.id = experiment.strategy_version_id
                    LEFT JOIN paper_benchmark_evaluations evaluation
                      ON evaluation.experiment_id = experiment.id
                    WHERE experiment.experiment_key = %s
                    """,
                    (key,),
                )
                row = cursor.fetchone()
                if row is None:
                    raise ValueError(
                        "paper benchmark experiment does not exist"
                    )
                return dict(row)

    def get_readiness(self) -> dict[str, Any]:
        """Report benchmark prerequisites without changing runtime state."""
        with self._connect() as connection:
            connection.set_session(readonly=True)
            with connection.cursor(
                cursor_factory=RealDictCursor,
            ) as cursor:
                cursor.execute(
                    """
                    WITH latest_reference AS (
                        SELECT
                            id,
                            instrument_count,
                            universe_checksum_sha256
                        FROM reference_data_snapshots
                        WHERE mic = 'XSTO'
                        ORDER BY snapshot_date DESC, id DESC
                        LIMIT 1
                    ),
                    active_strategy AS (
                        SELECT version, config_hash
                        FROM strategy_versions
                        WHERE status = 'ACTIVE'
                        ORDER BY activated_at DESC NULLS LAST, id DESC
                        LIMIT 1
                    ),
                    ready_quote AS (
                        SELECT contract.contract_key
                        FROM market_data_provider_contracts contract
                        CROSS JOIN latest_reference reference
                        JOIN LATERAL (
                            SELECT candidate.*
                            FROM market_data_provider_validations candidate
                            WHERE candidate.contract_id = contract.id
                            ORDER BY
                                candidate.validated_at DESC,
                                candidate.id DESC
                            LIMIT 1
                        ) validation ON TRUE
                        WHERE paper_benchmark_provider_evidence_ready(
                            contract.id,
                            reference.instrument_count,
                            ARRAY[
                                'delayed-post-trade-equity',
                                'realtime-equity-level-1'
                            ]
                        )
                          AND validation.reference_snapshot_id = reference.id
                          AND validation.reference_checksum_sha256 =
                                reference.universe_checksum_sha256
                        ORDER BY contract.updated_at DESC, contract.id DESC
                        LIMIT 1
                    ),
                    ready_top_of_book AS (
                        SELECT contract.contract_key
                        FROM market_data_provider_contracts contract
                        CROSS JOIN latest_reference reference
                        JOIN LATERAL (
                            SELECT candidate.*
                            FROM market_data_provider_validations candidate
                            WHERE candidate.contract_id = contract.id
                            ORDER BY
                                candidate.validated_at DESC,
                                candidate.id DESC
                            LIMIT 1
                        ) validation ON TRUE
                        WHERE paper_benchmark_provider_evidence_ready(
                            contract.id,
                            reference.instrument_count,
                            ARRAY[
                                'delayed-pre-trade-equity',
                                'realtime-equity-level-1'
                            ]
                        )
                          AND validation.reference_snapshot_id = reference.id
                          AND validation.reference_checksum_sha256 =
                                reference.universe_checksum_sha256
                        ORDER BY contract.updated_at DESC, contract.id DESC
                        LIMIT 1
                    ),
                    ready_benchmark AS (
                        SELECT contract.id, contract.contract_key
                        FROM market_data_provider_contracts contract
                        WHERE paper_benchmark_provider_evidence_ready(
                            contract.id,
                            1,
                            ARRAY[
                                'delayed-index-level',
                                'realtime-index-level'
                            ]
                        )
                        ORDER BY contract.updated_at DESC, contract.id DESC
                        LIMIT 1
                    ),
                    latest_benchmark_level AS (
                        SELECT level.id, level.event_time, level.received_at
                        FROM market_index_levels level
                        JOIN ready_benchmark contract
                          ON contract.id = level.provider_contract_id
                        WHERE level.symbol = 'OMXSGI'
                          AND level.mic = 'XSTO'
                          AND level.received_at <= NOW()
                        ORDER BY level.event_time DESC, level.id DESC
                        LIMIT 1
                    ),
                    active_experiment AS (
                        SELECT experiment_key, status
                        FROM paper_benchmark_experiments
                        WHERE status IN (
                            'DRAFT', 'APPROVED', 'RUNNING', 'PAUSED'
                        )
                        ORDER BY
                            CASE status
                                WHEN 'RUNNING' THEN 0
                                WHEN 'PAUSED' THEN 1
                                WHEN 'APPROVED' THEN 2
                                ELSE 3
                            END,
                            id DESC
                        LIMIT 1
                    )
                    SELECT
                        (SELECT version FROM active_strategy)
                            AS strategy_version,
                        (SELECT config_hash FROM active_strategy)
                            AS strategy_config_hash,
                        (SELECT id FROM latest_reference)
                            AS reference_snapshot_id,
                        (SELECT instrument_count FROM latest_reference)
                            AS reference_instrument_count,
                        (SELECT universe_checksum_sha256
                         FROM latest_reference)
                            AS reference_checksum_sha256,
                        (SELECT contract_key FROM ready_quote)
                            AS quote_contract_key,
                        (SELECT contract_key FROM ready_top_of_book)
                            AS top_of_book_contract_key,
                        (SELECT contract_key FROM ready_benchmark)
                            AS benchmark_contract_key,
                        (SELECT id FROM latest_benchmark_level)
                            AS benchmark_level_id,
                        (SELECT event_time FROM latest_benchmark_level)
                            AS benchmark_level_event_time,
                        (SELECT received_at FROM latest_benchmark_level)
                            AS benchmark_level_received_at,
                        (
                            (SELECT COUNT(*) FROM balance) = 1
                            AND EXISTS (
                                SELECT 1 FROM balance
                                WHERE cash = 20000 AND total_value = 20000
                            )
                            AND NOT EXISTS (SELECT 1 FROM portfolio)
                            AND NOT EXISTS (
                                SELECT 1 FROM position_lots
                                WHERE remaining_shares > 0
                            )
                        ) AS ledger_clean,
                        (SELECT experiment_key FROM active_experiment)
                            AS active_experiment_key,
                        (SELECT status FROM active_experiment)
                            AS active_experiment_status
                    """
                )
                row = cursor.fetchone()
                if row is None:
                    raise RuntimeError(
                        "benchmark readiness snapshot is missing"
                    )
                return build_benchmark_readiness(dict(row))

    def _transition(
        self,
        experiment_key: str,
        *,
        from_status: str,
        to_status: str,
        actor: str,
        reason: str,
    ) -> None:
        key = _experiment_key(experiment_key)
        operator = _operator_identity(actor)
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE paper_benchmark_experiments
                    SET
                        status = %s,
                        last_transition_by = %s,
                        last_transition_at = NOW(),
                        last_transition_reason = %s
                    WHERE experiment_key = %s
                      AND status = %s
                    RETURNING id
                    """,
                    (
                        to_status,
                        operator,
                        reason,
                        key,
                        from_status,
                    ),
                )
                if cursor.fetchone() is None:
                    raise ValueError(
                        f"{from_status.lower()} paper benchmark does not exist"
                    )

    def _connect(self):
        return psycopg2.connect(self.database_url)


def _experiment_key(value: Any) -> str:
    if not isinstance(value, str) or not _KEY_PATTERN.fullmatch(value):
        raise ValueError("experiment_key has invalid format")
    return value


def build_benchmark_readiness(
    snapshot: dict[str, Any],
) -> dict[str, Any]:
    """Turn a read-only database snapshot into stable blocker codes."""
    if not isinstance(snapshot, dict):
        raise ValueError("benchmark readiness snapshot must be an object")

    blockers: list[str] = []
    if not snapshot.get("strategy_version"):
        blockers.append("ACTIVE_STRATEGY_MISSING")
    reference_ready = (
        isinstance(snapshot.get("reference_instrument_count"), int)
        and snapshot["reference_instrument_count"] >= 300
        and bool(snapshot.get("reference_checksum_sha256"))
    )
    if not reference_ready:
        blockers.append("REFERENCE_SNAPSHOT_NOT_READY")
    quote_ready = bool(snapshot.get("quote_contract_key"))
    if not quote_ready:
        blockers.append("BENCHMARK_QUOTE_PROVIDER_NOT_READY")
    benchmark_ready = bool(snapshot.get("benchmark_contract_key"))
    if not benchmark_ready:
        blockers.append("OMXSGI_PROVIDER_NOT_READY")
    if not snapshot.get("benchmark_level_id"):
        blockers.append("OMXSGI_LEVEL_MISSING")
    if snapshot.get("ledger_clean") is not True:
        blockers.append("PAPER_LEDGER_NOT_CLEAN")

    execution_options = []
    if quote_ready:
        execution_options.append("LAST_TRADE_PLUS_BPS")
    if snapshot.get("top_of_book_contract_key"):
        execution_options.append("TOP_OF_BOOK_PLUS_SLIPPAGE")

    preregistration_blockers = {
        "ACTIVE_STRATEGY_MISSING",
        "REFERENCE_SNAPSHOT_NOT_READY",
        "BENCHMARK_QUOTE_PROVIDER_NOT_READY",
        "OMXSGI_PROVIDER_NOT_READY",
    }
    return {
        "system_ready_for_preregistration": not any(
            blocker in preregistration_blockers for blocker in blockers
        ),
        "system_ready_for_start": not blockers,
        "blockers": blockers,
        "execution_options": execution_options,
        "evidence": snapshot,
        "operator_inputs": [
            "OFFICIAL_RELEASE_EVIDENCE",
            "FROZEN_MODEL_EVIDENCE",
            "TRANSACTION_COST_ASSUMPTIONS",
            "EXPERIMENT_IDENTITY_AND_OPERATOR_APPROVAL",
        ],
    }


def _operator_identity(value: Any) -> str:
    if not isinstance(value, str) or not _OPERATOR_PATTERN.fullmatch(value):
        raise ValueError("operator identity must start with operator:")
    return value


def _positive_finite(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be numeric")
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise ValueError(f"{name} must be finite and greater than zero")
    return number


def _bounded_reason(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError("reason must be text")
    normalized = value.strip()
    if not normalized or len(normalized) > 2000:
        raise ValueError("reason must contain between 1 and 2000 characters")
    return normalized


def _load_json_object(path: str) -> dict[str, Any]:
    source = Path(path)
    if source.stat().st_size > 1_000_000:
        raise ValueError("JSON input exceeds one megabyte")
    value = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("JSON input must contain one object")
    return value


def registration_from_mapping(
    value: dict[str, Any],
) -> BenchmarkRegistration:
    if not isinstance(value, dict):
        raise ValueError("registration must be an object")
    values = dict(value)
    criteria_value = values.pop("criteria", None)
    if criteria_value is not None:
        if not isinstance(criteria_value, dict):
            raise ValueError("criteria must be an object")
        try:
            values["criteria"] = BenchmarkCriteria(**criteria_value)
        except TypeError as exc:
            raise ValueError("criteria fields do not match the contract") from exc
    try:
        return BenchmarkRegistration(**values)
    except TypeError as exc:
        raise ValueError(
            "registration fields do not match the contract"
        ) from exc


def incident_from_mapping(
    value: dict[str, Any],
) -> BenchmarkIncident:
    if not isinstance(value, dict):
        raise ValueError("incident must be an object")
    values = dict(value)
    try:
        values["session_date"] = date.fromisoformat(values["session_date"])
        values["detected_at"] = _parse_datetime(
            values["detected_at"],
            "detected_at",
        )
    except (AttributeError, KeyError, ValueError) as exc:
        raise ValueError(
            "incident dates must use ISO-8601"
        ) from exc
    try:
        return BenchmarkIncident(**values)
    except TypeError as exc:
        raise ValueError(
            "incident fields do not match the contract"
        ) from exc


def _parse_datetime(value: Any, name: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"{name} must use ISO-8601")
    try:
        parsed = datetime.fromisoformat(
            value.replace("Z", "+00:00")
        )
    except ValueError as exc:
        raise ValueError(f"{name} must use ISO-8601") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{name} must include a timezone")
    return parsed


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Govern the immutable OMXSGI forward paper experiment.",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    create = commands.add_parser("create")
    create.add_argument("--registration-json", required=True)

    commands.add_parser("readiness")

    for name in ("status", "approve", "start", "pause", "resume", "evaluate"):
        command = commands.add_parser(name)
        command.add_argument("--experiment", required=True)
        if name != "status":
            command.add_argument("--operator", required=True)
        if name == "start":
            command.add_argument(
                "--benchmark-level-id",
                required=True,
                type=int,
            )
            command.add_argument(
                "--benchmark-start-level",
                required=True,
                type=float,
            )
            command.add_argument(
                "--benchmark-event-time",
                required=True,
            )
            command.add_argument(
                "--benchmark-available-at",
                required=True,
            )
            command.add_argument(
                "--benchmark-checksum-sha256",
                required=True,
            )

    invalidate = commands.add_parser("invalidate")
    invalidate.add_argument("--experiment", required=True)
    invalidate.add_argument("--operator", required=True)
    invalidate.add_argument("--reason", required=True)

    incident = commands.add_parser("incident")
    incident.add_argument("--experiment", required=True)
    incident.add_argument("--incident-json", required=True)
    incident.add_argument("--operator", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        admin = BenchmarkAdmin()
        if args.command == "create":
            registration = registration_from_mapping(
                _load_json_object(args.registration_json)
            )
            result: Any = {
                "id": admin.create_paper_benchmark_draft(registration),
                "experiment_key": registration.experiment_key,
                "preregistration_hash": registration.payload_hash,
            }
        elif args.command == "readiness":
            result = admin.get_readiness()
        elif args.command == "status":
            result = admin.get_paper_benchmark(args.experiment)
        elif args.command == "approve":
            admin.approve_paper_benchmark(
                args.experiment,
                approved_by=args.operator,
            )
            result = admin.get_paper_benchmark(args.experiment)
        elif args.command == "start":
            evidence = BenchmarkStartEvidence(
                level_id=args.benchmark_level_id,
                benchmark_level=args.benchmark_start_level,
                event_time=_parse_datetime(
                    args.benchmark_event_time,
                    "benchmark_event_time",
                ),
                available_at=_parse_datetime(
                    args.benchmark_available_at,
                    "benchmark_available_at",
                ),
                source_checksum_sha256=(
                    args.benchmark_checksum_sha256
                ),
            )
            admin.start_paper_benchmark(
                args.experiment,
                evidence=evidence,
                started_by=args.operator,
            )
            result = admin.get_paper_benchmark(args.experiment)
        elif args.command == "pause":
            admin.pause_paper_benchmark(
                args.experiment,
                paused_by=args.operator,
            )
            result = admin.get_paper_benchmark(args.experiment)
        elif args.command == "resume":
            admin.resume_paper_benchmark(
                args.experiment,
                resumed_by=args.operator,
            )
            result = admin.get_paper_benchmark(args.experiment)
        elif args.command == "invalidate":
            admin.invalidate_paper_benchmark(
                args.experiment,
                invalidated_by=args.operator,
                reason=args.reason,
            )
            result = admin.get_paper_benchmark(args.experiment)
        elif args.command == "incident":
            incident = incident_from_mapping(
                _load_json_object(args.incident_json)
            )
            result = {
                "id": admin.record_incident(
                    args.experiment,
                    incident,
                    recorded_by=args.operator,
                )
            }
        elif args.command == "evaluate":
            result = admin.evaluate_paper_benchmark(
                args.experiment,
                evaluated_by=args.operator,
            )
        else:
            raise RuntimeError("unsupported command")
    except Exception as exc:
        print(f"benchmark admin error: {exc}", file=sys.stderr)
        return 1
    print(
        json.dumps(
            result,
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
