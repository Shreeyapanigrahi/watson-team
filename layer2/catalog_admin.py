"""Decisions on provisional entities (the human-in-the-loop part of entity resolution, brief 5.3 stage 2).

A provisional entity is a name a document used that the data catalog does not know. A SPOC or ADMIN decides:
  alias    it is a new spelling of an entity we already have  -> added to that entity's aliases
  new      it is a genuinely new brand, drug, drug class or indication -> a new reference row (with the relations the
           catalog needs: a brand needs a drug, an area and at least one indication)
  reject   it is noise (e.g. a heading that looks like 'Brand (inn)')  -> remembered, never proposed again

The catalog stays the single source of truth, so an approval edits layer2/catalog/watson_extension.json (the file that
holds OUR additions; the three Databricks files are never touched), rebuilds pharma_data_catalog.json, and re-enriches
the documents that mentioned the name. The rebuild is checked in a temporary folder first: the build refuses a
reference to a column or entity that does not exist, and if it refuses nothing is changed.

Every decision is written to the extension's change_log and to the audit log (TAXONOMY_UPDATE).
"""
import copy
import os
import re
import tempfile
from pathlib import Path
from typing import Optional

from layer1 import audit_log
from layer1.errors import NotFoundError
from layer1.jsonstore import LOCK, read_json, write_json

from . import config, enrichment_pipeline as pipeline, entity_extractor, entity_resolver, kg as kgmod, populate_graph
from .catalog import build_catalog
from .catalog import catalog_store

NAME_COLUMN = {"Brand": "commercial.brand.brand_name", "Drug": "product.drug_product.product_name",
               "DrugClass": "product.therapeutic_area.therapeutic_class", "Indication": "product.indication.indication_name"}
DECIDABLE_KINDS = tuple(NAME_COLUMN)


class DecisionError(ValueError):
    pass


def _slug(label: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_")


def _ids(ext: dict, kind: str) -> set:
    return {e["id"] for e in ext["instances"].get(kind, [])}


def _check_refs(ext: dict, kind: str, fields: dict) -> dict:
    rel = {}
    need = {"Brand": (("drug", "Drug", True), ("therapeutic_area", "TherapeuticArea", True), ("indications", "Indication", True)),
            "Drug": (("drug_class", "DrugClass", False),),
            "Indication": (("therapeutic_area", "TherapeuticArea", True), ("parent", "Indication", False)),
            "DrugClass": ()}[kind]
    for key, target_kind, required in need:
        v = fields.get(key)
        if v in (None, "", []):
            if required:
                raise DecisionError(f"A new {kind} needs '{key}'")
            continue
        for item in (v if isinstance(v, list) else [v]):
            if item not in _ids(ext, target_kind):
                raise DecisionError(f"'{item}' is not a {target_kind} in the catalog")
        rel[key] = v
    return rel


def _apply(ext: dict, entry: dict, decision: dict, actor: str) -> dict:
    kind = entry["kind"]
    if kind not in DECIDABLE_KINDS:
        raise DecisionError(f"Entities of kind {kind} cannot be approved here")
    text = entry["label"]
    if decision["mode"] == "alias":
        target = next((e for e in ext["instances"][kind] if e["id"] == decision.get("target_id")), None)
        if target is None:
            raise DecisionError(f"'{decision.get('target_id')}' is not a {kind} in the catalog")
        aliases = target.setdefault("aliases", [])
        if text not in aliases and text != target["label"]:
            aliases.append(text)
        resolved = target["id"]
    elif decision["mode"] == "new":
        new_id = f"{kind}_{_slug(text)}"
        if new_id in _ids(ext, kind):
            raise DecisionError(f"{new_id} already exists. Use 'alias' to map '{text}' to it")
        inst = {"id": new_id, "label": text, "aliases": [], "attributes": {NAME_COLUMN[kind]: text},
                "relations": _check_refs(ext, kind, decision.get("fields") or {})}
        if kind == "Indication":
            inst["grouping"] = False
            inst["relations"].setdefault("parent", None)
        ext["instances"][kind].append(inst)
        resolved = new_id
    else:
        raise DecisionError("mode must be 'alias' or 'new'")
    ext["change_log"].append({"time": audit_log.now_iso(), "actor": actor, "action": f"approve_{decision['mode']}", "provisional_id": entry["id"],
                              "text": text, "kind": kind, "entity": resolved})
    ext["version"] = int(ext.get("version", 1)) + 1
    return {"entity": resolved}


def approve(provisional_id: str, decision: dict, actor: str) -> dict:
    with LOCK:
        store = entity_resolver.load_store()
        entry = store["entities"].get(provisional_id)
        if entry is None:
            raise NotFoundError(f"No provisional entity '{provisional_id}'")
        if entry["status"] != "PENDING":
            raise DecisionError(f"{provisional_id} is already {entry['status']}")
        ext = copy.deepcopy(read_json(config.EXTENSION_JSON, None))
        outcome = _apply(ext, entry, decision, actor)

        # build the new catalog in a temp folder first; the build raises if anything refers to something that is not there
        with tempfile.TemporaryDirectory() as tmp:
            ext_tmp, out_tmp = Path(tmp) / "watson_extension.json", Path(tmp) / "catalog.json"
            write_json(ext_tmp, ext)
            try:
                build_catalog.build(extension_json=ext_tmp, out_path=out_tmp)
            except build_catalog.CatalogBuildError as e:
                raise DecisionError(f"The catalog would be inconsistent, nothing was changed: {e}") from e
            os.replace(ext_tmp, config.EXTENSION_JSON)
            os.replace(out_tmp, config.CATALOG_JSON)
        catalog_store._load.cache_clear()
        entity_extractor._index_for.cache_clear()
        cat = catalog_store.load()
        store["entities"][provisional_id].update(status="APPROVED", decided_by=actor, decided_at=audit_log.now_iso(),
                                                 resolved_to=outcome["entity"], note=decision.get("note"))
        entity_resolver._save(store)
    audit_log.log_event("TAXONOMY_UPDATE", None, {"action": f"catalog_{decision['mode']}", "text": entry["label"], "kind": entry["kind"],
                                                   "entity": outcome["entity"], "catalog_version": cat.version, "actor": actor})
    populate_graph.seed(kgmod.get(), cat)                          # additive: the new individual enters the graph
    re_enriched = {}
    for doc in entry["documents"]:                                  # the name now resolves in stage 1, so re-run the documents
        re_enriched[doc] = pipeline.run_now(doc, actor=actor)["state"]
    return {"provisional_id": provisional_id, "status": "APPROVED", "resolved_to": outcome["entity"], "catalog_version": cat.version,
            "re_enriched": re_enriched}


def reject(provisional_id: str, actor: str, note: Optional[str] = None) -> dict:
    with LOCK:
        store = entity_resolver.load_store()
        entry = store["entities"].get(provisional_id)
        if entry is None:
            raise NotFoundError(f"No provisional entity '{provisional_id}'")
        if entry["status"] != "PENDING":
            raise DecisionError(f"{provisional_id} is already {entry['status']}")
        entry.update(status="REJECTED", decided_by=actor, decided_at=audit_log.now_iso(), note=note)
        entity_resolver._save(store)
    kg = kgmod.get()
    with kg.write() as g:                                           # take its triples out so no orphan stays in the graph
        node = kgmod.uri(provisional_id)
        g.remove((None, kgmod.W.mentionsProvisional, node))
        g.remove((node, None, None))
    audit_log.log_event("TAXONOMY_UPDATE", None, {"action": "provisional_rejected", "text": entry["label"], "kind": entry["kind"], "actor": actor, "note": note})
    return {"provisional_id": provisional_id, "status": "REJECTED"}
