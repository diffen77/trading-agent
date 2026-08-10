"""Fail-closed SFTP transport for entitlement-delivered Nasdaq files."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import date
import base64
import hashlib
import os
from pathlib import Path
import re
import stat

from .market_data import MarketDataError


_NASDAQ_NDL_HOST = "sftp.data.nasdaq.com"
_NASDAQ_NDL_PORT = 22
_MAX_FILE_BYTES = 20_000_000
_MAX_CREDENTIAL_FILE_BYTES = 1_000_000
_READ_CHUNK_BYTES = 64 * 1024
_USERNAME_PATTERN = re.compile(
    r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@"
    r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
    r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+$"
)
_SOURCE_PATH_PATTERN = re.compile(
    r"^INET_Ref_Data/(?P<date>\d{8})/"
    r"Nordic_Equity_RefData\.tip$"
)
_HOST_KEY_ALGORITHM_PATTERN = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9@._+-]{2,99}$"
)
_HOST_KEY_FINGERPRINT_PATTERN = re.compile(
    r"^SHA256:[A-Za-z0-9+/]{43}$"
)


@dataclass(frozen=True)
class NasdaqNdlSftpConfig:
    """Validated key-only connection settings for the fixed Nasdaq host."""

    username: str
    private_key_file: Path
    known_hosts_file: Path
    timeout_seconds: int = 15
    max_file_bytes: int = _MAX_FILE_BYTES
    host: str = field(default=_NASDAQ_NDL_HOST, init=False)
    port: int = field(default=_NASDAQ_NDL_PORT, init=False)

    def __post_init__(self) -> None:
        if (
            not isinstance(self.username, str)
            or len(self.username) > 254
            or _USERNAME_PATTERN.fullmatch(self.username) is None
        ):
            raise MarketDataError("Nasdaq NDL username is invalid")
        if (
            not isinstance(self.timeout_seconds, int)
            or isinstance(self.timeout_seconds, bool)
            or not 5 <= self.timeout_seconds <= 30
        ):
            raise MarketDataError(
                "Nasdaq NDL timeout must be between 5 and 30 seconds"
            )
        if self.max_file_bytes != _MAX_FILE_BYTES:
            raise MarketDataError(
                "Nasdaq NDL max_file_bytes must remain 20000000"
            )

        _validate_local_file(
            self.private_key_file,
            field_name="Nasdaq NDL private key",
            private=True,
        )
        _validate_local_file(
            self.known_hosts_file,
            field_name="Nasdaq NDL known_hosts",
            private=False,
        )
        try:
            same_file = os.path.samefile(
                self.private_key_file,
                self.known_hosts_file,
            )
        except OSError:
            raise MarketDataError(
                "Nasdaq NDL credential files cannot be inspected"
            ) from None
        if same_file:
            raise MarketDataError(
                "Nasdaq NDL credential files must be distinct"
            )

    @classmethod
    def from_environ(
        cls,
        environ: Mapping[str, str],
    ) -> "NasdaqNdlSftpConfig":
        if not isinstance(environ, Mapping):
            raise MarketDataError("Nasdaq NDL environment is invalid")
        username = environ.get("NASDAQ_NDL_USERNAME", "")
        private_key = environ.get("NASDAQ_NDL_PRIVATE_KEY_FILE", "")
        known_hosts = environ.get("NASDAQ_NDL_KNOWN_HOSTS_FILE", "")
        timeout = _bounded_integer(
            environ.get("NASDAQ_NDL_TIMEOUT_SECONDS", "15"),
            field_name="NASDAQ_NDL_TIMEOUT_SECONDS",
            minimum=5,
            maximum=30,
        )
        if not private_key:
            raise MarketDataError(
                "NASDAQ_NDL_PRIVATE_KEY_FILE is required"
            )
        if not known_hosts:
            raise MarketDataError(
                "NASDAQ_NDL_KNOWN_HOSTS_FILE is required"
            )
        return cls(
            username=username,
            private_key_file=Path(private_key),
            known_hosts_file=Path(known_hosts),
            timeout_seconds=timeout,
        )


class NasdaqNdlSftpClient:
    """Download one exact Nasdaq reference path with bounded memory use."""

    def __init__(
        self,
        *,
        config: NasdaqNdlSftpConfig,
        ssh_client_factory: Callable[[], object] | None = None,
        host_key_fingerprint_loader: Callable[
            [Path, str],
            frozenset[tuple[str, str]],
        ] | None = None,
    ) -> None:
        if not isinstance(config, NasdaqNdlSftpConfig):
            raise MarketDataError("Nasdaq NDL SFTP config is invalid")
        self._config = config
        self._ssh_client_factory = (
            ssh_client_factory or _new_ssh_client
        )
        self._host_key_fingerprint_loader = (
            host_key_fingerprint_loader
            or _load_host_key_fingerprints
        )

    def download(
        self,
        source_path: str,
        *,
        expected_host_key_algorithm: str,
        expected_host_key_fingerprint_sha256: str,
    ) -> bytes:
        _validate_source_path(source_path)
        ssh_client = None
        sftp_client = None
        remote_file = None
        try:
            _assert_entitlement_host_key(
                self._host_key_fingerprint_loader(
                    self._config.known_hosts_file,
                    self._config.host,
                ),
                expected_algorithm=expected_host_key_algorithm,
                expected_fingerprint=(
                    expected_host_key_fingerprint_sha256
                ),
            )
            ssh_client = self._ssh_client_factory()
            ssh_client.load_host_keys(
                str(self._config.known_hosts_file)
            )
            ssh_client.set_missing_host_key_policy(_reject_policy())
            ssh_client.connect(
                hostname=self._config.host,
                port=self._config.port,
                username=self._config.username,
                key_filename=str(self._config.private_key_file),
                password=None,
                allow_agent=False,
                look_for_keys=False,
                timeout=self._config.timeout_seconds,
                banner_timeout=self._config.timeout_seconds,
                auth_timeout=self._config.timeout_seconds,
                channel_timeout=self._config.timeout_seconds,
            )
            sftp_client = ssh_client.open_sftp()
            expected_size = _validated_remote_size(
                sftp_client.stat(source_path).st_size,
                maximum=self._config.max_file_bytes,
            )
            remote_file = sftp_client.open(source_path, "rb")
            content = _read_exact_bounded(
                remote_file,
                expected_size=expected_size,
            )
            remote_file.close()
            remote_file = None
            final_size = _validated_remote_size(
                sftp_client.stat(source_path).st_size,
                maximum=self._config.max_file_bytes,
            )
            if final_size != expected_size:
                raise MarketDataError(
                    "Nasdaq NDL reference file changed during transfer"
                )
            return content
        except MarketDataError:
            raise
        except Exception:
            raise MarketDataError(
                "Nasdaq NDL SFTP download failed"
            ) from None
        finally:
            _safe_close(remote_file)
            _safe_close(sftp_client)
            _safe_close(ssh_client)


def nasdaq_reference_source_path(session_date: date) -> str:
    if (
        not isinstance(session_date, date)
        or hasattr(session_date, "hour")
    ):
        raise MarketDataError(
            "Nasdaq reference session date must be a date"
        )
    return (
        f"INET_Ref_Data/{session_date:%Y%m%d}/"
        "Nordic_Equity_RefData.tip"
    )


def _validate_local_file(
    path: Path,
    *,
    field_name: str,
    private: bool,
) -> None:
    if not isinstance(path, Path) or not path.is_absolute():
        raise MarketDataError(f"{field_name} path must be absolute")
    if path.is_symlink():
        raise MarketDataError(f"{field_name} path cannot be a symlink")
    try:
        file_stat = path.stat()
    except OSError:
        raise MarketDataError(
            f"{field_name} file is unavailable"
        ) from None
    if not stat.S_ISREG(file_stat.st_mode):
        raise MarketDataError(f"{field_name} must be a regular file")
    if (
        file_stat.st_size <= 0
        or file_stat.st_size > _MAX_CREDENTIAL_FILE_BYTES
    ):
        raise MarketDataError(f"{field_name} file size is invalid")
    permissions = stat.S_IMODE(file_stat.st_mode)
    if private and permissions & 0o077:
        raise MarketDataError(
            f"{field_name} permissions must not allow group or other access"
        )
    if not private and permissions & 0o022:
        raise MarketDataError(
            f"{field_name} permissions must not allow group or other writes"
        )


def _validate_source_path(source_path: str) -> date:
    if (
        not isinstance(source_path, str)
        or len(source_path) > 100
    ):
        raise MarketDataError("Nasdaq NDL source path is invalid")
    match = _SOURCE_PATH_PATTERN.fullmatch(source_path)
    if match is None:
        raise MarketDataError("Nasdaq NDL source path is invalid")
    try:
        parsed = date(
            int(match.group("date")[0:4]),
            int(match.group("date")[4:6]),
            int(match.group("date")[6:8]),
        )
    except ValueError as exc:
        raise MarketDataError(
            "Nasdaq NDL source path date is invalid"
        ) from exc
    if nasdaq_reference_source_path(parsed) != source_path:
        raise MarketDataError("Nasdaq NDL source path is invalid")
    return parsed


def _validated_remote_size(value: object, *, maximum: int) -> int:
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or not 1 <= value <= maximum
    ):
        raise MarketDataError(
            "Nasdaq NDL reference file size is invalid"
        )
    return value


def _read_exact_bounded(remote_file, *, expected_size: int) -> bytes:
    chunks = []
    remaining = expected_size
    while remaining:
        chunk = remote_file.read(min(_READ_CHUNK_BYTES, remaining))
        if not isinstance(chunk, bytes):
            raise MarketDataError(
                "Nasdaq NDL reference stream returned invalid data"
            )
        if not chunk:
            raise MarketDataError(
                "Nasdaq NDL reference file is shorter than declared"
            )
        if len(chunk) > remaining:
            raise MarketDataError(
                "Nasdaq NDL reference file exceeds declared size"
            )
        chunks.append(chunk)
        remaining -= len(chunk)
    extra = remote_file.read(1)
    if extra != b"":
        raise MarketDataError(
            "Nasdaq NDL reference file exceeds declared size"
        )
    return b"".join(chunks)


def _bounded_integer(
    value: object,
    *,
    field_name: str,
    minimum: int,
    maximum: int,
) -> int:
    try:
        parsed = int(str(value))
    except (TypeError, ValueError) as exc:
        raise MarketDataError(f"{field_name} must be an integer") from exc
    if not minimum <= parsed <= maximum:
        raise MarketDataError(
            f"{field_name} must be between {minimum} and {maximum}"
        )
    return parsed


def _new_ssh_client():
    try:
        import paramiko
    except ImportError:
        raise MarketDataError(
            "Nasdaq NDL SFTP support is unavailable"
        ) from None
    return paramiko.SSHClient()


def _load_host_key_fingerprints(
    known_hosts_file: Path,
    host: str,
) -> frozenset[tuple[str, str]]:
    try:
        import paramiko
        host_keys = paramiko.HostKeys(str(known_hosts_file))
        matching = host_keys.lookup(host)
        if not matching:
            raise MarketDataError(
                "Nasdaq NDL known_hosts lacks the approved host"
            )
        return frozenset(
            (
                key.get_name(),
                "SHA256:"
                + base64.b64encode(
                    hashlib.sha256(key.asbytes()).digest()
                ).decode("ascii").rstrip("="),
            )
            for key in matching.values()
        )
    except MarketDataError:
        raise
    except Exception:
        raise MarketDataError(
            "Nasdaq NDL host key verification failed"
        ) from None


def _assert_entitlement_host_key(
    fingerprints: object,
    *,
    expected_algorithm: object,
    expected_fingerprint: object,
) -> None:
    if (
        not isinstance(expected_algorithm, str)
        or _HOST_KEY_ALGORITHM_PATTERN.fullmatch(
            expected_algorithm
        ) is None
        or not isinstance(expected_fingerprint, str)
        or _HOST_KEY_FINGERPRINT_PATTERN.fullmatch(
            expected_fingerprint
        ) is None
    ):
        raise MarketDataError(
            "Nasdaq NDL entitlement host key is invalid"
        )
    expected = frozenset(
        {(expected_algorithm, expected_fingerprint)}
    )
    if fingerprints != expected:
        raise MarketDataError(
            "Nasdaq NDL known_hosts does not match entitlement host key"
        )


def _reject_policy():
    try:
        import paramiko
    except ImportError:
        raise MarketDataError(
            "Nasdaq NDL SFTP support is unavailable"
        ) from None
    return paramiko.RejectPolicy()


def _safe_close(resource) -> None:
    if resource is None:
        return
    try:
        resource.close()
    except Exception:
        pass
