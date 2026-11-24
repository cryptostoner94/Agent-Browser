#!/usr/bin/env python3
"""Replay the gateway access gate: token, Host header, CORS and WebSocket."""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOKEN = "gate-token-0123456789abcdef"
LOCAL_BASE_URL = "http://localhost:8088"

results: list[str] = []


def check(name: str, condition: bool) -> None:
    results.append(f"{'ok' if condition else 'FAILED'}  {name}")
    if not condition:
        raise SystemExit(f"gateway gate check failed: {name}")


def main() -> None:
    log_dir = Path(tempfile.mkdtemp(prefix="nexus-gate-"))
    os.environ["NEXUS_PROJECT_ROOT"] = str(ROOT)
    os.environ["NEXUS_LOG_DIR"] = str(log_dir)
    os.environ["NEXUS_PATCH_DIR"] = str(log_dir / "patches")
    os.environ["NEXUS_EXPORT_DIR"] = str(log_dir / "exports")
    os.environ["NEXUS_GATEWAY_TOKEN"] = TOKEN
    os.environ["NEXUS_SELF_EVOLUTION"] = "0"
    sys.path.insert(0, str(ROOT))

    from fastapi.testclient import TestClient
    from starlette.websockets import WebSocketDisconnect

    import gateway.main as gateway

    client = TestClient(gateway.app, base_url=LOCAL_BASE_URL)
    protected = ["/api/metrics", "/api/files/tree", "/api/logs", "/api/evolution/events"]

    check("health answers without a token", client.get("/health").status_code == 200)
    for path in protected:
        check(f"{path} rejects a request with no token", client.get(path).status_code == 401)
        check(
            f"{path} rejects a wrong token",
            client.get(path, headers={"X-Gateway-Token": "wrong"}).status_code == 401,
        )
        check(
            f"{path} passes the gate with the token header",
            client.get(path, headers={"X-Gateway-Token": TOKEN}).status_code != 401,
        )
    check(
        "the metrics route answers with the token",
        client.get("/api/metrics", headers={"X-Gateway-Token": TOKEN}).status_code == 200,
    )
    check(
        "a non-ASCII token is rejected, not raised",
        gateway.token_accepted("caf\u00e9") is False,
    )
    check(
        "bearer scheme is accepted",
        client.get("/api/metrics", headers={"Authorization": f"Bearer {TOKEN}"}).status_code == 200,
    )
    check(
        "file write is rejected without a token",
        client.post("/api/files/write", json={"path": "gate-check.txt", "content": "x"}).status_code
        == 401,
    )
    check(
        "foreign Host header is rejected",
        client.get(
            "/api/metrics",
            headers={"Host": "gateway.attacker.example", "X-Gateway-Token": TOKEN},
        ).status_code
        == 400,
    )

    preflight = {"Access-Control-Request-Method": "GET", "Origin": "http://localhost:3000"}
    allowed = client.options("/api/metrics", headers=preflight)
    check(
        "allowed origin gets a CORS preflight answer",
        allowed.status_code == 200
        and allowed.headers.get("access-control-allow-origin") == "http://localhost:3000",
    )
    foreign = client.options("/api/metrics", headers={**preflight, "Origin": "http://evil.example"})
    check("foreign origin gets no CORS answer", foreign.headers.get("access-control-allow-origin") is None)
    check(
        "no response allows every origin",
        foreign.headers.get("access-control-allow-origin") != "*",
    )

    # The test client sends Host: testserver, so the checks pass the local Host explicitly.
    local_host = {"host": "localhost:8088"}

    rejected = False
    try:
        with client.websocket_connect("/ws/events", headers=dict(local_host)):
            rejected = False
    except WebSocketDisconnect:
        rejected = True
    check("websocket rejects a client with no token", rejected)

    rejected = False
    try:
        with client.websocket_connect(
            "/ws/events?token=caf%C3%A9", headers=dict(local_host)
        ):
            rejected = False
    except WebSocketDisconnect:
        rejected = True
    check("websocket rejects a non-ASCII token", rejected)

    with client.websocket_connect(f"/ws/events?token={TOKEN}", headers=dict(local_host)) as socket:
        first = socket.receive_json()
    check("websocket accepts the token in the query string", first.get("type") == "metrics")

    gateway.GATEWAY_TOKEN = ""
    check(
        "an unconfigured token closes the gateway",
        client.get("/api/metrics").status_code == 503,
    )
    gateway.GATEWAY_TOKEN = TOKEN

    print("\n".join(results))
    print(f"gateway gate checks passed: {len(results)}")


if __name__ == "__main__":
    main()
