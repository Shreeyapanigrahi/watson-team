# Chunking strategy comparison (Layer 1)

Documents indexed: 42. Queries: 6. Retrieval: TF-IDF cosine (lexical stand-in; re-run with MiniLM embeddings in Layer 2).

| Strategy | Chunks | Mean tokens | Min-max tokens | Top-1 relevant | Top-3 relevant |
|---|---|---|---|---|---|
| A_fixed_256 | 126 | 219.6 | 57-256 | 4/6 | 10/18 |
| A_fixed_512 | 74 | 359.5 | 105-512 | 4/6 | 7/18 |
| B_structural | 179 | 130.2 | 14-422 | 4/6 | 7/18 |

## Per query (top-1)

| Query | A_fixed_256 | A_fixed_512 | B_structural |
|---|---|---|---|
| C1: What do EU oncologists think about Opdivra tolerability? | NOT relevant (study_015.pdf) | NOT relevant (study_015.pdf) | NOT relevant (study_013.pptx) |
| C2: Reimbursement agreement for Keytrava in Germany | relevant (study_028.pdf) | relevant (study_028.pdf) | relevant (study_028.pdf) |
| C3: Market share of brands in plaque psoriasis in the EU | relevant (study_017.pdf) | relevant (study_017.pdf) | NOT relevant (study_018.pptx) |
| C4: Unmet need in EGFR-mutant NSCLC | relevant (study_021.docx) | relevant (study_021.docx) | relevant (study_021.docx) |
| C5: Key opinion leader view on Aimovia tolerability | NOT relevant (study_034.docx) | NOT relevant (study_034.docx) | relevant (study_034.docx) |
| C6: Late-stage immunology pipeline targeting IL-17 and IL-23 | relevant (study_044.pptx) | relevant (study_044.pptx) | relevant (study_044.pptx) |
