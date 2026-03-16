"""
Agent orchestrator: retrieve context from PDF, then stream OpenAI chat completion.
All errors and important events are logged to backend/logs/maritime.log.
"""
import base64
import json
import logging
import re
import traceback
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, Form, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, PlainTextResponse, StreamingResponse
from openai import OpenAI
from pydantic import BaseModel

from config import (
    GITHUB_PDF_REPO_URL,
    GITHUB_PDF_REPO_SUBPATH,
    MAX_HISTORY_MESSAGES,
    OPENAI_API_KEY,
    OPENAI_MODEL,
    get_pdf_source,
    SYSTEM_PROMPT_TEMPLATE,
)
from github_fetcher import get_git_repo_root, push_to_github
from logging_config import get_log_path, setup_logging
from pdf_loader import load_pdf_text_from_bytes
from retriever import PDFRetriever

setup_logging()
logger = logging.getLogger(__name__)

retriever: PDFRetriever | None = None
pdf_load_error: str | None = None
pdf_source_path: str | None = None
pdf_source_type: str = "file"


@asynccontextmanager
async def lifespan(app: FastAPI):
    global retriever, pdf_load_error, pdf_source_path, pdf_source_type
    logger.info("Maritime Assessment backend starting (logs: %s)", get_log_path())
    pdf_load_error = None
    pdf_source_path = None
    pdf_source_type = "file"
    try:
        logger.info("Starting: fetching PDF source...")
        path, source_type = get_pdf_source()
        pdf_source_path = path
        pdf_source_type = source_type
        logger.info("PDF source: %s at %s", source_type, path)
        logger.info("Building search index (this may take 1–2 minutes for many PDFs)...")
        retriever = PDFRetriever(path, source_type=source_type)
        docs = getattr(retriever, "document_names", [])
        if docs:
            logger.info("Documents loaded from %s (%d total):", source_type, len(docs))
            for i, name in enumerate(docs, 1):
                logger.info("  %d. %s", i, name)
        else:
            logger.info("No PDF documents found in source.")
        logger.info("Application startup complete.")
    except Exception as e:
        retriever = None
        pdf_load_error = str(e)
        logger.warning("Could not load PDF index: %s", e, exc_info=True)
    yield
    retriever = None
    pdf_load_error = None
    pdf_source_path = None


app = FastAPI(title="Maritime Assessment", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:5500", "http://localhost:5500", "http://127.0.0.1:8080", "http://localhost:8080", "http://127.0.0.1:3000", "http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class ChatMessage(BaseModel):
    role: str  # "user" | "assistant"
    content: str


class ChatRequest(BaseModel):
    message: str
    history: list[ChatMessage] = []


class ClientErrorReport(BaseModel):
    message: str
    context: str | None = None


def build_messages(
    context: str,
    history: list[ChatMessage],
    new_message: str,
    *,
    extra_context: str | None = None,
    user_content: str | list | None = None,
) -> list[dict]:
    if extra_context:
        context = (context or "") + "\n\n" + extra_context.strip()
    system = SYSTEM_PROMPT_TEMPLATE.format(context=context or "(No context available)")
    messages = [{"role": "system", "content": system}]
    for m in history[-MAX_HISTORY_MESSAGES:]:
        if m.role in ("user", "assistant") and m.content.strip():
            messages.append({"role": m.role, "content": m.content.strip()})
    last_content = user_content if user_content is not None else new_message.strip()
    messages.append({"role": "user", "content": last_content})
    return messages


def stream_chat(message: str, history: list[ChatMessage]):
    if not OPENAI_API_KEY:
        logger.error("Chat failed: OPENAI_API_KEY is not set")
        yield "Error: OPENAI_API_KEY is not set."
        return
    if not retriever:
        logger.warning("Chat failed: Knowledge base not loaded (GITHUB_PDF_REPO_URL / PDF_PATH)")
        yield "Error: Knowledge base could not be loaded. Check server logs or GITHUB_PDF_REPO_URL / PDF_PATH."
        return

    context = retriever.get_relevant_context(message)
    # When the user asks about a code (e.g. WAC 317-31-200), tell the model to use the retrieved excerpts
    if context and re.search(r"\d{2,}[\s.\-]+\d{2,}", message):
        context = (
            "The excerpts below were retrieved for this question. "
            "If they mention the code or topic the user asked about, you must say so and answer from these excerpts. "
            "Do not say the document has no information if the excerpts below contain relevant content.\n\n"
            + context
        )
    messages = build_messages(context, history, message)
    client = OpenAI(api_key=OPENAI_API_KEY)

    try:
        logger.info("OpenAI chat request (model=%s, history_len=%d)", OPENAI_MODEL, len(history))
        stream = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=messages,
            stream=True,
        )
        for chunk in stream:
            if chunk.choices and chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content
        logger.debug("OpenAI chat stream completed")
    except Exception as e:
        err_msg = str(e)
        logger.error(
            "OpenAI API error: %s | type=%s | traceback=%s",
            err_msg,
            type(e).__name__,
            traceback.format_exc(),
        )
        yield f"Error: {err_msg}"


# Allowed image types for in-chat attachments (vision). Not added to knowledge base.
IMAGE_MIMES = {"image/jpeg", "image/png", "image/webp", "image/gif"}


def stream_chat_with_attachments(
    message: str,
    history: list[ChatMessage],
    attachment_context: str,
    image_parts: list[tuple[bytes, str]],
):
    """Stream chat using RAG context + attachment text (PDFs) and optional images (vision). Attachments are not added to the knowledge base."""
    if not OPENAI_API_KEY:
        logger.error("Chat with attachments failed: OPENAI_API_KEY is not set")
        yield "Error: OPENAI_API_KEY is not set."
        return

    context = ""
    if retriever:
        context = retriever.get_relevant_context(message) or ""
    if not context and attachment_context:
        context = "No knowledge-base context for this query. Use the uploaded document(s) and image(s) below to answer."
    if context and re.search(r"\d{2,}[\s.\-]+\d{2,}", message):
        context = (
            "The excerpts below were retrieved for this question. "
            "If they mention the code or topic the user asked about, you must say so and answer from these excerpts. "
            "Do not say the document has no information if the excerpts below contain relevant content.\n\n"
            + context
        )

    extra = (
        "--- Uploaded documents (for this chat only; not in the knowledge base) ---\n\n"
        + attachment_context.strip()
        if attachment_context.strip()
        else None
    )

    user_content = message.strip()
    if image_parts:
        parts = [{"type": "text", "text": user_content or "What do you see in these images? Please describe and answer any question I have."}]
        for img_bytes, mime in image_parts:
            b64 = base64.standard_b64encode(img_bytes).decode("ascii")
            parts.append({"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}})
        user_content = parts

    messages = build_messages(
        context or "(No context available)",
        history,
        message,
        extra_context=extra,
        user_content=user_content,
    )
    client = OpenAI(api_key=OPENAI_API_KEY)

    try:
        logger.info(
            "OpenAI chat-with-attachments (model=%s, history_len=%d, images=%d)",
            OPENAI_MODEL,
            len(history),
            len(image_parts),
        )
        stream = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=messages,
            stream=True,
        )
        for chunk in stream:
            if chunk.choices and chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content
        logger.debug("OpenAI chat-with-attachments stream completed")
    except Exception as e:
        err_msg = str(e)
        logger.error(
            "OpenAI API error (chat-with-attachments): %s | type=%s | traceback=%s",
            err_msg,
            type(e).__name__,
            traceback.format_exc(),
        )
        yield f"Error: {err_msg}"


def _redact_token_from_url(url: str | None) -> str | None:
    """Hide token in URLs like https://TOKEN@github.com/... for safe display."""
    if not url or "@" not in url:
        return url
    try:
        before_at, _, rest = url.partition("@")
        if "github" in rest.lower():
            return f"https://***@{rest}"
    except Exception:
        pass
    return url


@app.get("/api/documents")
def list_documents():
    """Return the list of PDF documents loaded from GitHub or local source."""
    if retriever is None:
        return {"documents": [], "pdf_loaded": False}
    names = getattr(retriever, "document_names", [])
    return {"documents": names, "pdf_loaded": True}


@app.post("/api/upload-pdf")
async def upload_pdf(files: list[UploadFile] = File(...)):
    """
    Upload one or more PDFs to the current knowledge-base directory (e.g. the cloned GitHub repo folder).
    Only available when the source is a directory. After upload, the index is rebuilt so new PDFs are searchable.
    """
    global retriever
    if pdf_source_type != "directory" or not pdf_source_path:
        return {
            "ok": False,
            "error": "Upload is only available when using a GitHub repo (directory) as the PDF source.",
        }
    if not files:
        return {"ok": False, "error": "No files provided."}
    saved = []
    root = Path(pdf_source_path)
    if not root.is_dir():
        return {"ok": False, "error": f"Target directory not found: {pdf_source_path}"}
    for uf in files:
        if not uf.filename or not uf.filename.lower().endswith(".pdf"):
            continue
        safe_name = Path(uf.filename).name
        dest = root / safe_name
        try:
            content = await uf.read()
            dest.write_bytes(content)
            saved.append(safe_name)
        except Exception as e:
            logger.exception("Upload PDF: failed to save %s", uf.filename)
            return {"ok": False, "error": f"Failed to save {uf.filename}: {e}", "saved_so_far": saved}
    if not saved:
        return {"ok": False, "error": "No valid PDF files to save. Send one or more .pdf files."}

    root = Path(pdf_source_path).resolve()
    repo_root = get_git_repo_root(root)
    push_error = None
    if repo_root:
        relative_paths = [str((root / name).relative_to(repo_root)) for name in saved]
        commit_msg = "Add PDF(s): " + ", ".join(saved)
        push_error = push_to_github(repo_root, relative_paths, commit_msg)

    try:
        retriever = PDFRetriever(pdf_source_path, source_type="directory")
    except Exception as e:
        logger.exception("Upload PDF: re-index failed after saving %s", saved)
        return {
            "ok": True,
            "saved": saved,
            "pushed_to_github": push_error is None,
            "push_error": push_error,
            "reindex_error": str(e),
            "message": f"Saved {len(saved)} PDF(s) to disk."
            + (" Pushed to GitHub." if push_error is None else f" Push to GitHub failed: {push_error}")
            + " Re-indexing failed; restart the server to include them.",
        }
    logger.info("Upload PDF: saved and re-indexed %s", saved)
    return {
        "ok": True,
        "saved": saved,
        "document_count": len(getattr(retriever, "document_names", [])),
        "pushed_to_github": push_error is None,
        "push_error": push_error,
        "message": f"Added {len(saved)} PDF(s) and re-indexed. You can search them now."
        + (" Pushed to GitHub." if push_error is None else f" (Push to GitHub failed: {push_error})"),
    }


@app.get("/api/debug-retrieval")
def debug_retrieval(q: str = ""):
    """Show which chunks are retrieved for a query (for debugging). Use ?q=WAC+317-31-200"""
    if retriever is None:
        return {"error": "Retriever not loaded", "chunks_preview": []}
    if not q.strip():
        return {"error": "Provide query with ?q=your+question", "chunks_preview": []}
    return retriever.get_retrieval_debug(q.strip())


@app.get("/health")
def health():
    documents = getattr(retriever, "document_names", []) if retriever else []
    return {
        "status": "ok",
        "pdf_loaded": retriever is not None,
        "pdf_source_type": pdf_source_type,
        "pdf_source_path": pdf_source_path,
        "documents": documents,
        "github_repo_url": _redact_token_from_url(GITHUB_PDF_REPO_URL) or None,
        "github_repo_subpath": GITHUB_PDF_REPO_SUBPATH or None,
        "pdf_error": pdf_load_error,
    }


@app.post("/api/log-client-error")
def log_client_error(body: ClientErrorReport):
    """Log errors reported by the frontend (e.g. Failed to fetch) into maritime.log."""
    logger.error(
        "Client-reported error | context=%s | message=%s",
        body.context or "unknown",
        body.message,
    )
    return {"ok": True}


@app.get("/api/logs")
def get_logs(lines: int = 500):
    """
    Return the last N lines of the application log file (errors, OpenAI API errors, etc.).
    Use this to share logs when debugging: "Check the logs" → GET /api/logs or download the file.
    """
    log_path = get_log_path()
    if not log_path.exists():
        return {"log_path": str(log_path), "content": "", "message": "Log file not created yet."}
    try:
        with open(log_path, "r", encoding="utf-8", errors="replace") as f:
            all_lines = f.readlines()
        last = all_lines[-lines:] if len(all_lines) > lines else all_lines
        content = "".join(last)
        return {"log_path": str(log_path), "content": content, "total_lines": len(all_lines)}
    except Exception as e:
        logger.exception("Failed to read log file")
        return {"log_path": str(log_path), "content": "", "error": str(e)}


@app.get("/api/logs/download")
def download_logs():
    """Download the full maritime.log file for sharing when reporting issues."""
    log_path = get_log_path()
    if not log_path.exists():
        return PlainTextResponse("Log file not created yet.\n", media_type="text/plain")
    return FileResponse(log_path, filename="maritime.log", media_type="text/plain")


@app.post("/chat")
def chat(request: ChatRequest):
    return StreamingResponse(
        stream_chat(request.message, request.history),
        media_type="text/plain; charset=utf-8",
    )


@app.post("/chat-with-attachments")
async def chat_with_attachments(
    message: str = Form(...),
    history: str = Form("[]"),
    files: list[UploadFile] = File(default=[]),
):
    """
    Chat with optional file attachments (PDF and images). Attachments are used only for this conversation
    and are NOT added to the knowledge base. PDFs are extracted as text; images are sent to the vision model.
    """
    try:
        hist = json.loads(history)
        history_list = [ChatMessage(role=m.get("role", "user"), content=m.get("content", "")) for m in hist]
    except Exception as e:
        logger.warning("Chat-with-attachments: invalid history JSON: %s", e)
        history_list = []

    pdf_texts = []
    image_parts: list[tuple[bytes, str]] = []

    for uf in files or []:
        if not uf.filename:
            continue
        try:
            data = await uf.read()
        except Exception:
            continue
        name = (uf.filename or "").lower()
        ct = (uf.content_type or "").lower()
        if name.endswith(".pdf") or "pdf" in ct:
            try:
                text = load_pdf_text_from_bytes(data, uf.filename or "document.pdf")
                if text.strip():
                    pdf_texts.append(text)
            except Exception:
                pdf_texts.append(f"--- {uf.filename} ---\n\n(Could not extract text from this PDF.)")
        elif ct in IMAGE_MIMES or any(name.endswith(ext) for ext in (".jpg", ".jpeg", ".png", ".webp", ".gif")):
            mime = ct if ct in IMAGE_MIMES else "image/jpeg"
            if mime == "image/jpg":
                mime = "image/jpeg"
            image_parts.append((data, mime))

    attachment_context = "\n\n".join(pdf_texts) if pdf_texts else ""
    if not attachment_context and not image_parts:
        return StreamingResponse(
            stream_chat(message, history_list),
            media_type="text/plain; charset=utf-8",
        )

    return StreamingResponse(
        stream_chat_with_attachments(message, history_list, attachment_context, image_parts),
        media_type="text/plain; charset=utf-8",
    )
