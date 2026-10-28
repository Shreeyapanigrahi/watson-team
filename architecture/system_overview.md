# Watson: system overview

Only Layer 1 is built. Layers 2 and 3 are planned; their boxes below are the intended design, not working code.
Each layer gets its own diagram file in this folder when it is built.

```mermaid
flowchart LR
    subgraph L1["Layer 1: Governed Ingestion (BUILT)"]
        I1["parse, dedup, chunk,<br/>PII, metadata, review,<br/>ABAC, lifecycle, expiry"]
    end
    subgraph L2["Layer 2: Semantic Foundation (PLANNED)"]
        I2["OWL / RDF ontology from the data catalog,<br/>SPARQL Q1-Q11, FAISS index,<br/>entity resolution, enrichment"]
    end
    subgraph L3["Layer 3: Retrieval + Answers (PLANNED)"]
        I3["plain RAG vs semantic (GraphRAG) retrieval,<br/>Qwen1.5 answers with citations,<br/>guardrails, MCP tool stubs, telemetry"]
    end
    UI["React UI: user panel + admin panel<br/>(Layer 1 screens built)"]
    LOG[("audit_log.jsonl")]
    I1 -->|"ACTIVE docs, chunks,<br/>access attributes, data catalog"| I2
    I2 -->|"graph + vector index"| I3
    UI --> I1
    UI -.-> I2
    UI -.-> I3
    I1 --> LOG
    I2 -.-> LOG
    I3 -.-> LOG
```

Solid lines exist today; dotted lines are planned.

## Folder map

| Folder / file | Purpose |
|---|---|
| `layer1/` | All Layer 1 code, data, stores, tests |
| `layer2/`, `layer3/` | Empty placeholders |
| `frontend/` | React UI |
| `architecture/` | These diagrams |
| `results/` | Evidence files (chunking comparison runs, ingest summary) |
| `README.md` | Milestones and run instructions |
