"""Step 2: the knowledge space as THIS user may see it.

The full graph is copied, and every document the user may not read is cut out of the copy (its chunks, sentiment
assertions, lineage, landscapes). Brand-to-brand links are then recomputed from what is left, because a stored
coMentionedWith edge could reveal that two brands appear together in a document the user may not read.
Everything after this step (SPARQL, entity mapping, evidence) works on the copy only, so a wrong or even malicious
generated query cannot reach a restricted document: it is not in the graph the query runs against.

Reference data (brands, drugs, indications, geographies, the ontology, the catalog assets) is kept: it is schema and
reference rows, not research content, and Layer 2 already shows it to every role.
"""
import time

from rdflib import Graph
from rdflib.namespace import RDF, RDFS

from layer1 import abac_engine as abac, audit_log, lifecycle_manager as lifecycle
from layer2 import graph_view, kg as kgmod, triple_writer
from layer2.kg import W, short


class UserSlice:
    def __init__(self, user, graph, records, stats):
        self.user, self.graph, self.records, self.stats = user, graph, records, stats

    @property
    def document_uris(self):
        return sorted(self.records)

    def chunk_to_document(self) -> dict:
        return {short(c): short(d) for c, d in self.graph.subject_objects(W.isChunkOf)}


def _subclass_closure(g: Graph) -> int:
    """Add the parent types a document implies (a HCPPerceptionSurvey is also a MarketResearchDocument) so a query that asks
    for the parent class finds it. This is plain rdfs:subClassOf only; the full OWL reasoner stays in Layer 2 (query Q6)."""
    n = 0
    for sub in set(g.objects(None, RDF.type)):
        for sup in g.transitive_objects(sub, RDFS.subClassOf):
            if sup == sub:
                continue
            for x in list(g.subjects(RDF.type, sub)):
                if (x, RDF.type, sup) not in g:
                    g.add((x, RDF.type, sup))
                    n += 1
    return n


def build(user: dict) -> UserSlice:
    t0 = time.time()
    records = graph_view.visible_records(user)            # ACTIVE and ABAC-allowed (sensitivity, TA, geography)
    kg = kgmod.get()
    with kg.lock:
        g = kgmod.bind(Graph())
        g += kg.g
    before_docs = {s for s in g.subjects(W.lifecycleState, None)}
    before = len(g)
    removed = 0
    for doc in before_docs:
        if short(doc) not in records:
            removed += 1
            triple_writer.remove_document(g, short(doc))
    triple_writer.relink_brands(g)
    triple_writer.prune_orphan_provisionals(g)
    inferred = _subclass_closure(g)
    stats = {"inferred_type_triples": inferred,"documents_in_graph": len(before_docs), "documents_readable": len(records), "documents_withheld": removed,
             "triples_before": before, "triples_after": len(g), "seconds": round(time.time() - t0, 3)}
    # the withheld ones are counted, never named: nothing about them reaches the model, the UI or the answer
    audit_log.log_event("QUERY", None, {"user_id": user["user_id"], "role": user["role"], "stage": "layer3_access_slice", **stats})
    return UserSlice(user, g, records, stats)


def describe(user: dict) -> dict:
    """What the user's ABAC attributes are, in words, for the UI."""
    return {"user": user["name"], "role": user["role"], "max_sensitivity": abac.ROLE_MAX_SENSITIVITY[user["role"]],
            "therapeutic_areas": user.get("therapeutic_areas") or ["all"], "geographies": user.get("geographies") or ["all"]}
