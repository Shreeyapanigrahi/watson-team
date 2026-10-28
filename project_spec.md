# Project spec

Source: `resources/watson.pdf` plus the team's additional instructions. Where they differ, the team's instructions win.
Sections marked (gap) are things the brief leaves unclear, with the choice we made.

## Scope by layer

| Layer | Requirements | Status |
|---|---|---|
| 1. Governed Ingestion | Upload and bulk ingest; PDF, DOCX, PPTX, TXT; dedup; chunking; PII flagging; 15-field metadata; human review; ABAC; lifecycle; expiry; quality score; audit log | Built |
| 2. Semantic Foundation | OWL/RDF ontology grounded in the data catalog; SPARQL Q1-Q11; FAISS vector index; entity resolution; enrichment | Not started |
| 3. Retrieval + Answers | Plain RAG vs semantic RAG comparison; natural-language answer plus a one-line answer, both citing sources; guardrails; MCP tool stubs; telemetry | Not started |

## Team requirements beyond the brief

- At least 40 documents, JSON storage, folders `layer1`, `layer2`, `layer3`, `results`, `architecture`, `frontend`, `README.md`.
- A real, interactive React UI connected to the backend (the brief's demo UI is ignored). Separate user and admin views.
- Users can upload documents, and uploads go through the pipeline.
- One architecture diagram per layer and a document lifecycle diagram.
- Agile delivery, one layer at a time. Layer 1 first.

## Layer 1 interfaces

REST API in `layer1/api/main.py` (FastAPI). Bearer token from `POST /api/login`.

| Endpoint | Purpose | Who |
|---|---|---|
| `GET /api/users`, `POST /api/login`, `GET /api/me`, `GET /api/meta` | Demo login and reference data | all |
| `GET/POST /api/documents` | List (ABAC-filtered) / upload | all |
| `GET /api/documents/{uri}`, `/content` | Detail / chunks and text | per ABAC |
| `POST /api/documents/{uri}/review` | Approve or reject | SPOC (own TA), ADMIN |
| `POST /api/documents/{uri}/archive`, `/reinstate` | Lifecycle | ADMIN |
| `GET /api/expiry/upcoming`, `POST /api/expiry/scan` | Retention | read: all; scan: ADMIN |
| `GET /api/audit` | Audit log | ADMIN |
| `GET /api/catalog`, `/api/stats`, `/api/chunking-comparison` | Catalog, dashboard, evidence | all |

Error mapping: lifecycle violation or PII block 409; access denied 403; incomplete metadata 422; not found 404; bad file type 415.

## Data

- Documents: synthetic, fictional brands (18), 18 indications, 4 therapeutic areas, 6 study types. No real drug data, no real people.
- URIs: `Watson:Doc_NNN`, `Watson:Chunk_NNN_III` (gap: the brief uses both uuid4 and `Doc_001`; we chose sequential).
- Stores (JSON, in `layer1/stores/`): lifecycle, dedup, taxonomy, data catalog. Audit log is `audit_log.jsonl`.
- Metadata: 15 fields, each recorded with its source (parsed, NER, rule, reviewer).

## Acceptance criteria (Layer 1)

1. 40+ documents ingested through the real pipeline. **Met: 47.**
2. Every state change is audited; illegal transitions are refused and logged. **Met, tested.**
3. A document outside a user's attributes is never listed or readable for them; denials are logged. **Met, tested.**
4. A PII-flagged document cannot become ACTIVE without an explicit, reasoned override. **Met, tested.**
5. Exact duplicates are rejected; near duplicates and new versions go to a human. **Met, tested.**
6. Expired documents return to review. **Met, tested.**
7. Chunking strategy justified by retrieval results. **Met with caveat: TF-IDF stand-in, see `design_decisions.md`.**
8. Working UI covering upload, review, browse, expiry, audit. **Built; not visually verified.**
9. 150+ chunks from active documents. **Not met on ACTIVE-only: about 146 (161 including pending).**
