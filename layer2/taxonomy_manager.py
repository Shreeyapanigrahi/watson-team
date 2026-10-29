"""Controlled vocabularies and their versions (brief 5.3 stage 5, and 'taxonomy_manager.py').

Layer 1 seeds taxonomy_store.json (TherapeuticArea, StudyType). Layer 2 owns every later edit: adding or deprecating a
term bumps that taxonomy's version and writes a TAXONOMY_UPDATE audit event. Documents record the version they were
validated against (Watson:taxonomyVersion), so an old triple can be traced to the vocabulary that was in force.

validate_document() is stage 5. It runs BEFORE any triple is written: a document whose study type or therapeutic area
is not in the current taxonomy, or whose area has no row in the data catalog, writes nothing.
"""
from typing import Optional

from layer1 import audit_log, config as l1config, metadata_extractor
from layer1.errors import NotFoundError
from layer1.jsonstore import LOCK, read_json, write_json

from .catalog.catalog_store import load


class TaxonomyError(ValueError):
    """The document does not conform to the controlled vocabulary."""


def _path():
    return l1config.store_path("taxonomy_store.json")


def get_all() -> dict:
    metadata_extractor.ensure_taxonomy()
    return read_json(_path(), {})


def get(name: str) -> dict:
    tax = get_all()
    if name not in tax:
        raise NotFoundError(f"No taxonomy '{name}'. Known: {', '.join(tax)}")
    return tax[name]


def version(name: str) -> int:
    return get(name)["version"]


def labels(name: str, include_deprecated: bool = False) -> set:
    return {t["label"] for t in get(name)["terms"] if include_deprecated or not t["deprecated"]}


def _next_id(tax: dict, prefix: str) -> str:
    nums = [int(t["id"].rsplit("_", 1)[1]) for t in tax["terms"] if t["id"].rsplit("_", 1)[-1].isdigit()]
    return f"{prefix}_{max(nums, default=0) + 1:02d}"


def add_term(name: str, label: str, actor: str, parent_id: Optional[str] = None) -> dict:
    label = label.strip()
    if not label:
        raise TaxonomyError("A term needs a label")
    with LOCK:
        all_tax = get_all()
        tax = all_tax[name]
        if label.lower() in {t["label"].lower() for t in tax["terms"]}:
            raise TaxonomyError(f"'{label}' is already in {name}")
        if parent_id and parent_id not in {t["id"] for t in tax["terms"]}:
            raise TaxonomyError(f"Parent '{parent_id}' is not in {name}")
        prefix = tax["terms"][0]["id"].rsplit("_", 1)[0] if tax["terms"] else name[:2].upper()
        term = {"id": _next_id(tax, prefix), "label": label, "parent_id": parent_id, "deprecated": False}
        tax["terms"].append(term)
        tax["version"] += 1
        write_json(_path(), all_tax)
    audit_log.log_event("TAXONOMY_UPDATE", None, {"taxonomy": name, "action": "add", "term": term, "version": tax["version"], "actor": actor})
    return {"taxonomy": name, "version": tax["version"], "term": term}


def deprecate_term(name: str, term_id: str, actor: str) -> dict:
    with LOCK:
        all_tax = get_all()
        tax = all_tax[name]
        term = next((t for t in tax["terms"] if t["id"] == term_id), None)
        if term is None:
            raise NotFoundError(f"No term '{term_id}' in {name}")
        if term["deprecated"]:
            raise TaxonomyError(f"{term_id} is already deprecated")
        term["deprecated"] = True
        tax["version"] += 1
        write_json(_path(), all_tax)
    audit_log.log_event("TAXONOMY_UPDATE", None, {"taxonomy": name, "action": "deprecate", "term": term, "version": tax["version"], "actor": actor})
    return {"taxonomy": name, "version": tax["version"], "term": term}


def validate_document(record: dict) -> dict:
    """Stage 5. Raises TaxonomyError listing every problem; returns the taxonomy versions it checked against."""
    meta, problems, cat = record["metadata"], [], load()
    study, ta = meta.get("study_type"), meta.get("therapeutic_area")
    if study not in labels("StudyType"):
        problems.append(f"study_type '{study}' is not an active term of the StudyType taxonomy")
    if ta not in labels("TherapeuticArea"):
        problems.append(f"therapeutic_area '{ta}' is not an active term of the TherapeuticArea taxonomy")
    elif ta not in {e["label"] for e in cat.instances.get("TherapeuticArea", [])}:
        problems.append(f"therapeutic_area '{ta}' is in the taxonomy but has no row in the data catalog (catalog {cat.version}); add it to the catalog first")
    if not record.get("expiry_date"):
        problems.append("expiry_date is missing")
    if not (record.get("access") or {}).get("sensitivity_level"):
        problems.append("sensitivity_level is missing")
    if problems:
        raise TaxonomyError("; ".join(problems))
    return {"TherapeuticArea": version("TherapeuticArea"), "StudyType": version("StudyType")}
