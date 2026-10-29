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


def _edge_variants(entities: list, shapes: dict, anchored: list | None = None, reach: dict | None = None) -> list:
    """One schema per allowed edge shape. With `anchored` (entity IRIs), a slot that may hold an entity is fixed to one of them.
    With `reach` ({entity IRI: keys of entity_edges}), an entity is only offered in an edge that has triples for it in the user's
    graph, so the first edge cannot be one that is guaranteed to return nothing (for example co-mentions of a brand that has none)."""
    out = []

    def reaches(iri, prop, direction, kind):
        return reach is None or (prop, direction, kind) in reach.get(iri, ())

    def slot(options):
        return {"enum": options}

    for (prop, sc, oc), _ in sorted(shapes.items()):
        s_enum, o_enum = _vars_for(sc, entities), (VALUE_VARS if oc == "value" else _vars_for(oc, entities))
        if not (s_enum and o_enum):
            continue
        forms = [(s_enum, o_enum)]
        if anchored is not None:
            s_a = [x for x in s_enum if x in anchored and reaches(x, prop, "out", oc)]
            o_a = [x for x in o_enum if x in anchored and reaches(x, prop, "in", sc)]
            s_v = [x for x in s_enum if x not in anchored]
            o_v = [x for x in o_enum if x not in anchored]
            forms = ([(s_a, o_v)] if s_a and o_v else []) + ([(s_v, o_a)] if s_v and o_a else [])
        for se, oe in forms:
            out.append({"type": "object", "required": ["s", "p", "o"], "properties": {"s": slot(se), "p": {"const": f"Watson:{prop}"}, "o": slot(oe)}})
    for cls_name in sorted(CANON - {"MarketResearchDocument", "DocumentChunk", "SentimentAssertion"}):       # names of reference things
        s_enum = _vars_for(cls_name, entities)
        if anchored is not None:
            s_enum = [x for x in s_enum if x in anchored]
        if s_enum:
            out.append({"type": "object", "required": ["s", "p", "o"], "properties": {
                "s": slot(s_enum), "p": {"const": "rdfs:label"}, "o": slot(["?name"])}})
    return out


def plan_schema(entities: list, shapes: dict, graph: Graph | None = None) -> dict:
    """The JSON schema the reply is decoded under. Each edge is one of the shapes that exist in the user's graph, so a property
    can only be used with subjects and objects of the right kind. If the question names an entity, the FIRST edge must use it,
    and (when the graph is given) only through a relation that has triples for that entity in the user's graph."""
    anchor_iris = [f"Watson:{e['entity_id']}" for e in entities[:2]]
    reach = {f"Watson:{e['entity_id']}": set(entity_edges(graph, e["entity_id"])) for e in entities[:2]} if graph is not None else None
    rest = {"oneOf": _edge_variants(entities, shapes)}
    first = _edge_variants(entities, shapes, anchored=anchor_iris, reach=reach) if anchor_iris else []
    defs = {"rest": rest}
    if first:
        # llama-server ignores `prefixItems` when `items` is also present (checked with a probe: the model put a non-allowed value
        # first), so "the first edge uses the entity" is written as one fixed-length tuple per allowed length. $defs keeps the
        # grammar from repeating the whole edge list for every length.
        defs["first"] = {"oneOf": first}
        edges = {"oneOf": [{"type": "array", "items": [{"$ref": "#/$defs/first"}] + [{"$ref": "#/$defs/rest"}] * (n - 1)}
                           for n in range(1, MAX_EDGES + 1)]}
    else:
        edges = {"type": "array", "minItems": 1, "maxItems": MAX_EDGES, "items": {"$ref": "#/$defs/rest"}}
    var = {"type": "string", "enum": [*TYPED_VARS, *VALUE_VARS]}
    return {"type": "object", "required": ["edges", "select"], "$defs": defs, "properties": {
        "edges": edges,
        "select": {"type": "array", "minItems": 1, "maxItems": 5, "items": {"type": "object", "required": ["var", "agg", "of"], "properties": {
            "var": var, "agg": {"type": "string", "enum": ["none", *AGGS]}, "of": {"type": "string", "enum": ["", *TYPED_VARS, *VALUE_VARS]}}}},
        "filters": {"type": "array", "maxItems": 3, "items": {"type": "object", "required": ["var", "op", "value"], "properties": {
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
def tidy(plan: dict) -> list:
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
    used = {t for e in kept for t in (e.get("s"), e.get("o")) if _VAR.match(t or "")}
    for c in plan.get("select") or []:
        if c.get("agg") not in (None, "none") and c.get("var") in used:
            new = c["var"] + "_result"
            if plan.get("order_by") == c["var"]:
                plan["order_by"] = new
            notes.append(f"renamed result {c['var']} to {new} (it was also an edge variable)")
            c["var"] = new
    return notes


def semantic_check(plan: dict, entities: list | None = None, numeric_values: set | None = None) -> str | None:
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
    return None


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


def generate(question: str, entities: list, slice_graph: Graph, on_attempt=None) -> dict:
    """{status: ok|empty|failed, sparql, plan, columns, rows, attempts: [{n, plan, sparql, error|note, seconds}]}."""
    shapes = edge_shapes(slice_graph)
    prompt, schema = build_prompt(question, entities, shapes), plan_schema(entities, shapes, slice_graph)
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
            att["tidied"] = tidy(plan)
            err = semantic_check(plan, entities, numeric_value_vars(plan, slice_graph))
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
                            "rows": res["rows"], "row_count": res["row_count"], "attempts": attempts, "prompt": prompt}
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
