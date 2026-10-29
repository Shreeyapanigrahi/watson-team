"""Reads HCP sentiment statements into structured assertions (brief 5.3 stage 3: SentimentAssertion nodes).

Only statements that name a brand, a place and a score are read:
    'Overall HCP sentiment index for Opdivra in Germany: 0.69 (scale 0 to 1, assessed 2021).'
    'Sentiment index for Keytrava in EU: 0.62 (scale 0 to 1, 2023)'
The scale is read from the sentence and the score is rescaled to 0..1, so '3.5 (scale 1 to 5)' becomes 0.625.
Rows of the 'segment' tables (High-volume prescribers | 128 | 0.71) name no brand or country on the row, so they are
not extracted: guessing which brand a table row belongs to would put a wrong fact in the graph.

assertionDate: the report date if its year is the year the sentence states; otherwise 'YEAR-01-01' (the sentence only
says the year). If neither exists the statement is skipped.
"""
import hashlib
import re
from collections import Counter

from .catalog.catalog_store import Catalog
from .entity_extractor import TermIndex, normalise

_SENTENCE = re.compile(
    r"(?:Overall HCP sentiment index|Sentiment index) for (?P<brand>[A-Z][\w-]+) in (?P<geo>[A-Za-z. ]+?): "
    r"(?P<score>\d+(?:\.\d+)?) \(scale (?P<lo>\d+(?:\.\d+)?) to (?P<hi>\d+(?:\.\d+)?)(?:, (?:assessed )?(?P<year>\d{4}))?\)")


def _descendants(cat: Catalog) -> dict:
    """indication id -> itself plus every sub-indication (EGFR-mutant NSCLC counts for a brand that targets NSCLC)."""
    parent = {e["id"]: (e.get("relations") or {}).get("parent") for e in cat.instances.get("Indication", [])}
    out = {i: {i} for i in parent}
    for i in parent:
        p = parent[i]
        while p:
            out[p].add(i)
            p = parent.get(p)
    return out


def extract(chunks: list, mentions: list, report_date: str, index: TermIndex, cat: Catalog) -> tuple:
    """(assertions, skipped). `mentions` are the resolved mentions of the whole document; they decide the indication."""
    desc = _descendants(cat)
    brand_ind = {b["id"]: (b.get("relations") or {}).get("indications", []) for b in cat.instances.get("Brand", [])}
    seen_ind = Counter(m["entity_id"] for m in mentions if m["kind"] == "Indication")
    out, skipped, seen = [], [], set()
    for ch in chunks:
        text, _ = normalise(ch["text"])
        for m in _SENTENCE.finditer(text):
            brand = index.lookup(m.group("brand"))
            geo = index.lookup(m.group("geo").strip())
            if not brand or brand[0].kind != "Brand" or not geo or geo[0].kind != "Geography":
                skipped.append({"text": m.group(0), "reason": "brand or place is not in the catalog"})
                continue
            lo, hi, raw = float(m.group("lo")), float(m.group("hi")), float(m.group("score"))
            score = (raw - lo) / (hi - lo) if hi > lo else -1
            if not 0 <= score <= 1:
                skipped.append({"text": m.group(0), "reason": f"score {raw} is outside the stated scale {lo} to {hi}"})
                continue
            year, date = m.group("year"), None
            if report_date and (not year or report_date[:4] == year):
                date = report_date
            elif year:
                date = f"{year}-01-01"
            if not date:
                skipped.append({"text": m.group(0), "reason": "no date in the sentence or the report"})
                continue
            targeted = set().union(*(desc.get(i, {i}) for i in brand_ind.get(brand[0].entity_id, []))) if brand_ind.get(brand[0].entity_id) else set()
            cands = [(n, i) for i, n in seen_ind.items() if i in targeted]
            if cands:
                indication = max(cands)[1]
            elif len(brand_ind.get(brand[0].entity_id, [])) == 1:
                indication = brand_ind[brand[0].entity_id][0]
            else:
                skipped.append({"text": m.group(0), "reason": "cannot tell which indication the score is about"})
                continue
            key = (brand[0].entity_id, geo[0].entity_id, round(score, 4), date)
            if key in seen:                       # chunks overlap by 20%, so a sentence can appear twice
                continue
            seen.add(key)
            digest = hashlib.sha1("|".join(map(str, key)).encode()).hexdigest()[:8]
            out.append({"id": digest, "brand": brand[0].entity_id, "geography": geo[0].entity_id, "indication": indication,
                        "score": round(score, 4), "raw_score": raw, "scale": [lo, hi], "date": date,
                        "chunk_uri": ch["chunk_uri"], "sentence": m.group(0)})
    return out, skipped
