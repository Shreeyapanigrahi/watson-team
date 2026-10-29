# Run Watson on a new device

Everything runs locally and costs nothing. Written for **Windows 11 + PowerShell**, the setup the project was built and tested on
(Python 3.13.12, Node 24). Not tested on a clean machine: if a step fails, see "If something goes wrong" at the end.

## What is not in GitHub (so you set it up on the new device)

| Left out on purpose | Where it comes from on the new device |
|---|---|
| Passwords | Step 4 (you choose them; stored only as salted hashes) |
| The ingested documents' stores (`layer1/stores`, `layer2/stores`, `layer3/stores`) | Steps 3 and 5 rebuild them |
| `.venv`, `node_modules`, `frontend/dist` | Steps 2 and 6 |
| The embedding model (~90 MB) | Downloaded automatically in step 5 |
| The language model (~2.1 GB) and `llama-server` | Step 7 |

## What the new device needs first

- **Git**
- **Python 3.13** (tick "Add python.exe to PATH" in the installer)
- **Node.js 24** (the version it was tested with)
- **winget** (comes with Windows 11)
- About **4 GB** of free disk and an internet connection for the one-time downloads

## Steps

Run these in PowerShell, one block at a time.

### 1. Get the code

The repository is private, so Git asks you to sign in (a browser window opens).

```powershell
git clone https://github.com/Shreeyapanigrahi/watson-team.git D:\watson_Team
cd D:\watson_Team
```

Any folder works; use your own path in place of `D:\watson_Team`. Run every later command from inside this folder.

### 2. Python environment

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r layer1\requirements.txt
.venv\Scripts\python -m spacy download en_core_web_sm
.venv\Scripts\python -m pip install -r layer2\requirements.txt
```

If a package fails to install, use the exact versions in `layer1\requirements.lock.txt` instead of `layer1\requirements.txt`.

### 3. Load the documents

The sample documents are already in the repository (`layer1/data`). This runs them through the real ingestion pipeline.

```powershell
.venv\Scripts\python -m layer1.bulk_ingest --reset
```

### 4. Set the passwords

There is one account per role: `viewer`, `analyst`, `spoc`, `admin`. The command asks for each password twice, typed hidden,
at least 10 characters. Nobody can sign in until this is done. It must be repeated on every new device.

```powershell
.venv\Scripts\python -m layer1.set_password
```

### 5. Build the knowledge graph and the search index

The first run downloads a ~90 MB embedding model (free, cached afterwards).

```powershell
.venv\Scripts\python -m layer2.backfill --reset
.venv\Scripts\python -m layer2.shacl_validator
```

The second command should print `"conforms": true`. You do not need `layer2.catalog.build_catalog`: the catalog file is already in the repository.

### 6. Build the web UI

```powershell
cd frontend
npm install
npm run build
cd ..
```

### 7. Install the local language model (needed for Ask Watson)

This is `llama-server` (llama.cpp, free) with the Qwen2.5-3B-Instruct Q4_K_M model, about 2.1 GB. **Close PowerShell and open a new one**
after the first line, so that `llama-server` is found.

```powershell
winget install ggml.llamacpp
New-Item -ItemType Directory -Force "$env:USERPROFILE\llm-models" | Out-Null
curl.exe -L -o "$env:USERPROFILE\llm-models\qwen2.5-3b-instruct-q4_k_m.gguf" https://huggingface.co/Qwen/Qwen2.5-3B-Instruct-GGUF/resolve/main/qwen2.5-3b-instruct-q4_k_m.gguf
```

The downloaded file should be 2,104,932,768 bytes. To keep it somewhere else, set the environment variable `WATSON_GGUF` to its full path.

### 8. Run it (two terminals, leave both open)

**Terminal A: the language model**

```powershell
cd D:\watson_Team
.\layer3\start_llm_server.ps1
```

If PowerShell blocks the script, run it as `powershell -ExecutionPolicy Bypass -File .\layer3\start_llm_server.ps1`.
It serves on `http://127.0.0.1:8080` and only listens on this machine. Stop it with Ctrl+C.

**Terminal B: the app**

```powershell
cd D:\watson_Team
.venv\Scripts\python -m uvicorn layer1.api.main:app --port 8000
```

Open **http://127.0.0.1:8000** and sign in with a role and the password you set in step 4.

## Check that it works

1. Sign in as `viewer`. The home page is the chat ("What do you want to know?"). There should be **no** "Local language model not ready" notice.
2. Ask a question. On a CPU one answer takes about 15 to 60 seconds. You will see the steps run, then the answer with its sources.
3. Sign in as `admin`. The Knowledge pages (graph, entities, ontology) should show data.

Optional, about 7 minutes, and these tests do not need the model server:

```powershell
.venv\Scripts\python -m pytest layer3/tests layer2/tests layer1/tests -q -p no:cacheprovider -c layer3/pytest.ini --rootdir=.
```

## Starting it again later

You only repeat step 8: start Terminal A and Terminal B. Restart Terminal B (uvicorn) after running `layer2.backfill --reset`
or after changing code, because a running server keeps the old graph in memory.

## If something goes wrong

| What you see | What to do |
|---|---|
| `python` is not recognised | Reinstall Python with "Add to PATH", or use `py -3.13` in place of `python` |
| Sign-in says passwords are not set | Run step 4 |
| Five wrong passwords | That username is locked for 5 minutes |
| Ask Watson shows "Local language model not ready" | Terminal A is not running, or the model file is missing or in the wrong place (step 7) |
| `llama-server not found` | Open a new PowerShell window after installing it (step 7) |
| Knowledge pages are empty | Run step 5, then restart Terminal B |
| Port 8000 or 8080 is already in use | Close the other program, or use `--port 8001` for uvicorn (then open that port) |
| The page looks old after an update | Hard-refresh the browser (Ctrl+Shift+R) |

## Mac or Linux

Not tested. The commands are the same, with these differences: use `.venv/bin/python` instead of `.venv\Scripts\python`, install llama.cpp
with your package manager (for example `brew install llama.cpp`), download the same model file with `curl -L -o <path> <url>`, and start
the model server directly, because the start script is PowerShell only:

```bash
llama-server -m <path-to-gguf> --alias qwen2.5-3b-instruct --host 127.0.0.1 --port 8080 -c 4096 -np 1
```
