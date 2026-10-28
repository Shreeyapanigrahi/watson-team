# Change log

Dates are the day the work was verified. Earlier planning days were not separately recorded.

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
