# Non-functional design (Layer 1)

What the prototype does and does not do. The "Gap" column is deliberate honesty for the review call.

| Area | What we have | Gap / what production would need |
|---|---|---|
| Authentication | Pick one of four users, receive an opaque bearer token held in server memory | No passwords, no expiry, tokens lost on restart. Use SSO (OIDC) and short-lived signed tokens |
| Authorisation | ABAC checked on every read and action, server-side; UI hiding is cosmetic only | Policy is in code; a policy engine would allow changes without redeploy |
| Audit | Every ingest, check, decision and denial written to `audit_log.jsonl` | The file is editable by anyone with disk access. Production: append-only store with hash chaining or write-once storage |
| Data at rest | Plain JSON and original files on local disk | Unencrypted. Production: encrypted storage, access limited by the OS |
| PII | Detected and gated before ACTIVE | Detector is rule-based plus spaCy; recall on real documents is unknown. Needs measurement on real data |
| Concurrency | Single process, one lock around store writes | No multi-process safety. Production: a database with transactions |
| Scale | 47 documents, 161 chunks; every request reads JSON from disk | Will not scale to thousands of documents. Production: database and an index |
| Upload safety | Allow-listed extensions, 20 MB limit, empty files refused, filename sanitised, stored in a random folder | No malware scanning; parsers (PyMuPDF etc.) run in-process, so a malicious file could harm the server. Production: sandboxed workers |
| Reliability | Atomic JSON writes avoid half-written files | No backup, no recovery plan |
| Performance | Not measured | We have not measured ingest time or API latency; do not quote any figure |
| Cost | Zero: local, open-source, no API keys | |
| Privacy | All data synthetic and fictional | |
| Accessibility | Labelled form controls, light and dark themes (written in, not tested) | No screen-reader, keyboard-only or contrast check done |
| Browser support | Built with Vite defaults (modern browsers) | Only a build check so far; no browser testing |

## Reproducibility

Seeded generator (seed 42), pinned versions in `layer1/requirements.lock.txt`, and one command (`bulk_ingest --reset`) rebuilds all state from the corpus.
