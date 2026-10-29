# Project spec

Source: `resources/watson.pdf` plus the team's additional instructions. Where they differ, the team's instructions win.
Sections marked (gap) are things the brief leaves unclear, with the choice we made.

## Scope by layer

| Layer | Requirements | Status |
|---|---|---|
| 1. Governed Ingestion | Upload and bulk ingest; PDF, DOCX, PPTX, TXT; dedup; chunking; PII flagging; 15-field metadata; human review; ABAC; lifecycle; expiry; quality score; audit log | Built |
| 2. Semantic Foundation | Entity recognition (drugs, diseases, countries); ontology grounded in the data catalog; knowledge graph with SHACL validation; knowledge space and catalog mappings in the UI; FAISS vector index; SPARQL Q1-Q11 | Built (see acceptance criteria below) |
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
9. 150+ chunks from active documents. **Was about 146 at the first Layer 1 run; the current stores have 43 ACTIVE documents and 153 chunks, so the floor is met (counted by the index-binding check, 2026-10-28).**

## Layer 2 interfaces

Mounted in the same app at `/api/l2` (`layer2/api/router.py`). Every read is filtered to what the caller may read.

| Endpoint | Purpose | Who |
|---|---|---|
| `GET /status`, `/spaces`, `/entities`, `/entities/{id}`, `/graph`, `/ontology` | Knowledge space, entities, graph, ontology | all (ABAC-filtered) |
| `GET /catalog/summary`, `/catalog/tables`, `/catalog/table`, `/catalog/search`, `/catalog/mappings` | Data catalog and every mapping into Watson | all |
| `GET /documents/{uri}/knowledge` | Enrichment, mentions, sentiment, lineage for one document | per document access |
| `POST /documents/{uri}/enrich`, `GET /enrichment` | Re-run or list enrichment | SPOC (own TA), ADMIN |
| `GET /provisional`, `POST /provisional/{id}/approve`, `/reject` | Unrecognised names: alias, new entry, or reject | SPOC, ADMIN |
| `GET /taxonomy`, `POST /taxonomy/{name}/terms` | Taxonomies | read: all; edit: per `edit_taxonomy` |
| `POST /validate`, `GET /validate/last` | SHACL run and last result | SPOC, ADMIN |
| `GET /queries`, `POST /queries/{id}/run`, `/queries/custom`, `GET /telemetry` | SPARQL Q1-Q11, custom SELECT/ASK | ADMIN |
| `POST /search` | Semantic search over readable chunks | all |

Run: `python -m layer2.backfill --reset` (rebuild), `python -m layer2.shacl_validator`, `python -m layer2.sparql_runner` (saves Q1-Q11 to `results/query_results/`).

## Acceptance criteria (Layer 2)

1. Entities are grounded in the catalog: every entity has at least one catalog column. **Met, tested (SHACL check 3 conforms).**
2. Ontology meets the brief's minimums (25 classes, 32 object and 41 datatype properties, 3 inverse pairs, transitive `subIndicationOf`, 4-level subclass chain). **Met; checked on the Ontology screen.**
3. SHACL validation of the graph: 15 shapes, 0 violations on 5,596 triples; 153 of 153 chunks have a vector. **Met, run 2026-10-28.**
4. A user's search and graph never include a chunk they may not read. **Met in tests and over HTTP with four roles; Q10 shows no violation in 80 logged retrievals.**
5. Eleven SPARQL queries run and saved. **Met (`results/query_results/`). Q3 and Q9 return rows; Q10 is real telemetry.**
6. UI: users can explore the knowledge space, entities and the catalog's mappings. **Built; compiled; not opened in a browser by us.**
7. Not met or partial: only 14 of 21 indications have an exact DOID; 80 of 98 ontology terms have no catalog counterpart ("extension"); no provisional entities arise from this corpus.

## Layer 3 interfaces

| Method and path | Who | What |
|---|---|---|
| `GET /api/l3/status` | any user | Is the llama-server model server reachable and ready, and which model |
| `POST /api/l3/model` | ADMIN | Switch to another installed model |
| `POST /api/l3/ask` | any user | Body `{question, mode: semantic or plain}`. Streams NDJSON: one event per step (running, then done/skipped/failed) and a final `result`. Empty question: 422 |
| `GET /api/l3/history` | any user (ADMIN sees all) | Past runs from `layer3/stores/runs.json` |
| `GET /api/l3/tools`, `POST /api/l3/tools/{name}` | any user | MCP-shaped stubs: search_documents, lookup_entity, list_knowledge_spaces |

## Acceptance criteria (Layer 3)

1. The question runs through the nine steps the team listed, each shown in the UI in order, then the answer. **Built; UI not opened in a browser by us.**
2. Documents the user may not read are removed before the query, the entity counts and the vector search. **Met in tests (scripted model) and in real runs as viewer vs analyst.**
3. The SPARQL is written by the local model for each question, with no stored query. **Met, with the caveat that it is a typed plan under a schema and about half of the test questions get an accepted query.**
4. Every answer line carries a source; uncited and unsupported lines are not shown as facts; only cited sources are listed. **Met in tests; heuristic check.**
5. Questions about the knowledge space itself are answered from live Layer 2 views, not canned text. **Met (real run as viewer).**
6. Plain RAG and semantic RAG can be run on the same question. **Built; compared on a few questions only.**
7. Not met or partial: answer quality of the 3B model is uneven; no larger model tried; MCP is stubs, not a server.
