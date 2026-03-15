"""
Load PDF and split into overlapping text chunks for embedding.
"""
import io
import re
from pathlib import Path

from pypdf import PdfReader


def load_pdf_text_from_bytes(data: bytes, filename: str = "") -> str:
    """Extract text from a PDF given as bytes (e.g. uploaded file). Used for in-chat attachments only."""
    reader = PdfReader(io.BytesIO(data))
    parts = []
    for page in reader.pages:
        text = page.extract_text()
        if text:
            parts.append(text)
    label = f"Document: {filename}" if filename else "Uploaded PDF"
    return f"--- {label} ---\n\n" + "\n\n".join(parts)


def load_pdf_text(path: str) -> str:
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"PDF not found: {path}")
    reader = PdfReader(path)
    parts = []
    for page in reader.pages:
        text = page.extract_text()
        if text:
            parts.append(text)
    return "\n\n".join(parts)


def list_pdf_documents(dir_path: str) -> list[str]:
    """List all PDF filenames under a directory (recursive), sorted."""
    root = Path(dir_path)
    if not root.is_dir():
        return []
    return sorted(p.name for p in root.rglob("*.pdf"))


def load_all_pdfs_from_dir(dir_path: str) -> str:
    """Load all PDFs under a directory (recursive) and return combined text."""
    root = Path(dir_path)
    if not root.is_dir():
        raise NotADirectoryError(f"Not a directory: {root}")
    pdf_files = sorted(root.rglob("*.pdf"))
    if not pdf_files:
        raise FileNotFoundError(f"No PDF files found in {root}")
    parts = []
    for p in pdf_files:
        try:
            text = load_pdf_text(str(p))
            if text.strip():
                parts.append(f"--- Document: {p.name} ---\n\n{text}")
        except Exception as e:
            # skip broken PDFs but continue with others
            parts.append(f"--- Document: {p.name} (read error: {e}) ---\n\n")
    return "\n\n".join(parts) if parts else ""


def _last_break_in_zone(text: str, start: int, end: int) -> int:
    """Return the index of the last paragraph/sentence break in text[start:end], or start."""
    segment = text[start:end]
    best = -1
    for sep in ("\n\n", "\n", ". ", "? ", "! ", "; "):
        idx = segment.rfind(sep)
        if idx >= 0:
            idx += len(sep)
            if idx > best:
                best = idx
    return start + best if best >= 0 else start


def chunk_text(
    text: str,
    chunk_size: int = 600,
    overlap: int = 100,
) -> list[str]:
    """Split text into overlapping chunks. Breaks on paragraph/sentence boundaries so context is preserved."""
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if not text:
        return []

    chunks = []
    start = 0
    while start < len(text):
        end = start + chunk_size
        if end >= len(text):
            chunk = text[start:].strip()
            if chunk:
                chunks.append(chunk)
            break

        # Prefer break at paragraph or sentence (avoid cutting mid-sentence)
        segment = text[start:end]
        break_at = -1
        for sep in ("\n\n", "\n", ". ", "? ", "! ", "; "):
            idx = segment.rfind(sep)
            if idx > chunk_size // 2:
                break_at = idx + len(sep)
                break
        if break_at > 0:
            end = start + break_at

        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)

        # Next chunk starts in the overlap zone; snap to last sentence/paragraph so we don't start mid-sentence
        next_start = end - overlap if end < len(text) else len(text)
        if end < len(text) and next_start < end:
            overlap_zone_start = max(start, end - overlap)
            next_start = _last_break_in_zone(text, overlap_zone_start, end)
            if next_start >= end:
                next_start = end - overlap
        start = next_start

    return chunks
