from __future__ import annotations

import asyncio
import json
import os
import sys
import time
import traceback
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

PROJECT_ROOT = Path(os.getenv("NEXUS_PROJECT_ROOT", "/app")).resolve()
CORE_DIR = PROJECT_ROOT / "core-agent"
BRIDGE_DIR = PROJECT_ROOT / "browser-bridge"
for path in (CORE_DIR, BRIDGE_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

load_dotenv(PROJECT_ROOT / ".env")

from filesystem_tools import MacFilesystemMesh, SafePathError
from master_agent import NexusLLMRouter
from self_evolution import SelfEvolutionDaemon
from skyvern_client import BrowserTaskRequest, SkyvernClient


APP_STARTED_AT = time.time()
LOG_DIR = Path(os.getenv("NEXUS_LOG_DIR", "/app/runtime/logs"))
LOG_DIR.mkdir(parents=True, exist_ok=True)
GATEWAY_LOG = LOG_DIR / "gateway.log"

app = FastAPI(title="NexusOS-Ultra Gateway", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

mesh = MacFilesystemMesh()
router = NexusLLMRouter()
skyvern = SkyvernClient(mesh=mesh)
evolver = SelfEvolutionDaemon(router=router)


class AgentQuery(BaseModel):
    prompt: str = Field(min_length=1, max_length=250000)
    task_type: str = "general"
    requires_json: bool = False


class FileReadRequest(BaseModel):
    root: str
    path: str


class FileWriteRequest(BaseModel):
    root: str
    path: str
    content: str
    overwrite: bool = True


class BrowserDispatchRequest(BaseModel):
    url: str
    goal: str
    data_extraction_goal: str = ""
    max_steps: int = 25
    wait: bool = True


class EvolutionRunRequest(BaseModel):
    error_context: str = ""


def log_event(event: str, payload: Dict[str, Any]) -> None:
    record = {"timestamp": time.time(), "event": event, "payload": payload}
    with GATEWAY_LOG.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def public_metrics() -> Dict[str, Any]:
    usage = router.usage_snapshot()
    prompt_tokens = sum(item.get("prompt_tokens_estimate", 0) for item in usage.values())
    completion_tokens = sum(item.get("completion_tokens_estimate", 0) for item in usage.values())
    requests = sum(item.get("requests", 0) for item in usage.values())
    failures = sum(item.get("failures", 0) for item in usage.values())
    return {
        "uptime_seconds": round(time.time() - APP_STARTED_AT, 2),
        "llm_usage": usage,
        "token_totals": {
            "prompt_tokens_estimate": prompt_tokens,
            "completion_tokens_estimate": completion_tokens,
            "requests": requests,
            "failures": failures,
        },
        "filesystem": mesh.root_status(),
        "skyvern": skyvern.health(),
        "self_evolution": {
            "enabled": evolver.enabled,
            "patch_dir": str(evolver.patch_dir),
            "allowed_roots": [str(path) for path in evolver.allowed_roots],
        },
    }


@app.get("/health")
async def health() -> Dict[str, Any]:
    return {
        "ok": True,
        "service": "nexusos-ultra-gateway",
        "started_at": APP_STARTED_AT,
        "metrics": public_metrics(),
    }


@app.get("/api/metrics")
async def metrics() -> Dict[str, Any]:
    return public_metrics()


@app.get("/api/files/tree")
async def file_tree(root: str = "local", path: str = "", depth: int = 2) -> Dict[str, Any]:
    try:
        node = mesh.list_tree(root, path, depth=depth)
        log_event("file_tree", {"root": root, "path": path, "depth": depth})
        return node.to_dict()
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/files/read")
async def file_read(request: FileReadRequest) -> Dict[str, Any]:
    try:
        result = mesh.read_file(request.root, request.path)
        log_event("file_read", {"root": request.root, "path": request.path, "bytes": result.get("bytes", 0)})
        return result
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/files/write")
async def file_write(request: FileWriteRequest) -> Dict[str, Any]:
    try:
        result = mesh.write_file(request.root, request.path, request.content, overwrite=request.overwrite)
        log_event("file_write", {"root": request.root, "path": request.path, "bytes": result.get("bytes", 0)})
        return result
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/agent/query")
async def agent_query(request: AgentQuery) -> Dict[str, Any]:
    response = router.ask(request.prompt, task_type=request.task_type, requires_json=request.requires_json)
    log_event("agent_query", {"route": asdict(response.route), "degraded": response.degraded})
    return {
        "text": response.text,
        "route": asdict(response.route),
        "usage": response.usage,
        "degraded": response.degraded,
    }


@app.post("/api/browser/task")
async def browser_task(request: BrowserDispatchRequest) -> Dict[str, Any]:
    try:
        task_request = BrowserTaskRequest(
            url=request.url,
            goal=request.goal,
            data_extraction_goal=request.data_extraction_goal,
            max_steps=request.max_steps,
        )
        result = skyvern.dispatch(task_request, wait=request.wait)
        log_event("browser_task", {"url": request.url, "status": result.status, "configured": result.configured})
        return asdict(result)
    except Exception as exc:
        log_event("browser_task_error", {"url": request.url, "error": str(exc)})
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/evolution/run")
async def evolution_run(request: EvolutionRunRequest) -> Dict[str, Any]:
    try:
        event = evolver.run_once(request.error_context)
        log_event("evolution_run", {"status": event.status, "file_path": event.file_path})
        return asdict(event)
    except Exception as exc:
        err = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        log_event("evolution_error", {"error": err[-4000:]})
        raise HTTPException(status_code=400, detail=str(exc))


@app.get("/api/evolution/events")
async def evolution_events(limit: int = 20) -> Dict[str, Any]:
    events: List[Dict[str, Any]] = []
    for path in sorted(evolver.patch_dir.glob("patch-event-*.json"), reverse=True)[: max(1, min(limit, 100))]:
        try:
            events.append(json.loads(path.read_text(encoding="utf-8")))
        except Exception:
            continue
    return {"events": events}


@app.get("/api/logs")
async def logs(limit: int = 100) -> Dict[str, Any]:
    if not GATEWAY_LOG.exists():
        return {"lines": []}
    lines = GATEWAY_LOG.read_text(errors="replace").splitlines()[-max(1, min(limit, 500)):]
    return {"lines": lines}


@app.websocket("/ws/events")
async def ws_events(websocket: WebSocket) -> None:
    await websocket.accept()
    try:
        while True:
            payload = {
                "type": "metrics",
                "timestamp": time.time(),
                "metrics": public_metrics(),
            }
            await websocket.send_json(payload)
            await asyncio.sleep(2.5)
    except WebSocketDisconnect:
        return
