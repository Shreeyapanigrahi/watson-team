# Starts the local language model for Layer 3: llama.cpp's llama-server serving Qwen2.5-3B-Instruct (Q4_K_M GGUF).
# Run it in its own terminal and leave it open:   .\layer3\start_llm_server.ps1
# Layer 3 talks to http://127.0.0.1:8080 (override with WATSON_LLM_URL). Stop it with Ctrl+C.
param(
    [string]$Model = $(if ($env:WATSON_GGUF) { $env:WATSON_GGUF } else { Join-Path $env:USERPROFILE "llm-models\qwen2.5-3b-instruct-q4_k_m.gguf" }),
    [int]$Port = 8080,
    [int]$Context = 4096
)

if (-not (Test-Path $Model)) { Write-Error "Model file not found: $Model (set WATSON_GGUF or pass -Model)"; exit 1 }
if (-not (Get-Command llama-server -ErrorAction SilentlyContinue)) { Write-Error "llama-server not found. Install: winget install ggml.llamacpp"; exit 1 }

# -np 1: one request slot, so the whole context window is ours (the default can split it across slots).
# --alias: the model name Layer 3 shows in the UI. Bound to localhost only: nothing leaves this machine.
llama-server -m $Model --alias qwen2.5-3b-instruct --host 127.0.0.1 --port $Port -c $Context -np 1
