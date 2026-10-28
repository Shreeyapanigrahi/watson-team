"""15-field metadata record (Section 4.5).

Auto-extracted where possible, auto-SUGGESTED (study_type, therapeutic_area,
geography) by simple classifiers, and finalised by the human reviewer. Each field
carries its source so the review screen can show where a value came from.
"""
import re
from collections import Counter
from typing import Optional

import spacy
from langdetect import DetectorFactory, detect_langs
from langdetect.lang_detect_exception import LangDetectException

from . import config, vocab
from .jsonstore import LOCK, read_json, write_json

DetectorFactory.seed = 0

FIELDS = [
    "document_uri", "source_filename", "format", "ingest_timestamp", "word_count",
    "extracted_title", "extracted_author", "study_type", "therapeutic_area", "geography",
    "brands_mentioned", "indications_mentioned", "language", "pii_assessment", "sensitivity_level",
]
# "Required for ACTIVE?" column of the brief. Title and author are nullable;
# brands/indications are "preferred".
REQUIRED = [
    "document_uri", "source_filename", "format", "ingest_timestamp", "word_count",
    "study_type", "therapeutic_area", "geography", "language", "pii_assessment", "sensitivity_level",
]
FIELD_INFO = {
    "document_uri": ("System", "Watson:Doc_NNN, assigned at ingest"),
    "source_filename": ("Filesystem", "os.path.basename"),
    "format": ("Extension", "pdf / docx / pptx / txt"),
    "ingest_timestamp": ("System clock", "UTC ISO 8601"),
    "word_count": ("Parser", "len(raw_text.split())"),
    "extracted_title": ("File metadata / first heading", "Flagged 'untitled' if absent"),
    "extracted_author": ("File metadata", "Nullable"),
    "study_type": ("Classifier, confirmed at review", "One of 6 types"),
    "therapeutic_area": ("NER vote, confirmed at review", "Matched against the taxonomy"),
    "geography": ("Text scan, confirmed at review", "EU / US / APAC / GLOBAL"),
    "brands_mentioned": ("NER", "spaCy EntityRuler against the brand dictionary"),
    "indications_mentioned": ("NER", "spaCy EntityRuler against the indication dictionary"),
    "language": ("langdetect", "Non-English flagged"),
    "pii_assessment": ("PII detector", "Must exist before UNDER_REVIEW"),
    "sensitivity_level": ("Human review", "PUBLIC / INTERNAL / RESTRICTED / CONFIDENTIAL"),
}

_RULER = None
META_LINE = re.compile(r"^(report date|prepared by|date)\s*:", re.I)


# ---------------------------------------------------------------- taxonomy
def _tax_path():
    return config.store_path("taxonomy_store.json")


def ensure_taxonomy() -> dict:
    """Seed the controlled vocabulary if absent. Layer 2's taxonomy manager owns later edits."""
    with LOCK:
        tax = read_json(_tax_path(), None)
        if tax is None:
            from .audit_log import now_iso
            made = now_iso()
            tax = {
                "TherapeuticArea": {"version": 1, "created_date": made, "terms": [
                    {"id": f"TA_{i:02d}", "label": t, "parent_id": None, "deprecated": False}
                    for i, t in enumerate(vocab.THERAPEUTIC_AREAS, 1)]},
                "StudyType": {"version": 1, "created_date": made, "terms": [
                    {"id": f"ST_{i:02d}", "label": t, "parent_id": None, "deprecated": False}
                    for i, t in enumerate(vocab.STUDY_TYPES, 1)]},
            }
            write_json(_tax_path(), tax)
        return tax


def taxonomy_labels(name: str) -> set:
    tax = ensure_taxonomy()
    return {t["label"] for t in tax[name]["terms"] if not t["deprecated"]}


def taxonomy_version(name: str) -> int:
    return ensure_taxonomy()[name]["version"]


def uncontrolled_terms(study_type, therapeutic_area) -> list:
    out = []
    if study_type and study_type not in taxonomy_labels("StudyType"):
        out.append({"field": "study_type", "value": study_type})
    if therapeutic_area and therapeutic_area not in taxonomy_labels("TherapeuticArea"):
        out.append({"field": "therapeutic_area", "value": therapeutic_area})
    return out


# ---------------------------------------------------------------- NER (brands, indications)
def _ruler():
    global _RULER
    if _RULER is None:
        nlp = spacy.blank("en")
        ruler = nlp.add_pipe("entity_ruler", config={"phrase_matcher_attr": "LOWER"})
        patterns = [{"label": "BRAND", "pattern": b, "id": b} for b in vocab.BRANDS]
        for ind, v in vocab.INDICATIONS.items():
            patterns.append({"label": "INDICATION", "pattern": ind, "id": ind})
            patterns += [{"label": "INDICATION", "pattern": a, "id": ind} for a in v["aliases"]]
        ruler.add_patterns(patterns)
        _RULER = nlp
    return _RULER


def find_entities(text: str) -> dict:
    doc = _ruler()(text)
    brands, inds = Counter(), Counter()
    for ent in doc.ents:
        (brands if ent.label_ == "BRAND" else inds)[ent.ent_id_ or ent.text] += 1
    return {"brands": brands, "indications": inds}


# ---------------------------------------------------------------- field helpers
def guess_title(parsed) -> tuple[Optional[str], bool]:
    """Return (title, is_untitled). Order: file metadata, heading, then the first line."""
    t = parsed.extracted_metadata.get("title")
    if t:
        return t, False
    heads = parsed.extracted_metadata.get("headings") or []
    if heads:
        return heads[0]["text"], False
    first = next((l.strip() for l in parsed.raw_text.split("\n") if l.strip()), "")
    if first and len(first) <= 120 and not first.endswith(".") and not META_LINE.match(first):
        return first, False
    return None, True


def extract_date(parsed) -> Optional[str]:
    m = re.search(r"(?:Report date|Date|Published)\s*[:\-]\s*(\d{4}-\d{2}-\d{2})", parsed.raw_text[:800], re.I)
    return m.group(1) if m else parsed.extracted_metadata.get("created")


def detect_language(text: str) -> Optional[str]:
    if len(text.split()) < 5:
        return None
    try:
        return detect_langs(text[:5000])[0].lang
    except LangDetectException:
        return None


def classify_study_type(title: Optional[str], text: str) -> Optional[str]:
    head = (text[:2000] or "").lower()
    t = (title or "").lower()
    scores = {}
    for name, v in vocab.STUDY_TYPES.items():
        scores[name] = sum(3 * t.count(k) + head.count(k) for k in v["keywords"])
    best = max(scores, key=scores.get)
    return best if scores[best] > 0 else None


def classify_ta(brands: Counter, inds: Counter) -> Optional[str]:
    votes = Counter()
    for b, n in brands.items():
        votes[vocab.BRANDS[b]["ta"]] += n
    for i, n in inds.items():
        votes[vocab.INDICATIONS[i]["ta"]] += n
    return votes.most_common(1)[0][0] if votes else None


_REGION_RES = {
    "EU": re.compile(r"\b(EU|Europe|European|Germany|France|Italy|Spain|United Kingdom|UK)\b"),
    "US": re.compile(r"\b(US|USA|United States)\b"),
    "APAC": re.compile(r"\b(APAC|Japan|JP)\b"),
}


def classify_geography(text: str) -> Optional[str]:
    if re.search(r"\bGLOBAL\b|\bglobal\b", text):
        return "GLOBAL"
    counts = {r: len(rx.findall(text)) for r, rx in _REGION_RES.items()}
    present = {r: n for r, n in counts.items() if n}
    if not present:
        return None
    if len([r for r, n in present.items() if n >= 2]) > 1:
        return "GLOBAL"
    return max(present, key=present.get)


# ---------------------------------------------------------------- record
def build_metadata(parsed, document_uri: str, ingest_timestamp: str, pii_assessment: dict) -> dict:
    """Assemble the 15-field record plus provenance and flags."""
    title, untitled = guess_title(parsed)
    ents = find_entities(parsed.raw_text)
    lang = detect_language(parsed.raw_text)
    date = extract_date(parsed)
    meta = {
        "document_uri": document_uri,
        "source_filename": parsed.source_filename,
        "format": parsed.format,
        "ingest_timestamp": ingest_timestamp,
        "word_count": parsed.word_count,
        "extracted_title": title,
        "extracted_author": parsed.extracted_metadata.get("author"),
        "study_type": classify_study_type(title, parsed.raw_text),
        "therapeutic_area": classify_ta(ents["brands"], ents["indications"]),
        "geography": classify_geography(parsed.raw_text),
        "brands_mentioned": [b for b, _ in ents["brands"].most_common()],
        "indications_mentioned": [i for i, _ in ents["indications"].most_common()],
        "language": lang,
        "pii_assessment": {k: pii_assessment[k] for k in ("pii_detected", "pii_types", "flagged_chunks")},
        "sensitivity_level": None,
    }
    flags = []
    if untitled:
        flags.append("untitled")
    if lang and lang != "en":
        flags.append(f"non_english:{lang}")
    if lang is None:
        flags.append("language_undetected")
    return {"fields": meta, "extracted_date": date, "flags": flags,
            "suggested": ["study_type", "therapeutic_area", "geography"]}


def missing_required(meta: dict) -> list:
    out = []
    for f in REQUIRED:
        v = meta.get(f)
        if v is None or v == "" or (f == "word_count" and v == 0):
            out.append(f)
    return out
