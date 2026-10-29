"""Client for the local LLM: llama.cpp's `llama-server` (OpenAI-style HTTP API on localhost). Standard library only, so Layer 3
adds no Python dependency. The server is started by hand in its own terminal (`layer3/start_llm_server.ps1`).

If the server is not running or is still loading the model, calls raise LLMUnavailable with a message that says what to do.
Nothing here ever falls back to a canned answer: the callers decide what to show when the model cannot be reached.
"""
import json
import time
import urllib.error
import urllib.request

from . import config


class LLMUnavailable(RuntimeError):
    pass


START_HINT = "Start it in a terminal with: .\\layer3\\start_llm_server.ps1"


def _call(path: str, body: dict | None = None, timeout: float = 5) -> dict:
    req = urllib.request.Request(config.LLM_URL + path, method="POST" if body is not None else "GET",
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:                      # before URLError: HTTPError is a subclass of it
        if e.code == 503:
            raise LLMUnavailable("The language model server is still loading the model. Wait a few seconds and retry.") from e
        raise LLMUnavailable(f"The language model server answered HTTP {e.code}: {e.read().decode('utf-8', 'replace')[:200]}") from e
    except urllib.error.URLError as e:
        raise LLMUnavailable(f"The language model server is not reachable at {config.LLM_URL} ({e.reason}). {START_HINT}") from e
    except TimeoutError as e:
        raise LLMUnavailable(f"The model did not answer within {timeout:.0f} s") from e
    except ConnectionError as e:                             # the server was stopped mid-request
        raise LLMUnavailable(f"The connection to the language model server was lost ({e}). {START_HINT}") from e


def status() -> dict:
    """Is the server up with the model loaded? `/health` is 200 only when it is ready to answer."""
    try:
        _call("/health")
    except LLMUnavailable as e:
        return {"available": False, "model": config.LLM_MODEL, "message": str(e)}
    return {"available": True, "model": config.LLM_MODEL, "message": "ready"}


def chat(system: str, user: str, temperature: float = 0.0, max_tokens: int = 400, seed: int = 7, schema: dict | None = None) -> dict:
    """One non-streaming chat call. Temperature 0 and a fixed seed, so the same evidence gives the same answer.
    With `schema` (a JSON Schema) the reply is decoded under that grammar: it can only be JSON of that shape, and any
    field with an enum can only take one of the listed values. The model still chooses the content."""
    body = {
        "model": config.LLM_MODEL, "stream": False,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "temperature": temperature, "max_tokens": max_tokens, "seed": seed,
    }
    if schema is not None:
        body["response_format"] = {"type": "json_schema", "json_schema": {"name": "plan", "strict": True, "schema": schema}}
    t0 = time.perf_counter()
    out = _call("/v1/chat/completions", body, timeout=config.LLM_TIMEOUT_S)
    usage = out.get("usage", {})
    msg = (out.get("choices") or [{}])[0].get("message", {}).get("content") or ""
    return {"text": msg.strip(), "model": config.LLM_MODEL, "prompt_tokens": usage.get("prompt_tokens"),
            "output_tokens": usage.get("completion_tokens"), "seconds": round(time.perf_counter() - t0, 2)}
