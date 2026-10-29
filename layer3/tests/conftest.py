"""Layer 3 test fixtures. They reuse Layer 2's `world` (empty Layer 1 and Layer 2 stores per test, inline enrichment) and
`add_doc`, and add Layer 3's own temporary stores plus a scripted stand-in for the language model.

The stand-in replaces `layer3.llm.chat` only. Everything else runs for real: the access slice, entity extraction, the
plan checker, rdflib, the vector index, the verification. So these tests prove the plumbing and the safeguards; they say
nothing about how good Qwen is. That was measured separately on the real model (see design_decisions.md).
"""
import json

import pytest

from layer2.tests.conftest import add_doc, catalog_sandbox, corpus, world            # noqa: F401  (fixtures)
from layer3 import config, llm


@pytest.fixture()
def l3_stores(world):                                                                 # noqa: F811
    old = config._stores["dir"]
    config.set_stores_dir(world / "layer3_stores")
    yield
    config.set_stores_dir(old)


class ScriptedLLM:
    """Replies are looked up by a marker found in the user prompt, so a test controls each step independently."""

    def __init__(self):
        self.plans, self.answer, self.calls, self.down = [], "", [], False

    def chat(self, system, user, temperature=0.0, max_tokens=400, seed=7, schema=None):
        self.calls.append({"schema": schema is not None, "user": user, "seed": seed})
        if self.down:
            raise llm.LLMUnavailable("The language model server is not reachable (test)")
        if schema is not None:
            plan = self.plans.pop(0) if len(self.plans) > 1 else self.plans[0]
            text = plan if isinstance(plan, str) else json.dumps(plan)
        else:
            text = self.answer(user) if callable(self.answer) else self.answer
        return {"text": text, "model": "scripted", "prompt_tokens": 1, "output_tokens": 1, "seconds": 0.0}


@pytest.fixture()
def fake_llm(monkeypatch, l3_stores):
    f = ScriptedLLM()
    monkeypatch.setattr(llm, "chat", f.chat)
    monkeypatch.setattr(llm, "status", lambda: {"available": True, "model": "scripted", "message": "ready"})
    return f


def plan(select, edges, filters=None, order_by="", descending=False, limit=0):
    return {"edges": edges, "select": select, "filters": filters or [], "order_by": order_by, "descending": descending, "limit": limit}


def col(var, agg="none", of=""):
    return {"var": var, "agg": agg, "of": of}


def pat(s, p, o):
    return {"s": s, "p": p, "o": o}


def quote_evidence(user_prompt: str) -> str:
    """A faithful model: it repeats the first two sentences of the first evidence item, with its tag."""
    import re
    m = re.search(r"\[(E\d+)\] (.+)", user_prompt)
    sents = re.split(r"(?<=[.!?])\s+", m.group(2).strip())
    tag = m.group(1)
    return f"SHORT: {sents[0]} [{tag}]\n" + "\n".join(f"{x} [{tag}]" for x in sents[:2])
