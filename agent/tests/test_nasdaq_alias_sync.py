from datetime import date, datetime, timezone
import logging
from pathlib import Path
import sys

import pytest

from src.data.market_data import MarketDataError
from src.data.nasdaq_reference import NasdaqAliasSyncResult
from src.data.provider_governance import ReferenceDataEntitlement
from src.nasdaq_alias_sync import (
    NasdaqAliasSyncService,
    build_nasdaq_alias_sync_service,
    main,
    run_daemon,
)


class _Database:
    def __init__(self, session_date=date(2026, 7, 30)):
        self.session_date = session_date
        self.requests = []
        self.entitlement_requests = []

    def get_latest_market_session_date(self, *, mic, on_or_before):
        self.requests.append((mic, on_or_before))
        return self.session_date

    def require_runtime_reference_entitlement(self, **kwargs):
        self.entitlement_requests.append(kwargs)
        return ReferenceDataEntitlement(
            entitlement_id=17,
            contract_key="nasdaq-nordic-reference-xsto-v1",
            provider="nasdaq-nordic",
            product_name="Nordic Equity Reference Data Files",
            mic="XSTO",
            transport="SFTP",
            usage_scope="INTERNAL_ANALYSIS_AND_PAPER",
            external_distribution=False,
            raw_storage_allowed=True,
            derived_storage_allowed=True,
            retention_policy="Internal retention permitted.",
            terms_url="https://example.com/agreement",
            terms_checksum_sha256="a" * 64,
            status="VALIDATED",
            valid_from=date(2026, 7, 1),
            valid_until=date(2026, 12, 31),
            legal_reviewed_at=datetime(
                2026,
                7,
                1,
                tzinfo=timezone.utc,
            ),
            storage_reviewed_at=datetime(
                2026,
                7,
                1,
                tzinfo=timezone.utc,
            ),
            entitlement_verified_at=datetime(
                2026,
                7,
                1,
                tzinfo=timezone.utc,
            ),
            host_key_algorithm="ssh-ed25519",
            host_key_fingerprint_sha256="SHA256:" + "A" * 43,
            reviewed_by="operator:test",
        )


class _NdlClient:
    def __init__(self, content=b"official-tip"):
        self.content = content
        self.paths = []

    def download(self, source_path, **kwargs):
        self.paths.append((source_path, kwargs))
        return self.content


class _ReferenceService:
    def __init__(self):
        self.calls = []

    def sync_file(self, *, content, source_path, entitlement_id):
        self.calls.append((content, source_path, entitlement_id))
        return NasdaqAliasSyncResult(
            inserted=True,
            aliases_inserted=416,
            sync_run_id=92,
            snapshot_id=93,
        )


def _credential_environment(tmp_path: Path) -> dict[str, str]:
    private_key = tmp_path / "nasdaq_ed25519"
    private_key.write_text("test-private-key", encoding="ascii")
    private_key.chmod(0o600)
    known_hosts = tmp_path / "known_hosts"
    known_hosts.write_text(
        "sftp.data.nasdaq.com ssh-ed25519 AAAATEST\n",
        encoding="ascii",
    )
    known_hosts.chmod(0o644)
    return {
        "ENABLE_NASDAQ_REFERENCE_SYNC": "true",
        "NASDAQ_NDL_USERNAME": "market-data@example.com",
        "NASDAQ_NDL_PRIVATE_KEY_FILE": str(private_key),
        "NASDAQ_NDL_KNOWN_HOSTS_FILE": str(known_hosts),
        "NASDAQ_REFERENCE_CONTRACT_KEY": (
            "nasdaq-nordic-reference-xsto-v1"
        ),
    }


def test_alias_sync_is_disabled_by_default():
    with pytest.raises(MarketDataError, match="disabled"):
        build_nasdaq_alias_sync_service(
            database=_Database(),
            environ={},
        )


def test_alias_sync_builds_only_with_explicit_credentials(tmp_path):
    service = build_nasdaq_alias_sync_service(
        database=_Database(),
        environ=_credential_environment(tmp_path),
        ssh_client_factory=lambda: object(),
        clock=lambda: datetime(
            2026,
            7,
            30,
            5,
            0,
            tzinfo=timezone.utc,
        ),
    )

    assert isinstance(service, NasdaqAliasSyncService)


def test_alias_sync_rejects_unapproved_tip_version_before_connect(tmp_path):
    environ = _credential_environment(tmp_path)
    environ["NASDAQ_NDL_TIP_VERSION"] = "3.10.18.0"
    factory_calls = []

    with pytest.raises(MarketDataError, match="TIP version"):
        build_nasdaq_alias_sync_service(
            database=_Database(),
            environ=environ,
            ssh_client_factory=lambda: factory_calls.append(True),
        )

    assert factory_calls == []


def test_alias_sync_uses_latest_official_xsto_session():
    database = _Database(session_date=date(2026, 7, 29))
    ndl_client = _NdlClient()
    reference_service = _ReferenceService()
    service = NasdaqAliasSyncService(
        database=database,
        ndl_client=ndl_client,
        reference_service=reference_service,
        contract_key="nasdaq-nordic-reference-xsto-v1",
        clock=lambda: datetime(
            2026,
            7,
            30,
            5,
            0,
            tzinfo=timezone.utc,
        ),
    )

    result = service.sync_latest()

    expected_path = (
        "INET_Ref_Data/20260729/Nordic_Equity_RefData.tip"
    )
    assert result.aliases_inserted == 416
    assert database.requests == [("XSTO", date(2026, 7, 30))]
    assert ndl_client.paths == [
        (
            expected_path,
            {
                "expected_host_key_algorithm": "ssh-ed25519",
                "expected_host_key_fingerprint_sha256": (
                    "SHA256:" + "A" * 43
                ),
            },
        )
    ]
    assert reference_service.calls == [
        (b"official-tip", expected_path, 17)
    ]
    assert database.entitlement_requests == [
        {
            "contract_key": "nasdaq-nordic-reference-xsto-v1",
            "provider": "nasdaq-nordic",
            "mic": "XSTO",
            "transport": "SFTP",
            "usage_scope": "INTERNAL_ANALYSIS_AND_PAPER",
            "now": datetime(
                2026,
                7,
                30,
                5,
                0,
                tzinfo=timezone.utc,
            ),
        }
    ]


def test_alias_sync_waits_until_daily_reference_file_is_available():
    database = _Database()
    ndl_client = _NdlClient()
    service = NasdaqAliasSyncService(
        database=database,
        ndl_client=ndl_client,
        reference_service=_ReferenceService(),
        contract_key="nasdaq-nordic-reference-xsto-v1",
        clock=lambda: datetime(
            2026,
            7,
            30,
            4,
            44,
            tzinfo=timezone.utc,
        ),
    )

    with pytest.raises(MarketDataError, match="06:45"):
        service.sync_latest()

    assert database.requests == []
    assert ndl_client.paths == []


def test_alias_sync_rejects_missing_market_calendar():
    database = _Database(session_date=None)
    ndl_client = _NdlClient()
    service = NasdaqAliasSyncService(
        database=database,
        ndl_client=ndl_client,
        reference_service=_ReferenceService(),
        contract_key="nasdaq-nordic-reference-xsto-v1",
        clock=lambda: datetime(
            2026,
            7,
            30,
            5,
            0,
            tzinfo=timezone.utc,
        ),
    )

    with pytest.raises(MarketDataError, match="calendar"):
        service.sync_latest()

    assert ndl_client.paths == []


def test_alias_sync_requires_timezone_aware_clock():
    service = NasdaqAliasSyncService(
        database=_Database(),
        ndl_client=_NdlClient(),
        reference_service=_ReferenceService(),
        contract_key="nasdaq-nordic-reference-xsto-v1",
        clock=lambda: datetime(2026, 7, 30, 7, 0),
    )

    with pytest.raises(MarketDataError, match="timezone-aware"):
        service.sync_latest()


def test_alias_daemon_retries_safe_failures_at_bounded_interval():
    calls = []
    sleeps = []

    class Service:
        def sync_latest(self):
            calls.append(True)
            if len(calls) == 1:
                raise MarketDataError("safe failure")
            raise KeyboardInterrupt

    run_daemon(
        service=Service(),
        interval_seconds=3600,
        sleeper=sleeps.append,
    )

    assert len(calls) == 2
    assert sleeps == [3600]


def test_alias_sync_once_exits_without_traceback_on_safe_error(
    monkeypatch,
    caplog,
):
    import src.nasdaq_alias_sync as alias_sync

    class Service:
        def sync_latest(self):
            raise MarketDataError("Nasdaq NDL SFTP download failed")

    monkeypatch.setattr(alias_sync, "Database", lambda: _Database())
    monkeypatch.setattr(
        alias_sync,
        "build_nasdaq_alias_sync_service",
        lambda **_values: Service(),
    )
    monkeypatch.setattr(sys, "argv", ["nasdaq-alias-sync", "once"])

    with caplog.at_level(logging.ERROR):
        with pytest.raises(SystemExit) as error:
            main()

    assert error.value.code == 1
    assert "Nasdaq NDL SFTP download failed" in caplog.text
    assert "Traceback" not in caplog.text


def test_alias_daemon_defaults_to_hourly_retry_after_morning_cutoff(
    monkeypatch,
):
    import src.nasdaq_alias_sync as alias_sync

    captured = []
    monkeypatch.setattr(alias_sync, "Database", lambda: _Database())
    monkeypatch.setattr(
        alias_sync,
        "build_nasdaq_alias_sync_service",
        lambda **_values: _ReferenceService(),
    )
    monkeypatch.setattr(
        alias_sync,
        "run_daemon",
        lambda **values: captured.append(values),
    )
    monkeypatch.delenv(
        "NASDAQ_REFERENCE_SYNC_INTERVAL_SECONDS",
        raising=False,
    )
    monkeypatch.setattr(sys, "argv", ["nasdaq-alias-sync", "daemon"])

    main()

    assert captured[0]["interval_seconds"] == 3600
