from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.data.market_data import MarketDataError
from src.data.nasdaq_ndl import (
    NasdaqNdlSftpClient,
    NasdaqNdlSftpConfig,
    nasdaq_reference_source_path,
)


_HOST_KEY_ALGORITHM = "ssh-ed25519"
_HOST_KEY_FINGERPRINT = "SHA256:" + "A" * 43


def _credential_files(tmp_path: Path) -> tuple[Path, Path]:
    private_key = tmp_path / "nasdaq_ed25519"
    private_key.write_text("test-private-key", encoding="ascii")
    private_key.chmod(0o600)
    known_hosts = tmp_path / "known_hosts"
    known_hosts.write_text(
        "sftp.data.nasdaq.com ssh-ed25519 AAAATEST\n",
        encoding="ascii",
    )
    known_hosts.chmod(0o644)
    return private_key, known_hosts


def _config(tmp_path: Path, **overrides) -> NasdaqNdlSftpConfig:
    private_key, known_hosts = _credential_files(tmp_path)
    values = {
        "username": "market-data@example.com",
        "private_key_file": private_key,
        "known_hosts_file": known_hosts,
    }
    values.update(overrides)
    return NasdaqNdlSftpConfig(**values)


class _RemoteFile:
    def __init__(self, content: bytes):
        self._content = content
        self._offset = 0
        self.closed = False
        self.read_sizes = []

    def read(self, size: int) -> bytes:
        self.read_sizes.append(size)
        chunk = self._content[self._offset:self._offset + size]
        self._offset += len(chunk)
        return chunk

    def close(self) -> None:
        self.closed = True


class _SftpClient:
    def __init__(
        self,
        content: bytes,
        *,
        reported_sizes: tuple[int, ...] | None = None,
    ):
        self.remote_file = _RemoteFile(content)
        self.reported_sizes = list(
            reported_sizes or (len(content), len(content))
        )
        self.paths = []
        self.closed = False

    def stat(self, path: str):
        self.paths.append(("stat", path))
        size = self.reported_sizes.pop(0)
        return SimpleNamespace(st_size=size)

    def open(self, path: str, mode: str):
        self.paths.append(("open", path, mode))
        return self.remote_file

    def close(self) -> None:
        self.closed = True


class _SshClient:
    def __init__(self, sftp_client: _SftpClient):
        self.sftp_client = sftp_client
        self.loaded_host_keys = []
        self.policy = None
        self.connect_kwargs = None
        self.closed = False

    def load_host_keys(self, path: str) -> None:
        self.loaded_host_keys.append(path)

    def set_missing_host_key_policy(self, policy) -> None:
        self.policy = policy

    def connect(self, **kwargs) -> None:
        self.connect_kwargs = kwargs

    def open_sftp(self):
        return self.sftp_client

    def close(self) -> None:
        self.closed = True


def _client(
    *,
    config: NasdaqNdlSftpConfig,
    ssh,
    fingerprints=None,
) -> NasdaqNdlSftpClient:
    loaded = fingerprints or frozenset(
        {(_HOST_KEY_ALGORITHM, _HOST_KEY_FINGERPRINT)}
    )
    return NasdaqNdlSftpClient(
        config=config,
        ssh_client_factory=lambda: ssh,
        host_key_fingerprint_loader=lambda _path, _host: loaded,
    )


def _download(client, source_path):
    return client.download(
        source_path,
        expected_host_key_algorithm=_HOST_KEY_ALGORITHM,
        expected_host_key_fingerprint_sha256=_HOST_KEY_FINGERPRINT,
    )


def test_ndl_config_is_built_only_from_explicit_safe_credentials(tmp_path):
    private_key, known_hosts = _credential_files(tmp_path)

    config = NasdaqNdlSftpConfig.from_environ(
        {
            "NASDAQ_NDL_USERNAME": "market-data@example.com",
            "NASDAQ_NDL_PRIVATE_KEY_FILE": str(private_key),
            "NASDAQ_NDL_KNOWN_HOSTS_FILE": str(known_hosts),
            "NASDAQ_NDL_TIMEOUT_SECONDS": "12",
        }
    )

    assert config.host == "sftp.data.nasdaq.com"
    assert config.port == 22
    assert config.username == "market-data@example.com"
    assert config.private_key_file == private_key
    assert config.known_hosts_file == known_hosts
    assert config.timeout_seconds == 12
    assert config.max_file_bytes == 20_000_000


@pytest.mark.parametrize(
    ("field", "value", "message"),
    (
        ("username", "not-an-email", "username"),
        ("username", " user@example.com", "username"),
        ("timeout_seconds", 4, "timeout"),
        ("timeout_seconds", 31, "timeout"),
        ("max_file_bytes", 20_000_001, "max_file_bytes"),
    ),
)
def test_ndl_config_rejects_values_that_weaken_the_fixed_contract(
    tmp_path,
    field,
    value,
    message,
):
    with pytest.raises(MarketDataError, match=message):
        _config(tmp_path, **{field: value})


def test_ndl_config_rejects_relative_symlink_and_public_private_key(
    tmp_path,
):
    private_key, known_hosts = _credential_files(tmp_path)

    with pytest.raises(MarketDataError, match="absolute"):
        NasdaqNdlSftpConfig(
            username="market-data@example.com",
            private_key_file=Path("relative-key"),
            known_hosts_file=known_hosts,
        )

    private_key.chmod(0o644)
    with pytest.raises(MarketDataError, match="permissions"):
        NasdaqNdlSftpConfig(
            username="market-data@example.com",
            private_key_file=private_key,
            known_hosts_file=known_hosts,
        )

    private_key.chmod(0o600)
    symlink = tmp_path / "key-link"
    symlink.symlink_to(private_key)
    with pytest.raises(MarketDataError, match="symlink"):
        NasdaqNdlSftpConfig(
            username="market-data@example.com",
            private_key_file=symlink,
            known_hosts_file=known_hosts,
        )


def test_ndl_config_does_not_chain_sensitive_filesystem_errors(tmp_path):
    _private_key, known_hosts = _credential_files(tmp_path)

    with pytest.raises(MarketDataError) as error:
        NasdaqNdlSftpConfig(
            username="market-data@example.com",
            private_key_file=tmp_path / "missing-private-key",
            known_hosts_file=known_hosts,
        )

    assert str(error.value) == "Nasdaq NDL private key file is unavailable"
    assert error.value.__cause__ is None


def test_reference_source_path_is_exact_and_date_bound():
    assert nasdaq_reference_source_path(date(2026, 7, 30)) == (
        "INET_Ref_Data/20260730/Nordic_Equity_RefData.tip"
    )

    with pytest.raises(MarketDataError, match="date"):
        nasdaq_reference_source_path("2026-07-30")


def test_sftp_download_uses_pinned_host_key_and_key_only_auth(tmp_path):
    content = b"BDBu;Bd20260730;\nEOBd;s1;\n"
    sftp = _SftpClient(content)
    ssh = _SshClient(sftp)
    config = _config(tmp_path, timeout_seconds=11)
    client = _client(
        config=config,
        ssh=ssh,
    )
    source_path = nasdaq_reference_source_path(date(2026, 7, 30))

    downloaded = _download(client, source_path)

    assert downloaded == content
    assert ssh.loaded_host_keys == [str(config.known_hosts_file)]
    assert type(ssh.policy).__name__ == "RejectPolicy"
    assert ssh.connect_kwargs == {
        "hostname": "sftp.data.nasdaq.com",
        "port": 22,
        "username": "market-data@example.com",
        "key_filename": str(config.private_key_file),
        "password": None,
        "allow_agent": False,
        "look_for_keys": False,
        "timeout": 11,
        "banner_timeout": 11,
        "auth_timeout": 11,
        "channel_timeout": 11,
    }
    assert sftp.paths == [
        ("stat", source_path),
        ("open", source_path, "rb"),
        ("stat", source_path),
    ]
    assert sftp.remote_file.closed is True
    assert sftp.closed is True
    assert ssh.closed is True


@pytest.mark.parametrize("reported_size", (0, 20_000_001))
def test_sftp_download_rejects_empty_or_oversized_remote_file(
    tmp_path,
    reported_size,
):
    sftp = _SftpClient(
        b"ignored",
        reported_sizes=(reported_size,),
    )
    ssh = _SshClient(sftp)
    client = _client(
        config=_config(tmp_path),
        ssh=ssh,
    )

    with pytest.raises(MarketDataError, match="size"):
        _download(
            client,
            nasdaq_reference_source_path(date(2026, 7, 30))
        )

    assert sftp.closed is True
    assert ssh.closed is True


def test_sftp_download_rejects_file_changed_during_transfer(tmp_path):
    content = b"complete"
    sftp = _SftpClient(
        content,
        reported_sizes=(len(content), len(content) + 1),
    )
    ssh = _SshClient(sftp)
    client = _client(
        config=_config(tmp_path),
        ssh=ssh,
    )

    with pytest.raises(MarketDataError, match="changed"):
        _download(
            client,
            nasdaq_reference_source_path(date(2026, 7, 30))
        )

    assert sftp.remote_file.closed is True
    assert sftp.closed is True
    assert ssh.closed is True


def test_sftp_download_rejects_server_returning_more_than_requested(
    tmp_path,
):
    class GreedyRemoteFile(_RemoteFile):
        def read(self, size: int) -> bytes:
            if self._offset == 0:
                self._offset = 8
                return b"too-long"
            return b""

    content = b"short"
    sftp = _SftpClient(content)
    sftp.remote_file = GreedyRemoteFile(content)
    ssh = _SshClient(sftp)
    client = _client(
        config=_config(tmp_path),
        ssh=ssh,
    )

    with pytest.raises(MarketDataError, match="declared size"):
        _download(
            client,
            nasdaq_reference_source_path(date(2026, 7, 30))
        )

    assert sftp.remote_file.closed is True
    assert sftp.closed is True
    assert ssh.closed is True


def test_sftp_download_rejects_invalid_remote_path_before_connect(tmp_path):
    factory_calls = []
    client = NasdaqNdlSftpClient(
        config=_config(tmp_path),
        ssh_client_factory=lambda: factory_calls.append(True),
        host_key_fingerprint_loader=lambda _path, _host: frozenset(
            {(_HOST_KEY_ALGORITHM, _HOST_KEY_FINGERPRINT)}
        ),
    )

    with pytest.raises(MarketDataError, match="source path"):
        _download(client, "../../private/file")

    assert factory_calls == []


def test_sftp_download_hides_transport_details_and_always_closes(tmp_path):
    class FailingSshClient(_SshClient):
        def connect(self, **kwargs) -> None:
            raise RuntimeError(
                "secret detail from /private/credentials/nasdaq_ed25519"
            )

    ssh = FailingSshClient(_SftpClient(b"ignored"))
    client = _client(
        config=_config(tmp_path),
        ssh=ssh,
    )

    with pytest.raises(MarketDataError) as error:
        _download(
            client,
            nasdaq_reference_source_path(date(2026, 7, 30))
        )

    assert str(error.value) == "Nasdaq NDL SFTP download failed"
    assert "credentials" not in str(error.value)
    assert ssh.closed is True


def test_sftp_download_rejects_host_key_not_bound_to_entitlement(
    tmp_path,
):
    factory_calls = []
    client = NasdaqNdlSftpClient(
        config=_config(tmp_path),
        ssh_client_factory=lambda: factory_calls.append(True),
        host_key_fingerprint_loader=lambda _path, _host: frozenset(
            {("ssh-ed25519", "SHA256:" + "B" * 43)}
        ),
    )

    with pytest.raises(MarketDataError, match="host key"):
        _download(
            client,
            nasdaq_reference_source_path(date(2026, 7, 30)),
        )

    assert factory_calls == []
