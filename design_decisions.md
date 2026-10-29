# Design decisions

Each entry: the choice, the reason, the evidence, and what would change our mind. Written so it can be said aloud to a reviewer.

## 1. Chunking: fixed 256 tokens, 20% overlap

- **Choice:** fixed-size chunks of 256 whitespace-separated words, overlapping 20%.
- **Why:** it retrieved the right document more often than the alternatives, and gives small, focused chunks.
- **Evidence:** `results/chunking_comparison_run4_final.md`. Six pre-written queries, relevance judged from the generator's ground truth.
  Run 4: fixed-256 got the right document at rank 1 for 6 of 6 queries and in the top 3 for 9 of 18; fixed-512 got 4/6 and 8/18; structural
  (by heading/section) got 3/6 and 7/18. Across the four corpus lengths the combined top-1 plus top-3 score was 13, 13, 14, 15 for fixed-256,
  11, 13, 11, 12 for fixed-512 and 8, 10, 11, 10 for structural. Fixed-256 was ahead in three runs and tied with fixed-512 in run 2 (13 each), where the tie rule
  (smaller chunk wins) decided it. The decision rule (higher top-1 plus top-3 wins, tie goes to the smaller chunk) was written before the final run.
- **Limits, stated plainly:** retrieval was TF-IDF, not embeddings, because the PyTorch download was not approved. Six queries, 42 documents, synthetic data.
  Differences are a few queries wide. We lengthened the synthetic documents between runs 1 and 4 because the first corpus was too short to compare chunk sizes; all runs are kept.
- **Would change if:** an embedding-based re-run in Layer 2 favours another size.

## 2. PII: flag, never decide

See README, "Hardest design decision". Direct identifiers always flag. A person name flags only next to an identifier or in a patient context.
Brand, drug and company names are ignored. A KOL quote ("Dr. X noted...") is not PII. Approving a flagged document needs `pii_override` and a reason, both logged.
Evidence: `test_planted_pii_is_flagged_with_all_identifier_types`, `test_named_kol_in_attributed_quote_is_not_pii`, `test_no_false_positives_across_the_rest_of_the_corpus`, `test_pii_blocks_approval_until_explicit_override_with_reason`.

## 3. Duplicates: three outcomes, not one

- Exact SHA-256 match: auto-REJECTED (certain, nothing to judge).
- Near duplicate (same first 200 characters and difflib ratio above 0.85, only compared against documents with the same title or filename base): held for a human.
- New version (same title and author as an ACTIVE document, later date): flagged; the reviewer may archive the old one.
- **Why:** auto-rejecting similar documents would silently drop real updates. Comparing only likely candidates keeps it fast and avoids false hits between unrelated reports.

## 4. Lifecycle: one added edge

ACTIVE to UNDER_REVIEW. The brief (section 4.8) requires expired documents to return to review but its transition table omits the edge. We added it. Everything else follows the brief.

## 5. ABAC

Roles set a maximum sensitivity; the document's ACL narrows it by therapeutic area and region (empty list means all). SPOC may review only inside their therapeutic area.
Documents a user may not see are omitted from lists, not shown redacted, so their existence is not leaked. The uploader sees their own pending document as status only,
because they have not been cleared for its content and the pipeline has not yet classified it. Every denial is audited.

## 6. Quality score

`0.3 completeness + 0.3 text richness + 0.2 sample size + 0.2 freshness`, weights from the brief.
- Text richness = unique clinical terms per word, scaled by a target of 0.10. We first used 0.08, which saturated (mean score 0.83), so we set the target from the corpus
  (observed ratio 0.038-0.112, median 0.069). This is a calibration to our own synthetic data, not a universal constant.
- Sample size: 300 or more scores 1.0; under 20, or none stated, scores 0.1; linear between.
- Freshness = 1 minus age in days over 730, floored at 0.

## 7. Retention

HCP survey 730 days, KOL interview 365, competitive landscape 1825 (from the brief). **The other three study types use 1095 days: our assumption**, not in the brief.
The reviewer can override the expiry date per document.

## 8. Identifiers

Sequential `Watson:Doc_NNN` and `Watson:Chunk_NNN_III`. The brief is inconsistent (uuid4 in one place, `Doc_001` in its demo commands). Sequential is readable in an audit log and in SPARQL results.
Cost: sequential IDs are guessable and collide across systems; acceptable for a single local prototype.

## 9. Storage

JSON files with atomic writes (write a temp file, then rename) and one process-wide lock; the audit log is append-only JSON lines. Required by the team; fine at tens of documents. See `nfr_design.md` for limits.

## 10. Scanned PDFs

A PDF with no text layer is detected, warned about, and cannot be approved (`test_scanned_pdf_cannot_be_approved`). We do not run OCR: it would need a download and its accuracy would be unverified.

# Layer 2: Semantic Foundation

Numbers below come from runs on 2026-10-28 (43 ACTIVE documents, 153 chunks, 5,596 triples). "Tested" means a test in `layer2/tests` covers it.

## 11. The data catalog is the single source of truth

- **Choice:** one JSON file, `layer2/catalog/pharma_data_catalog.json`, owns entity names, synonyms, hierarchy and the mapping from ontology terms to catalog tables and columns. `ontology/pharma.ttl` owns structure only (classes, properties, axioms). The graph, the FAISS index and caches are derived and can be rebuilt with `python -m layer2.backfill --reset`.
- **Why:** with two places that could say what "Opdivra" is, they drift apart. One owner means a fix is made once.
- **Evidence:** `test_drift_and_sparql.py` fails if Layer 1's `vocab.py` (a second copy of the brand and indication names) disagrees with the catalog.
- **Built from:** the three files in `architecture/`, never modified. `model.json` is the base (it holds every CSV field plus two extras), the CSV is a cross-check only, the TTL contributes class and property names. The catalog has table and column definitions, not data rows, so our own reference rows (18 brands, 17 drugs, 16 classes, 21 indications, 4 therapeutic areas, 11 geographies) live in a clearly marked Watson extension domain (`pmr`, 5 tables, 38 columns).
- **Limits, stated plainly:** the supplied model rates itself "needs_work" (91/100 by rule, 72/100 by its own AI review). 1,977 of its 2,423 foreign keys are marked AI-generated, and only 664 of 8,874 columns carry a classification tag. The UI shows these flags and does not hide them.
- **Would change if:** the catalog's licence does not allow publishing it (open item).

## 12. Grounding: `groundedIn`, not `owl:equivalentClass`

- **Choice:** an ontology term is linked to its catalog table or column with `Watson:groundedIn` (a sub-property of `rdfs:seeAlso`) and carries a status: grounded (13 terms), partial (5) or extension (80), of 98.
- **Why:** a catalog table is a storage structure; a class is a concept. Saying they are equivalent is a false claim, and a reasoner would act on it.
- **"Extension" is not a failure:** the supplied pharma model has nothing for documents, chunks, sentiment or lineage, so those terms honestly have no counterpart. 64 of the 80 were not listed by hand and default to "extension"; the count is reported, not hidden.
- **Class-level alignment that is looked up, not remembered:** `Watson:Drug owl:equivalentClass obo:CHEBI_23888`. No ID was written from memory; a guessed class-level DOID was removed.

## 13. The brief's four hard questions

1. **Why is a sentiment reading a named node (`SentimentAssertion`) and not a property on the brand?** A reading has its own brand, indication, region, date, score and source passage. A property holds one value, and a brand has several readings across years. A named node can be traced to its chunk (`derivedFrom`), which is how Q3 asks about change over time.
2. **Why is `DocumentChunk` a class with its own URI?** Access control, citations and vectors all work per chunk. The chunk URI is what the FAISS id, the ACL check and the answer's citation all point to. Chunk text is deliberately not stored in the graph (it stays in Layer 1 staging), so the sensitive text has one home.
3. **How is `lifecycleState` modelled?** As a datatype property on the document ("ACTIVE" etc.), not a named state individual. A plain `FILTER(?state = "ACTIVE")` is simple and fast, and the lifecycle has a fixed set of values. Trade-off: a state individual could carry its own metadata (who may enter it), which we do not need.
4. **Why `skos:exactMatch` for diseases but `skos:closeMatch` for therapeutic areas?** A disease label that equals the Disease Ontology label or one of its synonyms is the same thing. A therapeutic area such as "Oncology" is a business grouping that overlaps the MeSH heading "Neoplasms" without being identical, so the weaker claim is the honest one. 14 of 21 indications have an exact DOID; the 7 without one (for example heart failure, EGFR-mutant NSCLC, plaque psoriasis) are left blank, not guessed.

## 14. Entity extraction: a dictionary built from the catalog

- **Choice:** a spaCy `EntityRuler` whose patterns come from the catalog's names and synonyms. Confidence is 1.0 for an exact match, 0.8 ignoring case, 0.6 for a partial match. Terms under 4 characters match only with exact capitals. Unknown names that look like "Brand (inn)" become provisional entities for a person to decide.
- **Why not a trained model:** our documents are synthetic text built from a known dictionary, so a model would add a download and uncertainty without a measurable gain. A dictionary is explainable: every mention says which catalog entry it matched and how.
- **Limits:** this corpus produces 0 provisional entities, because every name is already in the catalog; the approval flow is tested with a planted candidate, and the corpus does not show it. The "Brand (inn)" rule is a heuristic and can be wrong, which is why a person decides. Two-letter region codes match only in exact case, so "IT" at the start of a sentence could still be a false hit.

## 15. Order of work: taxonomy first, compute before commit

- Taxonomy values are checked before anything is written, so a document with an unknown value fails with no side effects.
- Embeddings and triples are computed first and written together at the end. A failure logs `ENRICHMENT_FAILED`, the document stays ACTIVE, and vectors are rolled back (tested).
- Writes to the graph are idempotent per document, so a re-run replaces and does not duplicate.

## 16. Access control inside Layer 2

- **Rule:** Layer 2 uses `readable(user, record) = state is ACTIVE and ABAC allows the ACL`. Layer 1's `view_level` lets SPOC and ADMIN see non-ACTIVE documents; the graph must not, so Layer 2 is stricter.
- **Filter before search:** the set of readable chunks is computed first, and only those vectors are searched. Filtering afterwards could leak through ranking or counts.
- **Derived facts are recomputed per user** from readable chunks only: co-mentions, sentiment averages, space counts. Evidence from the running API: Opdivra's average sentiment is 0.48 for the viewer and 0.419 for the admin, because they may read different documents.
- **Audit by query:** every search is recorded as a `RetrievalEvent`. SPARQL Q10 joins those events to chunk sensitivity and shows no role above its ceiling over 80 logged retrievals (run 2026-10-28). Q10 is also tested against a planted violation, which it catches.

## 17. SHACL validation

- **Choice:** 15 shapes (pyshacl 0.40.1, RDFS inference with the ontology loaded) that mirror the brief's four checks: (1) a document has state, sensitivity and expiry; (2) a chunk has one parent, its access attributes and a vector in the index (the index half is checked in code against FAISS); (3) no entity is ungrounded or an orphan; (4) every chunk traces to a lineage record with a reviewer.
- **Result:** conforms, 0 violations over 5,596 triples; 153 of 153 chunks have a vector.
- **Evidence the shapes bite:** `test_ontology_shacl.py` has negative cases that break each rule and expect a violation.

## 18. Vector index: flat, exact, ONNX

- **Choice:** `fastembed` with all-MiniLM-L6-v2 through ONNX (384 dimensions), stored in a FAISS flat inner-product index with stable ids. Ranking is 0.7 x cosine + 0.3 x document quality.
- **Why:** an exact index is correct and simple at 153 vectors. ONNX avoids the large PyTorch download we had not approved. There is no silent fallback: if the model is unavailable the API says so (503).
- **Would change:** at around 100,000 vectors or more, switch to an approximate index (IVF or HNSW). Not needed here; noted in `nfr_design.md`.
- **Still open:** the Layer 1 chunking comparison used TF-IDF. It has not been re-run with these embeddings.

## 19. SPARQL: eleven queries, with the reasoning stated

- Queries are in `layer2/queries.sparql`, each with a question and a reasoning mode. Q6 runs on an OWL RL expanded copy and returns 12 rows, against 0 without inference, so inference visibly matters. Q3 returns Cosentex, Humivex and Opdivra (average above 0.6 before 2023, below 0.4 from 2023 on).
- The 37 sentiment statements in the source text were checked against the graph in both directions: all 37 match.
- Parameters are validated by pattern before substitution. Custom queries are read-only SELECT or ASK and ADMIN-only.
- **Limit:** statements inside segment tables are not extracted as sentiment.

## 20. Layer 3: eight visible steps, one slice per user

- **Order (the team's nine steps):** question, access check, entities and route, SPARQL by the local LLM, entity mapping, evidence and lineage, answer by the LLM, hallucination check; step 9 (questions about the knowledge space itself) is the same pipeline with a `knowledge_space` route. The API streams one event per step (`POST /api/l3/ask`, newline-delimited JSON) and the Ask screen shows them in sequence.
- **Access first, and by construction:** step 2 copies the graph and cuts out every document the user may not read, then recomputes brand links. Every later step (the generated query, the entity counts, the vector search) works on that copy, so a wrong or hostile generated query cannot reach a restricted document: it is not in the graph the query runs on. Withheld documents are counted, never named. Evidence: `test_viewer_slice_has_no_trace_of_the_restricted_document`, `test_a_query_only_sees_the_users_slice`; real run as the viewer: 11 of 44 documents readable, 33 withheld.
- **Plain RAG vs semantic RAG (the brief's comparison):** plain skips steps 3-5 and ranks by cosine alone; semantic adds graph rows and boosts passages that mention the entities in the question. Same model and same hallucination check in both, so the difference is the graph. The Ask screen has a "Compare both" mode. We have not yet measured which is better on a question set; do not claim it.
- **Routing is rule-based on purpose:** three routes with their reasons shown. A 1.8B-3B model is not reliable enough to route, and a rule is inspectable.

## 21. Layer 3: the model that writes the SPARQL (what we tried and measured)

The team asked for SPARQL written by a local LLM, unique per question, not template based. We tried four designs on real questions on the real graph. These are single runs on one machine with temperature 0, not a benchmark.

| Design | Model | Result |
|---|---|---|
| Free-text SPARQL, forced start | Qwen1.5-1.8B-Chat | 0 of 6 parsed (invented keywords, broken triple syntax, loops) |
| JSON plan, free variable names | Qwen1.5-1.8B-Chat | 6 of 6 parsed, about 0 of 6 meaningful (an entity used as a type, one variable that was a Brand and a sentiment at once) |
| Same, with an ontology type-checker | Qwen1.5-1.8B-Chat | 2 of 6 accepted, both answered a different question |
| Same | Qwen2.5-3B-Instruct | 4 of 10 accepted, about half meaningful |
| **Typed edges** (the design we kept) | **Qwen2.5-3B-Instruct** | **5 of 10 accepted, about 3-4 correct** (co-mentioned brands, indications of a brand, average sentiment per brand, space document counts); 3-4 failed outright or returned nothing |

- **What we kept:** the model writes a *plan*: edges between typed variables (`?brand`, `?sentiment`, ...) plus columns, filters, aggregates and ordering, decoded under a JSON schema in which every edge is a (property, subject kind, object kind) combination that actually occurs in the user's graph. Code renders the plan as SPARQL and adds the type triples and display labels the variables imply. The model still decides which edges, entities, filters and aggregates make up the query, so a different question gives a different query; nothing is selected from stored queries.
- **Is that "template based"?** Be ready to answer this. There is no per-question-type template and no stored query, but the *space* of queries is bounded: connected edges over the ontology's real shapes, no UNION, no sub-queries. We chose this because free text from these model sizes produced no usable query. The honest summary: the model composes the query, the grammar guarantees it is well-formed and type-correct, and the checks decide whether it is acceptable.
- **Checks that remain:** the question's entity must appear in an edge, edges must be connected (a stray edge makes a cross product), averages must be of numeric values, SELECT only (no SERVICE, FROM or updates), parse with rdflib, row limit, and a deadline that really stops evaluation (an earlier thread-based timeout left a runaway query burning CPU for 10 minutes; found while testing, fixed, tested).
- **Failure is shown, not hidden:** after 3 attempts the step reports "failed" with each attempt's reason and the answer rests on passages only. We never substitute a stored query.
- **Known weaknesses (measured on Ollama, before 23):** Ollama silently ignored a `prefixItems` constraint meant to force the first edge onto the question's entity, so the entity check still costs retries (latency 10-100 s on this CPU). The 3B model still fails questions that need a join through documents and countries, and negative questions. A 7B model would probably do better and was not tried (about 4.7 GB, slow on CPU).
- **Why 3B and not the brief's Qwen1.5:** the brief's Qwen1.5 was tried (through Ollama, at the time) and could not do the SPARQL step. Say so on the call. It is no longer installed in the project's setup (see 23).

## 22. Layer 3: the hallucination check

- **Method:** each sentence of the answer is checked against the evidence it cites: word overlap, embedding similarity (the same MiniLM model), every number must appear in the cited evidence, every catalog name (other than the one the question asked about) must appear in it. Two hard rules: an invented number or an invented name makes a sentence unsupported. Unsupported sentences are removed and listed with the reason, and logged as `HALLUCINATION_RISK`.
- **Evidence it bites:** the first real run with Qwen1.5-1.8B produced eight sentences about "Germany, Italy, the UK..." with percentages that are not in any document; seven were removed, one generic sentence kept. The same checks are tested (`test_guard_*`, `test_invented_answer_is_removed_and_logged`).
- **Limits:** it is a lexical and embedding heuristic, not a proof. It misses a sentence that reuses the right words with the wrong relation (for example swapping which brand scored higher). Thresholds (0.55 supported, 0.35 weak) were set from a handful of runs and are not tuned.
- **Citations:** the model is asked to tag every line `[E#]`. A sentence without a tag is kept only if the best evidence clearly supports it, and is then marked "source attached by checker". Only evidence cited by a surviving line, and the documents behind it, are shown as sources.
- **If the model is down:** the answer is quoted from the best-matching evidence sentences and the screen says so. No canned answers anywhere.

## 23. Layer 3: llama-server instead of Ollama

- **Decision (the team's):** remove Ollama from the project. The model runs in `llama-server` (llama.cpp), started by hand in a terminal with `layer3/start_llm_server.ps1`, so everyone can see it running and read its log. Same model as before: Qwen2.5-3B-Instruct, Q4_K_M. The GGUF file was copied out of Ollama's store (same SHA-256 as its blob, `5ee4f07c...caa6`), nothing was downloaded.
- **What changed in code:** `layer3/llm.py` now speaks llama-server's OpenAI-style API (`/health`, `/v1/chat/completions`). The JSON-schema constraint is sent as `response_format: json_schema`; the server turns it into a grammar. Removed: Ollama's `/api/*` calls, the unused `complete()` helper, `set_model`, `POST /api/l3/model` and the `can_switch_model` flag. A llama-server process holds one model, so "switch the model" now means restarting it with another file.
- **Server settings and why:** `-np 1` (one request slot, otherwise the 4096-token context can be divided between slots; the log confirms `n_slots = 1, n_ctx_slot = 4096`), `-c 4096`, `--host 127.0.0.1` (nothing leaves the machine).
- **Evidence it works:** the chat template and the schema constraint both worked on a smoke test; 30 Layer 3 tests pass (scripted model); two real questions through `pipeline.ask` as ANALYST, semantic mode, single runs: "Which brands are co-mentioned with Opdivra?" gave the same 5 supported lines as the Ollama run, in 29 s against 49 s; "What is the average sentiment score per brand?" gave 10 supported lines in 15 s against 8 lines in 22 s. Two of those 10 lines name "HCP Perception Survey" and "GLOBAL", which are not brands; the cause was not investigated (it may be a property of the data or of the plan). Two questions are not a benchmark, and the speed difference is not attributed to a cause.

## 24. SPARQL step: anchoring the first edge, and retries that differ (2026-10-29)

- **Question tested:** does llama-server honour `prefixItems` (the rule "the first edge must use the question's entity", 21)? **No.** A toy-schema probe showed it ignores `prefixItems` when `items` is also present (the model put a value first that the rule forbids). A tuple per allowed length (`oneOf` of arrays whose `items` is a list) is honoured and still allows 1 to 5 edges. `plan_schema` now builds that, with `$defs`/`$ref` so the edge list is not repeated per length.
- **Evidence, six real questions (ANALYST, semantic, single runs):** before, 3 of 6 needed 2-3 attempts and one failed; after, all 6 were accepted on attempt 1 (6-20 s each against 6-31 s). Accepted is not correct: of the six, two are right (co-mentions, knowledge spaces), one is partial (sentiment by country: no average), one has the right data in the wrong shape (document count grouped per document), one is wrong ("indications of Opdivra" goes through competitors) and one is unfocused (catalog grounding, 23 rows). No ground-truth scoring was done.
- **The Dupixa case ("Dupixa brand contains which drug"):** the graph holds `Dupixa containsDrug dupilumib` for every role, but the model chose `?brand coMentionedWith Dupixa` and chained `containsDrug` on that brand; Dupixa has no co-mentions for the analyst or viewer, so 3 identical attempts returned 0 rows. Two changes: (1) the anchored first edge may only use relations that have triples for that entity in the user's own graph (`entity_edges`; it reads the graph, not a stored query); (2) a retry uses a new seed (at temperature 0 with one seed the same prompt gave the same plan) and an empty result is explained from the graph: which edges match no triple, and which relations do exist for the entity (`empty_feedback`). After: attempt 1, 1 row, `dupilumib`, 7 s (single run).
- **Also added:** two checks in `semantic_check`: a filter must be on a variable an edge produces, and a filter value may not be an entity or variable (seen: `?name = "Watson:Brand_opdivra"`).
- **"No supported answer" although the query was right (two causes, found by replaying steps 4-8):** (1) `evidence.sentence_for` builds a graph row's sentence from the plan's edges, but when the query selected only a name (`?name`) the edge `Dupixa containsDrug ?drug` had no `?drug` column and was skipped, so the model was handed the bare text `dupilumib`, said "The evidence does not contain an answer", and the checker removed that. Fix: a variable that is not a column but has a label edge in the plan uses that name column (now "Dupixa contains drug dupilumib"). (2) The status looked only at body lines; a model that writes only the `SHORT:` line (usual for one-fact questions) ended as `no_supported_answer` with a supported, cited short answer on screen. Fix: a supported short answer counts as an answer. Before/after on the real pipeline (single runs, ANALYST): Dupixa's drug, therapeutic area and indication all went from `no_supported_answer` (the last two) or a refusal (the first) to `answered`, risk low/none.
- **Seen, not fixed:** after correct answers the model sometimes adds "The evidence does not contain an answer." (removed by the checker, so a "1 line removed" note shows), and for the average-sentiment question one of 9 supported lines is a copied passage, not a brand.
- **Still open:** the model can pick a relation that has data but means something else (competitors' indications). A larger model or question-specific hints are the next things to try; neither was.
