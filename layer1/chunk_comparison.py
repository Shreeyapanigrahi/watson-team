"""Chunking strategy comparison (Section 4.2): evidence for design_decisions.md.

Builds an index per strategy over the generated corpus, runs the same queries
against each, and checks whether the top-1 / top-3 chunks are relevant.

Relevance is judged automatically from the generator's ground-truth manifest: a
chunk is relevant when it comes from a gold document AND contains every
`must_contain` phrase. The queries and rules below were fixed BEFORE the first
run and are not tuned to the result.

HONEST LIMITATION: retrieval here is TF-IDF cosine similarity, a lexical
stand-in. The brief's pipeline uses sentence-transformer embeddings (all-MiniLM-L6-v2),
which need PyTorch (a download we have not made). Re-run with embeddings in Layer 2
before treating this as final; the harness is built so only retrieve() changes.

  python -m layer1.chunk_comparison
"""
import json
import statistics
from pathlib import Path

from sklearn.feature_extraction.text import TfidfVectorizer

from . import chunker, config
from .parser import parse_file

STRATEGIES = {
    "A_fixed_256": lambda p, uri: chunker.chunk_fixed(p.raw_text, uri, n=256),
    "A_fixed_512": lambda p, uri: chunker.chunk_fixed(p.raw_text, uri, n=512),
    "B_structural": lambda p, uri: chunker.chunk_structural(p, uri),
}

QUERIES = [
    {"id": "C1", "text": "What do EU oncologists think about Opdivra tolerability?",
     "gold": lambda m: m["study_type"] == "HCP Perception Survey" and "Opdivra" in m["brands"] and m["region"] == "EU",
     "must_contain": ["opdivra", "tolerability"]},
    {"id": "C2", "text": "Reimbursement agreement for Keytrava in Germany",
     "gold": lambda m: m["study_type"] == "Market Access Insight" and "Keytrava" in m["brands"] and m.get("country") == "DE",
     "must_contain": ["keytrava", "reimbursement"]},
    {"id": "C3", "text": "Market share of brands in plaque psoriasis in the EU",
     "gold": lambda m: m["study_type"] == "Competitive Landscape Report" and m.get("indication") == "plaque psoriasis" and m["region"] == "EU",
     "must_contain": ["market share"]},
    {"id": "C4", "text": "Unmet need in EGFR-mutant NSCLC",
     "gold": lambda m: m["study_type"] == "Indication Deep Dive" and m.get("indication") == "EGFR-mutant NSCLC",
     "must_contain": ["unmet need"]},
    {"id": "C5", "text": "Key opinion leader view on Aimovia tolerability",
     "gold": lambda m: m["study_type"] == "KOL Interview Summary" and "Aimovia" in m["brands"],
     "must_contain": ["aimovia", "tolerability"]},
    {"id": "C6", "text": "Late-stage immunology pipeline targeting IL-17 and IL-23",
     "gold": lambda m: m["study_type"] == "Therapy Area Landscape" and m.get("therapeutic_area") == "Immunology",
     "must_contain": ["pipeline"]},
]


def load_corpus(data_dir: Path):
    manifest = {m["filename"]: m for m in json.loads((data_dir / "manifest.json").read_text(encoding="utf-8"))}
    docs = []
    for fn, m in manifest.items():
        if m.get("planted"):                    # skip scanned, duplicate, version and PII files
            continue
        parsed = parse_file(data_dir / fn)
        if parsed.word_count:
            docs.append((fn, m, parsed))
    return docs


def evaluate(docs) -> dict:
    results = {}
    for name, make in STRATEGIES.items():
        chunks, owner = [], []
        for i, (fn, m, parsed) in enumerate(docs, start=1):
            for c in make(parsed, f"Watson:Doc_{i:03d}"):
                chunks.append(c)
                owner.append((fn, m))
        vec = TfidfVectorizer(stop_words="english", sublinear_tf=True)
        matrix = vec.fit_transform([c["text"] for c in chunks])
        sizes = [c["token_count"] for c in chunks]
        per_query, top1_hits, top3_hits = [], 0, 0
        for q in QUERIES:
            sims = (matrix @ vec.transform([q["text"]]).T).toarray().ravel()
            order = sims.argsort()[::-1][:3]
            flags = []
            for idx in order:
                fn, m = owner[idx]
                text = chunks[idx]["text"].lower()
                flags.append(bool(q["gold"](m) and all(t in text for t in q["must_contain"])))
            top1_hits += flags[0]
            top3_hits += sum(flags)
            best = order[0]
            per_query.append({"query_id": q["id"], "query": q["text"], "top1_relevant": flags[0],
                              "top3_relevant": sum(flags), "top1_document": owner[best][0],
                              "top1_chunk_tokens": chunks[best]["token_count"],
                              "top1_score": round(float(sims[best]), 3)})
        results[name] = {
            "chunk_count": len(chunks), "mean_tokens": round(statistics.mean(sizes), 1),
            "min_tokens": min(sizes), "max_tokens": max(sizes),
            "top1_relevant": f"{top1_hits}/{len(QUERIES)}", "top3_relevant": f"{top3_hits}/{3 * len(QUERIES)}",
            "per_query": per_query,
        }
    return results


def main() -> None:
    docs = load_corpus(config.DATA_DIR)
    res = evaluate(docs)
    out = {"retrieval": "TF-IDF cosine (lexical stand-in; re-run with MiniLM embeddings in Layer 2)",
           "documents_indexed": len(docs), "queries": len(QUERIES), "strategies": res}
    path = config.ROOT.parent / "results"
    path.mkdir(exist_ok=True)
    (path / "chunking_comparison.json").write_text(json.dumps(out, indent=2), encoding="utf-8")
    lines = ["# Chunking strategy comparison (Layer 1)", "",
             f"Documents indexed: {len(docs)}. Queries: {len(QUERIES)}. Retrieval: {out['retrieval']}.", "",
             "| Strategy | Chunks | Mean tokens | Min-max tokens | Top-1 relevant | Top-3 relevant |",
             "|---|---|---|---|---|---|"]
    for name, r in res.items():
        lines.append(f"| {name} | {r['chunk_count']} | {r['mean_tokens']} | {r['min_tokens']}-{r['max_tokens']} | {r['top1_relevant']} | {r['top3_relevant']} |")
    lines += ["", "## Per query (top-1)", "", "| Query | " + " | ".join(res) + " |", "|---|" + "---|" * len(res)]
    for i, q in enumerate(QUERIES):
        cells = []
        for r in res.values():
            pq = r["per_query"][i]
            cells.append(("relevant" if pq["top1_relevant"] else "NOT relevant") + f" ({pq['top1_document']})")
        lines.append(f"| {q['id']}: {q['text']} | " + " | ".join(cells) + " |")
    (path / "chunking_comparison.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
