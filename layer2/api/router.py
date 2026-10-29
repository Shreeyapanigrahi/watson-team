"""Layer 2 HTTP API, mounted by layer1/api/main.py under /api/l2.

build_router(current_user) takes Layer 1's sign-in dependency, so this module never imports Layer 1's app (no cycle) and
every endpoint here uses exactly the same users, tokens and ABAC engine as Layer 1.

Who may do what (layer1/abac_engine.py ROLE_ACTIONS): anyone signed in may browse the catalog, the ontology and the
entities, explore the graph and search, but always over what their own attributes allow. SPOC and ADMIN may re-run
enrichment, run the SHACL check, decide on provisional entities and edit taxonomies. Only ADMIN may run SPARQL
(the results are not filtered per user).
"""
import time
from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from layer1 import abac_engine as abac, audit_log, lifecycle_manager as lm
from layer1.errors import NotFoundError
from layer1.jsonstore import read_json, write_json

from .. import (catalog_admin, catalog_view, config, enrichment_pipeline as pipeline, entity_resolver, graph_view, kg as kgmod,
                ontology_view, search as search_mod, shacl_validator, sparql_runner, taxonomy_manager, telemetry, vector_index)
from ..catalog.catalog_store import load


class SearchBody(BaseModel):
    query: str
    k: int = 8


class DecisionBody(BaseModel):
    mode: str                                   # alias | new
    target_id: Optional[str] = None             # alias: the entity the name is another spelling of
    fields: Optional[dict] = None               # new: relations the catalog needs (drug, therapeutic_area, indications, ...)
    note: Optional[str] = None


class RejectBody(BaseModel):
    note: Optional[str] = None


class TermBody(BaseModel):
    label: str
    parent_id: Optional[str] = None


class SparqlBody(BaseModel):
    sparql: str


class RunBody(BaseModel):
    params: Optional[dict] = None
    save: bool = False


def build_router(current_user) -> APIRouter:
    r = APIRouter(prefix="/api/l2", tags=["layer2"])

    # ------------------------------------------------------------ overview
    @r.get("/status")
    def status(user=Depends(current_user)):
        cat, kg = load(), kgmod.get()
        records = graph_view.visible_records(user)
        chunks = graph_view.visible_chunks(user, records=records)
        privileged = user["role"] in ("SPOC", "ADMIN")
        st = pipeline.all_status()
        shown = [u for u in st if abac.view_level(user, lm.get_record(u)) == "full"] if st else []
        by_state = {}
        for u in shown:
            by_state[st[u]["state"]] = by_state.get(st[u]["state"], 0) + 1
        active_unenriched = [x["document_uri"] for x in lm.list_documents("ACTIVE")
                             if abac.view_level(user, x) == "full" and st.get(x["document_uri"], {}).get("state") != "COMPLETE"]
        out = {"catalog_version": cat.version, "embedding_model": config.EMBEDDING_NAME, "readable_documents": len(records),
               "readable_chunks": len(chunks), "enrichment_by_state": by_state, "active_not_enriched": active_unenriched,
               "pending_jobs": pipeline.pending_jobs(), "spaces": len(cat.knowledge_spaces),
               "entities": {k: len(cat.instances.get(k, [])) for k in graph_view.KINDS},
               "catalog_counts": cat.meta["counts"] | {"extension_tables": cat.meta["extension"]["extension_tables"]}}
        if privileged:
            out.update(triples=len(kg.g), vectors=vector_index.get().size(), provisional_pending=len(entity_resolver.list_provisional("PENDING")),
                       last_validation=read_json(config.store_path("shacl_last.json"), None))
        return out

    # ------------------------------------------------------------ data catalog (schema and reference rows: readable by every role)
    @r.get("/catalog/summary")
    def catalog_summary(user=Depends(current_user)):
        abac.authorize(user, "view_catalog")
        return catalog_view.summary()

    @r.get("/catalog/tables")
    def catalog_tables(domain: Optional[str] = None, q: Optional[str] = None, watson_only: bool = False, user=Depends(current_user)):
        abac.authorize(user, "view_catalog")
        rows = catalog_view.tables(domain, q, watson_only)
        return {"tables": rows[:400], "count": len(rows), "truncated": len(rows) > 400}

    @r.get("/catalog/table")
    def catalog_table(fqn: str, user=Depends(current_user)):
        abac.authorize(user, "view_catalog")
        return catalog_view.table(fqn)

    @r.get("/catalog/search")
    def catalog_search(q: str, kind: Optional[str] = None, domain: Optional[str] = None, user=Depends(current_user)):
        abac.authorize(user, "view_catalog")
        return {"hits": catalog_view.search(q, kind, domain)}

    @r.get("/catalog/mappings")
    def catalog_mappings(user=Depends(current_user)):
        abac.authorize(user, "view_catalog")
        return catalog_view.mappings()

    # ------------------------------------------------------------ ontology
    @r.get("/ontology")
    def ontology(user=Depends(current_user)):
        return ontology_view.describe()

    # ------------------------------------------------------------ entities and the graph (ABAC-filtered)
    @r.get("/entities")
    def entity_list(kind: Optional[str] = None, q: Optional[str] = None, user=Depends(current_user)):
        rows = graph_view.entities(user, kind, q)
        return {"entities": rows, "count": len(rows)}

    @r.get("/entities/{entity_id}")
    def entity(entity_id: str, user=Depends(current_user)):
        return graph_view.entity_detail(user, entity_id)

    @r.get("/graph")
    def graph(mode: str = Query("knowledge", pattern="^(knowledge|reference|entity)$"), space: Optional[str] = None,
              entity: Optional[str] = None, limit: int = Query(80, ge=1, le=200),
              documents: Optional[str] = None, brand: Optional[str] = None, indication: Optional[str] = None, drug: Optional[str] = None,
              area: Optional[str] = None, geography: Optional[str] = None, relation: Optional[str] = None, user=Depends(current_user)):
        """Filters are comma-separated ids ("Brand_opdivra,Brand_keytrava"); values of one filter are alternatives, different filters all apply."""
        given = {"documents": documents, "brand": brand, "indication": indication, "drug": drug, "area": area, "geography": geography,
                 "relation": relation}
        filters = {k: [x.strip() for x in v.split(",")] for k, v in given.items() if v}
        return graph_view.graph(user, mode, space, entity, limit, filters)

    @r.get("/graph/filters")
    def graph_filters(space: Optional[str] = None, user=Depends(current_user)):
        return graph_view.graph_filters(user, space)

    @r.get("/spaces")
    def spaces(user=Depends(current_user)):
        return {"spaces": graph_view.spaces(user)}

    # ------------------------------------------------------------ one document's knowledge, enrichment
    @r.get("/documents/{uri}/knowledge")
    def document_knowledge(uri: str, user=Depends(current_user)):
        return graph_view.document_knowledge(user, uri)

    @r.get("/documents/{uri}/facts")
    def document_facts(uri: str, user=Depends(current_user)):
        return graph_view.document_facts(user, uri)

    @r.post("/documents/{uri}/enrich")
    def enrich(uri: str, user=Depends(current_user)):
        rec = lm.get_record(uri)
        abac.authorize(user, "enrich", rec)
        if abac.view_level(user, rec) != "full":
            abac.deny(user, "enrich", "document is outside this user's access attributes", uri)
        return pipeline.run_now(uri, actor=user["user_id"])

    @r.get("/enrichment")
    def enrichment(user=Depends(current_user)):
        st, rows, cat = pipeline.all_status(), [], load()
        for rec in lm.list_documents("ACTIVE"):
            if abac.view_level(user, rec) != "full":
                continue
            s = st.get(rec["document_uri"]) or {}
            rows.append({"document_uri": rec["document_uri"], "title": rec["metadata"].get("extracted_title"), "state": s.get("state", "NOT_ENRICHED"),
                         "stage": s.get("stage"), "error": s.get("error"), "finished": s.get("finished"), "triples": s.get("triples"),
                         "chunks": s.get("chunks"), "mentions": s.get("mentions"), "sentiments": s.get("sentiments"),
                         "stale": bool(s.get("catalog_version") and s["catalog_version"] != cat.version)})
        return {"documents": rows, "catalog_version": cat.version}

    # ------------------------------------------------------------ provisional entities (human in the loop)
    @r.get("/provisional")
    def provisional(status: Optional[str] = None, user=Depends(current_user)):
        abac.authorize(user, "manage_entities")
        return {"entities": entity_resolver.list_provisional(status)}

    @r.post("/provisional/{pid}/approve")
    def approve(pid: str, body: DecisionBody, user=Depends(current_user)):
        abac.authorize(user, "manage_entities")
        return catalog_admin.approve(pid, body.model_dump(), user["user_id"])

    @r.post("/provisional/{pid}/reject")
    def reject(pid: str, body: RejectBody = RejectBody(), user=Depends(current_user)):
        abac.authorize(user, "manage_entities")
        return catalog_admin.reject(pid, user["user_id"], body.note)

    # ------------------------------------------------------------ taxonomies
    @r.get("/taxonomy")
    def taxonomy(user=Depends(current_user)):
        return {"taxonomies": taxonomy_manager.get_all()}

    @r.post("/taxonomy/{name}/terms")
    def add_term(name: str, body: TermBody, user=Depends(current_user)):
        abac.authorize(user, "edit_taxonomy")
        return taxonomy_manager.add_term(name, body.label, user["user_id"], body.parent_id)

    @r.post("/taxonomy/{name}/terms/{term_id}/deprecate")
    def deprecate(name: str, term_id: str, user=Depends(current_user)):
        abac.authorize(user, "edit_taxonomy")
        return taxonomy_manager.deprecate_term(name, term_id, user["user_id"])

    # ------------------------------------------------------------ SHACL
    @r.post("/validate")
    def validate(user=Depends(current_user)):
        abac.authorize(user, "validate_graph")
        t0 = time.time()
        result = shacl_validator.run()
        result["violations"] = result["violations"][:300]
        result.update(ran_at=audit_log.now_iso(), ran_by=user["user_id"], seconds=round(time.time() - t0, 2), catalog_version=load().version)
        write_json(config.store_path("shacl_last.json"), result)
        return result

    @r.get("/validate/last")
    def validate_last(user=Depends(current_user)):
        abac.authorize(user, "validate_graph")
        return read_json(config.store_path("shacl_last.json"), None) or {"ran_at": None}

    # ------------------------------------------------------------ SPARQL (ADMIN: results are not filtered per user)
    @r.get("/queries")
    def queries(user=Depends(current_user)):
        abac.authorize(user, "run_queries")
        return {"queries": sparql_runner.list_queries()}

    @r.post("/queries/{qid}/run")
    def run_query(qid: str, body: RunBody = RunBody(), user=Depends(current_user)):
        abac.authorize(user, "run_queries")
        try:
            res = sparql_runner.run(qid, body.params)
        except KeyError as e:
            raise NotFoundError(str(e)) from e
        if body.save:
            sparql_runner.save([res])
        return res

    @r.post("/queries/custom")
    def custom_query(body: SparqlBody, user=Depends(current_user)):
        abac.authorize(user, "run_queries")
        return sparql_runner.run_custom(body.sparql)

    @r.get("/telemetry")
    def telemetry_summary(user=Depends(current_user)):
        abac.authorize(user, "run_queries")
        ev = telemetry.events()
        by = {}
        for e in ev:
            by[e["role"]] = by.get(e["role"], 0) + 1
        return {"events": len(ev), "by_role": by}

    # ------------------------------------------------------------ semantic search
    @r.post("/search")
    def search(body: SearchBody, user=Depends(current_user)):
        return search_mod.search(user, body.query, body.k)

    return r
