# Project status

As of 2026-10-28.

## Done (verified by running it)

- Layer 1 backend: pipeline, lifecycle, ABAC, expiry, quality, audit, catalog. **49 tests pass.**
- Corpus: 47 synthetic files seeded through the real pipeline: 42 ACTIVE, 3 UNDER_REVIEW, 2 REJECTED, 161 chunks.
- API run end to end over HTTP with all four roles: visibility differs by role (viewer 9, analyst 26, SPOC 21, admin 47 documents),
  upload, review, second review refused (409), unsupported and empty uploads refused (415, 422), archive and reinstate, expiry scan, audit.
- Frontend: all 10 screens written, `npm run build` succeeds, the built app is served by the API (HTTP 200).
- Architecture diagrams, README, project documents.

## Built but not verified

- **The UI has not been opened in a browser by us.** It compiles, and every API call it makes was exercised by the scripted run,
  but layout, the review form behaviour and the dark theme are unchecked. Next step: someone clicks through the demo flow in README.

## Next

1. Click-through of the UI and fix what it shows (Layer 1 sign-off).
2. Team decisions below.
3. Layer 2: ontology from `stores/data_catalog.json`, SPARQL Q1-Q11, FAISS, entity resolution. Needs the embedding model download approved.
4. Layer 3: plain RAG vs semantic RAG, cited answers, guardrails, MCP stubs, telemetry.
5. `reflection.md` (to be written by the team from real experience, not generated).

## Open decisions (need a human)

| Decision | Why it matters |
|---|---|
| Approve downloading PyTorch + sentence-transformers (large, free) | Embeddings for FAISS and a proper re-run of the chunking comparison |
| Reuse anything from the earlier `D:\watson` / `D:\watson_sai` build? | Not inspected; this project started clean |
| 150+ chunk floor: approve pending documents, or add more documents? | ACTIVE chunks are about 146 |
| Read the four validation SPARQL checks (brief section 4.10) | Photos of the PDF page need a careful read; not done yet |
| Expiry retention for the three study types the brief does not specify (1095 days assumed) | Assumption flagged in `design_decisions.md` |
| Real authentication? | Prototype uses pick-a-user login; see `nfr_design.md` |

## Not committed

Nothing has been committed or pushed. The git root is `D:\` and `D:\watson_Team` is untracked.
