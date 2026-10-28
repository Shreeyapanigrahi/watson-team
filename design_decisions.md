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
