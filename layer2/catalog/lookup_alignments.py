"""Look up external vocabulary IDs for our indications, therapeutic areas and the Drug class.

  python -m layer2.catalog.lookup_alignments

The brief requires alignments to the Disease Ontology (DOID), MeSH and ChEBI. IDs are never
written from memory: each one is looked up in the public registry and kept only when the
registry returns an EXACT label or synonym match. Anything else is recorded as unmatched.

Network: small read-only requests to the EBI Ontology Lookup Service and the NLM MeSH lookup.
Output: layer2/catalog/alignments.json (committed, so the build does not need the network).
"""
import json
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone

from layer1 import vocab
from .. import config

OLS = "https://www.ebi.ac.uk/ols4/api/search"
MESH = "https://id.nlm.nih.gov/mesh/lookup/descriptor"
OUT = config.CATALOG_DIR / "alignments.json"

# Our own choice of MeSH category per therapeutic area (a modelling decision, see design_decisions.md).
MESH_CATEGORY = {"Oncology": "Neoplasms", "Cardiology": "Cardiovascular Diseases",
                 "Neurology": "Nervous System Diseases", "Immunology": "Immune System Diseases"}
# Grouping nodes above the indications in the corpus (our modelling, so the hierarchy can be transitive).
PARENTS = {"lung cancer": ["non-small cell lung cancer", "small cell lung cancer"],
           "breast cancer": ["HER2-positive breast cancer", "triple-negative breast cancer"],
           "heart failure": ["heart failure with reduced ejection fraction", "heart failure with preserved ejection fraction"]}


def _get(url: str):
    req = urllib.request.Request(url, headers={"User-Agent": "watson-capstone/0.1 (alignment lookup)", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def ols_exact(ontology: str, term: str):
    q = urllib.parse.urlencode({"q": term, "ontology": ontology, "exact": "true", "queryFields": "label,synonym", "rows": 20,
                                "fieldList": "iri,label,obo_id,synonym,ontology_name"})
    docs = _get(f"{OLS}?{q}")["response"]["docs"]
    low = term.lower()
    prefix = ontology.upper() + ":"
    for d in docs:
        names = [d.get("label", "")] + list(d.get("synonym", []) or [])
        hit = next((n for n in names if n.lower() == low), None)
        # the search also returns terms imported from other ontologies (e.g. SYMP:); keep only the ontology asked for
        if hit and (d.get("obo_id") or "").startswith(prefix):
            return {"id": d["obo_id"], "iri": d.get("iri"), "label": d.get("label"), "matched": hit,
                    "match": "label" if hit.lower() == (d.get("label") or "").lower() else "synonym"}
    return None


def mesh_exact(label: str):
    q = urllib.parse.urlencode({"label": label, "match": "exact", "limit": 5})
    hits = _get(f"{MESH}?{q}")
    for h in hits:
        if h["label"].lower() == label.lower():
            return {"id": h["resource"].rsplit("/", 1)[-1], "iri": h["resource"], "label": h["label"], "match": "label"}
    return None


def main() -> None:
    out = {"looked_up_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "sources": {"DOID": OLS + " (ontology=doid)", "MeSH": MESH, "ChEBI": OLS + " (ontology=chebi)"},
           "rule": "Only exact label or synonym matches are kept. No ID was written from memory.",
           "indications": {}, "indication_parents": {}, "therapeutic_areas": {}, "drug_class": None}
    for name, v in vocab.INDICATIONS.items():
        found = None
        for term in [name] + v["aliases"]:
            found = ols_exact("doid", term)
            time.sleep(0.2)
            if found:
                break
        out["indications"][name] = found or {"id": None, "reason": "no exact DOID label or synonym match"}
        print(f"{name:50s}", (found or {}).get("id"))
    for name in PARENTS:
        found = ols_exact("doid", name)
        out["indication_parents"][name] = found or {"id": None, "reason": "no exact DOID label or synonym match"}
        print(f"{name:50s}", (found or {}).get("id"))
    for ta, label in MESH_CATEGORY.items():
        found = mesh_exact(label)
        out["therapeutic_areas"][ta] = {**(found or {"id": None, "reason": "no exact MeSH match"}), "category_label": label}
        print(f"{ta:50s}", (found or {}).get("id"))
    out["drug_class"] = ols_exact("chebi", "drug") or {"id": None, "reason": "no exact ChEBI match"}
    print("ChEBI 'drug':", out["drug_class"])
    OUT.write_text(json.dumps(out, indent=1, ensure_ascii=False), encoding="utf-8")
    print("Wrote", OUT)


if __name__ == "__main__":
    main()
