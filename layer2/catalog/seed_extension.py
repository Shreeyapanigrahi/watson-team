"""Bootstrap layer2/catalog/watson_extension.json: our additions to the data catalog.

  python -m layer2.catalog.seed_extension            # refuses to overwrite an existing file
  python -m layer2.catalog.seed_extension --force    # start over (loses edits, e.g. approved provisional entities)

The Databricks model describes tables and columns. It holds no brands, indications or countries,
and nothing about market-research documents. This file adds, in the same shape as the model:
  * instances: our reference rows (brands, drugs, indications, areas, countries), keyed by real catalog columns
  * domains:   the 'pmr' tables Watson needs (documents, chunks, sentiment, spaces, lineage), marked as extension
  * grounding: each Watson ontology term and the catalog table or column it is grounded in
  * knowledge_spaces: the curated spaces and the rule that decides which documents belong
After this runs once, the JSON file is the source of truth. It is edited only through the catalog
admin code (approved provisional entities), never by re-running this script.
"""
import argparse
import json
import re
from datetime import datetime, timezone

from layer1 import vocab
from .. import config
from .lookup_alignments import PARENTS

CAT = "pharmaceuticals_ecm"


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")


def _instances() -> dict:
    inst = {"Brand": [], "Drug": [], "DrugClass": [], "Indication": [], "TherapeuticArea": [], "Geography": []}
    for ta in vocab.THERAPEUTIC_AREAS:
        inst["TherapeuticArea"].append({"id": f"TA_{slug(ta)}", "label": ta,
                                        "attributes": {"product.therapeutic_area.area_name": ta}, "relations": {}})
    # indications: grouping parents first, then the corpus indications
    for parent, kids in PARENTS.items():
        ta = vocab.INDICATIONS[kids[0]]["ta"]
        inst["Indication"].append({
            "id": f"Indication_{slug(parent)}", "label": parent, "grouping": True,
            "attributes": {"product.indication.indication_name": parent},
            "relations": {"therapeutic_area": f"TA_{slug(ta)}", "parent": None}})
    parent_of = {k: p for p, kids in PARENTS.items() for k in kids}
    for name, v in vocab.INDICATIONS.items():
        parent = parent_of.get(name)
        if name in ("EGFR-mutant NSCLC", "KRAS G12C-mutant NSCLC"):
            parent = "non-small cell lung cancer"          # a subtype of NSCLC
        inst["Indication"].append({
            "id": f"Indication_{slug(name)}", "label": name, "aliases": v["aliases"],
            "attributes": {"product.indication.indication_name": name},
            "relations": {"therapeutic_area": f"TA_{slug(v['ta'])}",
                          "parent": f"Indication_{slug(parent)}" if parent else None}})
    for code, g in vocab.GEOGRAPHIES.items():
        inst["Geography"].append({
            "id": f"Geo_{code}", "label": g["name"], "kind": "country", "aliases": [code] if code != "US" else ["USA", "U.S."],
            "attributes": {"masterdata.country.country_name": g["name"], "masterdata.country.iso_alpha_2_code": code,
                           "masterdata.country.region": g["region"],
                           "masterdata.country.reimbursement_framework_type": g["payer_type"],
                           "masterdata.country.regulatory_authority_name": g["regulatory_body"]},
            "relations": {"region": f"GeoRegion_{g['region']}"}})
    for r in vocab.REGIONS:
        inst["Geography"].append({
            "id": f"GeoRegion_{r}", "label": r, "kind": "region", "aliases": [],
            "attributes": {"masterdata.country.region": r}, "relations": {}})
    seen_drugs, seen_classes = {}, {}
    for name, b in vocab.BRANDS.items():
        cls = re.sub(r"\s*\(.*\)", "", b["drug_class"])
        seen_classes.setdefault(cls, {"id": f"DrugClass_{slug(cls)}", "label": cls})
        if b["drug"] not in seen_drugs:
            seen_drugs[b["drug"]] = {
                "id": f"Drug_{slug(b['drug'])}", "label": b["drug"],
                "attributes": {"product.drug_product.product_name": b["drug"], "product.drug_product.moa": b["mechanism"],
                               "masterdata.atc_classification.atc_code": b["atc"]},
                "relations": {"drug_class": f"DrugClass_{slug(cls)}"}}
        inst["Brand"].append({
            "id": f"Brand_{slug(name)}", "label": name,
            "attributes": {"commercial.brand.brand_name": name, "commercial.brand.global_brand_owner": b["company"]},
            "relations": {"drug": f"Drug_{slug(b['drug'])}", "therapeutic_area": f"TA_{slug(b['ta'])}",
                          "indications": [f"Indication_{slug(i)}" for i in b["indications"]]}})
    inst["Drug"] = list(seen_drugs.values())
    inst["DrugClass"] = [{**c, "attributes": {"product.therapeutic_area.therapeutic_class": c["label"]}, "relations": {}}
                         for c in seen_classes.values()]
    return inst


def _col(name, typ, desc, **kw):
    return {"name": name, "type": typ, "description": desc, **kw}


def _domains() -> list:
    t = lambda name, pk, desc, cols: {"name": name, "primary_key": pk, "description": desc, "columns": cols}
    return [{
        "name": "pmr", "description": "Primary market research documents as Watson governs them. Not part of the Databricks model.",
        "tables": [
            t("documents", "document_uri", "One row per governed market-research document (source of truth: Layer 1 lifecycle store).", [
                _col("document_uri", "STRING", "Watson document identifier, e.g. Watson:Doc_001."),
                _col("title", "STRING", "Extracted or reviewer-set title."),
                _col("study_type", "STRING", "One of the six PMR study types."),
                _col("therapeutic_area", "STRING", "Therapeutic area from the taxonomy.", foreign_key_to="product.therapeutic_area.area_name"),
                _col("geography", "STRING", "EU, US, APAC or GLOBAL."),
                _col("lifecycle_state", "STRING", "DRAFT, UNDER_REVIEW, ACTIVE, ARCHIVED or REJECTED."),
                _col("sensitivity_level", "STRING", "PUBLIC, INTERNAL, RESTRICTED or CONFIDENTIAL.", tags=["classification_attribute"]),
                _col("quality_score", "DECIMAL(5,4)", "Weighted quality score, 0 to 1."),
                _col("expiry_date", "DATE", "Date after which the document returns to review."),
                _col("ingest_timestamp", "TIMESTAMP", "When the document was ingested."),
                _col("reviewed_by", "STRING", "Reviewer who approved it (human in the loop)."),
            ]),
            t("chunks", "chunk_uri", "Sub-units of a document; the unit of retrieval and of access control.", [
                _col("chunk_uri", "STRING", "Watson chunk identifier."),
                _col("document_uri", "STRING", "Parent document.", foreign_key_to="pmr.documents.document_uri"),
                _col("chunk_index", "INT", "Position within the document."),
                _col("chunk_text", "STRING", "The text of the chunk."),
                _col("quality_score", "DECIMAL(5,4)", "Inherited from the parent document."),
                _col("sensitivity_level", "STRING", "Inherited from the parent document."),
                _col("faiss_id", "BIGINT", "Row in the vector index."),
            ]),
            t("sentiment_assertions", "assertion_uri", "One HCP sentiment score for a brand, in a place, at a time.", [
                _col("assertion_uri", "STRING", "Identifier of the assertion."),
                _col("document_uri", "STRING", "Source document.", foreign_key_to="pmr.documents.document_uri"),
                _col("brand_name", "STRING", "Brand the score is about.", foreign_key_to="commercial.brand.brand_name"),
                _col("indication_name", "STRING", "Indication, when the document names one.", foreign_key_to="product.indication.indication_name"),
                _col("country_or_region", "STRING", "Where the sentiment applies.", foreign_key_to="masterdata.country.country_name"),
                _col("sentiment_score", "DECIMAL(3,2)", "0 (negative) to 1 (positive)."),
                _col("assertion_date", "DATE", "When the sentiment was assessed."),
            ]),
            t("knowledge_spaces", "space_id", "Curated named collections of documents.", [
                _col("space_id", "STRING", "Space identifier."),
                _col("space_name", "STRING", "Display name."),
                _col("space_owner", "STRING", "Responsible user (SPOC or admin)."),
                _col("therapeutic_area", "STRING", "Area the space covers, if any.", foreign_key_to="product.therapeutic_area.area_name"),
                _col("membership_rule", "STRING", "Rule deciding which documents belong."),
            ]),
            t("lineage_records", "lineage_uri", "One enrichment run over one document.", [
                _col("lineage_uri", "STRING", "Identifier of the run."),
                _col("document_uri", "STRING", "Document processed.", foreign_key_to="pmr.documents.document_uri"),
                _col("processing_timestamp", "TIMESTAMP", "When the run happened."),
                _col("enrichment_model", "STRING", "Embedding model used."),
                _col("catalog_version", "STRING", "Version of this data catalog the run used."),
                _col("chunks_produced", "INT", "Chunks written by the run."),
                _col("triples_written", "INT", "Triples written by the run."),
                _col("reviewed_by", "STRING", "Human reviewer who approved the document."),
            ]),
        ]}]


def _grounding() -> list:
    c = lambda *x: [f"{CAT}.{i}" for i in x]
    g = lambda term, kind, status, refs, note="": {"term": term, "kind": kind, "status": status, "catalog": refs, "note": note}
    return [
        g("Brand", "class", "grounded", c("commercial.brand"), "Master record for a pharmaceutical brand."),
        g("Drug", "class", "grounded", c("product.drug_product", "product.drug_substance", "masterdata.atc_classification", "masterdata.inn_registry"),
          "Mechanism of action, ATC code and molecular weight all exist as catalog columns. Our fictional drugs have no molecular weight, so none is written."),
        g("DrugClass", "class", "partial", c("product.therapeutic_area.therapeutic_class"),
          "The catalog has a 'Therapeutic Class' column on the therapeutic-area table, which is not the same thing as a drug class."),
        g("Indication", "class", "grounded", c("product.indication"), "DOID alignment comes from outside the catalog (see alignments)."),
        g("TherapeuticArea", "class", "grounded", c("product.therapeutic_area")),
        g("Geography", "class", "grounded", c("masterdata.country"), "Countries from the table; regions from its 'region' column."),
        g("HCP", "class", "grounded", c("hcp.master", "hcp.kol_profile"),
          "No HCP individuals are created: the catalog tags name and NPI columns as PII, so only specialty and tier would be kept."),
        g("Document", "class", "extension", c("pmr.documents"), "Not in the Databricks model."),
        g("MarketResearchDocument", "class", "extension", c("pmr.documents")),
        g("DocumentChunk", "class", "extension", c("pmr.chunks")),
        g("SentimentAssertion", "class", "extension", c("pmr.sentiment_assertions")),
        g("CompetitiveLandscape", "class", "partial", c("commercial.brand.primary_competitor_brands"),
          "The catalog only has a free-text list of competitor brands on the brand table."),
        g("KnowledgeSpace", "class", "extension", c("pmr.knowledge_spaces")),
        g("LineageRecord", "class", "extension", c("pmr.lineage_records")),
        g("ProvisionalEntity", "class", "extension", [], "A candidate entity waiting for a human decision; not in the catalog until approved."),
        g("CatalogAsset", "class", "extension", [], "Represents a catalog table or column inside the graph so that grounding can be validated."),
        g("targetedIndication", "property", "grounded", c("commercial.brand.indication_id")),
        g("inTherapeuticArea", "property", "grounded", c("commercial.brand.therapeutic_area_id", "product.indication.therapeutic_area_id")),
        g("containsDrug", "property", "grounded", c("commercial.brand.drug_product_id")),
        g("isDrugOf", "property", "grounded", c("commercial.brand.drug_product_id"), "Inverse of containsDrug."),
        g("coversGeography", "property", "partial", c("commercial.brand.primary_market_country_id"),
          "The catalog links a brand to one primary market; a document can cover several places."),
        g("competesWithin", "property", "partial", c("commercial.brand.primary_competitor_brands")),
        g("mechanismOfAction", "property", "grounded", c("product.drug_product.moa")),
        g("atcCode", "property", "grounded", c("masterdata.atc_classification.atc_code")),
        g("molecularWeight", "property", "grounded", c("product.drug_substance.molecular_weight")),
        g("parentIndication", "property", "extension", [], "The catalog's indication table has no parent column."),
        g("subIndicationOf", "property", "extension", [], "Transitive hierarchy of indications; the catalog has none."),
        g("sensitivityLevel", "property", "partial", ["meta:classification_levels"],
          "Same four level names as the catalog's classification tags. The catalog defines no order between them."),
        g("lifecycleState", "property", "extension", c("pmr.documents.lifecycle_state")),
        g("expiry_date", "property", "extension", c("pmr.documents.expiry_date")),
        g("quality_score", "property", "extension", c("pmr.documents.quality_score")),
        g("sentimentScore", "property", "extension", c("pmr.sentiment_assertions.sentiment_score")),
        g("assertionDate", "property", "extension", c("pmr.sentiment_assertions.assertion_date")),
        g("isChunkOf", "property", "extension", c("pmr.chunks.document_uri")),
        g("containsDocument", "property", "extension", c("pmr.documents")),
    ]


def _spaces() -> list:
    return [
        {"id": f"Space_{slug(ta)}", "name": f"{ta} knowledge space", "owner": "u_spoc" if ta == "Oncology" else "u_admin",
         "therapeutic_area": ta, "description": f"All approved {ta} market research.", "rule": {"therapeutic_area": [ta]}}
        for ta in vocab.THERAPEUTIC_AREAS
    ] + [
        {"id": "Space_competitive", "name": "Competitive intelligence", "owner": "u_admin", "therapeutic_area": None,
         "description": "Competitive landscape reports across all therapeutic areas.", "rule": {"study_type": ["Competitive Landscape Report"]}},
        {"id": "Space_market_access", "name": "Market access", "owner": "u_admin", "therapeutic_area": None,
         "description": "Reimbursement, payer and pricing insight.", "rule": {"study_type": ["Market Access Insight"]}},
    ]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    out = config.CATALOG_DIR / "watson_extension.json"
    if out.exists() and not a.force:
        raise SystemExit(f"{out} exists. It may hold approved edits; use --force only to start over.")
    doc = {
        "version": 1, "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "origin": "Watson extension. Not part of the Databricks model.",
        "instances": _instances(), "domains": _domains(), "grounding": _grounding(), "knowledge_spaces": _spaces(),
        "relation_grounding": {
            "Brand.drug": "commercial.brand.drug_product_id", "Brand.indications": "commercial.brand.indication_id",
            "Brand.therapeutic_area": "commercial.brand.therapeutic_area_id",
            "Indication.therapeutic_area": "product.indication.therapeutic_area_id",
            "Indication.parent": None, "Drug.drug_class": None, "Geography.region": None,
        },
        "change_log": [],
    }
    out.write_text(json.dumps(doc, indent=1, ensure_ascii=False), encoding="utf-8")
    n = {k: len(v) for k, v in doc["instances"].items()}
    print("Wrote", out, n, "| grounding entries:", len(doc["grounding"]), "| spaces:", len(doc["knowledge_spaces"]))


if __name__ == "__main__":
    main()
