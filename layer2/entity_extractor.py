"""Stage 1: named-entity recognition with a spaCy EntityRuler built from the data catalog (brief 5.3).

The rule patterns are not typed in here. They are generated from the catalog's reference rows (labels and aliases of
brands, drugs, drug classes, indications, therapeutic areas, geographies), so the catalog stays the single source of
truth: add a brand to the catalog and the next run recognises it.

Confidence (brief): 1.0 the text is exactly a catalog label or registered alias, 0.8 it matches only when case is
ignored, 0.6 partial (a near-miss spelling, or a candidate that is not in the catalog). Below 0.5 is dropped.

Two kinds of candidate go on to entity_resolver.py (stage 2):
  near_miss  a capitalised word 85%+ similar to a brand, drug or indication name (a typo, e.g. 'Opdivva')
  pattern    'Brand (inn)': a capitalised word followed by a long lowercase word in brackets, the way pharma text
             introduces a new product. If the catalog does not know it, it becomes a provisional entity.
The pattern rule is a heuristic and can be wrong; that is why its output waits for a human (provisional queue).
"""
import re
from dataclasses import dataclass
from functools import lru_cache

import spacy
from rapidfuzz import fuzz, process

from . import config
from .catalog.catalog_store import Catalog, load

KIND_LABEL = {"Brand": "BRAND", "Drug": "DRUG", "DrugClass": "DRUG_CLASS", "Indication": "INDICATION",
              "TherapeuticArea": "THERAPEUTIC_AREA", "Geography": "GEOGRAPHY"}
LABEL_KIND = {v: k for k, v in KIND_LABEL.items()}
SHORT_TERM = 4        # terms shorter than this (IT, US, DE, RA) only match with the exact case, or they hit ordinary words
FUZZY_KINDS = ("Brand", "Drug", "Indication", "DrugClass")

_PAIR = re.compile(r"\b([A-Z][a-z]{3,}(?:[A-Z][a-z]+)?) \(([a-z]{8,})\)")
_WORD = re.compile(r"\b[A-Za-z][a-z]{6,}\b")
# Endings of ordinary English words. 'Methodology (overview)' has the Brand (inn) shape but is not a product.
_ENGLISH_ENDING = re.compile(r"(ing|tion|sion|ment|ness|ity|ally|view|ance|ence|ious|ical|able|ible|ology|ments|ings|ions)$")


@dataclass(frozen=True)
class Term:
    kind: str
    entity_id: str
    label: str          # the entity's canonical label
    text: str           # this surface form (the label or one alias)


class TermIndex:
    """Every surface form in the catalog, looked up exactly, ignoring case, or by similarity."""

    def __init__(self, cat: Catalog):
        self.catalog_version = cat.version
        self.terms, self.collisions = [], []
        self.by_exact, self.by_lower = {}, {}
        self.labels_by_kind = {k: [] for k in KIND_LABEL}
        for kind in KIND_LABEL:
            for e in cat.instances.get(kind, []):
                self.labels_by_kind[kind].append((e["label"], e["id"]))
                for text in [e["label"], *(e.get("aliases") or [])]:
                    self._add(Term(kind, e["id"], e["label"], text))
        self.entities = {e["id"]: (kind, e["label"]) for kind in KIND_LABEL for e in cat.instances.get(kind, [])}
        self._nlp = None

    def _add(self, t: Term) -> None:
        other = self.by_lower.get(t.text.lower())
        if other and other.entity_id != t.entity_id:
            self.collisions.append((t.text, other.entity_id, t.entity_id))   # the first registration wins
            return
        self.terms.append(t)
        self.by_exact[t.text] = t
        self.by_lower[t.text.lower()] = t

    def lookup(self, text: str):
        """(Term, confidence) for exact or case-insensitive text, else None."""
        t = self.by_exact.get(text)
        if t:
            return t, config.CONF_EXACT
        t = self.by_lower.get(text.lower())
        if t and len(t.text) >= SHORT_TERM:
            return t, config.CONF_CASE_INSENSITIVE
        return None

    def nlp(self):
        if self._nlp is None:
            nlp = spacy.blank("en")
            ruler = nlp.add_pipe("entity_ruler", config={"phrase_matcher_attr": "LOWER", "overwrite_ents": True})
            patterns = []
            for t in self.terms:
                if len(t.text) < SHORT_TERM:
                    patterns.append({"label": KIND_LABEL[t.kind], "id": t.entity_id,
                                     "pattern": [{"ORTH": tok.text} for tok in nlp.make_doc(t.text)]})
                else:
                    patterns.append({"label": KIND_LABEL[t.kind], "id": t.entity_id, "pattern": t.text})
            ruler.add_patterns(patterns)
            self._nlp = nlp
        return self._nlp

    def nearest(self, text: str, kinds=FUZZY_KINDS, cutoff: float = None):
        """Closest catalog label of the given kinds: (entity_id, kind, label, ratio 0..1) or None."""
        cutoff = config.RESOLVE_RATIO if cutoff is None else cutoff
        best = None
        for kind in kinds:
            labels = [l for l, _ in self.labels_by_kind[kind]]
            if not labels:
                continue
            hit = process.extractOne(text.lower(), [l.lower() for l in labels], scorer=fuzz.ratio, score_cutoff=cutoff * 100)
            if hit and (best is None or hit[1] > best[3] * 100):
                label, eid = self.labels_by_kind[kind][hit[2]]
                best = (eid, kind, label, hit[1] / 100)
        return best


@lru_cache(maxsize=2)
def _index_for(version: str, path: str) -> TermIndex:
    return TermIndex(load())


def term_index() -> TermIndex:
    cat = load()
    return _index_for(cat.version, str(config.CATALOG_JSON))


def normalise(text: str):
    """Collapse runs of whitespace (the corpus is hard-wrapped) and keep the map back to the original offsets."""
    out, idx, prev_space = [], [], False
    for i, ch in enumerate(text):
        if ch.isspace():
            if prev_space:
                continue
            out.append(" ")
            prev_space = True
        else:
            out.append(ch)
            prev_space = False
        idx.append(i)
    return "".join(out), idx


def _mention(text, kind, entity_id, start, end, chunk_uri, confidence, method, **extra) -> dict:
    return {"text": text, "label": KIND_LABEL.get(kind, kind), "kind": kind, "entity_id": entity_id,
            "start": start, "end": end, "chunk_uri": chunk_uri, "confidence": confidence, "method": method, **extra}


def extract(text: str, chunk_uri: str, index: TermIndex = None) -> list:
    """Mentions found in one chunk, in text order. Offsets point into the original (un-normalised) chunk text."""
    index = index or term_index()
    norm, idx = normalise(text)
    doc = index.nlp()(norm)
    out, taken = [], []
    for ent in doc.ents:
        surface = norm[ent.start_char:ent.end_char]
        hit = index.lookup(surface)
        if hit is None:                                  # short term matched without its exact case: should not happen
            continue
        term, conf = hit
        out.append(_mention(surface, term.kind, term.entity_id, idx[ent.start_char], idx[ent.end_char - 1] + 1,
                            chunk_uri, conf, "exact" if conf == config.CONF_EXACT else "case_insensitive"))
        taken.append((ent.start_char, ent.end_char))

    def free(s, e):
        return not any(s < te and e > ts for ts, te in taken)

    for m in _PAIR.finditer(norm):                       # 'Brand (inn)' -> two candidates
        if _ENGLISH_ENDING.search(m.group(1).lower()) or _ENGLISH_ENDING.search(m.group(2)):
            continue
        for g, kind in ((1, "Brand"), (2, "Drug")):
            s, e = m.start(g), m.end(g)
            if free(s, e):
                out.append(_mention(m.group(g), kind, None, idx[s], idx[e - 1] + 1, chunk_uri,
                                    config.CONF_PARTIAL, "candidate", source="pattern"))
                taken.append((s, e))
    for m in _WORD.finditer(norm):                       # near-miss spellings of names the catalog knows
        s, e = m.start(), m.end()
        if free(s, e) and not index.lookup(m.group(0)) and index.nearest(m.group(0)):
            out.append(_mention(m.group(0), "Unknown", None, idx[s], idx[e - 1] + 1, chunk_uri,
                                config.CONF_PARTIAL, "candidate", source="near_miss"))
            taken.append((s, e))
    out = [m for m in out if m["confidence"] >= config.CONF_MIN]
    out.sort(key=lambda m: m["start"])
    return out
