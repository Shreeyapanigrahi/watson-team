"""MCP-style tool stubs (brief, Layer 3). These are descriptors plus a dispatcher in the shape of the Model Context Protocol
(`tools/list`, `tools/call`), NOT a running MCP server: nothing here speaks the MCP wire protocol. Each tool calls the same
ABAC-filtered Layer 2 functions the UI uses, so an agent that called them would see exactly what the signed-in user may see.
"""
from layer2 import graph_view, search as search_mod

TOOLS = [
    {"name": "search_documents", "description": "Semantic search over the passages the signed-in user may read.",
     "inputSchema": {"type": "object", "required": ["query"], "properties": {"query": {"type": "string"}, "k": {"type": "integer", "default": 5}}}},
    {"name": "lookup_entity", "description": "Catalog grounding, relations and readable documents of one entity (a brand, drug, indication or country).",
     "inputSchema": {"type": "object", "required": ["entity_id"], "properties": {"entity_id": {"type": "string", "example": "Brand_opdivra"}}}},
    {"name": "list_knowledge_spaces", "description": "Knowledge spaces with the document counts this user may see.",
     "inputSchema": {"type": "object", "properties": {}}},
]


def call(user: dict, name: str, arguments: dict) -> dict:
    if name == "search_documents":
        r = search_mod.search(user, arguments["query"], int(arguments.get("k", 5)))
        return {"hits": [{k: h[k] for k in ("chunk_uri", "document_uri", "score", "snippet")} for h in r["hits"]]}
    if name == "lookup_entity":
        d = graph_view.entity_detail(user, arguments["entity_id"])
        return {k: d[k] for k in ("id", "kind", "label", "alignment", "average_sentiment")} | {"documents": [x["document_uri"] for x in d["documents"]]}
    if name == "list_knowledge_spaces":
        return {"spaces": [{k: s[k] for k in ("id", "name", "documents")} for s in graph_view.spaces(user)]}
    raise KeyError(f"No tool '{name}'")
