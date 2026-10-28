# Layer 1: Governed Ingestion, architecture

Status: **built and tested** (49 automated tests; UI built, not visually checked by us, see `project_status.md`).

## 1. Ingestion pipeline (data flow)

```mermaid
flowchart LR
    U["Upload via UI<br/>or bulk_ingest"] --> A["POST /api/documents<br/>type, size, empty checks"]
    A --> P["parser.py<br/>PDF / DOCX / PPTX / TXT"]
    P --> D{"dedup_detector.py<br/>SHA-256, then text similarity"}
    D -- "exact duplicate" --> R1["REJECTED<br/>(logged DEDUP_HIT)"]
    D -- "unique / near-dup / new version" --> C["chunker.py<br/>256 tokens, 20% overlap"]
    C --> PII["pii_detector.py<br/>flags, never decides"]
    PII --> M["metadata_extractor.py<br/>15 fields, each with a source"]
    M --> UR["DRAFT to UNDER_REVIEW"]
    UR --> H{"Human review<br/>(SPOC / ADMIN)"}
    H -- "reject + reason" --> R2["REJECTED"]
    H -- "approve" --> G["sensitivity + allowed TA / region<br/>(abac_engine.py)"]
    G --> Q["quality_scorer.py<br/>expiry_manager.py"]
    Q --> AC["ACTIVE<br/>indexable by Layer 2"]
```

Every box that changes something writes an event to `audit_log.jsonl`
(`INGEST`, `PARSE_WARNING`, `PII_FLAG`, `DEDUP_HIT`, `STATE_TRANSITION`, ...).

## 2. Components

```mermaid
flowchart TB
    subgraph FE["frontend/ (React 18 + Vite)"]
        UI["Login, Dashboard, Upload, Documents,<br/>Document detail + review, Review queue,<br/>Expiry, Audit, Data catalog, Design evidence"]
    end
    subgraph API["layer1/api/main.py (FastAPI)"]
        EP["REST endpoints<br/>bearer token, error mapping"]
    end
    subgraph CORE["layer1/ (pipeline modules)"]
        LM["lifecycle_manager.py<br/>state machine, ingest(), review()"]
        AB["abac_engine.py<br/>roles + ACL"]
        PIPE["parser, chunker, pii_detector,<br/>dedup_detector, metadata_extractor,<br/>quality_scorer, expiry_manager"]
        AL["audit_log.py"]
        CAT["catalog.py"]
    end
    subgraph ST["layer1/ (JSON on disk)"]
        S1["stores/lifecycle_store.json<br/>dedup_store.json, taxonomy_store.json,<br/>data_catalog.json"]
        S2["staging/Watson_Doc_NNN.json<br/>(parsed text + chunks)"]
        S3["audit_log.jsonl"]
        S4["uploads/&lt;id&gt;/original file"]
    end
    UI -->|"/api/* (Vite proxy in dev)"| EP
    EP --> LM
    EP --> AB
    LM --> PIPE
    LM --> AB
    LM --> AL
    LM --> S1
    LM --> S2
    AL --> S3
    EP --> S4
    EP --> CAT
    CAT --> S1
    S1 -.->|"Layer 2 reads ACTIVE docs + catalog"| L2["Layer 2 (planned)"]
```

## 3. Who can do what (ABAC)

| Role | Reads up to | Review | Archive / reinstate | Expiry scan, audit |
|---|---|---|---|---|
| VIEWER | INTERNAL | no | no | no |
| ANALYST | RESTRICTED | no | no | no |
| SPOC | all levels | yes, only inside their therapeutic area | no | no |
| ADMIN | all levels | yes | yes | yes |

A document is also filtered by its own ACL (`allowed_therapeutic_areas`, `allowed_geographies`; empty means all).
Documents a user may not see are left out of lists, never shown redacted. Every denial is logged as `ABAC_DENIED`.
Any role may upload; the uploader sees only the status of their own pending document.

## 4. Contract with Layer 2

Layer 2 may index a document only if `lifecycle_manager.assert_indexable(uri)` passes (state is ACTIVE).
It reads chunks from `staging/`, access attributes from the lifecycle record, and the entity inventory from
`stores/data_catalog.json`. Chunk URIs (`Watson:Chunk_NNN_III`) and document URIs (`Watson:Doc_NNN`) are the join keys.
