"""Layer 3 HTTP API, mounted by layer1/api/main.py under /api/l3 (same sign-in and ABAC as Layers 1 and 2).

POST /ask streams newline-delimited JSON: one object per pipeline step as it finishes, then a final object holding the
answer. The browser reads it as it arrives, so the user watches the steps in order instead of waiting for one big reply.
"""
import json
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from layer1 import audit_log

from .. import history, llm, mcp_tools, pipeline

log = logging.getLogger("watson.layer3")


class AskBody(BaseModel):
    question: str
    mode: str = "semantic"


class ToolBody(BaseModel):
    arguments: dict = {}


def build_router(current_user) -> APIRouter:
    r = APIRouter(prefix="/api/l3", tags=["layer3"])

    @r.get("/status")
    def status(user=Depends(current_user)):
        return {"llm": llm.status()}

    @r.post("/ask")
    def ask(body: AskBody, user=Depends(current_user)):
        gen = pipeline.ask(user, body.question, body.mode)
        try:
            first = next(gen)                         # validation happens here, so a bad question is a 422, not a broken stream
        except ValueError as e:
            raise HTTPException(422, str(e))

        def stream():
            yield json.dumps(first, default=str) + "\n"
            try:
                for ev in gen:
                    yield json.dumps(ev, default=str) + "\n"
            except Exception as e:                    # a step failed: tell the UI, never leave it hanging
                log.exception("Layer 3 step failed")
                yield json.dumps({"step": "error", "message": f"{type(e).__name__}: {e}"}) + "\n"

        return StreamingResponse(stream(), media_type="application/x-ndjson")

    @r.get("/history")
    def runs(user=Depends(current_user)):
        return {"runs": history.recent(user)}

    @r.delete("/history/{run_id}")
    def delete_run(run_id: str, user=Depends(current_user)):
        if not history.delete(user, run_id):
            raise HTTPException(404, "No such question in your history.")      # also the answer for another user's entry
        audit_log.log_event("HISTORY_DELETED", None, {"user_id": user["user_id"], "role": user["role"], "count": 1})
        return {"deleted": 1}

    @r.delete("/history")
    def clear_history(user=Depends(current_user)):
        n = history.delete(user)
        if n:
            audit_log.log_event("HISTORY_DELETED", None, {"user_id": user["user_id"], "role": user["role"], "count": n})
        return {"deleted": n}

    @r.get("/tools")
    def tools(user=Depends(current_user)):
        return {"tools": mcp_tools.TOOLS, "note": "Stubs in MCP's tools/list shape; not a running MCP server."}

    @r.post("/tools/{name}")
    def call_tool(name: str, body: ToolBody, user=Depends(current_user)):
        try:
            return {"result": mcp_tools.call(user, name, body.arguments)}
        except KeyError as e:
            raise HTTPException(404, str(e))

    return r
