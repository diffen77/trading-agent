from datetime import datetime, timezone

from src.knowledge_graph import KnowledgeSyncResult
from src.knowledge_worker import run_knowledge_cycle


NOW = datetime(2026, 8, 5, 8, 0, tzinfo=timezone.utc)


class _Database:
    def __init__(self):
        self.runs = []

    def record_knowledge_graph_sync_run(self, **values):
        self.runs.append(values)
        return 7


class _Graph:
    def __init__(self, *, error=None):
        self.error = error
        self.schema_ready = False

    def ensure_schema(self):
        self.schema_ready = True

    def sync_once(self, database, *, synced_at):
        assert synced_at == NOW
        if self.error:
            raise self.error
        return KnowledgeSyncResult(
            status="SUCCEEDED",
            synced_at=NOW.isoformat(),
            synced={
                "instruments": 417,
                "decisions": 36,
                "predictions": 140,
                "outcomes": 360,
                "trades": 3,
                "learnings": 0,
            },
            total_nodes=967,
            total_relationships=1022,
        )


def test_cycle_syncs_graph_and_records_visible_operational_evidence():
    database = _Database()
    graph = _Graph()

    result = run_knowledge_cycle(
        database,
        graph,
        synced_at=NOW,
    )

    assert graph.schema_ready
    assert result.status == "SUCCEEDED"
    assert result.total_nodes == 967
    assert database.runs == [
        {
            "run_key": "knowledge:20260805T080000000000Z",
            "synced_at": NOW,
            "status": "SUCCEEDED",
            "synced_counts": result.synced,
            "total_nodes": 967,
            "total_relationships": 1022,
            "error_code": None,
        }
    ]


def test_cycle_records_a_bounded_error_without_leaking_driver_details():
    database = _Database()
    graph = _Graph(error=RuntimeError("password=private graph detail"))

    result = run_knowledge_cycle(
        database,
        graph,
        synced_at=NOW,
    )

    assert result.status == "FAILED"
    assert result.error_code == "KNOWLEDGE_GRAPH_SYNC_FAILED"
    assert "private graph detail" not in str(database.runs)
    assert database.runs[0]["status"] == "FAILED"
