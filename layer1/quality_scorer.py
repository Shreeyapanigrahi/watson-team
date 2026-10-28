"""Quality score (Section 4.9): weighted composite of four 0-1 signals.

quality_score = 0.3*metadata_completeness + 0.3*text_richness
              + 0.2*hcp_sample_size      + 0.2*freshness

The brief suggests these weights and asks us to define and justify our own
(config.QUALITY_WEIGHTS; reasoning in design_decisions.md). Our choices where the
brief is silent:
  text_richness   unique clinical terms / total words, scaled so 0.10 -> 1.0
  hcp_sample_size >= 300 -> 1.0, < 20 -> 0.1, linear between; none stated -> 0.1
  freshness       1 - days_since_ingest / 730, clamped to [0, 1]
"""
import re
from datetime import datetime, timezone
from typing import Optional

from . import config, vocab
from .metadata_extractor import FIELDS

# Calibrated on our corpus: unique-term ratio ranged 0.038-0.112 (median 0.069), so
# 0.10 (about the 90th percentile) scores a dense document 1.0 without saturating
# the typical one. A first target of 0.08 gave a mean of 0.83 with most documents near 1.0.
RICHNESS_TARGET = 0.10

_TERM_RES = [(t, re.compile(r"\b" + re.escape(t) + r"\b")) for t in vocab.CLINICAL_TERMS]
_N_RES = [
    re.compile(r"\bn\s*=\s*(\d{1,5})\b", re.I),
    re.compile(r"\b(\d{1,5})\s+(?:respondents|HCPs|physicians|oncologists|cardiologists|neurologists|rheumatologists|dermatologists)\b", re.I),
]


def hcp_sample_size(text: str) -> Optional[int]:
    for rx in _N_RES:
        m = rx.search(text)
        if m:
            return int(m.group(1))
    return None


def metadata_completeness(meta: dict) -> float:
    present = 0
    for f in FIELDS:
        v = meta.get(f)
        if v is None or v == "" or v == []:
            continue
        present += 1
    return present / len(FIELDS)


def text_richness(text: str) -> tuple[float, int]:
    low = text.lower()
    unique = sum(1 for _, rx in _TERM_RES if rx.search(low))
    words = max(1, len(text.split()))
    return min(1.0, (unique / words) / RICHNESS_TARGET), unique


def sample_size_score(n: Optional[int]) -> float:
    if n is None or n < 20:
        return 0.1
    if n >= 300:
        return 1.0
    return round(0.1 + 0.9 * (n - 20) / 280, 4)


def freshness(ingest_iso: str, now: Optional[datetime] = None) -> float:
    now = now or datetime.now(timezone.utc)
    ingested = datetime.fromisoformat(ingest_iso)
    if ingested.tzinfo is None:
        ingested = ingested.replace(tzinfo=timezone.utc)
    days = (now - ingested).total_seconds() / 86400
    return max(0.0, min(1.0, 1 - days / 730))


def compute(meta: dict, raw_text: str, now: Optional[datetime] = None) -> dict:
    comp, uniq = text_richness(raw_text)
    n = hcp_sample_size(raw_text)
    parts = {
        "metadata_completeness": round(metadata_completeness(meta), 3),
        "text_richness": round(comp, 3),
        "hcp_sample_size": round(sample_size_score(n), 3),
        "freshness": round(freshness(meta["ingest_timestamp"], now), 3),
    }
    w = config.QUALITY_WEIGHTS
    score = sum(w[k] * parts[k] for k in parts)
    return {"quality_score": round(score, 3), "components": parts, "weights": dict(w),
            "evidence": {"unique_clinical_terms": uniq, "sample_size_found": n}}
