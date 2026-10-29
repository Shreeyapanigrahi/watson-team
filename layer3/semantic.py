"""Step 3 helper: match the everyday words in a question to the ontology's technical terms, by meaning.

The terms and the everyday phrases for them live in layer3/glossary.json (a reviewable file). The phrases are embedded once with
the same local model Layer 2 uses (all-MiniLM-L6-v2, already cached, no new download). A question is cut into windows of 1 to 5
words (with the entity's name taken out, and a kind word standing right next to it, as in "the Dupixa brand", too), the windows are
embedded, and each term scores as the best cosine between any window and any of its phrases. The words that scored are kept, so the
screen can show "your words -> term".

The result only NARROWS what the model may write for its first edge (sparql_gen.plan_schema); it never writes a query. If nothing
scores clearly, nothing is narrowed. If the embedding model cannot be loaded, the result says so and the older keyword rules in
sparql_gen are used instead.
"""
import json
import re
from functools import lru_cache
from pathlib import Path

import numpy as np

from layer2 import embedder

from . import config

GLOSSARY = Path(__file__).resolve().parent / "glossary.json"
STOP = {"the", "a", "an", "of", "is", "are", "was", "were", "do", "does", "did", "what", "which", "who", "whom", "whose", "in", "on", "to", "for",
        "and", "or", "with", "by", "me", "us", "it", "its", "this", "that", "these", "those", "can", "could", "would", "should", "be", "been",
        "there", "any", "all", "name", "tell", "show", "list", "give", "has", "have", "how", "many", "much"}


def _plain(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def _forms(phrase: str) -> set:
    """The phrase and its plural ('brand', 'brands'; 'country', 'countries'; 'drug class', 'drug classes')."""
    plural = phrase[:-1] + "ies" if phrase.endswith("y") else phrase + ("es" if phrase.endswith(("s", "ch", "sh")) else "s")
    return {phrase, plural}


def _singular(word: str) -> str:
    if word.endswith("ies"):
        return word[:-3] + "y"
    if word.endswith(("sses", "ches", "shes", "xes")):
        return word[:-2]
    return word[:-1] if word.endswith("s") and not word.endswith("ss") else word


def glossary() -> dict:
    return _glossary(GLOSSARY.stat().st_mtime)


@lru_cache(maxsize=2)
def _glossary(_mtime: float) -> dict:
    return json.loads(GLOSSARY.read_text(encoding="utf-8"))


@lru_cache(maxsize=2)
def _index(_mtime: float):
    """[(type, term, phrase)] and the matrix of their embeddings. Rebuilt when the glossary file changes."""
    g = glossary()
    terms = [(typ, name, ph) for section, typ in (("properties", "relation"), ("kinds", "kind")) for name, e in g[section].items() for ph in e["phrases"]]
    return terms, embedder.embed([_plain(ph) for _, _, ph in terms])


def words_of_question(question: str, entities: list) -> list:
    """The question's words with entity names removed, and a kind word next to an entity of that kind ('the Dupixa brand')."""
    q, g = " " + _plain(question) + " ", glossary()
    for e in entities:
        name = _plain(e.get("text"))
        if not name:
            continue
        for ph in g["kinds"].get(e.get("kind"), {}).get("phrases", []):
            for p in _forms(_plain(ph)):
                q = q.replace(f" {name} {p} ", f" {name} ").replace(f" {p} {name} ", f" {name} ")
        q = q.replace(f" {name} ", " ")
    return q.split()


def windows(tokens: list, longest: int = 5) -> list:
    out = [" ".join(tokens[i:i + n]) for n in range(1, longest + 1) for i in range(len(tokens) - n + 1) if not all(t in STOP for t in tokens[i:i + n])]
    return list(dict.fromkeys(out))


def _pick(ranked: list, minimum: float) -> list:
    if not ranked or ranked[0]["score"] < minimum:
        return []
    top = ranked[0]["score"]
    return [r["term"] for r in ranked if r["score"] >= minimum and r["score"] >= top - config.SEM_MARGIN][:config.SEM_TOP]


def kind_of(phrase: str) -> str | None:
    """The one kind of thing a short phrase names ('geographical areas' -> Geography), by meaning, or None when no kind scores clearly
    (same thresholds as match). Used for the thing a how-many question counts."""
    tokens = _plain(phrase).split()
    wins = list(dict.fromkeys(windows(tokens, 3) + windows([_singular(t) for t in tokens], 3)))      # 'specialties' next to the glossary's 'specialty'
    if not wins:
        return None
    try:
        terms, M = _index(GLOSSARY.stat().st_mtime)
        S = embedder.embed(wins) @ M.T
    except embedder.EmbeddingUnavailable:
        return None
    best = {}
    for j, (typ, name, _) in enumerate(terms):
        if typ == "kind":
            best[name] = max(best.get(name, 0.0), float(S[:, j].max()))
    ranked = sorted(best.items(), key=lambda kv: -kv[1])
    if not ranked or ranked[0][1] < config.SEM_MIN_KIND:
        return None
    if len(ranked) > 1 and ranked[0][1] - ranked[1][1] < config.SEM_MARGIN:
        return None
    return ranked[0][0]


def match(question: str, entities: list) -> dict:
    """{available, relations: [{term, score, phrase, words, selected}], kinds: [...], relations_selected, kinds_selected, reason}.
    `relations` and `kinds` hold the best few terms (selected or not) so the screen can show what was close."""
    empty = {"available": True, "relations": [], "kinds": [], "relations_selected": [], "kinds_selected": [], "reason": None,
             "thresholds": {"relation": config.SEM_MIN_RELATION, "kind": config.SEM_MIN_KIND}, "glossary_status": glossary()["status"][:5]}
    try:
        terms, M = _index(GLOSSARY.stat().st_mtime)
        wins = windows(words_of_question(question, entities))
        if not wins:
            return empty
        S = embedder.embed(wins) @ M.T
    except embedder.EmbeddingUnavailable as e:
        return {**empty, "available": False, "reason": str(e)}
    kind_words = sorted({w for e in glossary()["kinds"].values() for ph in e["phrases"] for w in _forms(_plain(ph))}, key=len, reverse=True)

    def only_kind_words(window: str) -> bool:
        """'brands', 'which brands', 'the documents': kind words and filler only. They say WHAT is asked for, not which relation."""
        rest = f" {window} "
        for w in kind_words:                                              # longest first, so 'drug classes' goes before 'drug'
            rest = rest.replace(f" {w} ", " ")
        return all(t in STOP for t in rest.split())
    relation_phrases = {_plain(ph) for typ, _, ph in terms if typ == "relation"}
    rel_rows = [i for i, w in enumerate(wins) if w in relation_phrases or not only_kind_words(w)]       # unless the glossary says it names a relation ('active ingredient')
    best = {}
    for j, (typ, name, phrase) in enumerate(terms):
        rows = range(len(wins)) if typ == "kind" else rel_rows
        if not rows:
            continue
        i = max(rows, key=lambda r: S[r, j])
        score = float(S[i, j])
        if (typ, name) not in best or score > best[(typ, name)]["score"]:
            best[(typ, name)] = {"term": name, "score": round(score, 3), "phrase": phrase, "words": wins[i]}
    out = dict(empty)
    for typ, section, minimum in (("relation", "relations", config.SEM_MIN_RELATION), ("kind", "kinds", config.SEM_MIN_KIND)):
        ranked = sorted((v for (t, _), v in best.items() if t == typ), key=lambda v: -v["score"])
        chosen = _pick(ranked, minimum)
        out[section] = [{**v, "selected": v["term"] in chosen} for v in ranked[:4]]
        out[section + "_selected"] = chosen
    return out
