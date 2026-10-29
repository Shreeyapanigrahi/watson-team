# Change log

Dates are the day the work was verified. Earlier planning days were not separately recorded.

## 2026-10-29 (SPARQL step fixes, Ask screen history)

- `layer3/sparql_gen.py`: first-edge anchoring rewritten (llama-server ignores `prefixItems`; now a tuple per length with `$defs`), anchoring limited to relations the entity has in the user's graph (`entity_edges`), a new seed per retry, graph-based feedback on empty results (`empty_feedback`), two filter checks. `layer3/mapping.py` and `pipeline.py`: step 5 now also returns `query_path` (the hops of the accepted plan). Details and measurements: `design_decisions.md` 24.
- `frontend/src/pages/Ask.jsx`: example-question chips removed; new Recent panel (from `GET /api/l3/history`; click a question to reuse it). `npm run build` succeeds.
- Tests: 33 Layer 3 tests pass (3 new, 1 changed). Real model, six questions plus the Dupixa question, single runs: see `design_decisions.md` 24. Not re-run: Layers 1+2 tests.
- `Ask.jsx` step 5 now shows the relationship path the query walks (`query_path`) and each entity's relationships; finished steps stay open (`open` was `undefined` once a step finished, which collapsed it). `npm run build` succeeds (58 modules).
- Fixed "no supported answer" on correct queries: `layer3/evidence.py` (`sentence_for` keeps context when the query selects only a name) and `layer3/pipeline.py` (a supported short answer alone counts as answered). Tests: 34 Layer 3 tests pass (2 more). Real pipeline, Dupixa drug / therapeutic area / indication: `answered`. Details: `design_decisions.md` 24.
- Not done: the Recent panel, the path display and the Ask screen have not been opened in a browser; the Layer 1+2 tests were not re-run.

## 2026-10-29 (Ollama replaced by llama-server)

- `layer3/llm.py` rewritten for llama-server (`/health`, `/v1/chat/completions`, JSON-schema `response_format`). Added `layer3/start_llm_server.ps1`. Config: `WATSON_LLM_URL` (default `http://127.0.0.1:8080`) replaces `WATSON_OLLAMA_URL`.
- Removed: model switching (`POST /api/l3/model`, `can_switch_model`, `l3SetModel` in `frontend/src/api.js`), the unused `complete()`.
- Model file copied (not downloaded) from Ollama's store to `%USERPROFILE%\llm-models\qwen2.5-3b-instruct-q4_k_m.gguf`, SHA-256 checked. Ollama itself is not uninstalled and its files are untouched; nothing in the project uses it.
- Tests: 30 Layer 3 tests pass (one added: unreachable server). Two real questions run on llama-server, see `design_decisions.md` 23. Docs updated: README, CLAUDE.md, architecture, spec, NFR, Layer 3 diagram.
- `npm run build` succeeds. Not re-run: Layers 1+2 tests (no code there changed).

## 2026-10-28 (Layer 3 build)

- Added `layer3/`: per-user access slice of the graph, entity routing, SPARQL written by a local Qwen model (typed-edge plan under a JSON schema, rendered and checked by code), entity mapping, evidence with lineage,
  answer generation, per-sentence hallucination check, history, MCP-shaped tool stubs, `/api/l3` (NDJSON stream). Mounted in `layer1/api/main.py`.
- Added the Ask Watson screen (8 step cards, answer, plain/semantic/compare) and `streamAsk` in `frontend/src/api.js`. `npm run build` succeeds.
- Runtime: Ollama installed with winget; models pulled: `qwen:1.8b-chat` (failed at SPARQL, 0/6 parsed) and `qwen2.5:3b-instruct` (default).
- Decisions and measurements: `design_decisions.md` 20-22; diagram: `architecture/layer3_architecture.md`.
- Answer step: one retry when the model says "no answer" although graph rows exist.
- Tests: 29 Layer 3 tests (scripted model); full regression 211 passed (Layers 1+2+3).

## 2026-10-28 (Semantic search screen removed)

- Removed the "Semantic search" screen from the Layer 2 UI (page, route, nav entry, `api.l2Search`) at the team's request. `npm run build` still succeeds.
- Kept the backend `POST /api/l2/search` and `layer2/search.py` (tested, and Layer 3 will call it). Not re-run: the Python tests, because no Python changed.

## 2026-10-28 (Layer 2 build)

- Added the Layer 2 backend in `layer2/`: ontology `pharma.ttl` (25 classes) and 15 SHACL shapes; entity extractor and resolver built from the catalog; sentiment extractor;
  fastembed (MiniLM, ONNX) and a FAISS flat index; triple writer; enrichment pipeline with status and rollback; knowledge graph store; telemetry; SPARQL runner and 11 queries;
  catalog and graph views; catalog admin (approve alias or new, reject); REST router at `/api/l2`.
- Layer 1 changed in three small places: `lifecycle_manager.register_state_hook` (called after each transition, failures logged and never block a review), new ABAC actions
  (`enrich`, `validate_graph`, `manage_entities`, `run_queries`), and `api/main.py` mounts the Layer 2 router and installs the hook at server start (not at import, so Layer 1 tests do not touch Layer 2 stores).
- Added 133 Layer 2 tests. Bugs found by them and fixed: null alignment crashed the entity list; SPARQL decimals returned as strings; Q9 parameter rejected the `Watson:` prefix;
  re-seeding duplicated `catalogVersion`; the ontology view showed "unknown" on a fresh install.
- Added the React Layer 2 UI: Knowledge space (Cytoscape graph), Entities, Entity detail, Data catalog, Ontology, Semantic search, Graph health, SPARQL queries, and a Knowledge tab on Document detail.
  The Layer 1 "Data catalog" screen is renamed "Document catalog" to avoid confusion with the new one.
- Found while checking the API: entity grounding returned blank column types (read `data_type`, the key is `type`). Fixed, test added.
- Ran the real build: `backfill --reset` (43 documents enriched, 5,596 triples, 153 vectors, 14.4 s), SHACL conforms, API checked over HTTP as four roles, Q1-Q11 saved.
  Q3 returns Cosentex, Humivex, Opdivra; Q6 returns 12 rows with inference and 0 without; Q10 shows no role above its ceiling over 80 retrievals.
- `layer2/requirements.txt` now lists all dependencies with the tested versions.
- Not done: browser check of the UI; Layer 3; chunking comparison re-run with embeddings.

## 2026-10-28 (later): data catalog

- Added `layer2/` (config, `catalog/build_catalog.py`, `catalog/catalog_store.py`, 21 tests) and `layer2/requirements.txt` (rdflib).
- Converted the three Databricks model files in `architecture/` into one JSON data catalog, `layer2/catalog/pharma_data_catalog.json`
  (15 domains, 213 tables, 8,874 columns, 2,423 foreign keys, 147 metric views, 229 ontology classes, 4,456 properties). The source files are not modified.
- Design: `model.json` is the base because it holds every CSV field plus two extras (primary-key tag, 194 glossary terms the CSV leaves blank).
  The CSV is only a cross-check and the build fails if it disagrees. The TTL contributes class and property names as links; it is lossy (see `meta.reconciliation`).
- Found and handled: one column tagged both `internal` and `restricted` is left unclassified with a recorded conflict, not guessed.
- Not done yet: API endpoints, UI pages, the Watson-to-catalog grounding map, any ontology or graph work.

## 2026-10-28

- Added `DocumentDetail.jsx` (document detail, checks, content, history, review form). Frontend now builds (`npm run build`, Vite 5).
- Ran the API end to end over HTTP with all four roles. No defects found.
- `bulk_ingest` now writes the data catalog snapshot at the end of a run.
- Added `layer1/requirements.lock.txt` and a note in `requirements.txt` about the spaCy model.
- Added `architecture/` (system overview, Layer 1, document lifecycle), root `README.md`, and the project documents.
- Reseeded stores with `bulk_ingest --reset` after the test run: 42 ACTIVE, 3 UNDER_REVIEW, 2 REJECTED, 161 chunks. 49 tests pass.

## Before 2026-10-28 (Layer 1 build, in order)

- Read the brief and team instructions; produced and published a plan.
- Project skeleton, `.venv`, requirements, spaCy model.
- Synthetic corpus generator (47 files, seed 42) with planted edge cases and a ground-truth manifest.
- Parser, chunker, PII detector, dedup, metadata extractor, quality scorer, expiry manager, ABAC engine, lifecycle manager, audit log, catalog.
- Chunking comparison, four runs on four corpus lengths; chose fixed 256 tokens with 20% overlap (rule fixed before the final run).
- Quality score richness target recalibrated 0.08 to 0.10 after it saturated (mean 0.83).
- FastAPI service and 49 tests. Bugs found and fixed by the tests: DOCX table cells skipped (lxml proxy ids reused); stale record returned after archiving a superseded document.
- React frontend: client, auth, shared components, and nine of ten screens.
