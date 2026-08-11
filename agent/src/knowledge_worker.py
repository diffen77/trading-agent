"""Continuously mirror bounded trading evidence into the knowledge graph."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import logging
import os
import time

from src.knowledge_graph import KnowledgeGraph, KnowledgeSyncResult


logger = logging.getLogger(__name__)


def _run_key(synced_at: datetime) -> str:
    return f"knowledge:{synced_at.strftime('%Y%m%dT%H%M%S%fZ')}"


def _interval_seconds(environ) -> int:
    raw = environ.get("KNOWLEDGE_GRAPH_SYNC_INTERVAL_SECONDS", "300")
    try:
        value = int(raw)
    except (TypeError, ValueError) as error:
        raise ValueError(
            "KNOWLEDGE_GRAPH_SYNC_INTERVAL_SECONDS must be an integer"
        ) from error
    if not 60 <= value <= 3600:
        raise ValueError(
            "KNOWLEDGE_GRAPH_SYNC_INTERVAL_SECONDS must be between 60 and 3600"
        )
    return value


def run_knowledge_cycle(
    database,
    graph,
    *,
    synced_at: datetime,
) -> KnowledgeSyncResult:
    if (
        not isinstance(synced_at, datetime)
        or synced_at.tzinfo is None
        or synced_at.utcoffset() is None
    ):
        raise ValueError("synced_at must be timezone-aware")
    checked_at = synced_at.astimezone(timezone.utc)
    try:
        graph.ensure_schema()
        result = graph.sync_once(database, synced_at=checked_at)
    except Exception as error:
        logger.error(
            "knowledge_graph_sync_failed error_type=%s",
            type(error).__name__,
        )
        result = KnowledgeSyncResult(
            status="FAILED",
            synced_at=checked_at.isoformat(),
            synced={},
            total_nodes=0,
            total_relationships=0,
            error_code="KNOWLEDGE_GRAPH_SYNC_FAILED",
        )

    try:
        database.record_knowledge_graph_sync_run(
            run_key=_run_key(checked_at),
            synced_at=checked_at,
            status=result.status,
            synced_counts=result.synced,
            total_nodes=result.total_nodes,
            total_relationships=result.total_relationships,
            error_code=result.error_code,
        )
    except Exception as error:
        logger.error(
            "knowledge_graph_evidence_persistence_failed error_type=%s",
            type(error).__name__,
        )
        return KnowledgeSyncResult(
            status="FAILED",
            synced_at=checked_at.isoformat(),
            synced=result.synced,
            total_nodes=result.total_nodes,
            total_relationships=result.total_relationships,
            error_code="SYNC_EVIDENCE_PERSISTENCE_FAILED",
        )
    return result


def main(
    argv: list[str] | None = None,
    *,
    database_factory=None,
    graph_factory=KnowledgeGraph.from_environment,
    now_factory=lambda: datetime.now(timezone.utc),
    sleeper=time.sleep,
    environ=None,
) -> int:
    parser = argparse.ArgumentParser(
        description="Synchronise the trading knowledge graph.",
    )
    parser.add_argument(
        "mode",
        choices=("once", "daemon", "health"),
        nargs="?",
        default="once",
    )
    args = parser.parse_args(argv)
    environment = os.environ if environ is None else environ
    if database_factory is None:
        from src.data.database import Database

        database_factory = Database
    graph = graph_factory(environment)
    try:
        if args.mode == "health":
            graph.verify_connectivity()
            return 0

        database = database_factory()
        if args.mode == "once":
            result = run_knowledge_cycle(
                database,
                graph,
                synced_at=now_factory(),
            )
            print(json.dumps(result.__dict__, ensure_ascii=False))
            return 0 if result.status == "SUCCEEDED" else 1

        interval = _interval_seconds(environment)
        while True:
            result = run_knowledge_cycle(
                database,
                graph,
                synced_at=now_factory(),
            )
            logger.info(
                "knowledge_graph_cycle status=%s nodes=%d "
                "relationships=%d error_code=%s",
                result.status,
                result.total_nodes,
                result.total_relationships,
                result.error_code,
            )
            sleeper(interval)
    finally:
        graph.close()


if __name__ == "__main__":
    raise SystemExit(main())
