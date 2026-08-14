"""
backend/config.py

Central configuration for the Nebula AI Banking Assistant.
Loads environment variables and defines model/hardware constants
per PRD §1.2 and §2.2.
"""

import os
from dotenv import load_dotenv

load_dotenv()

# Must be set before chromadb is imported anywhere (config.py is imported
# first by every module that eventually touches chromadb). Settings(
# anonymized_telemetry=False) passed at Client construction time doesn't
# fully suppress it in chromadb==0.5.18 — there's a known posthog
# capture() signature mismatch bug that fires regardless unless this env
# var is set before import. Harmless either way, just noisy in logs.
os.environ.setdefault("ANONYMIZED_TELEMETRY", "False")


# ---------------------------------------------------------------------------
# Local LLM (primary chat model) — served via Ollama
# ---------------------------------------------------------------------------
# VRAM-safe default. Only switch to the 7B stretch model after the Day-1
# nvidia-smi + benchmark step in the PRD confirms acceptable offloaded
# latency (~3-4s). Do not assume 7B works without measuring first.
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434")
PRIMARY_LLM_MODEL = os.getenv("PRIMARY_LLM_MODEL", "qwen2.5:3b-instruct-q4_K_M")
STRETCH_LLM_MODEL = os.getenv("STRETCH_LLM_MODEL", "qwen2.5:7b-instruct-q4_K_M")

# Which backend generate_reply() (llm_client.py) actually calls for live
# chat replies. "ollama" (default) — local dev, needs `ollama serve`
# running. "groq" — used in deployment (Render), since Render doesn't
# run a GPU-backed Ollama server well: no persistent GPU on standard web
# services, and pulling a multi-GB model on every cold start is a bad
# fit. Set LLM_PROVIDER=groq in Render's environment variables; leave
# unset (defaults to ollama) for local development.
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "ollama")

# ---------------------------------------------------------------------------
# Groq Cloud — handover-summary generation (§4.3) always. Also doubles as
# the live chat reply provider (llm_client.py) when LLM_PROVIDER=groq —
# see the LLM_PROVIDER comment above. GROQ_FALLBACK_MODEL is reused as
# that primary chat model in deployment: it's the same Qwen family
# already used locally via Ollama, just hosted instead of local.
# ---------------------------------------------------------------------------
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_HANDOVER_MODEL = os.getenv("GROQ_HANDOVER_MODEL", "llama-3.3-70b-versatile")
GROQ_FALLBACK_MODEL = os.getenv("GROQ_FALLBACK_MODEL", "openai/gpt-oss-120b")

# ---------------------------------------------------------------------------
# Embeddings + Vector DB (§2.4) — CPU-bound regardless of GPU vendor.
# ---------------------------------------------------------------------------
EMBEDDING_MODEL_NAME = os.getenv("EMBEDDING_MODEL_NAME", "BAAI/bge-small-en-v1.5")
EMBEDDING_DIM = 384

CHROMA_PERSIST_DIR = os.getenv("CHROMA_PERSIST_DIR", "data/chroma_db")
CHROMA_COLLECTION_NAME = os.getenv("CHROMA_COLLECTION_NAME", "banking_policy_kb")

# ---------------------------------------------------------------------------
# Sandbox / mock banking DB (§4.4)
# ---------------------------------------------------------------------------
SANDBOX_DB_PATH = os.getenv("SANDBOX_DB_PATH", "data/sandbox.db")

# ---------------------------------------------------------------------------
# Knowledge base ingestion source
# ---------------------------------------------------------------------------
KNOWLEDGE_BASE_DIR = os.getenv("KNOWLEDGE_BASE_DIR", "data/knowledge_base")

# ---------------------------------------------------------------------------
# Responsible AI thresholds (§4.2) — confidence decision matrix
# ---------------------------------------------------------------------------
CONFIDENCE_WEIGHTS = {
    "retrieval": 0.4,
    "grounding": 0.4,
    "intent": 0.2,
}
CONFIDENCE_AUTO_EXECUTE_THRESHOLD = 0.65
CONFIDENCE_CLARIFY_THRESHOLD = 0.50
# C >= 0.65               -> automated execution & direct response
# 0.50 <= C < 0.65        -> clarification loop
# C < 0.50                -> automated human handover

UNCLEAR_ATTEMPTS_HANDOVER_LIMIT = 2  # §4.3 trigger 2: low confidence twice consecutively

# ---------------------------------------------------------------------------
# FastAPI
# ---------------------------------------------------------------------------
API_HOST = os.getenv("API_HOST", "0.0.0.0")
API_PORT = int(os.getenv("API_PORT", "8000"))

# ---------------------------------------------------------------------------
# CORS — origins allowed to call this API from a browser.
# ---------------------------------------------------------------------------
# Comma-separated list via env var, e.g. in Render's dashboard:
#   ALLOWED_ORIGINS=https://nebula-customer.vercel.app,https://nebula-agent.vercel.app
# Defaults cover the two local Vite dev servers (customer app on 5173,
# agent dashboard on 5174 — see each app's vite.config.ts) so CORS never
# blocks local development out of the box.
ALLOWED_ORIGINS = [
    o.strip()
    for o in os.getenv(
        "ALLOWED_ORIGINS", "http://localhost:5173,http://localhost:5174"
    ).split(",")
    if o.strip()
]
