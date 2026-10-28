"""CLI over the audit log.

  python -m layer1.audit_query --last 15
  python -m layer1.audit_query --doc_uri Watson:Doc_001
  python -m layer1.audit_query --event_type PII_FLAG
"""
import argparse
import json

from . import audit_log


def main(argv=None) -> None:
    p = argparse.ArgumentParser(description="Query the Watson audit log")
    p.add_argument("--doc_uri")
    p.add_argument("--event_type")
    p.add_argument("--last", type=int)
    args = p.parse_args(argv)
    events = audit_log.read_events(args.doc_uri, args.event_type, args.last)
    for e in events:
        print(json.dumps(e, ensure_ascii=False))
    print(f"-- {len(events)} event(s)")


if __name__ == "__main__":
    main()
