"""Archive validated market-data payloads in isolated S3-compatible storage."""

from __future__ import annotations

import argparse
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import logging
import os
import re
import secrets
import time
from typing import Any
from urllib.parse import urlparse
from uuid import uuid4

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from .runtime_secrets import read_runtime_secret


logger = logging.getLogger(__name__)

_BUCKET_PATTERN = re.compile(
    r"^[a-z0-9](?:[a-z0-9.-]{1,61}[a-z0-9])?$"
)
_CHECKSUM_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_PREFIX_PATTERN = re.compile(r"^[a-z0-9](?:[a-z0-9/-]{0,98}[a-z0-9])?$")
_SLUG_PATTERN = re.compile(r"[^a-z0-9]+")
_MAX_ARCHIVE_BYTES = 64 * 1024 * 1024
_SOURCE_KINDS = frozenset({"market-data", "reference-data"})


class ObjectStorageError(RuntimeError, ValueError):
    """Raised when object-storage configuration or evidence is unsafe."""


@dataclass(frozen=True)
class ObjectStorageSettings:
    endpoint: str
    access_key_id: str = field(repr=False)
    secret_access_key: str = field(repr=False)
    bucket: str
    prefix: str = "trading-agent"
    region: str = "garage"

    def __post_init__(self) -> None:
        endpoint = self.endpoint.strip().rstrip("/")
        parsed = urlparse(endpoint)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in {"", "/"}
            or parsed.params
            or parsed.query
            or parsed.fragment
        ):
            raise ObjectStorageError("S3_ENDPOINT is invalid")
        try:
            parsed.port
        except ValueError as error:
            raise ObjectStorageError("S3_ENDPOINT has an invalid port") from error

        bucket = self.bucket.strip()
        if (
            not _BUCKET_PATTERN.fullmatch(bucket)
            or ".." in bucket
            or ".-" in bucket
            or "-." in bucket
        ):
            raise ObjectStorageError("S3_BUCKET is invalid")

        prefix = self.prefix.strip().strip("/")
        if (
            not _PREFIX_PATTERN.fullmatch(prefix)
            or ".." in prefix
            or "//" in prefix
        ):
            raise ObjectStorageError("S3 prefix is invalid")
        if prefix != "trading-agent":
            raise ObjectStorageError(
                "S3 prefix must isolate objects under trading-agent"
            )

        region = self.region.strip()
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,62}", region):
            raise ObjectStorageError("S3 region is invalid")

        for label, value in (
            ("S3_ACCESS_KEY_ID", self.access_key_id),
            ("S3_SECRET_ACCESS_KEY", self.secret_access_key),
        ):
            if (
                not isinstance(value, str)
                or not value
                or len(value.encode("utf-8")) > 1024
                or "\n" in value
                or "\r" in value
            ):
                raise ObjectStorageError(f"{label} is invalid")

        object.__setattr__(self, "endpoint", endpoint)
        object.__setattr__(self, "bucket", bucket)
        object.__setattr__(self, "prefix", prefix)
        object.__setattr__(self, "region", region)

    @classmethod
    def from_environment(
        cls,
        environ: Mapping[str, str] | None = None,
    ) -> "ObjectStorageSettings":
        values = os.environ if environ is None else environ
        endpoint = read_runtime_secret(
            "S3_ENDPOINT",
            environ=values,
            required=True,
            max_bytes=2048,
        )
        access_key_id = read_runtime_secret(
            "S3_ACCESS_KEY_ID",
            environ=values,
            required=True,
            max_bytes=1024,
        )
        secret_access_key = read_runtime_secret(
            "S3_SECRET_ACCESS_KEY",
            environ=values,
            required=True,
            max_bytes=1024,
        )
        bucket = read_runtime_secret(
            "S3_BUCKET",
            environ=values,
            required=True,
            max_bytes=255,
        )
        region = read_runtime_secret(
            "S3_REGION",
            environ=values,
            default="garage",
            max_bytes=64,
        )
        return cls(
            endpoint=endpoint or "",
            access_key_id=access_key_id or "",
            secret_access_key=secret_access_key or "",
            bucket=bucket or "",
            region=region or "garage",
        )


@dataclass(frozen=True)
class ArchivedObject:
    source_kind: str
    source_id: int
    bucket: str
    object_key: str
    compressed_checksum_sha256: str
    compressed_bytes: int
    uploaded: bool


@dataclass(frozen=True)
class ArchiveCycleResult:
    status: str
    reason_code: str
    selected_count: int
    archived_count: int
    uploaded_count: int
    existing_count: int
    failed_count: int
    compressed_bytes: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "reason_code": self.reason_code,
            "selected_count": self.selected_count,
            "archived_count": self.archived_count,
            "uploaded_count": self.uploaded_count,
            "existing_count": self.existing_count,
            "failed_count": self.failed_count,
            "compressed_bytes": self.compressed_bytes,
        }


def _slug(value: Any, label: str) -> str:
    if not isinstance(value, str):
        raise ObjectStorageError(f"{label} is invalid")
    slug = _SLUG_PATTERN.sub("-", value.strip().lower()).strip("-")
    if not slug or len(slug) > 80:
        raise ObjectStorageError(f"{label} is invalid")
    return slug


def _source_identity(
    source: Mapping[str, Any],
) -> tuple[str, int, datetime, str]:
    source_kind = source.get("source_kind", "market-data")
    if source_kind not in _SOURCE_KINDS:
        raise ObjectStorageError("archive source kind is invalid")
    file_id = source.get("id")
    if (
        not isinstance(file_id, int)
        or isinstance(file_id, bool)
        or file_id <= 0
    ):
        raise ObjectStorageError("archive source id is invalid")
    report_minute = source.get("report_minute")
    if (
        not isinstance(report_minute, datetime)
        or report_minute.tzinfo is None
        or report_minute.utcoffset() is None
    ):
        raise ObjectStorageError("report_minute must be timezone-aware")
    checksum = source.get("checksum_sha256")
    if not isinstance(checksum, str) or not _CHECKSUM_PATTERN.fullmatch(
        checksum
    ):
        raise ObjectStorageError("archive source checksum is invalid")
    return source_kind, file_id, report_minute, checksum


def build_object_key(
    source: Mapping[str, Any],
    *,
    prefix: str = "trading-agent",
) -> str:
    if prefix != "trading-agent":
        raise ObjectStorageError("object prefix is not isolated")
    source_kind, file_id, report_minute, checksum = _source_identity(source)
    provider = _slug(source.get("provider"), "provider")
    data_type = _slug(source.get("data_type"), "data_type")
    observed = report_minute.astimezone(timezone.utc)
    return (
        f"{prefix}/{source_kind}/{provider}/{data_type}/"
        f"{observed:%Y/%m/%d}/{file_id}-{checksum}.gz"
    )


def _validated_payload(source: Mapping[str, Any]) -> tuple[bytes, str]:
    if source.get("content_encoding") != "gzip":
        raise ObjectStorageError("archive content encoding is invalid")
    payload = source.get("compressed_content")
    if (
        not isinstance(payload, bytes)
        or not payload
        or len(payload) > _MAX_ARCHIVE_BYTES
    ):
        raise ObjectStorageError("archive payload is invalid")
    return payload, hashlib.sha256(payload).hexdigest()


class ObjectArchiveStore:
    def __init__(self, settings: ObjectStorageSettings, *, client) -> None:
        if not isinstance(settings, ObjectStorageSettings):
            raise TypeError("settings must be ObjectStorageSettings")
        self.settings = settings
        self.client = client

    @classmethod
    def from_environment(
        cls,
        environ: Mapping[str, str] | None = None,
    ) -> "ObjectArchiveStore":
        settings = ObjectStorageSettings.from_environment(environ)
        config = Config(
            region_name=settings.region,
            signature_version="s3v4",
            retries={"max_attempts": 3, "mode": "standard"},
            connect_timeout=5,
            read_timeout=15,
            s3={"addressing_style": "path"},
            user_agent_extra="trading-agent-object-archive/1",
        )
        session = boto3.Session(
            aws_access_key_id=settings.access_key_id,
            aws_secret_access_key=settings.secret_access_key,
            region_name=settings.region,
        )
        client = session.client(
            "s3",
            endpoint_url=settings.endpoint,
            config=config,
        )
        return cls(settings, client=client)

    def _head(self, key: str) -> Mapping[str, Any] | None:
        try:
            return self.client.head_object(
                Bucket=self.settings.bucket,
                Key=key,
            )
        except ClientError as error:
            code = str(error.response.get("Error", {}).get("Code", ""))
            if code in {"404", "NoSuchKey", "NotFound"}:
                return None
            raise ObjectStorageError("object metadata request failed") from error
        except BotoCoreError as error:
            raise ObjectStorageError("object metadata request failed") from error

    @staticmethod
    def _verify_head(
        head: Mapping[str, Any],
        *,
        payload_bytes: int,
        metadata: Mapping[str, str],
    ) -> None:
        actual_metadata = {
            str(key).lower(): str(value)
            for key, value in dict(head.get("Metadata") or {}).items()
        }
        if (
            head.get("ContentLength") != payload_bytes
            or any(
                actual_metadata.get(key) != value
                for key, value in metadata.items()
            )
        ):
            raise ObjectStorageError(
                "existing object conflicts with archive evidence"
            )

    def archive_file(
        self,
        source: Mapping[str, Any],
    ) -> ArchivedObject:
        (
            source_kind,
            file_id,
            _report_minute,
            raw_checksum,
        ) = _source_identity(source)
        payload, compressed_checksum = _validated_payload(source)
        key = build_object_key(source, prefix=self.settings.prefix)
        metadata = {
            "raw-sha256": raw_checksum,
            "compressed-sha256": compressed_checksum,
            "source-kind": source_kind,
            "source-id": str(file_id),
        }
        head = self._head(key)
        uploaded = False
        if head is None:
            try:
                self.client.put_object(
                    Bucket=self.settings.bucket,
                    Key=key,
                    Body=payload,
                    ContentType="application/octet-stream",
                    ContentEncoding="gzip",
                    Metadata=metadata,
                )
            except (BotoCoreError, ClientError) as error:
                raise ObjectStorageError("object upload failed") from error
            uploaded = True
            head = self._head(key)
            if head is None:
                raise ObjectStorageError(
                    "uploaded object metadata is unavailable"
                )
        self._verify_head(
            head,
            payload_bytes=len(payload),
            metadata=metadata,
        )
        return ArchivedObject(
            source_kind=source_kind,
            source_id=file_id,
            bucket=self.settings.bucket,
            object_key=key,
            compressed_checksum_sha256=compressed_checksum,
            compressed_bytes=len(payload),
            uploaded=uploaded,
        )

    def verify_connectivity(self) -> None:
        try:
            self.client.list_objects_v2(
                Bucket=self.settings.bucket,
                Prefix=f"{self.settings.prefix}/",
                MaxKeys=1,
            )
        except (BotoCoreError, ClientError) as error:
            raise ObjectStorageError(
                "object storage connectivity check failed"
            ) from error

    def verify_round_trip(
        self,
        *,
        key_factory: Callable[[], str] = lambda: (
            f"trading-agent/probes/{uuid4().hex}"
        ),
        payload: bytes | None = None,
    ) -> str:
        """Write, read, verify, and delete one exact bounded probe object."""
        key = key_factory()
        if not isinstance(key, str) or not re.fullmatch(
            r"trading-agent/probes/[a-z0-9-]{1,80}",
            key,
        ):
            raise ObjectStorageError("probe object key is invalid")
        value = secrets.token_bytes(32) if payload is None else payload
        if not isinstance(value, bytes) or not 1 <= len(value) <= 4096:
            raise ObjectStorageError("probe payload is invalid")
        checksum = hashlib.sha256(value).hexdigest()
        metadata = {"probe-sha256": checksum}
        uploaded = False
        try:
            self.client.put_object(
                Bucket=self.settings.bucket,
                Key=key,
                Body=value,
                ContentType="application/octet-stream",
                Metadata=metadata,
            )
            uploaded = True
            head = self._head(key)
            if head is None:
                raise ObjectStorageError("probe object is unavailable")
            self._verify_head(
                head,
                payload_bytes=len(value),
                metadata=metadata,
            )
            response = self.client.get_object(
                Bucket=self.settings.bucket,
                Key=key,
            )
            body = response.get("Body")
            if body is None or not hasattr(body, "read"):
                raise ObjectStorageError("probe response body is invalid")
            try:
                downloaded = body.read(4097)
            finally:
                close = getattr(body, "close", None)
                if callable(close):
                    close()
            if (
                downloaded != value
                or hashlib.sha256(downloaded).hexdigest() != checksum
            ):
                raise ObjectStorageError("probe content verification failed")
        except ObjectStorageError:
            if uploaded:
                try:
                    self.client.delete_object(
                        Bucket=self.settings.bucket,
                        Key=key,
                    )
                except (BotoCoreError, ClientError):
                    pass
            raise
        except (BotoCoreError, ClientError) as error:
            if uploaded:
                try:
                    self.client.delete_object(
                        Bucket=self.settings.bucket,
                        Key=key,
                    )
                except (BotoCoreError, ClientError):
                    pass
            raise ObjectStorageError(
                "object storage round-trip probe failed"
            ) from error

        try:
            self.client.delete_object(
                Bucket=self.settings.bucket,
                Key=key,
            )
        except (BotoCoreError, ClientError) as error:
            raise ObjectStorageError("probe cleanup failed") from error
        if self._head(key) is not None:
            raise ObjectStorageError("probe cleanup was not verified")
        return key


def run_archive_cycle(
    database,
    store,
    *,
    evaluated_at: datetime,
    max_files: int,
    run_key_factory: Callable[[], str] = lambda: (
        f"object-archive:{uuid4().hex}"
    ),
) -> ArchiveCycleResult:
    if evaluated_at.tzinfo is None or evaluated_at.utcoffset() is None:
        raise ValueError("evaluated_at must be timezone-aware")
    if not isinstance(max_files, int) or not 1 <= max_files <= 100:
        raise ValueError("max_files must be between 1 and 100")

    sources = database.get_pending_object_archive_inputs(limit=max_files)
    archived: list[ArchivedObject] = []
    failed_count = 0
    for source in sources:
        try:
            archived.append(store.archive_file(source))
        except ObjectStorageError:
            failed_count += 1
            logger.warning(
                "object_archive_item_failed source_kind=%s source_id=%s",
                (
                    source.get("source_kind")
                    if isinstance(source, Mapping)
                    else None
                ),
                source.get("id") if isinstance(source, Mapping) else None,
            )

    if not sources:
        status = "SKIPPED"
        reason_code = "NO_PENDING"
    elif failed_count == 0:
        status = "SUCCEEDED"
        reason_code = "ARCHIVED"
    elif archived:
        status = "PARTIAL"
        reason_code = "OBJECT_STORAGE_ERROR"
    else:
        status = "FAILED"
        reason_code = "OBJECT_STORAGE_ERROR"

    objects = [
        {
            "source_kind": item.source_kind,
            "source_id": item.source_id,
            "bucket": item.bucket,
            "object_key": item.object_key,
            "compressed_checksum_sha256": (
                item.compressed_checksum_sha256
            ),
            "compressed_bytes": item.compressed_bytes,
            "uploaded": item.uploaded,
        }
        for item in archived
    ]
    database.record_object_archive_run(
        run_key=run_key_factory(),
        evaluated_at=evaluated_at,
        status=status,
        reason_code=reason_code,
        selected_count=len(sources),
        archived_count=len(archived),
        uploaded_count=sum(item.uploaded for item in archived),
        existing_count=sum(not item.uploaded for item in archived),
        failed_count=failed_count,
        compressed_bytes=sum(item.compressed_bytes for item in archived),
        objects=objects,
    )
    return ArchiveCycleResult(
        status=status,
        reason_code=reason_code,
        selected_count=len(sources),
        archived_count=len(archived),
        uploaded_count=sum(item.uploaded for item in archived),
        existing_count=sum(not item.uploaded for item in archived),
        failed_count=failed_count,
        compressed_bytes=sum(item.compressed_bytes for item in archived),
    )


def _bounded_integer(
    environment: Mapping[str, str],
    name: str,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    value = environment.get(name, str(default))
    try:
        parsed = int(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{name} must be an integer") from error
    if not minimum <= parsed <= maximum:
        raise ValueError(
            f"{name} must be between {minimum} and {maximum}"
        )
    return parsed


def main(
    argv: list[str] | None = None,
    *,
    database_factory=None,
    store_factory=ObjectArchiveStore.from_environment,
    now_factory=lambda: datetime.now(timezone.utc),
    sleeper=time.sleep,
    environ=None,
) -> int:
    parser = argparse.ArgumentParser(
        description="Archive validated market data in object storage.",
    )
    parser.add_argument(
        "mode",
        choices=("once", "daemon", "health", "probe"),
        nargs="?",
        default="once",
    )
    arguments = parser.parse_args(argv)
    environment = os.environ if environ is None else environ
    if database_factory is None:
        from src.data.database import Database

        database_factory = Database

    store = store_factory(environment)
    if arguments.mode == "probe":
        store.verify_round_trip()
        print(json.dumps(
            {"status": "SUCCEEDED", "event": "object_storage_probe"},
            ensure_ascii=False,
        ))
        return 0
    if arguments.mode == "health":
        database_factory()
        store.verify_connectivity()
        return 0

    database = database_factory()
    max_files = _bounded_integer(
        environment,
        "OBJECT_ARCHIVE_MAX_FILES_PER_CYCLE",
        10,
        1,
        100,
    )
    if arguments.mode == "once":
        result = run_archive_cycle(
            database,
            store,
            evaluated_at=now_factory(),
            max_files=max_files,
        )
        print(json.dumps(result.to_dict(), ensure_ascii=False))
        return 0 if result.status in {"SUCCEEDED", "SKIPPED"} else 1

    interval = _bounded_integer(
        environment,
        "OBJECT_ARCHIVE_INTERVAL_SECONDS",
        300,
        60,
        3600,
    )
    while True:
        result = run_archive_cycle(
            database,
            store,
            evaluated_at=now_factory(),
            max_files=max_files,
        )
        logger.info(
            "object_archive_cycle status=%s selected=%d archived=%d "
            "uploaded=%d existing=%d failed=%d bytes=%d",
            result.status,
            result.selected_count,
            result.archived_count,
            result.uploaded_count,
            result.existing_count,
            result.failed_count,
            result.compressed_bytes,
        )
        sleeper(interval)


if __name__ == "__main__":
    raise SystemExit(main())
