from datetime import datetime, timezone
import gzip
import hashlib
from io import BytesIO

from botocore.exceptions import ClientError
import pytest

from src.object_archive import (
    ObjectArchiveStore,
    ObjectStorageError,
    ObjectStorageSettings,
    build_object_key,
    run_archive_cycle,
)


NOW = datetime(2026, 8, 5, 8, 30, tzinfo=timezone.utc)


def _source(
    *,
    file_id=17,
    provider="esma-firds",
    data_type="reference",
    source_kind="market-data",
):
    raw = b"verified market data"
    compressed = gzip.compress(raw, mtime=0)
    return {
        "id": file_id,
        "source_kind": source_kind,
        "provider": provider,
        "data_type": data_type,
        "file_name": "source.xml",
        "report_minute": NOW,
        "received_at": NOW,
        "checksum_sha256": hashlib.sha256(raw).hexdigest(),
        "content_encoding": "gzip",
        "compressed_content": compressed,
        "uncompressed_bytes": len(raw),
    }


class _S3Client:
    def __init__(self):
        self.objects = {}
        self.puts = []
        self.list_calls = []

    def head_object(self, *, Bucket, Key):
        try:
            value = self.objects[(Bucket, Key)]
        except KeyError:
            raise ClientError(
                {"Error": {"Code": "404", "Message": "not found"}},
                "HeadObject",
            )
        return {
            "ContentLength": len(value["Body"]),
            "Metadata": value["Metadata"],
        }

    def put_object(self, **values):
        self.puts.append(values)
        self.objects[(values["Bucket"], values["Key"])] = values
        return {"ETag": '"synthetic"'}

    def list_objects_v2(self, **values):
        self.list_calls.append(values)
        return {"KeyCount": 0}

    def get_object(self, *, Bucket, Key):
        value = self.objects[(Bucket, Key)]
        return {"Body": BytesIO(value["Body"])}

    def delete_object(self, *, Bucket, Key):
        self.objects.pop((Bucket, Key), None)
        return {}


class _Database:
    def __init__(self, sources):
        self.sources = list(sources)
        self.recorded = []

    def get_pending_object_archive_inputs(self, *, limit):
        return self.sources[:limit]

    def record_object_archive_run(self, **values):
        self.recorded.append(values)
        return 41


def _settings():
    return ObjectStorageSettings(
        endpoint="http://100.116.226.27:3900",
        access_key_id="synthetic-access",
        secret_access_key="synthetic-secret",
        bucket="orders",
    )


def test_settings_are_bounded_isolated_and_do_not_repr_credentials():
    settings = ObjectStorageSettings.from_environment({
        "S3_ENDPOINT": "http://100.116.226.27:3900/",
        "S3_ACCESS_KEY_ID": "synthetic-access",
        "S3_SECRET_ACCESS_KEY": "synthetic-secret",
        "S3_BUCKET": "orders",
    })

    assert settings.endpoint == "http://100.116.226.27:3900"
    assert settings.bucket == "orders"
    assert settings.prefix == "trading-agent"
    assert settings.region == "garage"
    assert "synthetic-access" not in repr(settings)
    assert "synthetic-secret" not in repr(settings)

    for endpoint in (
        "ftp://garage:3900",
        "http://user:pass@garage:3900",
        "http://garage:3900/path",
        "http://garage:3900?query=1",
    ):
        with pytest.raises(ObjectStorageError):
            ObjectStorageSettings(
                endpoint=endpoint,
                access_key_id="synthetic-access",
                secret_access_key="synthetic-secret",
                bucket="orders",
            )


def test_object_key_is_deterministic_and_never_uses_the_source_file_name():
    source = _source(
        provider="Nasdaq Nordic",
        data_type="Delayed/Equity",
    )

    key = build_object_key(source, prefix="trading-agent")

    assert key.startswith(
        "trading-agent/market-data/nasdaq-nordic/"
        "delayed-equity/2026/08/05/17-"
    )
    assert key.endswith(".gz")
    assert "source.xml" not in key
    assert ".." not in key


def test_reference_file_uses_an_isolated_reference_data_key():
    source = _source(
        source_kind="reference-data",
        provider="ESMA FIRDS",
        data_type="reference-universe",
    )

    key = build_object_key(source, prefix="trading-agent")

    assert key.startswith(
        "trading-agent/reference-data/esma-firds/"
        "reference-universe/2026/08/05/17-"
    )


def test_store_uploads_once_and_verifies_exact_object_metadata():
    client = _S3Client()
    store = ObjectArchiveStore(_settings(), client=client)
    source = _source()

    first = store.archive_file(source)
    second = store.archive_file(source)

    assert first.uploaded is True
    assert second.uploaded is False
    assert len(client.puts) == 1
    assert first.object_key == second.object_key
    assert first.compressed_bytes == len(source["compressed_content"])
    assert client.puts[0]["Metadata"]["raw-sha256"] == (
        source["checksum_sha256"]
    )
    assert client.puts[0]["Metadata"]["source-id"] == str(source["id"])
    assert client.puts[0]["Metadata"]["source-kind"] == "market-data"


def test_store_refuses_a_conflicting_existing_object():
    client = _S3Client()
    store = ObjectArchiveStore(_settings(), client=client)
    source = _source()
    key = build_object_key(source, prefix="trading-agent")
    client.objects[("orders", key)] = {
        "Body": b"wrong",
        "Metadata": {
            "raw-sha256": source["checksum_sha256"],
            "compressed-sha256": "0" * 64,
            "source-id": str(source["id"]),
        },
    }

    with pytest.raises(ObjectStorageError, match="conflicts"):
        store.archive_file(source)

    assert client.puts == []


def test_round_trip_probe_reads_back_and_removes_its_exact_object():
    client = _S3Client()
    store = ObjectArchiveStore(_settings(), client=client)

    key = store.verify_round_trip(
        key_factory=lambda: "trading-agent/probes/test-probe",
        payload=b"synthetic-probe",
    )

    assert key == "trading-agent/probes/test-probe"
    assert client.objects == {}


def test_cycle_archives_pending_rows_and_records_bounded_evidence():
    database = _Database([_source()])
    client = _S3Client()
    store = ObjectArchiveStore(_settings(), client=client)

    result = run_archive_cycle(
        database,
        store,
        evaluated_at=NOW,
        max_files=10,
        run_key_factory=lambda: "object-archive:test-cycle",
    )

    assert result.status == "SUCCEEDED"
    assert result.selected_count == 1
    assert result.archived_count == 1
    assert result.failed_count == 0
    assert database.recorded[0]["run_key"] == "object-archive:test-cycle"
    assert database.recorded[0]["reason_code"] == "ARCHIVED"
    assert database.recorded[0]["objects"][0]["source_kind"] == "market-data"
    assert database.recorded[0]["objects"][0]["source_id"] == 17
    assert "synthetic-access" not in repr(database.recorded)
    assert "synthetic-secret" not in repr(database.recorded)


def test_cycle_records_storage_failure_without_raising_or_losing_progress():
    class _FailingStore:
        def archive_file(self, source):
            raise ObjectStorageError("secret upstream detail")

    database = _Database([_source()])

    result = run_archive_cycle(
        database,
        _FailingStore(),
        evaluated_at=NOW,
        max_files=10,
        run_key_factory=lambda: "object-archive:test-failure",
    )

    assert result.status == "FAILED"
    assert result.failed_count == 1
    assert database.recorded[0]["reason_code"] == "OBJECT_STORAGE_ERROR"
    assert "secret upstream detail" not in repr(database.recorded)
