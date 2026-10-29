"""Step 3: find the entities in the question and choose the path.

Entities come from Layer 2's recogniser (a spaCy EntityRuler generated from the data catalog), so the question is read
with exactly the vocabulary the documents were read with. A name the ruler does not know but that is 85%+ similar to a
catalog name (a typo) is resolved by rapidfuzz and marked as such.

Routing is rule-based on purpose: it is a cheap, inspectable decision, and a 1.8B model is not reliable enough to make it.
Every route lists its reasons, so the UI (and a reviewer) can see why a question took that path.

  knowledge_space  the question is about the platform's own knowledge: its catalog, ontology, graph, entities, spaces
  graph_first      the question names entities and asks for a relation, comparison, count or trend: facts from the
                   graph decide which passages are read
  semantic_first   an open question: passages found by meaning lead, the graph adds context
"""
import re

from layer2 import entity_extractor as ext

META_CUES = re.compile(
    r"\b(catalog|catalogue|ontology|ontologies|knowledge space|knowledge spaces|knowledge graph|the graph|triples?|shacl|taxonom\w*|"
    r"schema|grounded|grounding|ground(?:s|ed)? in|mapping|mappings|mapped|classes|class hierarchy|properties|entity types?|"
    r"vector index|embeddings?|spaces?|tables?|columns?|lineage|provenance)\b", re.I)
COUNT_CUES = re.compile(r"\b(how many|number of|count|list all|what (?:kinds?|types?) of|which (?:entities|brands|drugs|indications|countries|spaces))\b", re.I)
RELATION_CUES = re.compile(
    r"\b(compare|compared|versus|vs\.?|between|competes?|competitors?|co-?mentioned|together with|same document|"
    r"sentiment|perception|trend|average|highest|lowest|best|worst|most|least|which|who|where|in which|treats?|"
    r"indicated|indication|drug class|contains?|part of|region)\b", re.I)
QUESTION_ENTITY_KINDS = ("Brand", "Drug", "DrugClass", "Indication", "TherapeuticArea", "Geography")


def extract_entities(question: str) -> list:
    index = ext.term_index()
    found, seen = [], set()
    for m in ext.extract(question, "question"):
        eid, kind, method, conf, label = m["entity_id"], m["kind"], m["method"], m["confidence"], None
        if eid is None:                                   # a candidate: near-miss spelling or unknown name
            hit = index.nearest(m["text"], kinds=ext.FUZZY_KINDS)
            if hit is None:
                continue
            eid, kind, label, ratio = hit
            method, conf = "fuzzy", round(min(conf, ratio), 2)
        if eid in seen:
            continue
        seen.add(eid)
        kind_, label_ = index.entities.get(eid, (kind, label))
        found.append({"text": m["text"], "entity_id": eid, "kind": kind_, "label": label_, "method": method, "confidence": conf})
    return found


def route(question: str, entities: list) -> dict:
    reasons, scores = [], {"knowledge_space": 0, "graph_first": 0, "semantic_first": 0}
    meta = META_CUES.findall(question)
    if meta:
        scores["knowledge_space"] += 2 + len(meta)
        reasons.append(f"mentions the platform's own structure ({', '.join(sorted({m.lower() for m in meta}))})")
    if COUNT_CUES.search(question):
        scores["graph_first"] += 1
        scores["knowledge_space"] += 1
        reasons.append("asks for a count or a list, which the graph answers exactly")
    rel = RELATION_CUES.findall(question)
    if entities and rel:
        scores["graph_first"] += 2 + len(rel)
        reasons.append(f"names {len(entities)} catalog entit{'y' if len(entities) == 1 else 'ies'} and asks about a relation or comparison "
                       f"({', '.join(sorted({r.lower() for r in rel})[:4])})")
    elif entities:
        scores["graph_first"] += 1
        reasons.append(f"names {len(entities)} catalog entit{'y' if len(entities) == 1 else 'ies'}")
    if not entities and not meta:
        scores["semantic_first"] += 2
        reasons.append("names no catalog entity, so passages found by meaning lead")
    if not any(scores.values()):
        scores["semantic_first"] = 1
        reasons.append("no structural cue: default to meaning-based retrieval")
    # a question that is about the platform AND names no research entity is a knowledge-space question
    chosen = max(scores, key=lambda k: (scores[k], k == "graph_first"))
    if chosen == "knowledge_space" and entities and not meta:
        chosen = "graph_first"
    return {"route": chosen, "scores": scores, "reasons": reasons}


def understand(question: str) -> dict:
    entities = extract_entities(question)
    r = route(question, entities)
    return {"entities": entities, **r}
