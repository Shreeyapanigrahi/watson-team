"""Read access to the JSON data catalog, with the lookups Layer 2 and the UI need.

  from layer2.catalog.catalog_store import load
  cat = load()
  cat.table("brand")                  # by short name, "commercial.brand", or full name
  cat.referenced_by("indication")     # which columns point at this table
  cat.search("brand name")            # ranked table and column hits

The catalog is read-only here. It is rebuilt from the source files, never edited in place.
"""
import json
import re
from collections import defaultdict
from functools import lru_cache
from pathlib import Path

from .. import config


class Catalog:
    def __init__(self, doc: dict):
        self.doc = doc
        self.meta = doc["meta"]
        self.name = self.meta["catalog"]
        self.version = self.meta["catalog_version"]
        self.extension = doc.get("extension") or {"domains": [], "relationships": []}
        all_domains = doc["domains"] + self.extension["domains"]       # upstream first, then our 'pmr' tables
        self.domains = {d["name"]: d for d in all_domains}
        self.tables = {t["fqn"]: t for d in all_domains for t in d["tables"]}
        self.columns = {c["fqn"]: c for t in self.tables.values() for c in t["columns"]}
        self.relationships = doc["relationships"]                       # upstream foreign keys only
        self.extension_relationships = self.extension["relationships"]
        self.metric_views = doc["metric_views"]
        self.ontology = doc["ontology"]
        self.instances = doc.get("instances", {})                       # our reference rows, by entity type
        self.grounding = doc.get("grounding", [])                       # Watson ontology term -> catalog refs
        self.knowledge_spaces = doc.get("knowledge_spaces", [])
        self.relation_grounding = doc.get("relation_grounding", {})

        self._by_short = defaultdict(list)
        for fqn, t in self.tables.items():
            self._by_short[t["name"]].append(fqn)
        self._table_of_column = {c["fqn"]: t["fqn"] for t in self.tables.values() for c in t["columns"]}
        self._out, self._in = defaultdict(list), defaultdict(list)
        for r in self.relationships + self.extension_relationships:
            self._out[r["from_table"]].append(r)
            self._in[r["to_table"]].append(r)
        self._metrics = defaultdict(list)
        for v in self.metric_views:
            self._metrics[v["source_table"]].append(v)
        self._search_text = None

    # ---- lookups
    def domain(self, name: str) -> dict:
        try:
            return self.domains[name]
        except KeyError:
            raise KeyError(f"No domain '{name}'. Domains: {', '.join(self.domains)}") from None

    def table(self, ref: str) -> dict:
        """ref is a full name, 'domain.table', or a short table name that is unique."""
        for candidate in (ref, f"{self.name}.{ref}"):
            if candidate in self.tables:
                return self.tables[candidate]
        hits = self._by_short.get(ref, [])
        if len(hits) == 1:
            return self.tables[hits[0]]
        if len(hits) > 1:
            raise KeyError(f"'{ref}' is ambiguous: {', '.join(hits)}")
        raise KeyError(f"No table '{ref}'")

    def column(self, fqn: str) -> dict:
        for candidate in (fqn, f"{self.name}.{fqn}"):
            if candidate in self.columns:
                return self.columns[candidate]
        raise KeyError(f"No column '{fqn}'")

    def table_of(self, column_fqn: str) -> dict:
        return self.tables[self._table_of_column[self.column(column_fqn)["fqn"]]]

    # ---- relationships
    def references(self, ref: str) -> list:
        """Foreign keys leaving this table."""
        return list(self._out[self.table(ref)["fqn"]])

    def referenced_by(self, ref: str) -> list:
        """Foreign keys in other tables that point at this table."""
        return list(self._in[self.table(ref)["fqn"]])

    def metrics_for(self, ref: str) -> list:
        return list(self._metrics[self.table(ref)["fqn"]])

    # ---- search
    def _index(self):
        if self._search_text is None:
            self._search_text = (
                [("table", t["fqn"], t["name"], t.get("description") or "", "") for t in self.tables.values()]
                + [("column", c["fqn"], c["name"], c.get("description") or "", c.get("glossary_term") or "") for c in self.columns.values()])
        return self._search_text

    def search(self, query: str, limit: int = 20, kind: str = None, domain: str = None) -> list:
        """Rank tables and columns for a free-text query.

        Score per query word: 5 if it is a word of the name, 3 if it is a word of the glossary term,
        1 if it appears in the description. Simple on purpose; it is explainable.
        """
        words = [w for w in re.findall(r"[a-z0-9]+", query.lower()) if w]
        if not words:
            return []
        hits = []
        for k, fqn, name, desc, gloss in self._index():
            if kind and k != kind:
                continue
            if domain and fqn.split(".")[1] != domain:
                continue
            name_w = set(re.findall(r"[a-z0-9]+", name.lower()))
            gloss_w = set(re.findall(r"[a-z0-9]+", gloss.lower()))
            desc_l = desc.lower()
            score = sum((5 if w in name_w else 0) + (3 if w in gloss_w else 0) + (1 if w in desc_l else 0) for w in words)
            if score:
                hits.append({"kind": k, "fqn": fqn, "name": name, "score": score, "glossary_term": gloss or None})
        hits.sort(key=lambda h: (-h["score"], h["kind"] != "table", h["fqn"]))
        return hits[:limit]


def load(path: Path = None) -> Catalog:
    return _load(str(path or config.CATALOG_JSON))


@lru_cache(maxsize=2)
def _load(path: str) -> Catalog:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"{p} not found. Build it with: python -m layer2.catalog.build_catalog")
    with open(p, encoding="utf-8") as f:
        return Catalog(json.load(f))
