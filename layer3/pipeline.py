"""Layer 3: question in, grounded and cited answer out, one visible step at a time.

ask() is a generator. It yields one event per step ("running" before a slow step, then "done", "warn", "skipped" or
"error") and finally one {"step": "final"} event holding the answer. The HTTP layer streams these straight to the UI, so
the user watches the same sequence the code runs:

  1 question   2 access check   3 entities and route   4 SPARQL by the LLM   5 entity mapping
  6 evidence and lineage   7 answer by the LLM   8 hallucination check and citations

mode "auto" (the default) lets the question choose its search (understand.choose_search, shown in step 3 with its reasons): the full
pipeline when the question shows something the graph can answer, vector search alone for an open question about what the documents
say. mode "semantic" forces the full pipeline. mode "plain" forces the plain-RAG baseline for the comparison the brief asks for: the
same access check, then vector search on cosine similarity alone, no graph, no SPARQL, no entity mapping. The result's `mode` is the
path that was taken ("semantic" or "plain"), `mode_requested` what was asked for.
"""
import time

from layer1 import audit_log
from layer2 import telemetry

from . import answer as answer_mod, config, evidence as evidence_mod, guard, history, llm, mapping, semantic as semantic_mod, sparql_gen, understand, user_graph


def _ev(step, key, title, status, data=None, started=None):
    e = {"step": step, "key": key, "title": title, "status": status, "data": data or {}}
    if started is not None:
        e["ms"] = int((time.time() - started) * 1000)
    return e


def evidence_path(items: list) -> dict:
    """What the answer was actually written from, which can differ from the plan: a count the graph returns leaves the passages out
    (graph only), and a graph query that fails leaves only passages. `search` says what the router planned; this says what happened."""
    passages = sum(1 for i in items if i["kind"] == "chunk")
    facts = len(items) - passages
    label = ("semantic search (graph + vector)" if passages and facts else "vector search only" if passages
             else "graph only" if facts else "no evidence found")
    return {"label": label, "graph_facts": facts, "passages": passages}


def _doc_row(rec):
    return {"document_uri": rec["document_uri"], "title": rec["metadata"].get("extracted_title"),
            "study_type": rec["metadata"].get("study_type"), "sensitivity_level": rec["access"]["sensitivity_level"]}


FORCED_SEARCH = {"semantic": "semantic search (graph + vector)", "plain": "vector search only"}


def ask(user: dict, question: str, mode: str = "auto"):
    question = (question or "").strip()
    if not question:
        raise ValueError("The question is empty")
    if len(question) > 600:
        raise ValueError("The question is longer than 600 characters")
    if mode not in ("auto", "semantic", "plain"):
        raise ValueError("mode must be 'auto', 'semantic' or 'plain'")
    t_all, timings = time.time(), {}

    # ---- 1 question
    t = time.time()
    yield _ev(1, "question", "Question received", "done",
              {"question": question, "mode": mode, "user": user_graph.describe(user), "llm": llm.status()}, t)

    # ---- 2 access check
    t = time.time()
    yield _ev(2, "access", "Access check: your view of the knowledge space", "running")
    sl = user_graph.build(user)
    timings["access"] = int((time.time() - t) * 1000)
    yield _ev(2, "access", "Access check: your view of the knowledge space", "done",
              {**sl.stats, "attributes": user_graph.describe(user), "documents": [_doc_row(r) for _, r in sorted(sl.records.items())][:80]}, t)

    # ---- 3 entities and route
    t = time.time()
    und = understand.understand(question)
    if mode == "auto":                                            # the question decides which search it goes through
        search = understand.choose_search(question, und["entities"])
    else:
        search = {"mode": mode, "label": FORCED_SEARCH[mode], "chosen_by": "caller", "reasons": [f"chosen manually: {FORCED_SEARCH[mode]}"]}
    used = search["mode"]                                         # the path actually taken: "semantic" or "plain"
    semantic = used == "semantic"
    und["search"] = search
    if semantic:                                                  # everyday words -> ontology terms, by meaning (shown in step 3, used in step 4)
        und["terms"] = semantic_terms = semantic_mod.match(question, und["entities"])
    timings["understand"] = int((time.time() - t) * 1000)
    yield _ev(3, "understand", "Entities in the question and the path taken", "done", und, t)

    # ---- 4 SPARQL
    sparql_res = None
    if semantic:
        t = time.time()
        yield _ev(4, "sparql", "SPARQL written by the local LLM", "running", {"model": config.LLM_MODEL})
        sparql_res = sparql_gen.generate(question, und["entities"], sl.graph, terms=semantic_terms)
        timings["sparql"] = int((time.time() - t) * 1000)
        status = {"ok": "done", "empty": "warn", "failed": "warn", "no_relation": "warn"}[sparql_res["status"]]
        yield _ev(4, "sparql", "SPARQL written by the local LLM", status,
                  {k: sparql_res.get(k) for k in ("status", "sparql", "columns", "row_count", "attempts", "reason")}
                  | {"rows": (sparql_res.get("rows") or [])[:15], "model": config.LLM_MODEL}, t)
    else:
        yield _ev(4, "sparql", "SPARQL written by the local LLM", "skipped", {"reason": "Vector search only: the graph is not used for this question."})

    # ---- 5 mapping
    mapped = None
    if semantic:
        t = time.time()
        mapped = mapping.map_entities(user, sl.graph, und["entities"], sparql_res.get("sparql"), sparql_res.get("plan"))
        timings["mapping"] = int((time.time() - t) * 1000)
        yield _ev(5, "mapping", "Question entities mapped to the knowledge space", "done" if mapped["mapped"] else "warn", mapped, t)
    else:
        yield _ev(5, "mapping", "Question entities mapped to the knowledge space", "skipped", {"reason": "Vector search only: the graph is not used for this question."})

    # ---- 6 evidence and lineage
    t = time.time()
    yield _ev(6, "evidence", "Evidence gathered, lineage traced", "running")
    ev = evidence_mod.gather(user, sl, question, und["entities"], und["route"], sparql_res, used)
    timings["evidence"] = int((time.time() - t) * 1000)
    yield _ev(6, "evidence", "Evidence gathered, lineage traced", "done" if ev["items"] else "warn",
              {"items": ev["items"], "lineage": ev["lineage"], "notes": ev["notes"]}, t)

    # ---- 7 answer
    t = time.time()
    yield _ev(7, "answer", "Answer written by the local LLM from the evidence only", "running", {"model": config.LLM_MODEL})
    draft = answer_mod.generate(question, ev["items"])
    timings["answer"] = int((time.time() - t) * 1000)
    yield _ev(7, "answer", "Answer written by the local LLM from the evidence only",
              "done" if draft["mode"] == "llm" else "warn",
              {k: draft.get(k) for k in ("mode", "raw", "model", "seconds", "prompt_tokens", "output_tokens", "note", "refused")}, t)

    # ---- 8 verify
    t = time.time()
    check = guard.verify(draft["sentences"], ev["items"], und["entities"]) if draft["sentences"] else {"sentences": [], "counts": {"supported": 0, "weak": 0, "unsupported": 0}, "risk": "none"}
    short_check = guard.verify([draft["short"]], ev["items"], und["entities"]) if draft.get("short") else None
    guard.log_risk(user, question, check)
    timings["verify"] = int((time.time() - t) * 1000)
    kept = [s for s in check["sentences"] if s["verdict"] != "unsupported"]
    removed = [s for s in check["sentences"] if s["verdict"] == "unsupported"]
    yield _ev(8, "verify", "Hallucination check and citations", "done" if not removed else "warn",
              {"sentences": check["sentences"], "counts": check["counts"], "risk": check["risk"], "kept": len(kept), "removed": len(removed),
               "method": "words + meaning + numbers + names against the cited evidence"}, t)

    # ---- final
    by_id = {e["id"]: e for e in ev["items"]}
    short_ok0 = bool(short_check and short_check["sentences"] and short_check["sentences"][0]["verdict"] != "unsupported")
    short_cites = short_check["sentences"][0]["cites"] if short_ok0 else []
    cited_ids = sorted({c for s in kept for c in s["cites"]} | set(short_cites), key=lambda x: int(x[1:]))
    cited = [by_id[c] for c in cited_ids]
    cited_docs = sorted({d for e in cited for d in e["documents"]})
    cited_chunks = {c for e in cited for c in e["chunks"]}
    lineage = []
    for rec in evidence_mod.lineage(sl, cited):
        rec["chunks_used"] = [c for c in rec["chunks_used"] if c["chunk_uri"] in cited_chunks]
        lineage.append(rec)
    short_ok = short_ok0
    short = short_check["sentences"][0] if short_ok else (kept[0] if kept else None)
    if not ev["items"]:
        status = "no_evidence"
    elif not kept and not short_ok:                      # a model that writes only the SHORT line has still answered
        status = "no_supported_answer"
    else:
        status = "answered"
    result = {
        "question": question, "mode": used, "mode_requested": mode, "search": search, "evidence_path": evidence_path(ev["items"]), "route": und["route"], "status": status,
        "short_answer": {"text": short["text"], "cites": short["cites"]} if short else None,
        "lines": [{"text": s["text"], "cites": s["cites"], "verdict": s["verdict"], "score": s["score"], "attributed": s["attributed"],
                   "checks": s["checks"]} for s in kept],
        "removed": [{"text": s["text"], "score": s["score"], "why": _why(s)} for s in removed],
        "sources": [{"id": e["id"], "kind": e["kind"], "label": e["source_label"], "text": e["text"], "documents": e["documents"],
                     "chunks": e["chunks"], "computed_over": e.get("computed_over", [])} for e in cited],
        "documents": lineage, "risk": check["risk"], "answer_mode": draft["mode"],
        "sparql": {"status": sparql_res["status"], "query": sparql_res.get("sparql")} if sparql_res else None,
        "message": _message(status, draft, sparql_res),
        "timings_ms": {**timings, "total": int((time.time() - t_all) * 1000)},
        "withheld_documents": sl.stats["documents_withheld"],
    }
    _record(user, question, used, mode, result, ev, cited_docs)
    yield {"step": "final", "result": result}


def _why(s: dict) -> str:
    c = s["checks"]
    why = []
    if s["attributed"]:
        why.append("it cited no evidence and the best match did not support it")
    if not c["numbers_in_evidence"]:
        why.append("a number in it is not in the cited evidence")
    if not c["names_in_evidence"]:
        why.append("it names " + ", ".join(c["names_missing"]) + " which the cited evidence does not")
    if not why:
        why.append(f"too little overlap with the cited evidence (score {s['score']})")
    return "; ".join(why)


def _message(status, draft, sparql_res):
    gap = (sparql_res["reason"] + " No graph facts were used" + ("." if status == "answered" else "; the passages did not answer it either.") + " "
           ) if sparql_res and sparql_res["status"] == "no_relation" else ""
    if status == "no_evidence":
        return gap + "Nothing you are allowed to read matches this question, so there is nothing to answer from."
    if status == "no_supported_answer":
        return gap + "The model's draft did not hold up against the evidence, so no answer is shown. See the removed lines and the steps above."
    notes = [gap.strip()] if gap else []
    if draft["mode"] == "extractive":
        notes.append("The local model was not reachable, so this answer quotes the best-matching evidence sentences instead of paraphrasing.")
    if sparql_res and sparql_res["status"] == "failed":
        notes.append("The model could not write a valid SPARQL query, so the graph facts were not used; the answer rests on passages only.")
    return " ".join(notes)


def _record(user, question, mode, requested, result, ev, cited_docs):
    chunks = [{"chunk_uri": c, "document_uri": i["documents"][0], "score": i.get("score", 0.0)}
              for i in ev["items"] if i["kind"] == "chunk" for c in i["chunks"]]
    if chunks:
        telemetry.record_search(user, question, chunks)          # what was handed to the model, so query Q10 audits Layer 3 too
    audit_log.log_event("QUERY", None, {"user_id": user["user_id"], "role": user["role"], "stage": "layer3_answer", "question": question,
                                         "mode": mode, "mode_requested": requested, "route": result["route"], "status": result["status"], "cited_documents": cited_docs,
                                         "evidence_items": len(ev["items"]), "risk": result["risk"]})
    history.add(user, result)
