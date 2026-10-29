# Project status

As of 2026-10-29. **Layer 3 now uses llama-server (llama.cpp) instead of Ollama**; start it with `.\layer3\start_llm_server.ps1`. Verified: 30 Layer 3 tests, two real questions (`design_decisions.md` 23). `npm run build` succeeds. Not re-run since: Layers 1+2 tests (no code there changed). The figures below that mention Ollama were measured before the swap.

## Done (verified by running it)

- Layer 1 backend: pipeline, lifecycle, ABAC, expiry, quality, audit, catalog. **49 tests pass.**
- Corpus: 47 synthetic files seeded through the real pipeline; the current stores have 43 ACTIVE documents and 153 chunks.
- Layer 1 API run end to end over HTTP with all four roles (upload, review, ABAC denials, archive, expiry scan, audit).
- Data catalog: the three model files in `architecture/` converted to one JSON (`layer2/catalog/pharma_data_catalog.json`): 213 upstream tables, 8,874 columns, 2,423 foreign keys, plus our `pmr` extension (5 tables).
- **Layer 2 backend:** entity extraction grounded in the catalog, resolution, sentiment extraction, ontology (25 classes, 32 object and 41 datatype properties),
  knowledge graph (5,596 triples), FAISS (153 vectors, all 153 chunks bound), SHACL (15 shapes, **conforms, 0 violations**), SPARQL Q1-Q11 saved to `results/query_results/`.
  **182 tests pass** (49 Layer 1 + 133 Layer 2), run 2026-10-28 after the last code change except one added assertion (that test file re-run: 14 passed).
- **Layer 2 over HTTP**, on a second server instance (port 8001) with the final code, as VIEWER, ANALYST, SPOC and ADMIN: every Layer 2 endpoint returned the expected status;
  visibility differs by role (readable documents 9 / 27 / 19 / 43), the viewer's searches returned only PUBLIC and INTERNAL passages, privileged endpoints returned 403 for roles without them.
- **Layer 2 UI written and compiled** (`npm run build` succeeds, 58 modules): Knowledge space, Entities, Entity detail, Data catalog (overview, browse, mappings), Ontology,
  Graph health, SPARQL queries, and a Knowledge tab on Document detail. Wired into the existing navigation.
- **Knowledge space filters (2026-10-29):** Document, Brand, Indication, Drug, Therapeutic area, Geography and Relationship filters plus a per-document facts card (`change_log.md`). Verified: full Layer 1+2+3 regression, 254 passed in 5 min 49 s; 33 Layer 2 ABAC and API tests pass (7 new), real-data numbers for viewer and admin, `npm run build` succeeds. **Not verified: the screen has not been opened in a browser** (filter bar layout, the bold outline on filtered nodes, the facts card). Restart uvicorn and hard-refresh to see it.
- **UI changes (2026-10-29), compiled but never opened in a browser:** white sidebar and page, viewer without the Layer 2 and About sections and with a browser-style frame, redesigned sign-in page (blister-pack panel, role tiles), and a ChatGPT-style home page for the viewer (conversation, New chat, Recent in the sidebar; Dashboard at `/dashboard`). Layout, colours, scrolling, the settings menu and the dark variants are unchecked.
- **Counts of documents tied to one entity (2026-10-29):** "how many documents are associated with {X}" is right for every entity of six kinds, admin and viewer (87/87 each, real model, independent counts); full regression 280 passed. Limits in `design_decisions.md` 27.
- **Username and password sign-in (2026-10-29):** one account per role, hashed passwords set with `python -m layer1.set_password`, lockout, 8-hour sessions, logout, change password, audit events. Verified: full Layer 1+2+3 regression, 269 passed (15 new auth tests); `npm run build` succeeds. **Not verified:** the login and Account screens in a browser, and a live server with real passwords. Passwords must be set on each new machine before anyone can sign in.
- Bug found while checking the API shape and fixed: the entity grounding table would have shown blank column types (`data_type` read from the wrong key). Test added.
- Documents updated: README, project_spec, architecture, `architecture/layer2_architecture.md`, design_decisions (11-19), nfr_design, CLAUDE.md.

- **Layer 3 (Ask Watson) built:** 8 visible steps streamed from `POST /api/l3/ask`, React Ask screen, local Qwen2.5-3B (via Ollama when built; llama-server since 2026-10-29). **29 tests pass** with a scripted model (they prove plumbing and safeguards, not model quality).
  Real-model runs through `pipeline.ask` (analyst, semantic): "Which brands are co-mentioned with Opdivra?" gave 5 supported lines in 49 s; "What is the average sentiment score per brand?" gave 8 supported lines in 22 s.
  A viewer's knowledge-space question saw 11 of 44 documents (33 withheld). Details and measurements: `design_decisions.md` 20-22.
  **Full regression (Layers 1+2+3): 211 passed in 4 min 44 s, 2026-10-28**, after the last code change.
  `/api/l3` checked over HTTP on a fresh server (port 8002, since stopped) as viewer, analyst and admin: status 200 for all, model switch 403 for non-admins and 200 for admin, blank question 422,
  and a real streamed question as viewer returned step events one by one and a grounded knowledge-space answer in 31 s.

## Built but not verified

- **Layer 3 quality:** an accepted, correct SPARQL on roughly 3-4 of 10 test questions (5 of 10 accepted), 10-100 s per SPARQL step; answer lines can be terse; the 3B model once refused despite good evidence (a single retry now handles that case, seen working on one question only).
  The plain-vs-semantic comparison has few questions. The Ask screen has not been opened in a browser. The server on port 8000 must be restarted to expose `/api/l3`. The MCP tools are stubs.

- **The UI (Layers 1 and 2) has not been opened in a browser by us.** It compiles and its API calls were exercised over HTTP, but layout, the graph rendering and the dark theme are unchecked.
  The Cytoscape graph is the most likely place for a visual problem. Next step: someone clicks through the Layer 2 demo flow in the README.
- A server was already running on port 8000 when we started (it was not started by this session and was not touched). It predates the rebuild and holds the old graph in memory: **restart it** to see the new graph and screens.
- Telemetry in the stores now contains 80 retrievals produced by our own HTTP check (4 users, 4 queries, 5 hits each). They are real searches but scripted; Q10 is computed from them.

## Next

1. Click-through of both UIs and fix what it shows.
2. Team decisions below.
3. Layer 3 follow-ups: run a fixed question set in both modes and save it under `results/` (real runs only); click through the Ask screen; decide whether to try a larger model.
4. Re-run the chunking comparison with the new embeddings (Layer 1 caveat).
5. `reflection.md` (to be written by the team from real experience, not generated).

## Open decisions (need a human)

| Decision | Why it matters |
|---|---|
| Reuse anything from the earlier `D:\watson` / `D:\watson_sai` build? | Not inspected; this project started clean |
| Read the four validation checks (brief section 4.10) against our SHACL mapping | We mapped them from photographed pages; a person should confirm the wording |
| Expiry retention for the three study types the brief does not specify (1095 days assumed) | Assumption flagged in `design_decisions.md` |
| SSO / MFA / HTTPS for sign-in? | Username and password per role now exist (2026-10-29); the rest is listed in `nfr_design.md` |
| Licence of the Databricks model files | Check before committing them or the generated catalog (9.8 MB) to GitHub |
| Classification order (public, internal, restricted, confidential) | The source lists four levels but defines no ranking |
| `masterdata_address_id` has two classification tags (internal and restricted) | Left unclassified until a steward decides |
| 7 of 21 indications have no exact disease ID | Left blank rather than guessed; a domain expert could assign them |
| Bundle size (Cytoscape, about 730 kB) | Code-splitting the graph pages would help; not done |

## Not committed

Nothing has been committed or pushed. The git root is `D:\` and `D:\watson_Team` is partly untracked (all of `layer2/` is new). Layer 1 files changed for the hook:
`layer1/lifecycle_manager.py`, `layer1/abac_engine.py`, `layer1/api/main.py`.
