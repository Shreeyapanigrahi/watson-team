# Watson: pharma knowledge intelligence prototype

A local, zero-cost prototype of a three-layer knowledge platform (brief: `resources/watson.pdf`).
Everything runs on one laptop with free open-source tools. All data is synthetic and fictional.

| Layer | What it does | Status |
|---|---|---|
| 1. Governed Ingestion | Parse, dedup, chunk, PII check, metadata, human review, ABAC, lifecycle, expiry, audit | **Built, 49 tests pass** |
| 2. Semantic Foundation | Entities grounded in the data catalog, OWL ontology, knowledge graph with SHACL, FAISS, SPARQL Q1-Q11, knowledge-space UI | **Built, 133 tests pass (182 with Layer 1)** |
| 3. Retrieval + Answers | Eight visible steps: access slice, entities and route, SPARQL written by a local Qwen model, mapping, evidence and lineage, cited answer, hallucination check. Plain vs semantic RAG, MCP-shaped tool stubs | **Built, 29 tests (scripted model), 211 with Layers 1-2; real-model quality is mixed, see Known limits** |

Working method: agile, one layer at a time. Layer 1 plus a working React UI first.

## Milestones

Only things that were run and observed are marked done.

| # | Milestone | Status | Evidence |
|---|---|---|---|
| M0 | Read the brief and the team's additional instructions; plan published | Done | Plan artifact (private link shared in chat) |
| M1 | Synthetic corpus: 47 files (18 PDF, 17 DOCX, 9 PPTX, 3 TXT) with planted edge cases | Done | `layer1/data/manifest.json` |
| M2 | Ingestion pipeline: parser, dedup, chunker, PII, metadata, quality, expiry | Done | `layer1/*.py`, tests |
| M3 | Lifecycle state machine and ABAC with audit logging | Done | `tests/test_pipeline.py` |
| M4 | Chunking strategy chosen from retrieval evidence (4 runs) | Done, with caveats below | `results/chunking_comparison*.md` |
| M5 | REST API (FastAPI) and automated tests | Done | 49 passed (last run 2026-10-28) |
| M6 | React UI: 10 screens, user and admin views | Built and compiled; **not visually checked** | `npm run build` succeeds |
| M7 | End-to-end flow over HTTP: 4 roles, upload, review, ABAC denials, archive, expiry scan | Done | Scripted run, 2026-10-28 |
| M8 | Architecture diagrams (system, Layer 1, lifecycle) | Done | `architecture/` |
| M8a | JSON data catalog built from the three Databricks model files in `architecture/` | Done | `layer2/catalog/pharma_data_catalog.json`; 21 tests pass |
| M9 | Layer 2 backend: entity extraction grounded in the catalog, ontology (25 classes), knowledge graph (5,596 triples), SHACL (15 shapes, conforms), FAISS (153 vectors), SPARQL Q1-Q11 | Done | 182 tests pass (Layer 1 + 2, 2026-10-28); `results/query_results/` |
| M9a | Layer 2 UI: knowledge space graph, entities, data catalog and mappings, ontology, search, graph health, SPARQL | Built and compiled; **not visually checked**; every endpoint checked over HTTP as 4 roles | `npm run build`; `layer2/api/router.py` |
| M10 | Layer 3: Ask Watson (8 visible steps), plain vs semantic RAG, cited answers, hallucination check | Built; 29 tests pass with a scripted model; real Qwen2.5-3B runs on a few questions; **UI not opened in a browser** | `layer3/`, `architecture/layer3_architecture.md`, `design_decisions.md` 20-22 |

Seeded state after `bulk_ingest --reset`: 47 files, 42 ACTIVE, 3 UNDER_REVIEW, 2 REJECTED, 161 chunks, no unexpected PII flags.

## Run it

Setting it up on a new device? Follow the step-by-step guide in `SETUP.md`. The commands below are the short version.

Tested on Windows 11, Python 3.13.12, Node 24. Run from the project root.

```powershell
# 1. Environment (once)
python -m venv .venv
.venv\Scripts\python -m pip install -r layer1\requirements.txt
.venv\Scripts\python -m spacy download en_core_web_sm

# 2. Data (the corpus is already in layer1/data; regenerate only if you want to)
.venv\Scripts\python -m layer1.generate_data
.venv\Scripts\python -m layer1.bulk_ingest --reset      # seeds the stores through the real pipeline

# 3. Tests
.venv\Scripts\python -m pytest layer1/tests -q -p no:cacheprovider -c layer1/pytest.ini --rootdir=.

# 4. Passwords (once, and on every new machine): asks for each role's password, typed hidden, at least 10 characters
.venv\Scripts\python -m layer1.set_password

# 5. UI (once), then the app on http://127.0.0.1:8000
cd frontend; npm install; npm run build; cd ..
.venv\Scripts\python -m uvicorn layer1.api.main:app --port 8000
```

Until step 4 has been run nobody can sign in (the login screen says so). The passwords are stored only as salted hashes in `layer1/stores/credentials.json`,
which is git-ignored and is not touched by `bulk_ingest --reset`. To change one account later: `.venv\Scripts\python -m layer1.set_password admin`, or use **Account** in the app.

Layer 2 (needs `.venv\Scripts\python -m pip install -r layer2\requirements.txt` once; the first embedding run downloads a ~90 MB model, then it is cached):

```powershell
.venv\Scripts\python -m layer2.catalog.build_catalog     # architecture/*.json|csv|ttl -> layer2/catalog/pharma_data_catalog.json
.venv\Scripts\python -m layer2.backfill --reset          # enrich every ACTIVE document: graph, vectors, mentions
.venv\Scripts\python -m layer2.shacl_validator           # SHACL + index binding; prints "conforms": true
.venv\Scripts\python -m layer2.sparql_runner             # runs Q1-Q11, saves to results/query_results/
.venv\Scripts\python -m pytest layer2/tests layer1/tests -q -p no:cacheprovider -c layer2/pytest.ini --rootdir=.   # about 5 minutes
```

Layer 3 (needs `llama-server` from llama.cpp, a free local model server, and a Qwen2.5-3B-Instruct Q4_K_M GGUF file of about 1.9 GB; no new Python packages).
Install once: `winget install ggml.llamacpp`, and put the GGUF at `%USERPROFILE%\llm-models\qwen2.5-3b-instruct-q4_k_m.gguf` (or set `WATSON_GGUF` to its path).
Start the model server in its own terminal and leave it open:

```powershell
.\layer3\start_llm_server.ps1        # llama-server on http://127.0.0.1:8080, one request slot, context 4096; Ctrl+C stops it
.venv\Scripts\python -m pytest layer3/tests layer2/tests layer1/tests -q -p no:cacheprovider -c layer3/pytest.ini --rootdir=.   # 5+ minutes; Layer 3 tests need no model
```

`WATSON_LLM_URL` points Layer 3 at a different server address. Then restart uvicorn and open **Ask Watson**. Each question takes about 15-60 s on CPU (the SPARQL step alone 6-100 s).
Without the model server the steps still run and the answer is quoted from the evidence, and says so.

Restart the server after `backfill --reset` or after changing code: a running server keeps the old graph in memory.
New approvals made in the UI are enriched automatically.

For UI development run `npm run dev` in `frontend/` (port 5173, proxies `/api` to port 8000).
`layer1/requirements.lock.txt` has the exact versions used.

Sign in with a username and the password you set in step 4. There is one account per role:

| Username | Person | Role | Sees |
|---|---|---|---|
| `viewer` | Vera Hoffmann | VIEWER | up to INTERNAL, Oncology, EU |
| `analyst` | Arjun Mehta | ANALYST | up to RESTRICTED, Oncology + Cardiology, EU/US/APAC |
| `spoc` | Lena Okafor | SPOC | all levels; can review Oncology documents |
| `admin` | Marcus Webb | ADMIN | everything; archive, reinstate, expiry scan, audit log |

Five wrong passwords lock that username for 5 minutes; a session lasts 8 hours; sign-ins, failures, sign-outs and password changes are in the audit log (admin).

Try: upload a file as `analyst`, review it as `admin`, then sign in as `viewer` and check whether Vera can see it.
Layer 2 demo: open **Knowledge space** as Marcus (everything) and again as Vera (only what she may read, and fewer nodes); open an entity such as Opdivra
to see its catalog columns, DOID and sentiment; open **Data catalog > Mappings into Watson**; as Marcus run **Graph health > Run validation** and **SPARQL queries** Q3, Q6, Q10.

## Folders

`layer1/` code, data, stores, tests · `layer2/` catalog, ontology, enrichment, graph, API, tests · `layer3/` questions, SPARQL generation, evidence, answers, checks, API, tests · `frontend/` React UI ·
`architecture/` diagrams · `results/` evidence · `project_spec.md`, `architecture.md`, `project_status.md`,
`change_log.md`, `design_decisions.md`, `nfr_design.md` at the root.

## Hardest design decision

Whether the PII detector should decide or only flag. A detector that auto-rejects would throw out legitimate documents,
because a pharma report names key opinion leaders ("Dr. X noted...") and brands, which are not personal data.
A detector that auto-approves would leak a patient's date of birth. So direct identifiers (email, phone, date of birth,
national ID) always flag, a person name flags only next to an identifier or in a patient context, and nothing is decided
automatically: approving a flagged document needs an explicit override with a written reason, which is logged.
Evidence: the one planted PII file is caught, and the other 46 documents, which mention KOLs and brands, raise no flags.

## Known limits (read before the demo)

- **Chunking evidence is a stand-in.** The comparison used TF-IDF retrieval, not embeddings, because we have not yet
  approved the PyTorch/sentence-transformers download. 6 queries, 42 documents, synthetic data. Fixed 256 tokens won on
  all four corpus lengths we tried, but the margins are a few queries wide. Re-run with embeddings in Layer 2.
- **Chunk count:** the current stores have 43 ACTIVE documents and 153 chunks (counted by the Layer 2 index check), so the brief's 150+ is met. The Layer 1 seeding run alone gave 146.
- **Layer 2 limits:** the pharma catalog we were given rates itself "needs_work", and 1,977 of its 2,423 links are AI-generated (flagged in the UI).
  80 of 98 ontology terms have no catalog counterpart (documents, chunks, sentiment have none), and 7 of 21 indications have no exact disease ID, left blank on purpose.
  The corpus produces no unrecognised names, so that review flow is tested but not shown by real data. Sentiment in segment tables is not extracted.
  The chunking comparison above has not been re-run with the new embeddings.
- **Layer 3 limits (measured, single runs, not a benchmark):** the model writes the SPARQL, but a 1.8B Qwen could not (0/6 parsed as free text), so we use
  Qwen2.5-3B and a typed-edge plan that code renders to SPARQL. About half of the test questions got an accepted query and roughly 3-4 of 10 a correct one; failed
  queries fall back to passages only. The query is generated per question, but it is constrained by a grammar built from our own graph, so say "model-written, schema-constrained", not "free-form".
  Answer lines from the 3B model can be terse ("Tagrisa: 0.78"). The hallucination check is heuristic (words, meaning, numbers, names), not a proof. Plain vs semantic RAG was compared on a few questions only.
  The MCP tools are stubs, not a server. The Ask screen has not been opened in a browser; the streaming endpoint has been exercised with a scripted model, and the real model through the pipeline.
- **Classifier accuracy (about 0.96-0.98)** was measured on synthetic documents whose titles carry the cues. Do not quote it as real-world accuracy.
- **Sign-in is basic, not production authentication.** Username and password per role (salted scrypt hashes, lockout, 8-hour sessions), but no HTTPS, no MFA, no SSO, no self-service reset, and sessions and lockout counters live in server memory (a restart signs everyone out). Anyone who can run `set_password` on the machine can reset any account. Verified by tests over the API; the login and Account screens have not been opened in a browser.
- **UI:** compiled and served, but no one has clicked through it in a browser yet (Layers 1 and 2). The graph screen in particular needs a look.
- Brief section 4.10's four validation checks were mapped to SHACL shapes from our reading of photographed pages; please compare with the brief.
