"""Step 4: the local LLM writes the SPARQL for THIS question. There is no template and no stored query.

How it works, and why it is built this way
------------------------------------------
Attempt 1 asked Qwen1.5-1.8B-Chat for SPARQL as free text: 0 of 6 test questions parsed. Attempt 2 had it fill a JSON
query plan with free variable names: it parsed, but the plans were wrong (a variable that was a Brand and a sentiment at
once, an entity used as a type, loops of repeated patterns); Qwen2.5-3B did better but still produced 4 of 10 acceptable
plans, about half of them meaningful (design_decisions.md section 21 has the numbers).
So the plan is now TYPED, and the grammar the model decodes under makes a type error impossible to write:

  variables  ?brand ?drug ?indication ?country ?document ?chunk ?sentiment ?space ... each stands for one kind of thing;
             ?name ?score ?count ?value ?date ... stand for plain values
  edges      each edge is {s, p, o}, and only combinations that really occur in this user's graph are allowed, for example
             (?brand, Watson:targetedIndication, ?indication) or (?sentiment, Watson:forBrand, Watson:Brand_opdivra).
             Entities named in the question may stand in any slot of their own kind.
  select     columns, each optionally an aggregate (count, count_distinct, avg, sum, min, max)
  filters, order, limit

The model decides everything that makes the query specific to the question: which edges join which entities, which
filter, which aggregate, which ordering. Code only turns the plan into SPARQL syntax and adds the type triples and
display labels the typed variables imply (which never changes what the plan asks). A different question gives a different
set of edges and a different query; nothing is picked from a list of stored queries.

What is still checked (the model is small, so it is checked, not trusted):
  * the question's entity must be used in an edge; an average must be of a numeric value; no result name may clash
  * the rendered query must parse (rdflib): SELECT only, no SERVICE, FROM or updates
  * it runs on the user's slice only (step 2), with a row limit and a time limit
  * a query that runs but returns nothing is fed back and tried again (SPARQL_ATTEMPTS in total)
If every attempt fails we say so and carry on with passage retrieval. We never substitute a stored query.
"""
import json
import re
import time
from collections import Counter

from rdflib import Graph, Literal, URIRef
from rdflib.namespace import OWL, RDF, RDFS
from rdflib.plugins.sparql import prepareQuery

from layer2 import config as l2config, kg as kgmod, sparql_runner
from layer2.kg import W

from . import config, llm

PREFIXES = {
    "Watson": l2config.NS, "rdf": str(RDF), "rdfs": str(RDFS), "owl": str(OWL),
    "xsd": "http://www.w3.org/2001/XMLSchema#", "skos": "http://www.w3.org/2004/02/skos/core#",
}
EXEC_TIMEOUT_S = 10
AGGS = {"count": "COUNT({v})", "count_distinct": "COUNT(DISTINCT {v})", "avg": "AVG({v})", "min": "MIN({v})", "max": "MAX({v})", "sum": "SUM({v})"}
NUMERIC_AGGS = ("avg", "sum", "min", "max")
OPS = ["=", "!=", "<", ">", "<=", ">=", "contains"]
MAX_EDGES = 5
_VAR = re.compile(r"^\?[A-Za-z_][A-Za-z0-9_]*$")

# Typed variables: the name says what kind of thing it is, and the compiler adds the matching `a Watson:Class` triple.
TYPED_VARS = {"?brand": "Brand", "?brand2": "Brand", "?drug": "Drug", "?drugclass": "DrugClass", "?indication": "Indication",
              "?country": "Geography", "?area": "TherapeuticArea", "?document": "MarketResearchDocument", "?chunk": "DocumentChunk",
              "?sentiment": "SentimentAssertion", "?space": "KnowledgeSpace"}
VALUE_VARS = ["?name", "?title", "?score", "?count", "?value", "?date", "?company", "?type"]
CANON = set(TYPED_VARS.values())
KIND_CLASS = {"Brand": "Brand", "Drug": "Drug", "DrugClass": "DrugClass", "Indication": "Indication", "TherapeuticArea": "TherapeuticArea",
              "Geography": "Geography"}
# bookkeeping properties the model does not need (and that only add noise for a small model)
HIDDEN_PROPS = {"groundedIn", "retrievedChunk", "requestedBy", "userRole", "eventTime", "processedDocument", "reviewedBy",
                "chunksProduced", "triplesWritten", "processingTimestamp", "enrichmentModel", "taxonomyVersion", "catalogVersion",
                "faissId", "confidence", "groundingStatus", "groundingNote", "assetOrigin", "provisionalStatus", "entityType",
                "spaceOwner", "created", "isGrouping", "molecularWeight", "isoCode", "payerType", "hasChunk", "derivedFrom",
                "isInSpace", "allowedTA", "allowedGeo", "sensitivityLevel", "lifecycleState", "ingest_timestamp", "expiry_date",
                "freshness_date", "chunk_index", "includesBrand", "describedBy", "mechanismOfAction", "atcCode", "description",
                "geographyKind", "fqn", "spaceTherapeuticArea", "assertionDate"}

SYSTEM = (
    "You turn a question into a query plan for a knowledge graph. Reply with JSON only. "
    "A plan is a list of edges between typed variables, plus the columns to return. Use the entity IRIs given for the question."
)

EXAMPLES = """Plans for OTHER questions, to show the format:
Question: Which brands target heart failure?
{"edges":[{"s":"?brand","p":"Watson:targetedIndication","o":"Watson:Indication_heart_failure"}],"select":[{"var":"?brand","agg":"none","of":""}],"filters":[],"order_by":"","descending":false,"limit":0}
Question: What is the average sentiment score for each brand, highest first?
{"edges":[{"s":"?sentiment","p":"Watson:forBrand","o":"?brand"},{"s":"?sentiment","p":"Watson:sentimentScore","o":"?score"}],"select":[{"var":"?brand","agg":"none","of":""},{"var":"?value","agg":"avg","of":"?score"}],"filters":[],"order_by":"?value","descending":true,"limit":0}
Question: How many documents are there for each study type?
{"edges":[{"s":"?document","p":"Watson:study_type","o":"?type"}],"select":[{"var":"?type","agg":"none","of":""},{"var":"?count","agg":"count_distinct","of":"?document"}],"filters":[],"order_by":"?count","descending":true,"limit":0}"""


# ---------------------------------------------------------------- what edges exist in this user's graph
def _node_class(g: Graph, node, anc: dict) -> str | None:
    for t in g.objects(node, RDF.type):
        name = kgmod.local(t)
        if name in CANON:
            return name
        if "MarketResearchDocument" in anc.get(name, ()):
            return "MarketResearchDocument"
    return None


def _ancestors() -> dict:
    o = kgmod.get().ontology()
    parents = {}
    for s, p in o.subject_objects(RDFS.subClassOf):
        parents.setdefault(kgmod.local(s), set()).add(kgmod.local(p))
    anc = {}

    def up(c, seen):
        for p in parents.get(c, ()):
            if p not in seen:
                seen.add(p)
                up(p, seen)
        return seen
    for c in list(parents):
        anc[c] = up(c, set())
    return anc


def edge_shapes(g: Graph) -> dict:
    """{(property, subject class, object class or 'value'): number of triples}, read from the graph the user may see."""
    anc, cache, shapes = _ancestors(), {}, {}

    def cls(n):
        if n not in cache:
            cache[n] = _node_class(g, n, anc)
        return cache[n]
    for s, p, o in g:
        if not str(p).startswith(l2config.NS):
            continue
        name = kgmod.local(p)
        if name in HIDDEN_PROPS:
            continue
        sc = cls(s)
        if sc is None:
            continue
        if isinstance(o, Literal):
            key = (name, sc, "value")
        else:
            oc = cls(o)
            if oc is None:
                continue
            key = (name, sc, oc)
        shapes[key] = shapes.get(key, 0) + 1
    return shapes


def entity_edges(g: Graph, entity_id: str, anc: dict | None = None) -> dict:
    """{(property, 'out' or 'in', kind of the other end or 'value'): number of triples} for the triples that really touch this entity
    in the graph the user may see. 'out' means the entity is the subject. Same bookkeeping filter as edge_shapes."""
    anc = _ancestors() if anc is None else anc
    node, found = W[entity_id], {}

    def add(p, direction, other):
        name = kgmod.local(p)
        if not str(p).startswith(l2config.NS) or name in HIDDEN_PROPS:
            return
        kind = "value" if isinstance(other, Literal) else _node_class(g, other, anc)
        if kind is not None:
            found[(name, direction, kind)] = found.get((name, direction, kind), 0) + 1
    for _, p, o in g.triples((node, None, None)):
        add(p, "out", o)
    for s, p, _ in g.triples((None, None, node)):
        add(p, "in", s)
    return found


def describe_edges(found: dict) -> str:
    return "; ".join(f"Watson:{p} ({'to' if d == 'out' else 'from'} {'a plain value' if k == 'value' else 'a ' + k})" for (p, d, k) in sorted(found))


def _vars_for(cls_name: str, entities: list) -> list:
    return [v for v, c in TYPED_VARS.items() if c == cls_name] + [f"Watson:{e['entity_id']}" for e in entities if KIND_CLASS.get(e["kind"]) == cls_name]


def _edge_variants(entities: list, shapes: dict, anchored: list | None = None, reach: dict | None = None, kinds: set | None = None,
                   drop: set | None = None, skip: set | None = None) -> list:
    """One schema per allowed edge shape. With `anchored` (entity IRIs), a slot that may hold an entity is fixed to one of them.
    With `reach` ({entity IRI: keys of entity_edges}), an entity is only offered in an edge that has triples for it in the user's
    graph, so the first edge cannot be one that is guaranteed to return nothing (for example co-mentions of a brand that has none).
    With `drop` (class names), no variable of those kinds is offered at all; with `skip` (shape keys), those shapes are not offered."""
    out = []

    def reaches(iri, prop, direction, kind):
        return reach is None or (prop, direction, kind) in reach.get(iri, ())

    def slot(options):
        return {"enum": options}

    for (prop, sc, oc), _ in sorted(shapes.items()):
        if (drop and (sc in drop or oc in drop)) or (skip and (prop, sc, oc) in skip):
            continue
        s_enum, o_enum = _vars_for(sc, entities), (VALUE_VARS if oc == "value" else _vars_for(oc, entities))
        if not (s_enum and o_enum):
            continue
        forms = [(s_enum, o_enum, None)]                  # third item: the kind at the end that is NOT the entity (only known when anchored)
        if anchored is not None:
            s_a = [x for x in s_enum if x in anchored and reaches(x, prop, "out", oc)]
            o_a = [x for x in o_enum if x in anchored and reaches(x, prop, "in", sc)]
            s_v = [x for x in s_enum if x not in anchored]
            o_v = [x for x in o_enum if x not in anchored]
            forms = ([(s_a, o_v, oc)] if s_a and o_v else []) + ([(s_v, o_a, sc)] if s_v and o_a else [])
        for se, oe, other in forms:
            if anchored is not None and kinds is not None and other not in kinds:      # the edge must lead to the kind of thing asked for
                continue
            out.append({"type": "object", "required": ["s", "p", "o"], "properties": {"s": slot(se), "p": {"const": f"Watson:{prop}"}, "o": slot(oe)}})
    if kinds is not None and anchored is not None:        # a name (rdfs:label) is not an edge to the kind of thing asked for
        return out
    for cls_name in sorted(CANON - {"MarketResearchDocument", "DocumentChunk", "SentimentAssertion"} - (drop or set())):       # names of reference things
        s_enum = _vars_for(cls_name, entities)
        if anchored is not None:
            s_enum = [x for x in s_enum if x in anchored]
        if s_enum:
            out.append({"type": "object", "required": ["s", "p", "o"], "properties": {
                "s": slot(s_enum), "p": {"const": "rdfs:label"}, "o": slot(["?name"])}})
    return out


def named_relations(question: str, shapes: dict) -> set:
    """Ontology properties the question itself names, as the property name ('containsDrug') or its words in order ('contains drug',
    'co-mentioned with', 'company'), with at most two other words between them ('contains which drug'). Whole words only."""
    q = " " + re.sub(r"[^a-z0-9]+", " ", question.lower()).strip() + " "
    found = set()
    for prop in {p for (p, _, _) in shapes}:
        words = re.sub(r"(?<!^)(?=[A-Z])", " ", prop).replace("_", " ").lower().split()
        phrase = r"(?:\s+\w+){0,2}\s+".join(re.escape(w) for w in words)
        if re.search(rf"(?<!\w){phrase}(?!\w)", q) or f" {prop.lower()} " in q:
            found.add(prop)
    return found


KIND_WORDS = {"Brand": ("brand", "brands"), "Drug": ("drug", "drugs"), "DrugClass": ("drug class", "drug classes"),
              "Indication": ("indication", "indications"), "TherapeuticArea": ("therapeutic area", "therapeutic areas"),
              "Geography": ("country", "countries", "geography", "geographies", "region", "regions"),
              "MarketResearchDocument": ("document", "documents", "report", "reports"), "KnowledgeSpace": ("knowledge space", "knowledge spaces")}


def answer_kinds(question: str, entities: list) -> set:
    """The kinds of thing the question asks for ('which drug' -> Drug). A kind word right next to an entity's own name of that
    kind ('the Dupixa brand') describes the entity, not the answer, so it is not counted."""
    q = " " + re.sub(r"[^a-z0-9]+", " ", question.lower()).strip() + " "
    for e in entities:
        name = re.sub(r"[^a-z0-9]+", " ", (e.get("text") or "").lower()).strip()
        for w in KIND_WORDS.get(e.get("kind"), ()):
            if name:
                q = q.replace(f" {name} {w} ", f" {name} ").replace(f" {w} {name} ", f" {name} ")
    kinds = set()
    for w, kind in sorted(((w, k) for k, ws in KIND_WORDS.items() for w in ws), key=lambda x: -len(x[0])):     # 'drug class' before 'drug'
        if f" {w} " in q:
            kinds.add(kind)
            q = q.replace(f" {w} ", " ")
    return kinds


COUNT_CUE = re.compile(r"\b(?:how\s+many|total\s+number\s+of|number\s+of|count\s+of)\b\s*(.*)")
FILLER_NUMBER_OF = re.compile(r"^(?:(?:the\s+)?(?:total\s+)?(?:number|count)\s+of\s+)+")   # a second cue right after 'how many'
NOT_ONE_NUMBER = re.compile(r"\b(?:per|each|every|by|broken\s+down|breakdown|grouped|group|split|distribution|most|least|highest|lowest|largest|"
                            r"smallest|top|rank|ranked|more|fewer|than|compare|trend)\b")
NOUN_PHRASE_END = {"are", "is", "do", "does", "did", "were", "was", "have", "has", "had", "in", "of", "for", "on", "at", "with", "from", "exist",
                   "exists", "there", "mentioned", "covered", "appear", "appears", "can", "could", "will", "would", "that", "which", "who", "within",
                   "under", "inside", "across"}
NOUN_PHRASE_SKIP = {"our", "the", "my", "your", "different", "distinct", "unique", "total"}      # words before the noun that say nothing about its kind
COUNT_NOT_OFFERED = {"SentimentAssertion", "KnowledgeSpace"}  # a count of things is not narrowed by opinions, nor by a space (no entity names one, so it would only add a join)
COUNT_SCOPE = ("MarketResearchDocument", "DocumentChunk")        # what the user's slice is made of: counting over these follows the user's access
REFERENCE_KINDS = set(KIND_CLASS.values())                       # Brand, Drug, ... : things the documents mention, as opposed to documents and passages themselves


def count_intent(question: str) -> dict | None:
    """A question that asks for ONE number ('how many drugs ...', 'number of countries ...'): {'phrase': the thing counted, in the
    question's words}. Wording that asks for a breakdown or a ranking ('per', 'each', 'by', 'most', ...) is not one number, and
    gets the ordinary plan. Only the shape of the sentence is read here; which kind of thing is counted comes from the ontology's
    kind words and the glossary (counted_class), so nothing is listed per domain term."""
    q = " " + re.sub(r"[^a-z0-9']+", " ", question.lower()).strip() + " "
    m = COUNT_CUE.search(q.strip())
    grouping_words = re.sub(r"\b(\w+(?:ed|en|wn|ten))\s+by\b|\bby\s+(?:the|our|a|an)\b", r"\1", q)           # 'covered by the space', 'targeted by X': by an agent, not by a group
    if not m or NOT_ONE_NUMBER.search(grouping_words):
        return None
    after, words, end = FILLER_NUMBER_OF.sub("", m.group(1)).split(), [], 0         # 'how many number of documents': the thing counted is 'documents'
    for i, w in enumerate(after):
        if w in NOUN_PHRASE_END or len(words) >= 3:
            break
        if w not in NOUN_PHRASE_SKIP:
            words.append(w)
        end = i + 1
    rest = q.strip()[:m.start()].split() + after[end:]               # the question without 'how many' and the thing counted
    return {"phrase": " ".join(words), "rest": " ".join(rest)} if words else None


def counted_class(phrase: str, entities: list) -> str | None:
    """The kind of thing in the counted phrase: the ontology's own kind words first ('drugs', 'countries'), else the glossary by
    meaning ('geographical areas' -> Geography). None when it is not clear: the grammar then leaves the counted variable open."""
    from . import semantic                                       # imported here: semantic has no need of this module
    kinds = answer_kinds(" " + phrase + " ", entities)
    if len(kinds) == 1:
        return next(iter(kinds))
    return None if kinds else semantic.kind_of(phrase)


STRUCTURAL_KINDS = {"MarketResearchDocument", "DocumentChunk", "KnowledgeSpace", "SentimentAssertion"}      # what the graph is built of, not what it is about


def _count_of(graph: Graph, pattern: str) -> int:
    rows = list(graph.query(with_prefixes(f"SELECT (COUNT(DISTINCT ?x) AS ?c) WHERE {{ {pattern} }}")))
    return int(rows[0][0]) if rows else 0


def _variant(prop: str, s_enum: list, o_enum: list) -> dict:
    return {"type": "object", "required": ["s", "p", "o"], "properties": {"s": {"enum": s_enum}, "p": {"const": prop}, "o": {"enum": o_enum}}}


BRIDGE = ("isChunkOf", "DocumentChunk", "MarketResearchDocument")      # a passage belongs to its document


def _minus_entities(text: str, entities: list) -> str:
    """The text in plain lower-case words without the entities' own names. A name carries words that mean something else on their own
    ('HER2 antibody-drug conjugate': a number and the word 'drug'), and they must not be read as part of what the question asks."""
    out = " " + re.sub(r"[^a-z0-9]+", " ", text.lower()).strip() + " "
    for e in entities:
        name = re.sub(r"[^a-z0-9]+", " ", (e.get("text") or "").lower()).strip()
        if name:
            out = out.replace(f" {name} ", " ")
    return out.strip()


def entity_document_route(intent: dict, entities: list, shapes: dict, graph: Graph | None) -> tuple:
    """(first edge variants, edges that must follow) for 'how many documents ... <one named entity>'. A document is associated with an
    entity if it links to it itself, or through one of its passages (passage -[prop]-> entity, passage -isChunkOf-> document). A plan is
    a conjunction of edges, so it can follow one level only; which level is complete is read from the user's graph, per entity: the
    level whose distinct documents equal those of all the entity's links together. Measured on this data one level always contains the
    other, but not always the same one: EU has 25 documents at document level and 15 through passages, Germany 0 and 10, brands equal.
    An entity nothing readable links to is routed to the document level, so the count is an honest 0 rather than a failed query.
    ([], []) when this does not apply, or when neither level contains the other (the ordinary plan is left to decide)."""
    named = [e for e in entities if e.get("entity_id")]
    if graph is None or len(named) != 1 or named[0].get("kind") not in KIND_CLASS:
        return [], []
    e, kind = named[0], KIND_CLASS[named[0]["kind"]]
    others = answer_kinds(" " + _minus_entities(intent["rest"], entities) + " ", entities) - {"KnowledgeSpace", *COUNT_SCOPE, kind}
    if others:
        return [], []
    links, node = entity_edges(graph, e["entity_id"]), W[e["entity_id"]]
    props = {lvl: sorted({p for (p, d, k) in links if d == "in" and k == scope}) for lvl, scope in (("doc", "MarketResearchDocument"), ("chunk", "DocumentChunk"))}
    if not props["doc"] and not props["chunk"]:                  # nothing readable links to it: what the graph's shapes allow for its kind
        props = {lvl: sorted({p for (p, sc, oc) in shapes if sc == scope and oc == kind}) for lvl, scope in (("doc", "MarketResearchDocument"), ("chunk", "DocumentChunk"))}
    if BRIDGE not in shapes:
        props["chunk"] = []
    pattern = {("doc", p): f"?x a Watson:MarketResearchDocument . ?x Watson:{p} <{node}>" for p in props["doc"]}
    pattern |= {("chunk", p): f"?c Watson:{BRIDGE[0]} ?x . ?c Watson:{p} <{node}>" for p in props["chunk"]}
    if not pattern:
        return [], []
    union = _count_of(graph, " UNION ".join("{ " + pat + " }" for pat in pattern.values()))
    complete = [key for key, pat in pattern.items() if _count_of(graph, pat) == union]
    if not complete:
        return [], []
    level = "doc" if any(lvl == "doc" for lvl, _ in complete) else "chunk"                # on a tie the plain document edge needs no bridge
    iri, subject = [f"Watson:{e['entity_id']}"], _vars_for("MarketResearchDocument" if level == "doc" else "DocumentChunk", [])
    first = [_variant(f"Watson:{p}", subject, iri) for lvl, p in complete if lvl == level]
    bridge = [_variant(f"Watson:{BRIDGE[0]}", subject, _vars_for("MarketResearchDocument", []))] if level == "chunk" else []
    return first, bridge


def count_setup(question: str, entities: list, shapes: dict, graph: Graph | None = None) -> dict | None:
    """What a how-many plan must look like: one column, a count_distinct of the counted kind, no opinion or space variables, and, when
    no entity is named, the finest-grain relation only. Documents carry some links that their passages carry in more detail
    (a document covers 3 regions, its passages cover 9 places); a passage of a readable document is a mention by that document, so
    the passage-level relation is the complete count, and offering both lets a small model require both (the intersection).
    Measured on this data: the passage-level SET OF DISTINCT ENTITIES always contains the document-level set. For the documents linked to
    ONE entity that does not hold either way (see entity_document_route). None for any other question."""
    intent = count_intent(question)
    if not intent:
        return None
    cls = counted_class(intent["phrase"], entities)
    if not cls:                                                  # 'how many classes does the ontology have': not a count over the graph's things
        return None
    named = any(e.get("entity_id") for e in entities)
    fine = cls in REFERENCE_KINDS and not named
    skip = {k for k in shapes if fine and k[1] == "MarketResearchDocument" and (k[0], "DocumentChunk", k[2]) in shapes}
    of = [v for v, c in TYPED_VARS.items() if c == cls]
    # A bare count of one kind (no entity, number, other relation or other kind in the question) is one edge. For a kind the documents
    # mention it is the edge from the documents' side: a 3B model cannot find it among 100 edges (it padded plans with unrelated
    # ones), but the user's graph lists exactly which edges leave the documents or passages for that kind, so only those are offered.
    # For what the graph is built of (documents, spaces, ...) it is an edge that EVERY one of them has, found by counting in the user's
    # graph, so the join cannot silently leave some out.
    only, then = [], []
    variant = _variant
    if (fine or cls in STRUCTURAL_KINDS and not named) and not re.search(r"\d", question):
        if fine:
            for (p, sc, oc) in sorted(shapes):
                if (p, sc, oc) in skip or {sc, oc} - set(COUNT_SCOPE) != {cls} or (sc == oc):
                    continue
                s_enum, o_enum = _vars_for(sc, entities), _vars_for(oc, entities)
                if s_enum and o_enum:
                    only.append(variant(f"Watson:{p}", s_enum, o_enum))
        elif graph is not None:
            total = _count_of(graph, f"?x a Watson:{cls}")
            for p, o_enum in sorted([(p, VALUE_VARS) for (p, sc, oc) in shapes if sc == cls and oc == "value"] + [("rdfs:label", ["?name"])]):
                pred = p if p == "rdfs:label" else f"Watson:{p}"
                if total and _count_of(graph, f"?x a Watson:{cls} . ?x {pred} ?v") == total:
                    only.append(variant(pred, of, o_enum))
        if only:        # judged on the rest of the question: the counted phrase itself ('drug classes') would match a kind and a relation
            from . import semantic
            rest = intent["rest"]
            others = answer_kinds(" " + rest + " ", entities) - {"KnowledgeSpace", *COUNT_SCOPE}      # 'our knowledge space', 'in documents': scope words
            scope_words = {w for k in ("KnowledgeSpace", "MarketResearchDocument") for w in KIND_WORDS[k]} | {
                f for k in ("KnowledgeSpace", "MarketResearchDocument", "DocumentChunk") for ph in semantic.glossary()["kinds"][k]["phrases"]
                for f in semantic._forms(semantic._plain(ph))}
            rest = " " + rest + " "
            for w in sorted(scope_words, key=len, reverse=True):          # the scope words are not a relation ('knowledge space' scored 0.857 against spaceName)
                rest = rest.replace(f" {w} ", " ")
            rest = rest.strip()
            related = named_relations(rest, shapes) | set(semantic.match(rest, entities)["relations_selected"] if rest else [])
            offered = {v["properties"]["p"]["const"].split(":", 1)[-1] for v in only}                   # 'covered' is fine: coversGeography is the edge the count uses
            if others or related - offered:
                only = []
    route = set()                    # the relations an entity route counts through: a gap in one of them is a 0, not "no such relation"
    if cls == "MarketResearchDocument" and not only and not re.search(r"\d", _minus_entities(question, entities)):
        only, then = entity_document_route(intent, entities, shapes, graph)
        route = {v["properties"]["p"]["const"].split(":", 1)[-1] for v in [*only, *then]}
    mentioned = answer_kinds(question, entities) | {KIND_CLASS[e["kind"]] for e in entities if e.get("kind") in KIND_CLASS} | {cls}
    return {"phrase": intent["phrase"], "kind": cls, "of": of, "drop": COUNT_NOT_OFFERED - {cls}, "skip": skip, "only": only,
            "then": then, "route": route, "mentioned": mentioned}


def plan_schema(entities: list, shapes: dict, graph: Graph | None = None, relations: set | None = None, kinds: set | None = None,
                count: dict | None = None) -> dict:
    """The JSON schema the reply is decoded under. Each edge is one of the shapes that exist in the user's graph, so a property
    can only be used with subjects and objects of the right kind. If the question names an entity, the FIRST edge must use it,
    and (when the graph is given) only through a relation that has triples for that entity in the user's graph. If the question
    also names a relation (`relations`) that is available for the entity, the first edge must use that relation."""
    anchor_iris = [f"Watson:{e['entity_id']}" for e in entities[:2]]
    reach = {f"Watson:{e['entity_id']}": set(entity_edges(graph, e["entity_id"])) for e in entities[:2]} if graph is not None else None
    drop, skip = (count["drop"], count["skip"]) if count else (None, None)
    rest = {"oneOf": _edge_variants(entities, shapes, drop=drop, skip=skip)}
    first = _edge_variants(entities, shapes, anchored=anchor_iris, reach=reach, drop=drop, skip=skip) if anchor_iris else []
    if relations and first:
        wanted = {f"Watson:{p}" for p in relations}
        named = [v for v in first if v["properties"]["p"]["const"] in wanted]
        first = named or first                              # a named relation the entity does not have changes nothing
    if kinds and first:
        led_to = [v for v in _edge_variants(entities, shapes, anchored=anchor_iris, reach=reach, kinds=kinds, drop=drop, skip=skip) if v in first]
        first = led_to or first                             # no edge to that kind from this entity: leave the choice open
    defs = {"rest": rest}
    if count and count.get("only"):                          # a bare count: exactly one edge, from the documents' side to the counted kind (plus the edge that joins a passage to its document, when it is needed)
        defs["first"] = {"oneOf": count["only"]}
        edges = {"oneOf": [{"type": "array", "items": [{"$ref": "#/$defs/first"}, *count.get("then", [])]}]}
    elif first:
        # llama-server ignores `prefixItems` when `items` is also present (checked with a probe: the model put a non-allowed value
        # first), so "the first edge uses the entity" is written as one fixed-length tuple per allowed length. $defs keeps the
        # grammar from repeating the whole edge list for every length.
        defs["first"] = {"oneOf": first}
        edges = {"oneOf": [{"type": "array", "items": [{"$ref": "#/$defs/first"}] + [{"$ref": "#/$defs/rest"}] * (n - 1)}
                           for n in range(1, MAX_EDGES + 1)]}
    else:
        edges = {"type": "array", "minItems": 1, "maxItems": MAX_EDGES, "items": {"$ref": "#/$defs/rest"}}
    var = {"type": "string", "enum": [*TYPED_VARS, *VALUE_VARS]}
    select = {"type": "array", "minItems": 1, "maxItems": 5, "items": {"type": "object", "required": ["var", "agg", "of"], "properties": {
        "var": var, "agg": {"type": "string", "enum": ["none", *AGGS]}, "of": {"type": "string", "enum": ["", *TYPED_VARS, *VALUE_VARS]}}}}
    if count:                                  # a how-many question has one column: a distinct count of the counted kind (fixed-length tuple, as for edges)
        one = {"type": "object", "required": ["var", "agg", "of"], "properties": {
            "var": {"enum": ["?count"]}, "agg": {"enum": ["count_distinct"]}, "of": {"enum": count["of"] or [*TYPED_VARS]}}}
        select = {"oneOf": [{"type": "array", "items": [one]}]}
    return {"type": "object", "required": ["edges", "select"], "$defs": defs, "properties": {
        "edges": edges,
        "select": select,
        # edges fixed and no number in the question: nothing to filter on
        "filters": {"type": "array", "maxItems": 0 if count and count.get("only") else 3, "items": {"type": "object", "required": ["var", "op", "value"], "properties": {
            "var": {"type": "string", "enum": VALUE_VARS}, "op": {"type": "string", "enum": OPS}, "value": {"type": "string"}}}},
        "order_by": {"type": "string", "enum": ["", *TYPED_VARS, *VALUE_VARS]}, "descending": {"type": "boolean"},
        "limit": {"type": "integer"}}}


def vocabulary(shapes: dict) -> str:
    """The allowed edges in words, for the prompt. Generated from the graph, so it follows the data."""
    lines = [f"Watson:{p}: {s} -> {o}" for (p, s, o), _ in sorted(shapes.items())]
    types = ", ".join(f"{v} is a {c}" for v, c in TYPED_VARS.items())
    return (f"Variables: {types}. Plain values: {', '.join(VALUE_VARS)}.\n"
            "Allowed edges (property: subject kind -> object kind; 'value' means a plain value):\n" + "\n".join(lines) +
            "\nrdfs:label links a thing to its ?name.")


def build_prompt(question: str, entities: list, shapes: dict) -> str:
    ents = "\n".join(f"- \"{e['text']}\" is Watson:{e['entity_id']} ({e['kind']})" for e in entities) or "- none found in the question"
    return (f"{vocabulary(shapes)}\n\nsentiment scores are 0 to 1 (Watson:sentimentScore). quality_score is a document's quality.\n\n{EXAMPLES}\n\n"
            f"Entities in the question (use these IRIs in your edges):\n{ents}\n\nQuestion: {question}\nPlan:")


# ---------------------------------------------------------------- plan -> SPARQL
def tidy(plan: dict, single: bool = False) -> list:
    """Mechanical clean-up that never changes what the plan asks. Returns what it did, for the UI.
    Drops repeated edges (small models loop) and renames an aggregate's result variable when it clashes with an edge variable."""
    notes, seen, kept = [], set(), []
    for e in plan.get("edges") or []:
        key = (e.get("s"), e.get("p"), e.get("o"))
        if key not in seen:
            seen.add(key)
            kept.append(e)
    if len(kept) != len(plan.get("edges") or []):
        notes.append(f"removed {len(plan['edges']) - len(kept)} repeated edge(s)")
    plan["edges"] = kept
    if single and (plan.get("order_by") or plan.get("limit")):           # one number: nothing to order or cut
        plan["order_by"], plan["descending"], plan["limit"] = "", False, 0
        notes.append("dropped the ordering and limit (a single number has none)")
    used = {t for e in kept for t in (e.get("s"), e.get("o")) if _VAR.match(t or "")}
    for c in plan.get("select") or []:
        if c.get("agg") not in (None, "none") and c.get("var") in used:
            if not c.get("of"):                                          # "count distinct ?document" with no 'of': it is the variable itself
                c["of"] = c["var"]
                notes.append(f"counted {c['var']} (the plan left 'of' empty)")
            new = c["var"] + "_result"
            if plan.get("order_by") == c["var"]:
                plan["order_by"] = new
            notes.append(f"renamed result {c['var']} to {new} (it was also an edge variable)")
            c["var"] = new
    return notes


def count_problem(plan: dict, count: dict, named: list) -> str | None:
    """What a how-many plan must satisfy (see count_setup), for the cases the grammar cannot enforce (no kind found, or the grammar
    was not used). Counting with no named entity counts what the documents the user may read cover, so the counted thing must be
    joined to a document or a passage; otherwise it would count catalog entities that no readable document mentions."""
    sel, terms = plan.get("select") or [], {t for e in plan.get("edges") or [] for t in (e.get("s"), e.get("o"))}
    if len(sel) != 1 or sel[0].get("agg") != "count_distinct":
        return "This question asks for one number. Select exactly one column: a count_distinct of the thing being counted, and nothing else."
    of = sel[0].get("of")
    if count.get("of") and of not in count["of"]:
        return f"The question counts {count['kind']}. Set 'of' to {' or '.join(count['of'])}."
    unwanted = sorted(t for t in terms if TYPED_VARS.get(t) in count["drop"])
    if unwanted:
        return f"{', '.join(unwanted)} is not needed to count this, and would narrow the count to things that have one. Leave it out."
    for e in plan.get("edges") or []:                                    # the document-level relation, where the passage-level one is the complete record
        if (e["p"].split(":", 1)[-1], TYPED_VARS.get(e["s"]), TYPED_VARS.get(e["o"])) in count["skip"]:
            return (f"{e['p']} is also available with ?chunk as the subject, which is the complete record. Use ?chunk instead of "
                    f"{e['s']}, so the count is not limited to what the document level holds.")
    uses = Counter(t for e in plan.get("edges") or [] for t in (e["s"], e["o"]))
    for v in sorted(t for t in uses if uses[t] == 1 and t in TYPED_VARS and t != of):                  # a variable in one edge only is a requirement, not a path
        if TYPED_VARS[v] not in COUNT_SCOPE and TYPED_VARS[v] not in count["mentioned"]:
            return (f"{v} is in one edge only, so it only requires the counted things to have one, and the question does not ask about it. "
                    f"Remove that edge: the count is of {of}.")
    if count["kind"] in STRUCTURAL_KINDS:                                    # documents, spaces: counted as they are, not through what they mention
        return None
    if not named and TYPED_VARS.get(of) not in COUNT_SCOPE and not any(TYPED_VARS.get(t) in COUNT_SCOPE for t in terms):
        return (f"Count what the documents the user may read cover: join {of} to a ?chunk or ?document with an edge such as mentions or covers, "
                f"so the count follows the user's access and does not include things no readable document mentions.")
    return None


def semantic_check(plan: dict, entities: list | None = None, numeric_values: set | None = None, count: dict | None = None) -> str | None:
    """The few things the grammar cannot guarantee. Messages are written for the model, which sees them on the next attempt."""
    edges, sel = plan.get("edges") or [], plan.get("select") or []
    terms = {t for e in edges for t in (e.get("s"), e.get("o"))}
    named = [e for e in entities or [] if e.get("entity_id")]
    filt = " ".join(str(f.get("value", "")).lower() for f in plan.get("filters") or [])
    if named and not any(f"Watson:{e['entity_id']}" in terms or e["label"].lower() in filt for e in named):
        e = named[0]
        return (f"The question is about {e['label']} (Watson:{e['entity_id']}) but no edge uses it. Use Watson:{e['entity_id']} "
                f"as the subject or the object of an edge so the answer is about it.")
    if not _connected(edges):
        return ("The edges are not all joined together: every edge must share a variable or entity with another edge, "
                "otherwise the query multiplies unrelated results.")
    bound = {t for t in terms if _VAR.match(t or "")}
    for f in plan.get("filters") or []:
        if f.get("var") not in bound:
            return f"The filter is on {f.get('var')} but no edge uses it. A filter can only test a value that an edge produces."
        if str(f.get("value", "")).startswith(("Watson:", "?")):
            return (f"The filter compares {f.get('var')} with '{f.get('value')}', which is an entity or variable, not a value. "
                    f"Put the entity in an edge instead, and use filters only for plain text or numbers.")
    for c in sel:
        var, agg, of = c.get("var"), c.get("agg") or "none", c.get("of") or ""
        if agg == "none":
            if var not in bound:
                return f"{var} is selected but no edge uses it."
            continue
        if of not in bound:
            return f"The aggregate for {var} needs 'of' to be a variable used in an edge, not '{of}'."
        if agg in NUMERIC_AGGS and numeric_values is not None and of not in numeric_values:
            return (f"{agg.upper()} needs a number, but {of} is not the value of a numeric property "
                    f"(for example Watson:sentimentScore or Watson:quality_score).")
    if len({c.get("var") for c in sel}) != len(sel):
        return "A column appears twice in the select list."
    return count_problem(plan, count, named) if count else None


def _connected(edges: list) -> bool:
    """True when the edges form one connected piece (a stray edge would make a cross product)."""
    if len(edges) <= 1:
        return True
    parent = {}

    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    for e in edges:
        parent[find(e["s"])] = find(e["o"])
    return len({find(e["s"]) for e in edges}) == 1


def numeric_value_vars(plan: dict, g: Graph) -> set:
    """Value variables that are the object of a property whose values in this graph are numbers."""
    numeric = set()
    for e in plan.get("edges") or []:
        o = e.get("o")
        if o not in VALUE_VARS:
            continue
        prop = URIRef(l2config.NS + (e.get("p") or "").split(":", 1)[-1])
        vals = [v for v in g.objects(None, prop)][:20]
        if vals and all(isinstance(v, Literal) and isinstance(v.toPython(), (int, float)) and not isinstance(v.toPython(), bool) for v in vals):
            numeric.add(o)
    return numeric


def render(plan: dict) -> tuple:
    """(SPARQL text without prefixes, error)."""
    edges, sel = plan.get("edges") or [], plan.get("select") or []
    if not edges or not sel:
        return "", "The plan needs at least one edge and one select column."
    lines, used = [], []
    for e in edges:
        lines.append(f"  {e['s']} {e['p']} {e['o']} .")
        used += [t for t in (e["s"], e["o"]) if t in TYPED_VARS]
    for v in dict.fromkeys(used):
        lines.insert(0, f"  {v} a Watson:{TYPED_VARS[v]} .")
    cols, groupable, has_agg, optional = [], [], False, []
    for c in sel:
        var, agg, of = c["var"], c.get("agg") or "none", c.get("of") or ""
        if agg == "none":
            cols.append(var)
            groupable.append(var)
            if var in TYPED_VARS:                                        # also show its name, so rows are readable
                label = var + "_label"
                prop = "Watson:documentTitle" if var == "?document" else "rdfs:label"
                optional.append(f"  OPTIONAL {{ {var} {prop} {label} . }}")
                cols.append(label)
                groupable.append(label)
        else:
            has_agg = True
            cols.append(f"({AGGS[agg].format(v=of)} AS {var})")
    if not has_agg:                                                     # show what the answer was reached through, so each row says how
        shown = {c["var"] for c in sel}
        next_to_answer = {t for e in edges for a, t in ((e["s"], e["o"]), (e["o"], e["s"])) if a in shown}      # directly linked to a selected variable
        in_edges = {v: sum(v in (e["s"], e["o"]) for e in edges) for v in used}
        for v in dict.fromkeys(used):
            if v in shown or v not in next_to_answer or in_edges[v] < 2 or TYPED_VARS[v] in ("DocumentChunk", "SentimentAssertion"):
                continue                                                # leaves, join nodes and side branches would only multiply rows
            prop = "Watson:documentTitle" if v == "?document" else "rdfs:label"
            optional.append(f"  OPTIONAL {{ {v} {prop} {v}_label . }}")
            cols += [v, v + "_label"]
    for f in plan.get("filters") or []:
        v, op, val = f["var"], f["op"], str(f.get("value", ""))
        if op == "contains":
            lines.append(f"  FILTER(CONTAINS(LCASE(STR({v})), {json.dumps(val.lower())}))")
        elif re.fullmatch(r"-?\d+(\.\d+)?", val):
            lines.append(f"  FILTER({v} {op} {val})")
        else:
            lines.append(f"  FILTER({v} {op} {json.dumps(val)})")
    q = "SELECT " + ("DISTINCT " if not has_agg else "") + " ".join(cols) + "\nWHERE {\n" + "\n".join(lines + optional) + "\n}"
    if has_agg and groupable:
        q += "\nGROUP BY " + " ".join(groupable)
    ob = (plan.get("order_by") or "").strip()
    if ob:
        known = {c["var"] for c in sel} | set(used) | {t for e in edges for t in (e["s"], e["o"])}
        if ob not in known:
            return "", f"order_by '{ob}' is not a variable of the query."
        q += f"\nORDER BY {'DESC' if plan.get('descending') else 'ASC'}({ob})"
    lim = plan.get("limit") or 0
    if isinstance(lim, int) and 0 < lim <= 200:
        q += f"\nLIMIT {lim}"
    return q, None


# ---------------------------------------------------------------- checking and running
def with_prefixes(body: str) -> str:
    return "\n".join(f"PREFIX {p}: <{u}>" for p, u in PREFIXES.items()) + "\n" + body.strip()


def check(body: str) -> tuple:
    """(full query text with prefixes, error or None)."""
    q = with_prefixes(body)
    if re.search(r"\bSERVICE\b|\bFROM\b|\bLOAD\b|\bINSERT\b|\bDELETE\b|\bDROP\b", body, re.I):
        return q, "Only a read-only SELECT on the local graph is allowed (no SERVICE, FROM or update keywords)."
    try:
        parsed = prepareQuery(q)
    except Exception as e:
        return q, f"Not valid SPARQL: {str(e).splitlines()[0][:240]}"
    if parsed.algebra.name != "SelectQuery":
        return q, "Only SELECT queries are allowed."
    if not re.search(r"\bLIMIT\s+\d+", body, re.I):
        q += f"\nLIMIT {config.SPARQL_MAX_ROWS}"
    return q, None


class _Deadline(Graph):
    """A view of the user's slice that stops query evaluation once the time is up. rdflib has no query timeout, and a thread
    cannot be killed, so a runaway join would keep burning CPU after we gave up on it. rdflib asks the graph for triples at every
    step of a join, so raising here ends the evaluation itself."""

    def __init__(self, base: Graph, seconds: float):
        super().__init__(store=base.store, identifier=base.identifier)
        self._until = time.time() + seconds

    def triples(self, triple):
        if time.time() > self._until:
            raise TimeoutError
        return super().triples(triple)


def execute(slice_graph: Graph, q: str) -> dict:
    try:
        cols, rows = sparql_runner._execute(_Deadline(slice_graph, EXEC_TIMEOUT_S), q)
    except TimeoutError:
        return {"error": f"The query ran longer than {EXEC_TIMEOUT_S} s and was stopped (probably edges that are not joined together)."}
    except Exception as e:
        return {"error": f"The query failed to run: {str(e)[:240]}"}
    return {"columns": cols, "rows": rows[:config.SPARQL_MAX_ROWS], "row_count": len(rows)}


# ---------------------------------------------------------------- the loop
def _edge_has_match(g: Graph, e: dict) -> bool:
    """Does any triple in the user's graph fit this edge, with variables as wildcards?"""
    def term(t):
        return URIRef(l2config.NS + t.split(":", 1)[1]) if t.startswith("Watson:") else None
    p = RDFS.label if e["p"] == "rdfs:label" else term(e["p"])
    return next(iter(g.triples((term(e["s"]), p, term(e["o"])))), None) is not None


def empty_feedback(plan: dict, entities: list, g: Graph) -> str:
    """Why a valid query returned nothing, from the graph itself: the edges that match no triple, and what does exist for the entity.
    Written for the model's next attempt."""
    dead = [f"{e['s']} {e['p']} {e['o']}" for e in plan.get("edges") or [] if not _edge_has_match(g, e)]
    if not dead:
        return ("The query is valid but returned no rows. Each edge matches something on its own, but not all together: "
                "check that the edges join through the same variables.")
    msg = "The query returned no rows. No triple in the user's graph matches: " + "; ".join(dead) + "."
    for ent in [e for e in entities if e.get("entity_id")][:2]:
        found = entity_edges(g, ent["entity_id"])
        if found:
            msg += f" The relations that exist for Watson:{ent['entity_id']} are: {describe_edges(found)}."
    return msg + " Use only relations that exist."


def _touches(prop: str, cls: str, shapes: dict) -> bool:
    """Does this relation have `cls` (the entity's kind) at one end? Read from the user's graph, or from the ontology's domain and range
    when the user's graph has no triple of it at all."""
    if any(p == prop and cls in (sc, oc) for (p, sc, oc) in shapes):
        return True
    o = kgmod.get().ontology()
    return any(kgmod.local(x) == cls for x in [*o.objects(W[prop], RDFS.domain), *o.objects(W[prop], RDFS.range)])


def relation_gap(entities: list, shapes: dict, graph: Graph, relations: set) -> dict | None:
    """The question names relations (in its words or by meaning) that apply DIRECTLY to its entity, and the entity has none of them in
    the part of the graph this user may read. Without this the plan would quietly use another relation and answer a different
    question ("competitors of X" answered with the brands co-mentioned with X). Returns {relations, entity, reason} or None.
    A relation that does not touch the entity's kind (forGeography for a brand) may still be reached in two hops: never a gap."""
    anchored = [e for e in entities[:2] if e.get("entity_id") and KIND_CLASS.get(e.get("kind"))]
    if not relations or not anchored:
        return None
    anc = _ancestors()
    for e in anchored:
        cls, have = KIND_CLASS[e["kind"]], {p for (p, _, _) in entity_edges(graph, e["entity_id"], anc)}
        for r in relations:
            if not _touches(r, cls, shapes) or r in have:
                return None
    e = anchored[0]
    ontology = kgmod.get().ontology()

    def plain(prop: str) -> str:                                     # the ontology's own label ('contains drug'), else the name in words
        lab = ontology.value(W[prop], RDFS.label)
        return str(lab) if lab is not None else re.sub(r"(?<!^)(?=[A-Z])", " ", prop).replace("_", " ").lower()
    words = ", ".join(plain(r) for r in sorted(relations))
    has = ", ".join(sorted({plain(p) for (p, _, _) in entity_edges(graph, e["entity_id"], anc)}))
    return {"relations": sorted(relations), "entity": e["entity_id"], "reason": (
        f"The part of the graph you may read has no '{words}' relation for {e['label']}, so there is nothing to query for it."
        + (f" What it does have: {has}." if has else ""))}


def generate(question: str, entities: list, slice_graph: Graph, on_attempt=None, terms: dict | None = None) -> dict:
    """{status: ok|empty|failed, sparql, plan, columns, rows, attempts: [{n, plan, sparql, error|note, seconds}]}.
    `terms` is semantic.match(...): everyday words matched to ontology terms by meaning (step 3). The exact-word rules are always
    applied too, and are the only ones used when the embedding model is unavailable."""
    shapes = edge_shapes(slice_graph)
    relations, kinds = named_relations(question, shapes), answer_kinds(question, entities)
    if terms and terms.get("available"):
        relations, kinds = relations | set(terms["relations_selected"]), kinds | set(terms["kinds_selected"])
    count = count_setup(question, entities, shapes, slice_graph)
    gap = relation_gap(entities, shapes, slice_graph, relations)
    if gap and count and count.get("route") and set(gap["relations"]) <= count["route"]:
        gap = None                                                   # 'documents associated with X' where no readable document links to X is a count of 0; another relation is still a gap
    if gap:                                                          # say so, instead of letting the model answer with another relation
        return {"status": "no_relation", "sparql": None, "columns": [], "rows": [], "attempts": [], "prompt": None,
                "reason": gap["reason"], "missing": gap["relations"]}
    prompt, schema = build_prompt(question, entities, shapes), plan_schema(entities, shapes, slice_graph, relations, kinds, count)
    attempts, last_ok, feedback = [], None, ""

    def record(att):
        attempts.append(att)
        if on_attempt:
            on_attempt(att)

    for n in range(1, config.SPARQL_ATTEMPTS + 1):
        t0 = time.time()
        try:
            # a new seed per attempt: at temperature 0 with one seed, a retry with similar feedback repeats the same plan
            reply = llm.chat(SYSTEM, prompt + feedback, max_tokens=380, schema=schema, seed=7 + n)
        except llm.LLMUnavailable as e:
            record({"n": n, "error": str(e), "unavailable": True})
            return {"status": "failed", "sparql": None, "columns": [], "rows": [], "attempts": attempts, "prompt": prompt, "reason": str(e)}
        att = {"n": n, "plan": reply["text"], "seconds": round(time.time() - t0, 1), "output_tokens": reply["output_tokens"]}
        err, plan = None, None
        try:
            plan = json.loads(reply["text"])
        except json.JSONDecodeError as e:
            err = f"The reply was not valid JSON ({e.msg})."
        if err is None:
            att["tidied"] = tidy(plan, single=bool(count))
            err = semantic_check(plan, entities, numeric_value_vars(plan, slice_graph), count)
        if err is None:
            body, err = render(plan)
            if err is None:
                q, err = check(body)
                att["sparql"] = q
        if err is None:
            res = execute(slice_graph, q)
            err = res.get("error")
            if err is None:
                att["row_count"] = res["row_count"]
                if res["row_count"] > 0 or n == config.SPARQL_ATTEMPTS:
                    record(att)
                    return {"status": "ok" if res["row_count"] else "empty", "sparql": q, "plan": plan, "columns": res["columns"],
                            "rows": res["rows"], "row_count": res["row_count"], "attempts": attempts, "prompt": prompt,
                            "counted": count["kind"] if count else None}
                last_ok = {"sparql": q, "plan": plan, **res}
                err = empty_feedback(plan, entities, slice_graph)
                att["note"] = "valid, no rows"
        att["error"] = err
        record(att)
        feedback = f"\n\nYour previous plan:\n{reply['text']}\nProblem: {err}\nWrite a corrected plan."
    if last_ok:
        return {"status": "empty", "sparql": last_ok["sparql"], "plan": last_ok["plan"], "columns": last_ok["columns"], "rows": [],
                "row_count": 0, "attempts": attempts, "prompt": prompt}
    return {"status": "failed", "sparql": None, "columns": [], "rows": [], "attempts": attempts, "prompt": prompt,
            "reason": f"No valid query after {len(attempts)} attempts: " + (attempts[-1].get("error") or "")}
