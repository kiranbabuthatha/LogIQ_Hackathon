"""LogIQ web app: `uvicorn app:app --reload` then open http://127.0.0.1:8000"""
import json
import uuid
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

import agent
import tools

app = FastAPI(title="LogIQ")
STATIC = Path(__file__).parent / "static"
SESSIONS = {}  # session_id -> agent.Session (in memory; restarts clear it)


class Ask(BaseModel):
    role: str
    question: str
    session_id: str | None = None


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/api/meta")
def meta():
    try:
        overview = tools.corpus_overview()
        db_error = None
    except Exception as e:
        overview, db_error = None, f"{type(e).__name__}: {e}"
    return {
        "roles": {k: {"label": r["label"], "goal": r["goal"], "examples": r["examples"]} for k, r in agent.ROLES.items()},
        "tools": [{"name": n, **s} for n, (_, s) in tools.TOOLS.items()],
        "corpus": overview,
        "db_error": db_error,
        "agent_ready": agent.ready()[0],
        "agent_setup": agent.ready()[1],
        "model": agent.label(),
    }


@app.post("/api/ask")
def ask(body: Ask):
    if body.role not in agent.ROLES:
        raise HTTPException(400, f"role must be one of {list(agent.ROLES)}")
    if not body.question.strip():
        raise HTTPException(400, "question is empty")
    sid = body.session_id if body.session_id in SESSIONS and SESSIONS[body.session_id].role == body.role else None
    if sid is None:
        sid = uuid.uuid4().hex
        SESSIONS[sid] = agent.Session(body.role)

    def stream():
        yield f"data: {json.dumps({'type': 'session', 'session_id': sid})}\n\n"
        try:
            for event in agent.run(SESSIONS[sid], body.question.strip()):
                yield f"data: {json.dumps(event, default=str)}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'text': f'{type(e).__name__}: {e}'})}\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


@app.post("/api/tool/{name}")
def run_tool(name: str, args: dict):
    if name not in tools.TOOLS:
        raise HTTPException(404, f"unknown tool {name}")
    try:
        return tools.call_tool(name, {k: v for k, v in args.items() if v not in (None, "")})
    except (TypeError, ValueError) as e:
        raise HTTPException(400, str(e))
