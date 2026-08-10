"""Validated configuration for external model gateways."""

from __future__ import annotations

from ipaddress import ip_address, ip_network
from urllib.parse import urlparse


HERMES_MODELS = frozenset(
    {
        "gpt-5.6-sol",
        "gpt-5.6-terra",
        "gpt-5.6-luna",
    }
)
HERMES_PROVIDERS = frozenset({"openai-codex"})
REASONING_EFFORTS = frozenset(
    {"none", "low", "medium", "high", "xhigh", "max"}
)
_TAILSCALE_NETWORK = ip_network("100.64.0.0/10")


def validate_hermes_url(value: str) -> str:
    """Return a normalized loopback or private-Tailscale Hermes base URL."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError("HERMES_URL is required")
    normalized = value.strip().rstrip("/")
    parsed = urlparse(normalized)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise ValueError(
            "HERMES_URL must be an absolute base URL without credentials"
        )
    hostname = (parsed.hostname or "").lower()
    if hostname == "localhost":
        return normalized
    try:
        address = ip_address(hostname)
    except ValueError:
        if parsed.scheme == "https" and hostname.endswith(".ts.net"):
            return normalized
        raise ValueError(
            "HERMES_URL must use loopback, a Tailscale IP, or HTTPS "
            "on a ts.net host"
        )
    if address.is_loopback or address in _TAILSCALE_NETWORK:
        return normalized
    raise ValueError(
        "HERMES_URL must use loopback, a Tailscale IP, or HTTPS "
        "on a ts.net host"
    )


def validate_hermes_model(value: str) -> str:
    if value not in HERMES_MODELS:
        raise ValueError("LLM_MODEL is not an approved Hermes GPT-5.6 model")
    return value


def validate_hermes_provider(value: str) -> str:
    if value not in HERMES_PROVIDERS:
        raise ValueError("HERMES_PROVIDER is not approved")
    return value


def validate_reasoning_effort(value: str) -> str:
    if value not in REASONING_EFFORTS:
        raise ValueError("LLM_REASONING_EFFORT is not supported")
    return value
