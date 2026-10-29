"""Step 8: check every sentence of the answer against the evidence it cites. This is the hallucination check.

A small model will sometimes write a fluent sentence the evidence does not say. Each sentence gets four checks:
  words     the share of its content words that appear in the cited evidence
  meaning   cosine similarity between the sentence and the closest cited evidence (the same MiniLM model as the index)
  numbers   every number in the sentence must appear in the cited evidence (within rounding)
  names     every catalog entity (brand, drug, indication, country) in the sentence must appear in the cited evidence

  score = 0.5 * words + 0.5 * meaning
  supported (>= SUPPORT_OK)  kept          weak (>= SUPPORT_WEAK)  kept, flagged        unsupported  removed from the answer
  Two hard rules override the score: a number that is not in the cited evidence, or a catalog name (other than one the
  question itself asked about) that is not in the cited evidence, makes the sentence unsupported. A fluent sentence with an
  invented figure or an invented country is exactly what this check exists to stop.

A sentence with no citation is not trusted: we look for the evidence that supports it best and attach it only if that
evidence passes as "supported"; otherwise the sentence is removed. Removed sentences are reported, never silently dropped.

Limits, stated plainly: this is a lexical and embedding heuristic, not a proof. It catches invented numbers, invented names
and off-topic sentences well; it can miss a sentence that reuses the right words with the wrong relation (for example
swapping which brand is higher). The thresholds were set from a few runs on the synthetic corpus and are not tuned.
"""
import re

import numpy as np

from layer1 import audit_log
from layer2 import embedder, entity_extractor as ext

from . import config

TAG = re.compile(r"\[\s*(E\d+)\s*\]")
NUMBER = re.compile(r"(?<![\w.])\d+(?:\.\d+)?%?")
STOP = set("the a an of in on for to and or is are was were be been being what which who how with by from at as it its this that than then "
           "there their about into over under between versus has have had not no can may also more most less per among across".split())


def _stem(w: str) -> str:
    return w[:-1] if len(w) > 4 and w.endswith("s") else w


def content_words(text: str) -> set:
    return {_stem(w) for w in re.findall(r"[a-z0-9]+", text.lower()) if len(w) > 2 and w not in STOP}


def _numbers(text: str) -> set:
    out = set()
    for m in NUMBER.findall(text):
        try:
            out.add(float(m.rstrip("%")))
        except ValueError:
            pass
    return out


def _numbers_ok(sentence: str, evidence_text: str) -> bool:
    have = _numbers(evidence_text)
    for n in _numbers(sentence):
        if not any(abs(n - h) <= max(0.006, abs(h) * 0.01) for h in have):
            return False
    return True


def _names_ok(sentence: str, evidence_text: str, exempt: set) -> tuple:
    low, missing = evidence_text.lower(), []
    try:
        mentions = ext.extract(sentence, "answer")
    except Exception:
        mentions = []
    index = ext.term_index()
    for m in mentions:
        label = index.entities.get(m["entity_id"], (None, m["text"]))[1] if m["entity_id"] else m["text"]
        if m["entity_id"] and m["entity_id"] not in exempt and label.lower() not in low and m["text"].lower() not in low:
            missing.append(label)
    return not missing, sorted(set(missing))


def _unit(v: np.ndarray) -> np.ndarray:
    n = np.linalg.norm(v, axis=-1, keepdims=True)
    return v / np.where(n == 0, 1, n)


def split_sentences(text: str) -> list:
    """Sentences with their [E#] tags attached. A fragment that is only a tag belongs to the sentence before it."""
    text = re.sub(r"\s+", " ", text.replace("\n", " \n ")).strip()
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z\[])|(?<=\])\s+(?=[A-Z])", text)
    out = []
    for p in (x.strip() for x in parts):
        if not p:
            continue
        if out and not TAG.sub("", p).strip(" .,;"):
            out[-1] += " " + p
        else:
            out.append(p)
    return out


def verify(sentences: list, evidence: list, question_entities: list | None = None) -> dict:
    """sentences: list of raw sentence strings (tags included). evidence: items from evidence.gather().
    question_entities: entities the question named; a sentence may repeat them even if the cited passage does not."""
    exempt = {e["entity_id"] for e in question_entities or []}
    by_id = {e["id"]: e for e in evidence}
    ev_vecs = _unit(embedder.embed([e["text"] for e in evidence])) if evidence else np.zeros((0, 1))
    pos = {e["id"]: i for i, e in enumerate(evidence)}
    results = []
    for raw in sentences:
        tags = [t for t in TAG.findall(raw)]
        valid = [t for t in dict.fromkeys(tags) if t in by_id]
        invalid = [t for t in dict.fromkeys(tags) if t not in by_id]
        body = TAG.sub("", raw).strip()
        body = re.sub(r"\s+([.,;:])", r"\1", body).strip()
        if len(body) < 3 or not content_words(body):
            continue
        svec = _unit(embedder.embed([body]))[0]
        attributed = False
        if not valid and evidence:                           # uncited: find the best home for it, or drop it
            sims = ev_vecs @ svec
            best = int(np.argmax(sims))
            valid, attributed = [evidence[best]["id"]], True
        text_of_cited = " ".join(by_id[t]["text"] for t in valid)
        words = len(content_words(body) & content_words(text_of_cited)) / max(1, len(content_words(body)))
        meaning = max([float(ev_vecs[pos[t]] @ svec) for t in valid] or [0.0])
        nums_ok = _numbers_ok(body, text_of_cited)
        names_ok, missing = _names_ok(body, text_of_cited, exempt)
        score = 0.5 * words + 0.5 * max(meaning, 0.0)
        verdict = "supported" if score >= config.SUPPORT_OK else "weak" if score >= config.SUPPORT_WEAK else "unsupported"
        if not nums_ok or not names_ok:
            verdict = "unsupported"
        if attributed and verdict != "supported":
            verdict = "unsupported"                          # an uncited sentence is only kept if its best source clearly backs it
        results.append({"text": body, "cites": valid, "invalid_tags": invalid, "attributed": attributed, "verdict": verdict,
                        "score": round(score, 3),
                        "checks": {"words": round(words, 2), "meaning": round(meaning, 2), "numbers_in_evidence": nums_ok,
                                   "names_in_evidence": names_ok, "names_missing": missing}})
    counts = {v: sum(1 for r in results if r["verdict"] == v) for v in ("supported", "weak", "unsupported")}
    risk = "high" if counts["unsupported"] and not counts["supported"] else "medium" if counts["unsupported"] or counts["weak"] else "low"
    return {"sentences": results, "counts": counts, "risk": risk}


def log_risk(user: dict, question: str, check: dict) -> None:
    if check["counts"]["unsupported"] or check["counts"]["weak"]:
        audit_log.log_event("HALLUCINATION_RISK", None, {
            "user_id": user["user_id"], "role": user["role"], "question": question, "risk": check["risk"], **check["counts"],
            "removed": [s["text"][:160] for s in check["sentences"] if s["verdict"] == "unsupported"]})
