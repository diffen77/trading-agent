#!/bin/sh
set -eu

base_url=${1:-http://127.0.0.1:3020}
: "${DASHBOARD_AUTH_USERNAME:?DASHBOARD_AUTH_USERNAME must be set}"
: "${DASHBOARD_AUTH_PASSWORD:?DASHBOARD_AUTH_PASSWORD must be set}"

python3 - "$base_url" <<'PY'
import base64
import json
import os
import sys
from urllib.error import HTTPError
from urllib.parse import urlparse
from urllib.request import Request, urlopen


base_url = sys.argv[1].rstrip("/")
parsed = urlparse(base_url)
if (
    parsed.scheme not in {"http", "https"}
    or not parsed.netloc
    or parsed.username is not None
    or parsed.password is not None
):
    raise SystemExit("invalid dashboard URL")


def request(path, *, authorization=None):
    headers = {}
    if authorization is not None:
        headers["Authorization"] = authorization
    try:
        with urlopen(
            Request(f"{base_url}{path}", headers=headers),
            timeout=10,
        ) as response:
            return response.status, response.read(1_000_000)
    except HTTPError as error:
        return error.code, error.read(1_000_000)


health_status, health_body = request("/api/health")
if health_status != 200:
    raise SystemExit(f"health failed with HTTP {health_status}")
try:
    health = json.loads(health_body)
except json.JSONDecodeError as error:
    raise SystemExit("health returned invalid JSON") from error
if health.get("status") != "ok":
    raise SystemExit("health did not report ok")

private_status, _ = request("/api/portfolio")
if private_status != 401:
    raise SystemExit(
        f"unauthenticated API returned HTTP {private_status}, expected 401"
    )

credentials = (
    f"{os.environ['DASHBOARD_AUTH_USERNAME']}:"
    f"{os.environ['DASHBOARD_AUTH_PASSWORD']}"
).encode("utf-8")
authorization = "Basic " + base64.b64encode(credentials).decode("ascii")
authorized_status, authorized_body = request(
    "/api/portfolio",
    authorization=authorization,
)
if authorized_status != 200:
    raise SystemExit(
        f"authenticated API returned HTTP {authorized_status}"
    )
try:
    json.loads(authorized_body)
except json.JSONDecodeError as error:
    raise SystemExit("authenticated API returned invalid JSON") from error

print("dashboard smoke passed: health=200 unauthenticated=401 authenticated=200")
PY
