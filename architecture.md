# Architecture

Diagrams are in `architecture/` (`system_overview.md`, `layer1_architecture.md`, `document_lifecycle.md`).
This file explains how the parts fit together in words.

## Shape

One Python process (FastAPI) serves the REST API and, once built, the React app from `frontend/dist`. State is JSON files on
disk. There is no database and no external service. Layers 2 and 3 will add their own folders and read from Layer 1's outputs.

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
Data catalog, Design evidence.

## Extension points for Layers 2 and 3

- Layer 2 reads `stores/data_catalog.json` (fields, entity inventory) and ACTIVE chunks from `staging/`.
- Layer 3 must apply the same `abac_engine.chunk_acl` filter before any chunk reaches an answer, and log `QUERY` events.
- The audit event type list already includes the Layer 2 and 3 events (`ENRICHMENT_*`, `QUERY`, `HALLUCINATION_RISK`, `TAXONOMY_UPDATE`).
