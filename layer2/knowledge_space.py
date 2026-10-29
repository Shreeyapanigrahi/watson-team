"""Knowledge spaces: curated, named collections of documents (brief 5.3, 'KnowledgeSpace').

A space is a rule over document attributes, defined in the data catalog's extension (knowledge_spaces):
    {"therapeutic_area": ["Oncology"]}  or  {"study_type": ["Competitive Landscape Report"]}
A document belongs to a space when it matches EVERY key of the rule (and any ONE value inside a key). Membership is
computed from the document's attributes, never typed in, so it cannot drift from them; it is rewritten whenever the
document is enriched.

Membership says nothing about who may SEE a document. A space lists a document only to users whose ABAC attributes
allow it (graph_view.py).
"""
from .catalog.catalog_store import Catalog

RULE_KEYS = {"therapeutic_area": "therapeutic_area", "study_type": "study_type", "geography": "geography"}


def matches(rule: dict, metadata: dict) -> bool:
    for key, allowed in rule.items():
        value = metadata.get(RULE_KEYS[key])
        if key == "geography":
            # a GLOBAL document belongs to a space about any region
            if value != "GLOBAL" and value not in allowed:
                return False
        elif value not in allowed:
            return False
    return True


def memberships(metadata: dict, cat: Catalog) -> list:
    """Ids of the spaces this document belongs to."""
    return [s["id"] for s in cat.knowledge_spaces if matches(s["rule"], metadata)]
