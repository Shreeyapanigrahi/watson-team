# Non-functional design (Layer 1)

What the prototype does and does not do. The "Gap" column is deliberate honesty for the review call.

| Area | What we have | Gap / what production would need |
|---|---|---|
| Authentication | Pick one of four users, receive an opaque bearer token held in server memory | No passwords, no expiry, tokens lost on restart. Use SSO (OIDC) and short-lived signed tokens |
| Authorisation | ABAC checked on every read and action, server-side; UI hiding is cosmetic only | Policy is in code; a policy engine would allow changes without redeploy |
| Audit | Every ingest, check, decision and denial written to `audit_log.jsonl` | The file is editable by anyone with disk access. Production: append-only store with hash chaining or write-once storage |
| Data at rest | Plain JSON and original files on local disk | Unencrypted. Production: encrypted storage, access limited by the OS |
| PII | Detected and gated before ACTIVE | Detector is rule-based plus spaCy; recall on real documents is unknown. Needs measurement on real data |
| Concurrency | Single process, one lock around store writes | No multi-process safety. Production: a database with transactions |
| Scale | 47 documents, 161 chunks; every request reads JSON from disk | Will not scale to thousands of documents. Production: database and an index |
| Upload safety | Allow-listed extensions, 20 MB limit, empty files refused, filename sanitised, stored in a random folder | No malware scanning; parsers (PyMuPDF etc.) run in-process, so a malicious file could harm the server. Production: sandboxed workers |
| Reliability | Atomic JSON writes avoid half-written files | No backup, no recovery plan |
| Performance | Not measured | We have not measured ingest time or API latency; do not quote any figure |
| Cost | Zero: local, open-source, no API keys | |
| Privacy | All data synthetic and fictional | |
| Accessibility | Labelled form controls, light and dark themes (written in, not tested) | No screen-reader, keyboard-only or contrast check done |
| Browser support | Built with Vite defaults (modern browsers) | Only a build check so far; no browser testing |

## Reproducibility

Seeded generator (seed 42), pinned versions in `layer1/requirements.lock.txt`, and one command (`bulk_ingest --reset`) rebuilds all state from the corpus.
Layer 2 adds `python -m layer2.backfill --reset`, which rebuilds the graph, the vector index and the entity mentions from the ACTIVE documents (14 seconds for 43 documents on this laptop, one run).

# Non-functional design (Layer 2)

| Area | What we have | Gap / what production would need |
|---|---|---|
| Access control | Graph, search and counts are built from the chunks the user may read; the vector search looks only at those chunks. Q10 audits what was returned | The graph is one shared file. Per-user views are recomputed on request, which is correct but not cached; large graphs would need a store with row-level access |
| SPARQL access | SPARQL screens and custom queries are ADMIN-only because results are not filtered per user | A production endpoint would need query rewriting by user, or one named graph per access level |
| Data residency (GDPR) | Geography is part of the ACL (`allowedGeo`), so a user only reads documents for their regions. The graph has no residency property | Nothing records or enforces where a document is stored. Single machine, so not applicable here, but a cloud deployment would need a residency attribute and region-bound storage |
| Scale | Flat exact FAISS index (153 vectors); rdflib graph in memory (5,596 triples); search copies the readable vectors into a temporary index per request | Switch to IVF or HNSW at around 100,000 vectors. rdflib in memory will not hold millions of triples; a triple store (Fuseki, GraphDB) would be needed |
| Concurrency | Enrichment runs on one worker thread behind a lock; graph writes are saved atomically and reloaded on error | Single process only. No multi-process safety |
| Reliability | A failed enrichment is logged, the document stays ACTIVE, vectors are rolled back; stale-catalog documents are flagged and can be re-run | No automatic retry. If the server stops mid-run the status could be left as RUNNING (not tested); re-running the document fixes it |
| Catalog change | A new catalog version marks documents as stale and re-enriches the ones a decision touches | Removing a term from the catalog needs `backfill --reset` (seeding is additive) |
| Dependencies | rdflib, pyshacl, owlrl, faiss-cpu, fastembed, rapidfuzz, numpy, pinned in `layer2/requirements.txt` | The embedding model (about 90 MB) is downloaded on first use and then cached. Offline first run fails with a clear error (503), by design |
| Performance | Backfill of 43 documents took 14.4 s; the 11 saved SPARQL runs took 0.01 to 2.9 s each (Q6 with inference is the slowest) | Single runs on one machine. No load test. Do not quote as a benchmark |
| Privacy | Chunk text is not copied into the graph; the telemetry store keeps the query text and chunk ids, not chunk text | Query text can itself be sensitive; production would need retention rules for telemetry |
| Accessibility | The graph has a text alternative (a list of the same nodes) and uses shape as well as colour per kind | Not tested with a screen reader or keyboard-only. The graph canvas itself is not keyboard-navigable |
| Browser | `npm run build` succeeds (Vite). Cytoscape makes the bundle about 730 kB | Pages were not opened in a browser by us. Code-splitting would shrink the first load |

# Non-functional design (Layer 3)

| Area | What we have | Gap / what production would need |
|---|---|---|
| Access control | A per-user copy of the graph with unreadable documents cut out; the generated query, entity counts and vector search touch only that copy. Withheld documents are counted, never named | Copy is rebuilt per question (about 0.25 s here); a large graph needs cached views or row-level access in the store |
| Model safety | Generated SPARQL is SELECT only (no SERVICE, FROM or updates), parsed, row-limited, and stopped at a time limit that raises inside the graph scan | The model never sees data outside the user's slice, but prompts and evidence are not yet screened for injected instructions inside documents |
| Hallucination | Per-sentence check against cited evidence (words, meaning, numbers, names); removed lines logged as `HALLUCINATION_RISK` | Heuristic, not proof; a paraphrase can be flagged, and a subtle wrong claim can pass. No human-labelled test set yet |
| Privacy | Everything runs locally; no text leaves the laptop. History keeps the question and answer in `layer3/stores/runs.json` (git-ignored) | Retention rules for history and telemetry |
| Performance | Single runs on one CPU: 20-60 s per question; SPARQL step 10-100 s; answer step about 10 s | No load test, one request at a time. A GPU or a smaller schema would help. Do not quote as a benchmark |
| Reliability | If the model server (llama-server) is down the steps still run and the answer is quoted from the evidence, and says so; if no query is accepted, status is "failed" and passages are used | No queueing; a second concurrent question will slow both |
| Dependencies | `llama-server` (llama.cpp, winget) started by hand in a terminal, and a 1.9 GB GGUF file; no new Python packages. The server listens on 127.0.0.1 only and has no API key | Model file SHA-256 is `5ee4f07c...caa6` (as recorded when copied); the server's own log warns that CORS is open to all origins, harmless while it is bound to localhost |
