"""Step 7: the local LLM writes the answer from the evidence only. Step 8 (guard.py) then checks it.

The model is told to put one statement per line and end each with the evidence tags it relied on ([E2], [E1][E4]).
It is also asked for a one-line answer first. If the model cannot be reached we build an extractive answer from the
evidence itself (the best-matching sentences, quoted), and say so; we never show a canned answer.
"""
import re

import numpy as np

from layer2 import embedder

from . import guard, llm

SYSTEM = (
    "You answer questions about pharma market research using ONLY the numbered evidence the user gives you. "
    "Write plain English. Put each statement on its own line and end every line with the tag of the evidence it comes from, "
    "like [E2] or [E1][E3]. Never state a number, name or fact that is not in the evidence. "
    "Begin with one line that starts with SHORT: and gives the answer in one sentence, with its tag. "
    "When several evidence lines answer the question (for example one line per brand), write one statement for each of them. "
    "Only if no evidence line is relevant, write only: The evidence does not contain an answer."
)
NO_ANSWER = re.compile(r"does not contain an answer|cannot answer|no answer", re.I)


def build_prompt(question: str, evidence: list) -> str:
    ev = "\n".join(f"[{e['id']}] {e['text']}" for e in evidence)
    return f"Evidence:\n{ev}\n\nQuestion: {question}\n\nAnswer using only the evidence. One statement per line, each ending with its tag."


def parse(text: str) -> dict:
    short, lines = None, []
    for ln in (x.strip() for x in text.replace("\r", "").split("\n")):
        ln = re.sub(r"^(?:[-*•]+\s*|\d+[.)]\s+)+(?=[A-Z\[])", "", ln)               # list markers only: a leading '15' or '2023' is part of the sentence
        if not ln:
            continue
        m = re.match(r"^\**SHORT\**\s*[:\-]\s*(.*)$", ln, re.I)
        if m:
            short = m.group(1).strip()
        else:
            lines.append(ln)
    sentences = [s for ln in lines for s in guard.split_sentences(ln)]
    return {"short": short, "sentences": sentences, "refused": bool(NO_ANSWER.search(text)) and len(sentences) <= 1}


def _states(parsed: dict, number: str) -> bool:
    """The reply gives the number inside a statement that has words of its own. A bare '15 [E1]' does not count: the checker
    compares a statement with its cited evidence by its words, so it drops a number alone and nothing would be left to show."""
    bodies = (guard.TAG.sub("", s) for s in [parsed["short"] or "", *parsed["sentences"]])
    return any(re.search(rf"(?<![\d.]){number}(?![\d.])", b) and guard.content_words(b) for b in bodies)


def generate(question: str, evidence: list) -> dict:
    """{mode: llm|extractive, raw, short, sentences, refused, model info}."""
    if not evidence:
        return {"mode": "none", "raw": "", "short": None, "sentences": [], "refused": True, "note": "No evidence was found."}
    prompt, retried, fallback = build_prompt(question, evidence), False, None
    counts = [(e["id"], re.sub(r"\.0+$", "", str(e["row"]["count"])), e["text"]) for e in evidence
              if e["kind"] == "graph" and isinstance(e.get("row"), dict) and list(e["row"]) == ["count"]]       # a database count for this question
    try:
        out = llm.chat(SYSTEM, prompt, max_tokens=420)
        parsed = parse(out["text"])
        graph_ids = [e["id"] for e in evidence if e["kind"] == "graph"]
        if graph_ids and (parsed["refused"] or NO_ANSWER.search(parsed["short"] or "")):
            # The model says there is no answer although a database query returned rows for this very question. Say so and ask again, once.
            retried = True
            nudge = (f"\n\nNote: evidence lines {graph_ids[0]} to {graph_ids[-1]} are the result of a database query for this question. "
                     + ("It is the database's count for exactly this question: state its number, ending with its tag." if counts
                        else "Turn each of them into one sentence that says what it measures, ending with its tag."))
            out = llm.chat(SYSTEM, prompt + nudge, max_tokens=420)
            parsed = parse(out["text"])
        if counts and not any(_states(parsed, n) for _, n, _ in counts):
            # a count whose number the model did not state in a sentence (it said "no answer", described the line, or wrote only "15 [E1]"): quote the evidence line itself
            cid, _, text = counts[0]
            parsed = parse(f"SHORT: {text} [{cid}]\n{text} [{cid}]")
            fallback = "The model did not state the number in a sentence, so the evidence line is quoted as the answer."
    except llm.LLMUnavailable as e:
        return {**extractive(question, evidence), "note": f"LLM unavailable: {e}"}
    return {"mode": "llm", "raw": out["text"], "model": out["model"], "seconds": out["seconds"], "retried": retried,
            "prompt_tokens": out["prompt_tokens"], "output_tokens": out["output_tokens"], **parsed, **({"note": fallback} if fallback else {})}


def extractive(question: str, evidence: list, n: int = 3) -> dict:
    """No model: quote the evidence sentences closest in meaning to the question, each with its tag."""
    cands = []
    for e in evidence:
        for s in (x.strip() for x in re.split(r"(?<=[.!?;])\s+", e["text"]) if x.strip()):
            cands.append((s, e["id"]))
    if not cands:
        return {"mode": "extractive", "raw": "", "short": None, "sentences": [], "refused": True}
    vecs = embedder.embed([question] + [c[0] for c in cands])
    vecs = vecs / np.where(np.linalg.norm(vecs, axis=1, keepdims=True) == 0, 1, np.linalg.norm(vecs, axis=1, keepdims=True))
    order = np.argsort(-(vecs[1:] @ vecs[0]))[:n]
    picked = [f"{cands[i][0].rstrip('.')}. [{cands[i][1]}]" for i in order]
    return {"mode": "extractive", "raw": "\n".join(picked), "short": picked[0] if picked else None, "sentences": picked, "refused": False}
