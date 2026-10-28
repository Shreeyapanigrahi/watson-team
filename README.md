# Watson: pharma knowledge intelligence prototype

A local, zero-cost prototype of a three-layer knowledge platform (brief: `resources/watson.pdf`).
Everything runs on one laptop with free open-source tools. All data is synthetic and fictional.

| Layer | What it does | Status |
|---|---|---|
| 1. Governed Ingestion | Parse, dedup, chunk, PII check, metadata, human review, ABAC, lifecycle, expiry, audit | **Built, 49 tests pass** |
| 2. Semantic Foundation | OWL/RDF ontology from the data catalog, SPARQL Q1-Q11, FAISS, enrichment | Not started |
| 3. Retrieval + Answers | Plain RAG vs semantic RAG, Qwen1.5 answers with citations, guardrails, MCP stubs | Not started |

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
| M9 | Layer 2: ontology + SPARQL + FAISS | Not started | |
| M10 | Layer 3: plain vs semantic RAG, answers with citations | Not started | |

Seeded state after `bulk_ingest --reset`: 47 files, 42 ACTIVE, 3 UNDER_REVIEW, 2 REJECTED, 161 chunks, no unexpected PII flags.

## Run it

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

# 4. UI (once), then the app on http://127.0.0.1:8000
cd frontend; npm install; npm run build; cd ..
.venv\Scripts\python -m uvicorn layer1.api.main:app --port 8000
```

For UI development run `npm run dev` in `frontend/` (port 5173, proxies `/api` to port 8000).
`layer1/requirements.lock.txt` has the exact versions used.

Log in by choosing one of four demo users (no passwords, prototype only):

| User | Role | Sees |
|---|---|---|
| Vera Hoffmann | VIEWER | up to INTERNAL, Oncology, EU |
| Arjun Mehta | ANALYST | up to RESTRICTED, Oncology + Cardiology, EU/US/APAC |
| Lena Okafor | SPOC | all levels; can review Oncology documents |
| Marcus Webb | ADMIN | everything; archive, reinstate, expiry scan, audit log |

Try: upload a file as Arjun, review it as Marcus, then log in as Vera and check whether she can see it.

## Folders

`layer1/` code, data, stores, tests · `layer2/`, `layer3/` placeholders · `frontend/` React UI ·
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
- **Chunk count:** ACTIVE documents give 146 chunks; the brief asks for 150+. Approving the pending documents adds more.
- **Classifier accuracy (about 0.96-0.98)** was measured on synthetic documents whose titles carry the cues. Do not quote it as real-world accuracy.
- **Login is not real authentication.** Pick-a-user and an in-memory token. Fine for a demo, not for production.
- **UI:** compiled and served, but no one has clicked through it in a browser yet.
- Four validation SPARQL checks from brief section 4.10 are not yet read or built (Layer 2).
