# Watson (team capstone): guide for Claude

Local, zero-cost prototype of a pharma knowledge platform. Brief: `resources/watson.pdf` (photos of a screen, no text layer).
Read these instead of re-deriving them:

- `project_spec.md`: requirements, interfaces, data, acceptance criteria
- `architecture.md` and `architecture/`: design and diagrams
- `project_status.md`: what is done, next, open decisions
- `change_log.md`: dated log
- `design_decisions.md`: the reasons behind non-obvious choices, with evidence
- `nfr_design.md`: security, scale and operations notes and known gaps

Rules for this project:
- Everything for Layer 1 lives in `layer1/`; UI in `frontend/`; Layers 2 and 3 in `layer2/`, `layer3/`.
- Use `.venv`. Free, local, open-source only. Ask before large downloads (models) or anything that could bill.
- Never fabricate results. Report what was run, what failed and what is unverified.
- Do not commit, push or branch unless asked. The git root is `D:\`, shared with unrelated projects.
- Do not edit source files with PowerShell `Get-Content | Set-Content` (it corrupted UTF-8 once). Use the Edit tool.
- Tests: `.venv\Scripts\python -m pytest layer1/tests -q -p no:cacheprovider -c layer1/pytest.ini --rootdir=.`
- Layer 2 tests (run both together, about 5 minutes): `.venv\Scripts\python -m pytest layer2/tests layer1/tests -q -p no:cacheprovider -c layer2/pytest.ini --rootdir=.`
- Layer 3 needs `llama-server` running in its own terminal (`.\layer3\start_llm_server.ps1`, model `qwen2.5-3b-instruct` Q4_K_M GGUF). There is no Ollama dependency. Full regression: `.venv\Scripts\python -m pytest layer3/tests layer2/tests layer1/tests -q -p no:cacheprovider -c layer3/pytest.ini --rootdir=.` (Layer 3 tests use a scripted model and need no model server). Diagram: `architecture/layer3_architecture.md`.
- Do not write Python source through bash heredocs (`\n` inside strings corrupted files three times); use Edit/Write.
- The three files in `architecture/` (`model.json`, `*.csv`, `*.ttl`) are source data: never modify or move them. The catalog JSON is the single source of truth for entities; the graph and index are rebuilt with `python -m layer2.backfill --reset`.
- Keep `project_status.md` and `change_log.md` current at each milestone.
