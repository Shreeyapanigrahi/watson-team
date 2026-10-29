# Architecture

Diagrams are in `architecture/` (`system_overview.md`, `layer1_architecture.md`, `document_lifecycle.md`).
This file explains how the parts fit together in words.

## Shape

One Python process (FastAPI) serves the REST API and, once built, the React app from `frontend/dist`. State is JSON files on
disk. There is no database and no external service. Layer 2 adds its own folder and reads Layer 1's outputs; Layer 3 will do the same.

## Layer 1 in one paragraph

A file arrives through the upload endpoint or `bulk_ingest`. `lifecycle_manager.ingest()` runs the pipeline in a fixed order:
parse, exact-duplicate check, chunk, PII scan, metadata extraction, near-duplicate check. The result is saved as a lifecycle
record with a state of UNDER_REVIEW, and the parsed text and chunks go to `staging/`. A reviewer then approves or rejects.
Approval sets the sensitivity level and ACL, computes the quality score and expiry date, and moves the document to ACTIVE.
Only ACTIVE documents may be indexed by Layer 2 (`assert_indexable`). The ABAC engine is called at every read and every action.
The audit log records every step.

## Module map

| Concern | Module |
|---|---|
| Orchestration, state machine | `lifecycle_manager.py` |
| Access control | `abac_engine.py` |
| Text extraction | `parser.py` |
| Chunking | `chunker.py` |
| PII | `pii_detector.py` |
| Duplicates | `dedup_detector.py` |
| Metadata and taxonomy | `metadata_extractor.py`, `vocab.py` |
| Scoring and retention | `quality_scorer.py`, `expiry_manager.py`, `expiry_scanner.py` |
| Storage | `jsonstore.py` (atomic writes, one lock), `audit_log.py` |
| Catalog for Layer 2 | `catalog.py` |
| HTTP | `api/main.py` |
| Seeding and evidence | `generate_data.py`, `bulk_ingest.py`, `chunk_comparison.py` |

## Frontend

React 18 and Vite, plain CSS, no UI library. `src/api.js` is the only place that calls the backend. Routes are guarded by role.
Screens: Login, Dashboard, Upload, Documents, Document detail (with the review form), Review queue, Expiry, Audit,
Document catalog, Design evidence. Layer 2 adds Knowledge space, Entities (and detail), Data catalog, Ontology, Graph health, SPARQL queries, and a Knowledge tab on Document detail.
The graph view uses Cytoscape (the only added dependency).

## Layer 2 in one paragraph

The data catalog (`layer2/catalog/pharma_data_catalog.json`, built from the three model files in `architecture/`) is the single source of truth for what entities are called
and where they are stored; the ontology (`layer2/ontology/pharma.ttl`) holds structure only. When a reviewer approves a document, Layer 1's state hook starts enrichment:
check the taxonomy, find entities with a dictionary built from the catalog, resolve them (exact, fuzzy, or held as provisional for a person to decide), extract sentiment
statements, embed the chunks, and write triples and vectors together. SHACL (15 shapes) validates the graph and a code check ties every chunk to a vector. Reads go through
`graph_view.visible_records()`, which allows only ACTIVE documents the user's attributes permit; the vector search sees only those chunks. Diagram: `architecture/layer2_architecture.md`.
It is mounted into the same FastAPI app at `/api/l2`.

## Layer 3 (built)

`layer3/` reads Layer 1 (ABAC, audit) and Layer 2 (graph, FAISS, catalog, ontology views) and mounts `/api/l3` in the same FastAPI app. It copies the graph per user and cuts out unreadable documents before anything else runs;
a Qwen model served by llama-server (llama.cpp) writes the SPARQL plan and the answer; code checks both. Diagram, safeguards and files: `architecture/layer3_architecture.md`.

## Extension points for Layer 3 (original notes, now implemented)

- Layer 3 should call `layer2.search.search(user, query)` (already ABAC-filtered, returns chunk and document ids) and cite those ids.
- Layer 3 must apply the same `abac_engine.chunk_acl` filter before any chunk reaches an answer, and log `QUERY` events.
- The audit event type list already includes the Layer 2 and 3 events (`ENRICHMENT_*`, `QUERY`, `HALLUCINATION_RISK`, `TAXONOMY_UPDATE`).
