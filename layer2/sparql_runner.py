"""Run the saved SPARQL queries (queries.sparql) against the knowledge graph and save the results.

  python -m layer2.sparql_runner                 # run Q1..Q11, write results/query_results/
  python -m layer2.sparql_runner Q3 Q9 Q10       # the three the brief runs live
  python -m layer2.sparql_runner Q9 --param chunk=Chunk_002_001

Queries run on the live graph. Q6 runs on a COPY expanded by the OWL RL reasoner (the stored graph is never expanded),
and the plain result is reported next to it so the effect of inference is visible. Q10 runs on a copy that also holds
the search telemetry.
"""
import argparse
import json
import re
import time
from datetime import date, timedelta
from decimal import Decimal
from typing import Optional

import owlrl
from rdflib import Graph, Literal, URIRef
from rdflib.plugins.sparql import prepareQuery

from . import config, kg as kgmod, telemetry

_PARAM_OK = re.compile(r"^[A-Za-z0-9_.:\-]+$")      # letters, digits and _ . : - only: nothing that could close an IRI or a string
MAX_ROWS = 500


# ---------------------------------------------------------------- parsing
def parse(path=None) -> list:
    text = (path or config.QUERIES_FILE).read_text(encoding="utf-8")
    prefixes = "\n".join(l for l in text.splitlines() if l.startswith("PREFIX"))
    out = []
    for block in re.split(r"^#---\s*$", text, flags=re.M)[1:]:
        meta, body = {}, []
        for line in block.splitlines():
            m = re.match(r"#\s*@(\w+)\s*(.*)$", line)
            if m:
                meta.setdefault(m.group(1), []).append(m.group(2).strip())
            elif not line.strip().startswith("#") or body:
                body.append(line)
        if "id" not in meta:
            continue
        params = {}
        for p in meta.get("param", []):
            k, _, v = p.partition("=")
            params[k.strip()] = v.strip()
        out.append({"id": meta["id"][0], "title": meta["title"][0], "question": meta["question"][0],
                    "reasoning": (meta.get("reasoning") or [None])[0], "data": (meta.get("data") or [None])[0],
                    "params": params, "prefixes": prefixes, "sparql": "\n".join(body).strip()})
    return out


def list_queries() -> list:
    return [{k: q[k] for k in ("id", "title", "question", "reasoning", "data", "params", "sparql")} for q in parse()]


# ---------------------------------------------------------------- execution
def _plain(v):
    if isinstance(v, URIRef):
        return kgmod.short(v)
    if isinstance(v, Literal):
        p = v.toPython()
        if isinstance(p, Decimal):                   # ROUND(...) and AVG(...) give xsd:decimal: send a number, not a string
            return float(p)
        return p if isinstance(p, (int, float, bool)) else str(v)
    return None if v is None else str(v)


def _execute(graph: Graph, sparql: str) -> tuple:
    res = graph.query(sparql)
    cols = [str(v) for v in res.vars]
    rows = [{c: _plain(r[c]) for c in cols} for r in res]
    return cols, rows


def _substitute(q: dict, params: Optional[dict], as_of: Optional[date]) -> tuple:
    today = as_of or date.today()
    values = {"today": today.isoformat(), "in90": (today + timedelta(days=90)).isoformat(), **q["params"], **(params or {})}
    for k, v in values.items():
        if not _PARAM_OK.match(str(v)):
            raise ValueError(f"Parameter {k}='{v}' has characters that are not allowed")
    if "chunk" in values and values["chunk"].startswith("Watson:"):
        values["chunk"] = values["chunk"][len("Watson:"):]
    text = q["sparql"]
    for k, v in values.items():
        text = text.replace("{{" + k + "}}", str(v))
    left = re.findall(r"\{\{(\w+)\}\}", text)
    if left:
        raise ValueError(f"Missing parameter(s): {', '.join(left)}")
    return q["prefixes"] + "\n" + text, values


def expanded_copy(g: Graph) -> Graph:
    c = kgmod.bind(Graph())
    c += g
    owlrl.DeductiveClosure(owlrl.OWLRL_Semantics).expand(c)
    return c


def run(qid: str, params: Optional[dict] = None, as_of: Optional[date] = None) -> dict:
    q = next((x for x in parse() if x["id"] == qid.upper()), None)
    if q is None:
        raise KeyError(f"No query '{qid}'")
    sparql, used = _substitute(q, params, as_of)
    kg = kgmod.get()
    t0 = time.time()
    with kg.lock:
        base = kg.g
        out = {"id": q["id"], "title": q["title"], "question": q["question"], "sparql": sparql, "params": used,
               "reasoning": q["reasoning"], "ran_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "triples_in_graph": len(base)}
        if q["reasoning"] == "owlrl":
            out["columns"], out["rows_without_inference"] = _execute(base, sparql)
            graph = expanded_copy(base)
            out["triples_after_inference"] = len(graph)
        elif q["data"] == "telemetry":
            graph = kgmod.bind(Graph())
            graph += base
            graph += telemetry.as_graph()
            out["telemetry_events"] = len(telemetry.events())
        else:
            graph = base
        out["columns"], out["rows"] = _execute(graph, sparql)
    out["count"] = len(out["rows"])
    out["seconds"] = round(time.time() - t0, 3)
    return out


def run_custom(sparql: str) -> dict:
    """A read-only SELECT or ASK typed by an ADMIN. Updates are impossible through Graph.query, and the shape is checked."""
    try:
        parsed = prepareQuery(sparql)
    except Exception as e:
        raise ValueError(f"Not valid SPARQL: {e}") from e
    if parsed.algebra.name not in ("SelectQuery", "AskQuery"):
        raise ValueError("Only SELECT and ASK queries are allowed")
    kg = kgmod.get()
    with kg.lock:
        cols, rows = _execute(kg.g, sparql)
    return {"columns": cols, "rows": rows[:MAX_ROWS], "count": len(rows), "truncated": len(rows) > MAX_ROWS}


# ---------------------------------------------------------------- saving
def _md(r: dict) -> str:
    lines = [f"# {r['id']}: {r['title']}", "", f"**Question:** {r['question']}", "",
             f"Run at {r['ran_at']} on a graph of {r['triples_in_graph']} triples."]
    if r.get("reasoning"):
        lines.append(f"Without inference: {len(r['rows_without_inference'])} rows. "
                     f"With OWL RL ({r['triples_after_inference']} triples after expansion): {r['count']} rows.")
    if r.get("telemetry_events") is not None:
        lines.append(f"Telemetry events loaded: {r['telemetry_events']}.")
    lines += ["", "```sparql", r["sparql"], "```", "", f"**{r['count']} row(s)**", ""]
    if r["rows"]:
        cols = r["columns"]
        lines += ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
        lines += ["| " + " | ".join(str(row[c]) for c in cols) + " |" for row in r["rows"][:60]]
        if r["count"] > 60:
            lines.append(f"\n(first 60 of {r['count']} rows; the full result is in {r['id']}.json)")
    return "\n".join(lines) + "\n"


def save(results: list) -> None:
    config.RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    for r in results:
        (config.RESULTS_DIR / f"{r['id']}.json").write_text(json.dumps(r, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
        (config.RESULTS_DIR / f"{r['id']}.md").write_text(_md(r), encoding="utf-8")
    index = ["# SPARQL query results", "", "Produced by `python -m layer2.sparql_runner` against the live graph. Re-run to refresh.", "",
             "| Query | Title | Rows |", "|---|---|---|"]
    index += [f"| [{r['id']}]({r['id']}.md) | {r['title']} | {r['count']} |" for r in results]
    (config.RESULTS_DIR / "README.md").write_text("\n".join(index) + "\n", encoding="utf-8")


def main(argv=None) -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("ids", nargs="*")
    p.add_argument("--param", action="append", default=[], help="name=value")
    p.add_argument("--no-save", action="store_true")
    a = p.parse_args(argv)
    params = dict(x.split("=", 1) for x in a.param)
    ids = [i.upper() for i in a.ids] or [q["id"] for q in parse()]
    results = [run(i, params if len(ids) == 1 else None) for i in ids]
    for r in results:
        print(f"{r['id']}  {r['title']}: {r['count']} row(s) in {r['seconds']}s")
    if not a.no_save and not a.ids:
        save(results)
        print(f"saved to {config.RESULTS_DIR}")


if __name__ == "__main__":
    main()
