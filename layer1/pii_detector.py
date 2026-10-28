"""PII detection (Section 4.4), run per chunk.

Calibration (documented in design_decisions.md):
  * Direct identifiers always flag: email, phone, date of birth, national ID.
  * A PERSON (spaCy) flags only when it is next to a direct identifier, or sits in
    a patient context ("patient", "Mr/Mrs/Ms", "diagnosed with") without a KOL
    attribution. A named KOL quoted publicly ("Dr. Rossi noted that ...") is NOT PII.
  * Brand, drug and company names that spaCy mistakes for people are ignored.
The detector FLAGS; it never decides. A human override is required to approve.
"""
import re

import spacy

from . import vocab

_NLP = None

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
PHONE_RES = [
    re.compile(r"(?<!\w)\+\d{1,3}[\s.-]?\(?\d{1,4}\)?(?:[\s.-]?\d{2,5}){2,4}(?!\d)"),
    re.compile(r"(?<!\d)\(?\d{3}\)?[\s.-]\d{3}[\s.-]\d{4}(?!\d)"),
]
DOB_RE = re.compile(
    r"(?:\bDOB\b|\bD\.O\.B\.?|date of birth|\bborn(?: on)?)\s*[:\-]?\s*"
    r"(\d{1,2}[/.\-]\d{1,2}[/.\-]\d{2,4}|\d{4}-\d{2}-\d{2}|\d{1,2}\s+[A-Z][a-z]+\s+\d{4})", re.I)
NATID_RES = [
    ("NATIONAL_ID", re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),                       # US SSN
    ("NATIONAL_ID", re.compile(r"\b[A-CEGHJ-PR-TW-Z][A-CEGHJ-NPR-TW-Z]\s?\d{2}\s?\d{2}\s?\d{2}\s?[A-D]\b")),  # UK NINO
]
KOL_VERBS = re.compile(r"^[^.]{0,40}?\b(said|noted|stated|explained|commented|highlighted|emphasi[sz]ed|observed|reported|added|recommended)\b")
KOL_TITLE = re.compile(r"(?:Dr\.?|Prof\.?|Professor)\s*$")
PATIENT_CUE = re.compile(r"\b(patient|Mr\.?|Mrs\.?|Ms\.?|diagnosed with|admitted|treated for)\b", re.I)

_NOT_PEOPLE = {n.lower() for n in vocab.BRANDS} | {v["company"].lower() for v in vocab.BRANDS.values()} \
    | {v["drug"].lower() for v in vocab.BRANDS.values()}


def _nlp():
    global _NLP
    if _NLP is None:
        _NLP = spacy.load("en_core_web_sm", exclude=["parser", "lemmatizer", "attribute_ruler", "tagger"])
    return _NLP


def _span(m_start, m_end, type_, text, reason=None):
    s = {"start": m_start, "end": m_end, "type": type_, "text": text}
    if reason:
        s["reason"] = reason
    return s


def _is_person_candidate(ent) -> bool:
    toks = ent.text.split()
    if len(toks) < 2 or any(ch.isdigit() for ch in ent.text):
        return False
    if ent.text.lower() in _NOT_PEOPLE or any(t.lower() in _NOT_PEOPLE for t in toks):
        return False
    return all(t[0].isupper() for t in toks if t[0].isalpha())


def _detect(text: str, doc) -> dict:
    spans = []
    for m in EMAIL_RE.finditer(text):
        spans.append(_span(m.start(), m.end(), "EMAIL", m.group()))
    for rx in PHONE_RES:
        for m in rx.finditer(text):
            spans.append(_span(m.start(), m.end(), "PHONE", m.group()))
    for m in DOB_RE.finditer(text):
        spans.append(_span(m.start(1), m.end(1), "DATE_OF_BIRTH", m.group(1)))
    for typ, rx in NATID_RES:
        for m in rx.finditer(text):
            spans.append(_span(m.start(), m.end(), typ, m.group()))
    direct = [s["start"] for s in spans]
    for ent in doc.ents:
        if ent.label_ != "PERSON" or not _is_person_candidate(ent):
            continue
        near_direct = any(abs(ent.start_char - d) <= 150 for d in direct)
        pre = text[max(0, ent.start_char - 12):ent.start_char]
        post = text[ent.end_char:ent.end_char + 60]
        kol_attribution = bool(KOL_TITLE.search(pre) or KOL_VERBS.search(post))
        patient_ctx = bool(PATIENT_CUE.search(text[max(0, ent.start_char - 80):ent.end_char + 80])) and not kol_attribution
        if near_direct:
            spans.append(_span(ent.start_char, ent.end_char, "PERSON", ent.text, "named person next to a personal identifier"))
        elif patient_ctx:
            spans.append(_span(ent.start_char, ent.end_char, "PERSON", ent.text, "named person in a patient context"))
    spans.sort(key=lambda s: s["start"])
    return {"pii_detected": bool(spans), "pii_types": sorted({s["type"] for s in spans}), "pii_spans": spans}


def detect_chunk(text: str) -> dict:
    return _detect(text, _nlp()(text))


def assess_chunks(chunks: list) -> dict:
    """Annotate each chunk in place; return the document-level assessment.
    Span offsets in the document-level list are relative to raw_text."""
    nlp = _nlp()
    all_spans, flagged = [], 0
    for chunk, doc in zip(chunks, nlp.pipe([c["text"] for c in chunks], batch_size=16)):
        res = _detect(chunk["text"], doc)
        chunk.update(res)
        if res["pii_detected"]:
            flagged += 1
            for s in res["pii_spans"]:
                all_spans.append({**s, "chunk_uri": chunk["chunk_uri"],
                                  "start": chunk["char_start"] + s["start"], "end": chunk["char_start"] + s["end"]})
    # Overlapping fixed-size chunks can report the same span twice; keep unique ones.
    unique = {(s["start"], s["end"], s["type"]): s for s in all_spans}
    spans = sorted(unique.values(), key=lambda s: s["start"])
    return {
        "pii_detected": flagged > 0,
        "pii_types": sorted({s["type"] for s in spans}),
        "flagged_chunks": flagged,
        "total_chunks": len(chunks),
        "spans": spans,
    }
