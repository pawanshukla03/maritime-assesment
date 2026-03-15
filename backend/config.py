import os
from pathlib import Path

# Load .env from backend directory so you don't need to set variables in the terminal (Windows-friendly)
_BACKEND_DIR = Path(__file__).resolve().parent
try:
    from dotenv import load_dotenv
    load_dotenv(_BACKEND_DIR / ".env")
except ImportError:
    pass

# Paths
_CHAT_UI_DIR = _BACKEND_DIR.parent
_PDF_REPO_DIR = _BACKEND_DIR / "data" / "pdf_repos"

# Source: GitHub repo URL (priority) or local PDF path.
# URL can be base repo (https://github.com/user/repo) or include path (https://github.com/user/repo/tree/main/WAC).
GITHUB_PDF_REPO_URL_RAW = os.environ.get("GITHUB_PDF_REPO_URL", "").strip()
# Optional: use only this subfolder inside the repo (e.g. WAC). Can also be parsed from URL.
_SUBPATH_ENV = os.environ.get("GITHUB_PDF_REPO_SUBPATH", "").strip()


def _parse_github_url(url: str) -> tuple[str, str]:
    """Return (base_clone_url, subpath). e.g. .../acai/tree/main/WAC -> (https://github.com/Beto22/acai, WAC)."""
    url = url.rstrip("/")
    if "/tree/" in url:
        base, _, rest = url.partition("/tree/")
        parts = rest.split("/", 1)
        subpath = parts[1].strip("/") if len(parts) > 1 else ""
        return (base, subpath)
    return (url, "")


GITHUB_PDF_REPO_URL, _GITHUB_SUBPATH_FROM_URL = _parse_github_url(GITHUB_PDF_REPO_URL_RAW)
GITHUB_PDF_REPO_SUBPATH = _SUBPATH_ENV or _GITHUB_SUBPATH_FROM_URL

def _resolve_pdf_path() -> str:
    """Return local PDF path from PDF_PATH env. No default document; use GITHUB_PDF_REPO_URL or set PDF_PATH."""
    env_path = os.environ.get("PDF_PATH", "").strip()
    if env_path:
        return env_path
    raise RuntimeError(
        "No knowledge-base source configured. Set GITHUB_PDF_REPO_URL (for a GitHub repo with PDFs) or PDF_PATH (for a local PDF file)."
    )


def get_pdf_source():
    """
    Return (path, source_type) where source_type is "directory" or "file".
    If GITHUB_PDF_REPO_URL is set, clone repo and return path (optionally to subfolder).
    Otherwise return local PDF path as file. Call this once at startup (e.g. in lifespan).
    """
    if GITHUB_PDF_REPO_URL:
        from github_fetcher import clone_or_update_repo
        repo_path = clone_or_update_repo(GITHUB_PDF_REPO_URL, _PDF_REPO_DIR)
        if GITHUB_PDF_REPO_SUBPATH:
            path = repo_path / GITHUB_PDF_REPO_SUBPATH
            if not path.is_dir():
                raise FileNotFoundError(f"Repo subpath not found: {path}")
            return (str(path), "directory")
        return (str(repo_path), "directory")
    return (_resolve_pdf_path(), "file")


OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")

# Retrieval (larger chunks = fewer chunks = faster startup, lower embedding cost)
_CHUNK_SIZE = os.environ.get("CHUNK_SIZE", "800")
CHUNK_SIZE = int(_CHUNK_SIZE) if _CHUNK_SIZE.isdigit() else 800
_CHUNK_OVERLAP = os.environ.get("CHUNK_OVERLAP", "200")
CHUNK_OVERLAP = int(_CHUNK_OVERLAP) if _CHUNK_OVERLAP.isdigit() else 200
_TOP_K = os.environ.get("TOP_K_CHUNKS", "10")
TOP_K_CHUNKS = int(_TOP_K) if _TOP_K.isdigit() else 10
# Cap chunks to embed when cache is cold. Set MAX_CHUNKS=0 for no cap (recommended if WAC codes are missing).
MAX_CHUNKS_RAW = os.environ.get("MAX_CHUNKS", "0")
MAX_CHUNKS = int(MAX_CHUNKS_RAW) if MAX_CHUNKS_RAW.isdigit() else None
if MAX_CHUNKS is not None and MAX_CHUNKS <= 0:
    MAX_CHUNKS = None

# Chat
MAX_HISTORY_MESSAGES = 20  # last N messages to send for context
OPENAI_MODEL = "gpt-4o-mini"

# System prompt sent to the LLM. Use {context} as placeholder for RAG context.
SYSTEM_PROMPT_TEMPLATE = """You are a helpful assistant for Maritime Assessment. Answer using ONLY the context below.

CRITICAL RULES:
1. The context below was retrieved for the user's question. You MUST use it. Do NOT say "the document does not contain information" or "no information" or "no mention" if the context below has any text—instead, summarize what the context says about the user's question.
2. If the context mentions a code (e.g. WAC 317-31-200, 317.31.200), regulation, or document name the user asked about, say that it appears in the knowledge base, name the document(s) from "Document: ..." in the context, and summarize the relevant content.
3. Only if the context is empty or completely irrelevant to the question may you say no information is available.
4. Format your answer in Markdown: use **bold** for key terms and codes, bullet lists (- or *) for multiple items, numbered lists (1. 2.) for steps or sequences, and line breaks between sections. Be concise. Quote or paraphrase the context when answering.

Context:
{context}"""
