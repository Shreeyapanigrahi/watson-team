"""Check the document counts the Entities page and the Knowledge space filters show. Read-only: nothing is written.

  python -m layer2.check_counts                 # admin and viewer
  python -m layer2.check_counts --user u_analyst

For each user it prints:
  1. how many entities of each kind show a different count than the first counting rule (a document's own links, plus drug classes named in
     passages) would give, with examples: what the passage and "implied" rules (graph_view.IMPLIES) changed;
  2. consistency checks that must hold, and the exit status is 1 if one fails:
     - the Entities page count equals the Knowledge space filter count for every entity the filters offer, and no entity with documents is left out;
     - the count on each entity's own page (its list of documents) equals its count in the list;
     - no count is above the number of documents the user may read;
     - every value offered after one choice leaves exactly the documents its count says (never 0), for all pairs of choices.
Documents are counted only where the user may read them, so a viewer's numbers are lower than the admin's.
"""
import argparse
import sys
from collections import Counter, defaultdict

from layer1 import abac_engine as abac

from . import graph_view as gv, kg as kgmod
from .kg import W, local, uri

FILTERS = tuple(gv.FILTER_KINDS)


def first_rule_counts(user: dict) -> Counter:
    """The very first rule, for comparison: a document's own links, plus the drug classes its passages name."""
    kg, out = kgmod.get(), Counter()
    with kg.lock:
        for d in gv.visible_records(user):
            dn, seen = uri(d), set()
            for prop in gv.DOC_LINKS.values():
                seen.update(local(o) for o in kg.g.objects(dn, prop))
            for c in kg.g.subjects(W.isChunkOf, dn):
                seen.update(local(o) for o in kg.g.objects(c, W.mentionsDrugClass))
            out.update(seen)
    return out


def changes(user: dict, ents: list) -> None:
    first = first_rule_counts(user)
    kinds = defaultdict(lambda: [0, 0, []])
    for e in ents:
        row, was = kinds[e["kind"]], first.get(e["id"], 0)
        row[0] += 1
        if was != e["documents"]:
            row[1] += 1
            row[2] += [f'{e["label"]} {was}->{e["documents"]}'] if len(row[2]) < 3 else []
    print(f"  {sum(r[1] for r in kinds.values())} of {len(ents)} entities show a different count than the first rule")
    for kind, (total, changed, examples) in kinds.items():
        print(f"    {kind:16} {changed:2} of {total:2}   {', '.join(examples)}")


def check(user: dict) -> int:
    ents = gv.entities(user)
    readable = len(gv.visible_records(user))
    print(f"{user['user_id']} ({user['role']}): may read {readable} documents, {len(ents)} entities")
    changes(user, ents)

    problems = []
    listed = {e["id"]: e["documents"] for e in ents}
    base = gv.graph_filters(user)
    offered = {o["id"]: o["documents"] for k in FILTERS for o in base[k]}
    wanted = set(gv.FILTER_KINDS.values())
    kind_of = {e["id"]: e["kind"] for e in ents}
    problems += [f"list {listed[i]} but filter {n}: {i}" for i, n in offered.items() if listed[i] != n]
    problems += [f"has documents but no filter offers it: {i}" for i, n in listed.items() if n and kind_of[i] in wanted and i not in offered]
    problems += [f"count above the readable documents: {i}" for i, n in listed.items() if n > readable]
    problems += [f"entity page lists {len(gv.entity_detail(user, i)['documents'])}, list says {n}: {i}"
                 for i, n in listed.items() if len(gv.entity_detail(user, i)["documents"]) != n]

    pairs = 0
    for k1 in FILTERS:
        for first in base[k1]:
            after = gv.graph_filters(user, filters={k1: [first["id"]]})
            for k2 in FILTERS:
                for o in after[k2]:
                    if (k2, o["id"]) == (k1, first["id"]):
                        continue
                    chosen = {k1: [first["id"]]}
                    chosen.setdefault(k2, []).append(o["id"]) if k2 != k1 else chosen[k1].append(o["id"])
                    pairs += 1
                    matching = gv.graph_filters(user, filters=chosen)["matching"]
                    if matching != o["documents"] or matching == 0:
                        problems.append(f"after {first['label']}, {o['label']} offers {o['documents']} but {matching} match")
    print(f"  consistency: {len(offered)} filter options, {pairs} pairs of choices checked, {len(problems)} problem(s)")
    for p in problems[:10]:
        print("   -", p)
    return len(problems)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--user", action="append", help="user id (repeatable); default: u_admin and u_viewer")
    args = ap.parse_args()
    failed = sum(check(abac.get_user(u)) for u in args.user or ["u_admin", "u_viewer"])
    print("OK" if not failed else f"{failed} problem(s)")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
