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
- **Same question, different answer per role (found from the history log, which showed the asker was ADMIN):** only the ADMIN's view has Dupixa co-mentions (4 brands; none for viewer, analyst, spoc), so for the admin the model's plan `?brand coMentionedWith Dupixa . ?brand containsDrug ?drug` ran and returned other brands' drugs (secukimab, adalimutab, risankimab); the evidence was three bare drug names, so the model picked a different one each run. Earlier checks had used the analyst only. Two changes: (1) `named_relations`: if the question names a relation (`containsDrug`, "contains drug", "co-mentioned with", "company"; whole words, from the ontology's own property names) and the entity has it, the first edge must use it; a named relation the entity lacks changes nothing. (2) for queries without aggregates, a join variable directly linked to a selected variable is also selected (`?brand` next to `?drug`), so each row, and its evidence sentence, says how it was reached. Join nodes (chunks, sentiment assertions) and side branches are excluded because they multiplied rows (a first version turned 3 documents into 6 by adding `?country`; caught by re-running, then narrowed). After, real model, ADMIN, 3 runs: `dupilumib`, `answered`; all four roles give the same query. "Which company makes Opdivra?" also became correct (Opdivra's own company; before it returned the companies of co-mentioned brands).
- **The same admin failure with another wording ("Dupixa brand contains which drug ?" gave "Adalibra contains drug adalimutab"):** the relation-naming rule needed the words adjacent ("contains drug"), and 3 of 4 other phrasings ("What drug does Dupixa have?", "Which drug is in Dupixa?", "Name the drug of the Dupixa brand") still took the co-mention path for the admin. Changes: (1) relation words may have up to two other words between them; (2) **answer kind** (`answer_kinds`): "which drug" means the answer is a Drug, so the first edge from the entity must lead to a Drug (a kind word right next to the entity's name, "the Dupixa brand", describes the entity and is ignored; if no such edge exists the choice stays open). After, real model, ADMIN: all 5 wordings give `dupilumib`; full pipeline with the exact question: `answered`. Side effects measured on the same questions as before (analyst, single runs): "Which indications does Opdivra target?" now goes direct (2 rows; before it went through competitors and was wrong); "Which drug class does dupilumib belong to?" now answers (IL-4/IL-13 receptor antagonist; before: 3 failed attempts); company of Opdivra still correct.
- **A regression from the answer-kind rule, found by re-running, then repaired:** for "How many documents mention X" the rule forced a document-level first edge, the model padded it with throwaway edges (`?document mentionsBrand ?brand`, `?brand2`), my context columns turned those leaves into columns (Opdivra: 40 rows) and it wrote `count distinct ?document` with an empty `of` (Eliquex: failed in 2 of 3 runs). Repairs: a context column must be a variable used in at least two edges, and `tidy` fills an empty `of` with the counted variable (it does not change what the plan asks). After: Opdivra 7 rows as before; Eliquex runs, but see below.
- **Still wrong:** "How many documents mention Eliquex?" returns a table (16 rows, or 3 rows on one of three runs) because the model selects its padding variables; it does not return the single number 3. "How does HCP sentiment for Opdivra compare across countries?" now counts sentiment assertions per country instead of averaging scores. Neither was fixed.
- **Run-to-run variation:** the same question can get different plans on repeated runs of the same server (seen: "How many documents mention Eliquex?" gave a document list once and a chunk count twice, three identical calls); the cause was not investigated. That question now gives the chunk count three times in a row, which is the weaker plan (it counts chunks, not documents).
- **Seen, not fixed:** after correct answers the model sometimes adds "The evidence does not contain an answer." (removed by the checker, so a "1 line removed" note shows), and for the average-sentiment question one of 9 supported lines is a copied passage, not a brand.
- **Still open:** the model can pick a relation that has data but means something else (competitors' indications). A larger model or question-specific hints are the next things to try; neither was.

## 25. Matching everyday words to ontology terms by meaning (2026-10-29)

- **Why:** the team wants a question in common vocabulary ("what drug does Dupixa have", "which disease does it treat") to reach the technical term (`containsDrug`, `targetedIndication`) by meaning. The keyword rules of 24 only fire when the property's own words appear: on 16 held-out paraphrases they selected the right relation for 1.
- **What was built:** `layer3/glossary.json` (25 properties and 10 kinds with a plain-English "means" and everyday phrases; **a draft written by Claude, not yet reviewed by a domain person**), `layer3/semantic.py` (the question is cut into windows of 1-5 words with the entity's name and a kind word next to it removed; windows and glossary phrases are embedded with the model Layer 2 already uses, all-MiniLM-L6-v2, no new download; a term scores as its best window-phrase cosine), step 3 shows the matches ("your words -> term, score, closest glossary phrase, whether it narrowed the query"), step 4 uses them. The match only **narrows the model's first edge** (the relation, and the kind of thing asked for); it never writes a query. Nothing clear means nothing narrowed. If the embedding model cannot load, the exact-word rules of 24 are used and the screen says so.
- **How it was measured:** 16 `dev` and 16 `test` paraphrases with the expected relation and kind (`layer3/tests/semantic_eval.json`, labels written by Claude before tuning, may be wrong). Thresholds were chosen on `dev` only; `test` was first looked at after they were fixed. A wrong narrowing forces a wrong plan, a missed one changes nothing, so the relation threshold favours precision: 0.85 (lower thresholds gave 12 of 16 dev hits but also 4 wrong ones). Kind threshold 0.80, margin 0.05.
- **Result, selection only:** relation chosen correctly dev 7/16, test 5/16, wrongly forced 0 and 0 (keyword rules: 1/16 and 1/16); kind chosen correctly dev 10/16, test 12/16, wrongly forced 1 and 0 (keyword rules: 6/16 and 7/16, 1 wrong each).
- **Result, end to end (real model, ADMIN, `test` set, single runs):** the first edge of the plan used an expected relation for 13 of 16 questions with the matching, against 8 of 16 with the exact-word rules alone; never worse on any question; all 16 returned an accepted query either way. Of the 3 still wrong: "competitors of Dupixa" and "brands rival Eliquex" were matched to `competesWithin` (score 0.98-1.0) but those brands have no `competesWithin` edges in the data, so the plan falls back to another relation (co-mentions) and answers a different question without saying so; "Which firm owns Dupixa?" scored 0.843, just under 0.85.
- **Three bugs of my own found on the way, by looking at the numbers:** the kind-word filter did not know plurals ("brands"), so generic words kept scoring against relation phrases; a window made only of kind words ("which brands") also scored against relations and is now excluded; that exclusion also removed "active ingredient", which is a phrase of both the Drug kind and `containsDrug`, so a window that is a relation phrase in the glossary is kept.
- **Full pipeline regression (ADMIN, single runs):** Dupixa's drug, its active ingredient, Opdivra's indications and company, co-mentions, and knowledge spaces answered; "average sentiment" answered but the model added ten "The evidence does not contain an answer." lines (all removed by the checker, risk medium); "How many documents mention Opdivra?" gave `no_supported_answer`. The last one is not caused by the matching (same query and same failure with the exact-word rules only): the query returns a list of documents, not a number, and the model wrote only "[E1]".
- **Not done / limits:** the glossary has not been reviewed; the evaluation labels are mine; end to end was run for one role (ADMIN) and one run per question; step 3 latency was not measured; the matcher is a small 384-dim model and will miss phrasings the glossary does not cover (edit the file, no code change needed; restart the server). Open: say so when a named relation does not exist for the entity instead of falling back to another one (done, see 26).

## 26. Saying so when the named relation does not exist for the entity (2026-10-29)

- **Why:** "competitors of Dupixa" was matched to `competesWithin` correctly, but Dupixa has no such edge in the data. The plan fell back to co-mentions and answered another question without saying so. Wrong and silent is worse than "not found".
- **What was built:** `relation_gap` in `layer3/sparql_gen.py`, run before the model is called. For each named relation (exact words or semantic match) and each anchored entity it asks: does this relation touch the entity's class at all (the user's own shapes first, else the ontology's `rdfs:domain`/`range`, so a multi-hop path stays possible)? And does the entity have an edge of it in the user's graph? If it can touch the class and the entity has none, `generate` returns `status: no_relation` with a reason and no query. The reason names the relation and lists, in the ontology's own labels, the relations the entity does have. Step 4 shows a warning with that reason; the final message says "No graph facts were used" and the answer rests on passages only.
- **It is per user:** the check reads the ABAC-filtered graph, so a relation a role cannot see counts as missing for that role.
- **Result (real model, ADMIN, held-out `test` set, single runs):** the two competitor questions now return `no_relation` instead of a co-mention table; the other 14 are unchanged (first edge right 13/16 as before, no false `no_relation`). Control: "Which brands rival Opdivra?" still queries, because Opdivra has `competesWithin` edges (Keytrava, Lumakro, Tagrisa). 44 Layer 3 tests pass (2 new).
- **Limits:** after the gap the passages still answer and the checker accepts them (Dupixa: "maintains a 19% market share" came from a passage; risk medium) — the message says the graph was not used, but the answer text itself does not say so. "Which firm owns Dupixa?" is still wrong (score 0.843, below 0.85). Checked for one role and one run per question. The Ask screen was not opened in a browser.

## 27. "How many ...": one number, counted over what the user may read (2026-10-29)

- **Why:** "how many drugs are in our knowledge space" returned 12 rows of "drug, 1" (the model selected the name next to `COUNT`, so it grouped), and the answer "17" came from a catalog sentence. "How many geographical areas are mentioned" returned 3 when the readable passages cover 9: the model required a passage AND a document to cover the same place (an intersection).
- **Decision on meaning (the user did not choose; I took the recommended one):** "in our knowledge space" means what the documents **this user may read** mention, so a viewer gets a smaller number than the admin. Entity totals in the catalog are the same for every role, so counting them would tell a viewer how many things exist in documents they cannot read. Not done: a wording that asks for "all entities regardless of access".
- **What was built (`layer3/sparql_gen.py`, no per-term rule):**
  - `count_intent`: only the sentence shape is read (`how many`, `number of`, `count of`); wording that asks for a breakdown or ranking (`per`, `each`, `by <group>`, `most`, ...) is not one number. "by" after a participle or before "the/our" ("covered by", "targeted by") is an agent, not a group.
  - `counted_class`: the kind counted comes from the ontology's kind words, then from the glossary by meaning (`semantic.kind_of`, with the singular form tried too: "specialties"). No kind found (the ontology's "classes", "tables", "triples") means this is not a count over the graph and nothing changes.
  - Grammar: the select is exactly one column, `count_distinct` of that kind; no opinion or knowledge-space variable (no entity names a space, so it only adds a join).
  - **Finest grain:** the documents carry some links their passages carry in more detail. Measured: passage-level always contains the document-level set (geography 9 against 3 and drug classes 11 against 0 for the admin; the other kinds are equal). With no entity named, only the passage-level relation is offered where both exist.
  - **A bare count is one edge** (no entity, number, other relation or other kind in the question, judged on the question without the counted phrase and without scope words): for a kind the documents mention, only the edges from documents or passages to that kind are offered, taken from the user's graph; for documents, spaces, passages and opinions, only edges that every one of them has, found by counting in the user's graph. A 3B model padded plans with unrelated edges when it had to find the edge among about 100.
  - `count_problem` (feedback for the retry): one column, `of` of the right kind, no unwanted variable, no document-level edge where the passage-level one exists, and **no variable in a single edge for a kind the question does not mention** (it only adds a requirement: "documents that mention Opdivra" with an extra `?drug` edge gave 4, not 8, on some runs).
  - `layer3/evidence.py`: a single number is worded "Number of distinct drugs in the documents you may read: 12" (the ontology's own class label, plural); when the graph answers a count, the knowledge-space and catalog sentences are left out (the model copied "3" from one of them); the catalog sentence now says "(the same list for every role)" and adds what the readable documents mention. It had been labelled "your view" while identical for every role.
- **Measured (real model, SPARQL step only, expected numbers from an independent query on each role's graph, single runs):**
  - Development set (13 questions I tuned against): admin 13/13, analyst 13/13, spoc 13/13, viewer 12/13.
  - Held-out set (16 new wordings and kinds, written before the last fixes): first pass admin 13/16, viewer 12/16; the three failures ("by" read as a breakdown, "specialties", "covered" read as a qualifier) were fixed, so the re-run is **partly tuned**: admin 16/16, analyst 16/16, viewer 15/16, spoc 15/16.
  - Three control questions ("classes in the ontology", "for each study type", "most documents") are not treated as counts.
  - The user's two questions through the whole pipeline: admin 12 and 9, viewer 3 and 7, each equal to the independent count.
- **Known misses and limits:**
  - Viewer, "how many brands compete with Opdivra": the wording scores 0.801 against `competesWithin` (threshold 0.85), so the gap rule does not fire and the count of co-mentioned brands (5) is returned for a true 0. It is the relation recall limit of the draft glossary, not specific to counts.
  - Viewer and spoc, "How many documents talk about Eliquex": `no_relation` (they read no document that mentions it) rather than 0; I kept it because "0" would suggest no such documents exist.
  - The answer step still sometimes writes "Note: E1 ... is 9" before the number; the check accepts it. The number check accepts a number found in ANY evidence item, which is why leaving the distractor sentences out matters.
  - Qualified counts beyond a named entity (a numeric condition, "brands with sentiment above 0.7") use the free plan with the single-number select; not measured.
  - "Knowledge space" cannot name one space (no entity for it), so "how many documents are in the Oncology space" counts all readable documents.
  - One run per question per role; the model's output varies a little between runs on identical input.
- **Addendum, "how many total documents are ingested":** the query was right (43 admin, 9 viewer) but it was routed `semantic_first`, so five unrelated passages were added, and the 3B model answered "no answer" (or, after the old retry prompt "say what it measures", described the line without the number). Fixed in two places: passages are left out whenever the graph returned a count (any route), and `answer.generate`'s retry tells the model the line is the database's count (the old retry said "say what it measures", which produced a description without the number); if the reply still does not contain the number, the evidence line is quoted as the answer with a note in step 7. The checker supports it because it is the evidence line. **Tried and removed:** the same sentence in the first prompt. With it the model wrote only "E1 [E1]" even on questions where it had stated the number before ("SHORT: 12"); without it, 4 of 6 count questions had the model state the number itself and 2 used the quoted line (single runs, admin and viewer).

**Addendum: "how many number of documents are associated with US ?" (admin saw no answer).** Two causes, found by replaying steps 4-8. (1) `count_intent` took the first cue, "how many", and read the next word, "number", as the thing counted (it stopped at "of"). "number" has no kind, so the question was not treated as a count and the model wrote a per-document listing (15 rows of "1"); the evidence held 8 of those rows, and the model answered with bare tags (`[E1] ... [E10]`), which the checker drops. Fix: a second cue straight after the first ("number of", "count of", "total number of") is skipped (`FILLER_NUMBER_OF`), a grammar rule, not a list of terms. (2) With the count recognised the model wrote only `15 [E1]`, and `answer.parse` treated the leading `15` as a list marker and removed it, while the checker drops a statement with no words. Fix: only real list markers (`-`, `*`, `1.`, `1)`) are removed, and a count counts as stated only inside a sentence with words of its own (`_states`); otherwise the evidence line is quoted. Side effect worth knowing: any line that starts with a number or a year ("2023 sales rose") used to lose it. **Measured:** admin, real model, two runs: 15, supported 1.0, matches an independent count of 15; both count sets re-run for admin and viewer give the same results as before (dev 13/13 and 12/13, held-out 16/16 and 15/16, same two known misses), so no regression. That first fix only worked for "US", which is a region that documents link to directly; every country failed (3 attempts, no join from passage to document), and so did the viewer for entities nothing readable links to.

**Second fix: "documents associated with one named entity" works for any entity (`entity_document_route`).** Found by running the same wording for EVERY entity (names read from the graph, not listed): 4/11 geographies right. A plan is a conjunction of edges, so it can follow one level only: the document's own link, or passage -[prop]-> entity plus passage -isChunkOf-> document. Measured per entity on both roles' graphs (independent counts): one level always contains the other, but not always the same one. EU: 25 documents directly, 15 through passages; US: 15 and 12; Germany, France, Spain, Italy, UK, United States and 11 drug classes: 0 directly, passages only; brands, drugs, indications: equal; therapeutic areas: directly only. So an earlier claim in this file ("the passage-level set always contains the document-level set") holds for the set of distinct entities of a kind, not for the documents of one entity. The rule: for one named entity and a count of documents, read the user's graph, count the distinct documents each level reaches and all of them together (union), and offer the level whose count equals the union (document level on a tie, no bridge needed). If neither level contains the other, the ordinary plan decides (not seen in this data). An entity that nothing readable links to is routed to the document level, so the count is 0 and not a failed query. Two things the first version got wrong, both general: the entity's own name was read as part of the question (the digits in "PD-1" and "Anti-CD20", and the word "drug" in "antibody-drug conjugate", switched the route off), and the model padded the plan with a made-up filter on `?count` (the grammar now allows no filters when the edges are fixed and the question has no number). The relation-gap message ("no such relation") now stands aside only when the missing relation is the very link being counted, so "documents that mention Eliquex" gives the viewer 0 instead of `no_relation`, while "competes with" still gets its message.

**Measured (real model, SPARQL step, single runs, expected = readable documents linked directly or through a passage, independent query per role):** "how many number of documents are associated with {X} ?" for every entity of every kind, admin and viewer: Geography 11/11 and 11/11, DrugClass 16/16 and 16/16, TherapeuticArea 4/4 and 4/4, Indication 21/21 and 21/21, Drug 17/17 and 17/17, Brand 18/18 and 18/18 (87 each, all on the first attempt in the geography run). The earlier count sets, re-run: tuned 13/13 admin and 12/13 viewer (same known miss: "compete with" scores below the threshold, 5 where the answer is 0), held-out 16/16 and 16/16 (the viewer's Eliquex question is now 0 instead of `no_relation`); the three controls are still not counts. **Limits:** one named entity only (two named entities, or another kind in the question, use the ordinary plan); only counts of documents (not passages); "associated with" means linked directly or through a passage, which is how the system defines association, not a medical claim; the unit tests use a six-document toy graph, the evidence above is on this data.

## 28. Username and password sign-in, one account per role (2026-10-29)

**Before:** the login screen listed the four registry users and anyone could click one; `/api/users` listed them to any caller; tokens never expired.

**What we chose and why**
- **Account = registry user.** The username is the `user_id` without `u_` (`viewer`, `analyst`, `spoc`, `admin`). Signing in only decides which registry user a session belongs to, so ABAC, Layer 2 and Layer 3 are unchanged. That is why 254 existing tests could keep their logic and only their `login` helper changed.
- **scrypt from the standard library, salted per account.** No new dependency, no cost, and a slow hash (measured 52 ms per hash on this laptop, mean of 10, while the full regression was running) that makes offline guessing expensive. Stored as `scrypt$n$r$p$salt$hash`; the test checks the password is not in the file and that equal passwords give different hashes. Not measured: how scrypt cost compares with bcrypt or argon2 on this machine.
- **The user types the passwords, not us.** `python -m layer1.set_password` prompts with hidden input (twice) in the user's own terminal, so no password passes through chat, source or docs. There are no default passwords such as admin/admin.
- **Same answer for a wrong name and a wrong password**, and a dummy hash is checked for unknown names, so neither the message nor the response time says which usernames exist. Lockout counts unknown names too, so a 429 does not reveal a real account either.
- **Lockout: 5 wrong passwords in 10 minutes lock that username for 5 minutes.** Cheap and enough against guessing. Cost: someone who types a real username can lock that person out for 5 minutes (documented in `nfr_design.md`).
- **Sessions: 8 hours, logout ends them, a password change ends the user's other sessions.** Before, tokens lived until restart.
- **Audit:** LOGIN, LOGIN_FAILED, LOGOUT, PASSWORD_CHANGED. An unknown username is logged as `null` and passwords never, because people paste passwords into the username box.
- **`/api/users` removed**, since it listed every account to anyone.

**Evidence:** 15 new tests in `layer1/tests/test_auth.py` (each role lands on its own user, same answer for wrong name and wrong password, no plaintext password in the store or the audit log, lockout and its expiry with a fake clock, 8-hour expiry, logout, password change, the script), plus the full regression.

**Known limits:** no HTTPS (passwords cross the network in clear text), no MFA or SSO, no self-service reset, sessions and counters in memory, and anyone with access to the machine can run `set_password`. The login and Account screens have not been opened in a browser.

## 29. The question chooses its search: graph + vectors, or vectors alone (2026-10-31)

- **What:** the Ask Watson page no longer has Semantic / Plain / Compare radios. The pipeline's `mode` defaults to `auto`, and `understand.choose_search` decides from the question alone. Graph + vector search ("semantic") is used when the question shows something the graph can answer: a catalog entity, a count or list, a relation, comparison, ranking or trend, or the platform's own structure. Only a question with none of these (an open question about what the documents say, such as "Tell me about prescribing barriers") goes to vector search alone ("plain"): no SPARQL step, no entity mapping, passages ranked by meaning. Step 3 shows the choice and the reasons, the answer carries a badge, and the audit line records `mode` and `mode_requested`.
- **Why a new decision and not the existing `route`:** `route` only says how evidence is ranked. "How many documents are ingested?" names no entity, so `route` calls it `semantic_first`, yet only a graph count can answer it; using the route label would have sent it to vector search and lost the exact number. So the new rule looks for graph cues (entity, count/list wording, relation/ranking/trend wording, platform terms) and only their absence means vector search.
- **Why the doubtful case goes to the graph:** the full path runs the same vector search and adds the graph's facts to it (it also weighs entity overlap and document quality when ranking passages, and leaves the passages out when the graph returns an exact count), so a wrong choice toward the graph costs a little time, while a wrong choice toward vectors loses the graph's facts. The cue list is broad on purpose ("which", "most", "region" count as relation cues).
- **Evidence:** on the 55 distinct questions asked so far (history), 55 stay on the graph path, exactly as before; of the open questions tried, 4 of 4 go to vector search and 5 of 5 graph-type ones (entity, count without entity, ranking, trend, platform structure) to the graph (checked with the router, no model needed). 5 new Layer 3 tests (router decisions, auto to vector with no SPARQL call and no schema request to the model, auto to graph, a forced mode is honoured and marked `chosen_by: caller`, the HTTP default is `auto` and a bad mode is a 422).
- **Not measured:** answer quality of the vector-only path against the full path on open questions, because the language model server was not running (it is stopped for memory). The plain path is the old plain-RAG baseline, whose quality was compared earlier. Removing the radios also removes the side-by-side Compare view from the admin page; the API still accepts `semantic` and `plain` (kept for tests and scripts), and the viewer chat's "Retrieval mode" menu (Semantic, Plain, Compare both) was removed too, so no screen lets a person pick the path.
