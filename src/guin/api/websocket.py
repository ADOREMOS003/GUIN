"""WebSocket log streaming and in-memory run state."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class WorkflowRun:
    """Lightweight run record used by the web UI and API."""

    run_id: str
    instruction: str
    bids_dir: str
    output_dir: str
    model: str
    dry_run: bool
    status: str
    created_at: str = field(default_factory=_now_iso)
    updated_at: str = field(default_factory=_now_iso)
    generated_code: str = ""
    provenance_file: str | None = None
    logs: list[str] = field(default_factory=list)


class LogHub:
    """In-memory pub/sub for run logs."""

    def __init__(self) -> None:
        self._runs: dict[str, WorkflowRun] = {}
        self._subscribers: dict[str, set[WebSocket]] = {}

    def new_run(
        self,
        *,
        instruction: str,
        bids_dir: str,
        output_dir: str,
        model: str,
        dry_run: bool,
        generated_code: str,
    ) -> WorkflowRun:
        run_id = uuid4().hex[:12]
        run = WorkflowRun(
            run_id=run_id,
            instruction=instruction,
            bids_dir=bids_dir,
            output_dir=output_dir,
            model=model,
            dry_run=dry_run,
            status="running" if not dry_run else "success",
            generated_code=generated_code,
        )
        if dry_run:
            run.logs.append("Dry run: generated code only.")
        self._runs[run_id] = run
        return run

    def list_runs(self, limit: int = 20) -> list[dict[str, Any]]:
        runs = sorted(self._runs.values(), key=lambda r: r.updated_at, reverse=True)
        return [self._run_to_dict(r) for r in runs[:limit]]

    def get_run(self, run_id: str) -> WorkflowRun | None:
        return self._runs.get(run_id)

    def append_log(self, run_id: str, line: str) -> None:
        run = self._runs.get(run_id)
        if run is None:
            return
        run.logs.append(line)
        run.updated_at = _now_iso()

    def mark_done(self, run_id: str, *, success: bool, provenance_file: str | None) -> None:
        run = self._runs.get(run_id)
        if run is None:
            return
        run.status = "success" if success else "failed"
        run.provenance_file = provenance_file
        run.updated_at = _now_iso()

    async def connect(self, run_id: str, websocket: WebSocket) -> None:
        await websocket.accept()
        self._subscribers.setdefault(run_id, set()).add(websocket)
        run = self._runs.get(run_id)
        if run:
            await websocket.send_json(
                {
                    "status": "ok",
                    "data": {
                        "type": "snapshot",
                        "run_id": run_id,
                        "logs": run.logs,
                        "state": self._run_to_dict(run),
                    },
                }
            )

    def disconnect(self, run_id: str, websocket: WebSocket) -> None:
        peers = self._subscribers.get(run_id)
        if not peers:
            return
        peers.discard(websocket)
        if not peers:
            self._subscribers.pop(run_id, None)

    async def publish(self, run_id: str, payload: dict[str, Any]) -> None:
        peers = list(self._subscribers.get(run_id, set()))
        for ws in peers:
            await ws.send_json(payload)

    @staticmethod
    def _run_to_dict(run: WorkflowRun) -> dict[str, Any]:
        return {
            "run_id": run.run_id,
            "instruction": run.instruction,
            "bids_dir": run.bids_dir,
            "output_dir": run.output_dir,
            "model": run.model,
            "dry_run": run.dry_run,
            "status": run.status,
            "created_at": run.created_at,
            "updated_at": run.updated_at,
            "generated_code": run.generated_code,
            "provenance_file": run.provenance_file,
        }


HUB = LogHub()

router = APIRouter()


def ok(data: object) -> dict[str, object]:
    return {"status": "ok", "data": data}


@router.websocket("/api/v1/ws/logs")
async def ws_logs(websocket: WebSocket, run_id: str = Query(...)) -> None:
    await HUB.connect(run_id, websocket)
    try:
        while True:
            msg = await websocket.receive_text()
            if msg.strip().lower() in {"ping", "keepalive"}:
                await websocket.send_json(ok({"type": "pong", "run_id": run_id}))
    except WebSocketDisconnect:
        HUB.disconnect(run_id, websocket)
