"""
Embed PDF chunks with OpenAI and retrieve relevant context for a query.
Uses disk cache so repeated startups with same content skip API calls.
"""
import hashlib
import logging
import pickle
import re
from pathlib import Path

import numpy as np
from openai import OpenAI

from config import CHUNK_OVERLAP, CHUNK_SIZE, MAX_CHUNKS, OPENAI_API_KEY, TOP_K_CHUNKS
from pdf_loader import chunk_text, list_pdf_documents, load_all_pdfs_from_dir, load_pdf_text

logger = logging.getLogger(__name__)

EMBEDDING_MODEL = "text-embedding-3-small"
# Batch size for embedding API (fewer calls = faster startup)
EMBEDDING_BATCH_SIZE = 100

_CACHE_DIR = Path(__file__).resolve().parent / "data" / "embedding_cache"


def _cache_key(chunks: list[str]) -> str:
    """Stable hash of chunk content so cache invalidates when PDFs change."""
    content = "\n".join(chunks)
    return hashlib.sha256(content.encode("utf-8")).hexdigest()[:24]


def _load_cached_index(cache_key: str) -> tuple[list[str], np.ndarray] | None:
    path = _CACHE_DIR / f"{cache_key}.pkl"
    if not path.exists():
        return None
    try:
        with open(path, "rb") as f:
            data = pickle.load(f)
        chunks = data.get("chunks")
        embeddings = data.get("embeddings")
        if chunks and embeddings is not None and len(chunks) == len(embeddings):
            return (chunks, np.array(embeddings, dtype=float))
    except Exception:
        pass
    return None


def _save_cached_index(cache_key: str, chunks: list[str], embeddings: np.ndarray) -> None:
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = _CACHE_DIR / f"{cache_key}.pkl"
    try:
        with open(path, "wb") as f:
            pickle.dump({"chunks": chunks, "embeddings": embeddings.tolist()}, f)
    except Exception as e:
        logger.warning("Could not save embedding cache: %s", e)


def get_embedding(client: OpenAI, text: str) -> list[float]:
    resp = client.embeddings.create(model=EMBEDDING_MODEL, input=text.strip())
    return resp.data[0].embedding


def get_embeddings_batch(client: OpenAI, texts: list[str]) -> list[list[float]]:
    """Embed multiple texts in one API call. Returns list of embeddings in same order as input."""
    if not texts:
        return []
    inputs = [t.strip() if t else "" for t in texts]
    resp = client.embeddings.create(model=EMBEDDING_MODEL, input=inputs)
    return [item.embedding for item in sorted(resp.data, key=lambda x: x.index)]


def cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    a = np.array(a, dtype=float)
    b = np.array(b, dtype=float)
    return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-9))


class PDFRetriever:
    def __init__(self, source_path: str, source_type: str = "file"):
        """
        source_path: path to a single PDF file or a directory containing PDFs.
        source_type: "file" for one PDF, "directory" for all PDFs in a folder (e.g. from GitHub).
        """
        if not OPENAI_API_KEY:
            raise ValueError("OPENAI_API_KEY environment variable is not set")
        self.client = OpenAI(api_key=OPENAI_API_KEY)
        self.chunks: list[str] = []
        self.embeddings: np.ndarray | None = None
        self.document_names: list[str] = []
        self._build_index(source_path, source_type)

    def _build_index(self, source_path: str, source_type: str) -> None:
        if source_type == "directory":
            self.document_names = list_pdf_documents(source_path)
            text = load_all_pdfs_from_dir(source_path)
        else:
            self.document_names = [Path(source_path).name]
            text = load_pdf_text(source_path)
        self.chunks = chunk_text(text, chunk_size=CHUNK_SIZE, overlap=CHUNK_OVERLAP)
        if not self.chunks:
            return
        if MAX_CHUNKS and len(self.chunks) > MAX_CHUNKS:
            self.chunks = self.chunks[:MAX_CHUNKS]
            logger.info("Capped to %s chunks (set MAX_CHUNKS=0 for no cap).", MAX_CHUNKS)
        n = len(self.chunks)
        cache_key = _cache_key(self.chunks)
        cached = _load_cached_index(cache_key)
        if cached is not None:
            self.chunks, self.embeddings = cached
            logger.info("Loaded %s chunks from cache (no API calls).", n)
            return
        batch_size = EMBEDDING_BATCH_SIZE
        num_batches = (n + batch_size - 1) // batch_size
        logger.info("Embedding %s chunks in %s batch(es)...", n, num_batches)
        vecs = []
        try:
            for start in range(0, n, batch_size):
                batch = self.chunks[start : start + batch_size]
                batch_vecs = get_embeddings_batch(self.client, batch)
                vecs.extend(batch_vecs)
                done = min(start + batch_size, n)
                logger.debug("Embedding progress: %s/%s chunks", done, n)
            self.embeddings = np.array(vecs, dtype=float)
            _save_cached_index(cache_key, self.chunks, self.embeddings)
            logger.info("Embedding cache saved for next startup.")
        except Exception as e:
            logger.exception("OpenAI embedding API error: %s", e)
            raise

    def _normalize_code(self, s: str) -> str:
        """Normalize a code like '317 31 200' or '317.31.200' to '317-31-200' for matching."""
        s = re.sub(r"[\s.]+", "-", s.strip())
        s = re.sub(r"-+", "-", s)  # collapse multiple hyphens
        return s.strip("-")

    def _number_parts_from_query(self, query: str) -> list[str]:
        """Extract code parts from query (e.g. 317, 31, 200 from 'WAC 317-31-200' or '317 31 200')."""
        # Prefer one clear code pattern: three number groups with separators
        code_patterns = [
            r"(?:WAC|SEC)?\s*(\d{2,})[\s.\-\u2013\u2014]+(\d{2,})[\s.\-\u2013\u2014]+(\d{2,})",
            r"(\d{2,})\s+(\d{2,})\s+(\d{2,})",
        ]
        for pat in code_patterns:
            m = re.search(pat, query, re.IGNORECASE)
            if m:
                return list(m.groups())
        # Fallback: all 2+ digit runs (may have duplicates; used for fallback matching only)
        parts: list[str] = []
        for m in re.finditer(r"\d{2,}", query):
            parts.append(m.group(0))
        return parts

    # Unicode hyphen/minus variants that can appear in PDFs
    _CODE_SEP = re.compile(r"[\s.\-\u2010\u2011\u2012\u2013\u2014\u2212]+")

    def _chunk_normalized_for_code(self, text: str) -> str:
        """Normalize text so '317.31.200' and '317 31 200' both become '317-31-200' for substring check."""
        t = self._CODE_SEP.sub("-", text.lower())
        return re.sub(r"-+", "-", t).strip("-")

    def _chunk_contains_normalized_code(self, chunk: str, code_parts: list[str]) -> bool:
        """True if chunk contains the code in any formatting (e.g. 317.31.200 or 317 31 200)."""
        if len(code_parts) < 2:
            return False
        target = "-".join(code_parts)  # "317-31-200"
        normalized = self._chunk_normalized_for_code(chunk)
        return target in normalized

    def _doc_name_from_chunk(self, chunk: str) -> str:
        """Extract document name from chunk header '--- Document: name.pdf ---'."""
        match = re.search(r"Document:\s*([^\s]+\.pdf)", chunk, re.IGNORECASE)
        if not match:
            return ""
        return (match.group(1) or "").lower()

    def _keyword_indices(self, query: str) -> list[int]:
        """Find chunk indices that contain code-like phrases or document names matching the query."""
        # Separators: space, dot, hyphen, en-dash, em-dash (PDFs often use these)
        sep = r"[\s.\-\u2013\u2014]+"
        patterns = [
            r"WAC\s*\d+[\s.\-\u2013\u2014]+\d+[\s.\-\u2013\u2014]+\d+",
            r"\d{2,}\s+\d{2,}\s+\d{2,}",
            r"\d{2,}\s*[.\-\u2013\u2014]\s*\d{2,}\s*[.\-\u2013\u2014]\s*\d{2,}",
            r"\d{2,}\s*-\s*\d{2,}\s*-\s*\d{2,}",
            r"Chapter\s+\d+[\s\-.]*\d*[\s\-.]*\d*",
            r"SEC\s*\d+[\s\-.]*\d*[\s\-.]*\d*",
        ]
        keywords: list[str] = []
        for pat in patterns:
            for m in re.finditer(pat, query, re.IGNORECASE):
                s = m.group(0).strip()
                if len(s) >= 4:
                    keywords.append(s)
                    norm = self._normalize_code(re.sub(r"^(WAC|SEC|Chapter)\s*", "", s, flags=re.IGNORECASE))
                    if len(norm) >= 5 and norm not in keywords:
                        keywords.append(norm)
        seen_kw: set[str] = set()
        unique_kw: list[str] = []
        for k in keywords:
            knorm = self._normalize_code(k) if re.search(r"\d", k) else k.lower()
            if knorm not in seen_kw:
                seen_kw.add(knorm)
                unique_kw.append(k)
        keywords = unique_kw
        number_parts = self._number_parts_from_query(query) if re.search(r"\d{2,}", query) else []

        seen: set[int] = set()
        out: list[int] = []

        for i, chunk in enumerate(self.chunks):
            chunk_lower = chunk.lower()
            doc_name = self._doc_name_from_chunk(chunk)

            # 1) Match by document name: e.g. "317-31-200.pdf" or "WAC_317_31_200.pdf"
            if number_parts and doc_name:
                if all(p in doc_name for p in number_parts):
                    if i not in seen:
                        seen.add(i)
                        out.append(i)
                    continue

            # 2) Exact keyword in chunk text
            for kw in keywords:
                kw_lower = kw.lower()
                if kw_lower in chunk_lower:
                    if i not in seen:
                        seen.add(i)
                        out.append(i)
                    break
            else:
                # 3) Code-like keyword: flexible regex (317[\s.\-]*31[\s.\-]*200, incl. unicode dashes)
                for kw in keywords:
                    if not re.search(r"\d", kw):
                        continue
                    parts = re.split(r"[\s.\-\u2013\u2014]+", kw.lower())
                    parts = [p for p in parts if p.isdigit()]
                    if len(parts) >= 2:
                        regex_pat = re.escape(parts[0])
                        for p in parts[1:]:
                            regex_pat += sep + re.escape(p)
                        if re.search(regex_pat, chunk_lower):
                            if i not in seen:
                                seen.add(i)
                                out.append(i)
                            break
                else:
                    # 4) Fallback: chunk contains all number parts (317, 31, 200) as substrings
                    if len(number_parts) >= 2 and all(p in chunk_lower for p in number_parts):
                        if i not in seen:
                            seen.add(i)
                            out.append(i)
                    else:
                        # 5) Normalized substring: chunk contains "317-31-200" in any form (317.31.200, 317 31 200, etc.)
                        if self._chunk_contains_normalized_code(chunk, number_parts):
                            if i not in seen:
                                seen.add(i)
                                out.append(i)

        return out

    def get_relevant_context(self, query: str, top_k: int = TOP_K_CHUNKS) -> str:
        selected = self._get_selected_indices(query, top_k)
        if not selected:
            return ""
        return "\n\n---\n\n".join(self.chunks[i] for i in selected)

    def _brute_force_code_chunk_indices(self, query: str) -> list[int]:
        """Scan every chunk for the normalized code (e.g. 317-31-200). No embedding, no regex—just substring."""
        number_parts = self._number_parts_from_query(query)
        if len(number_parts) < 2:
            return []
        out: list[int] = []
        relaxed: list[int] = []  # chunks with first+last part only (code may be split or formatted oddly)
        target = "-".join(number_parts)
        for i, chunk in enumerate(self.chunks):
            if self._chunk_contains_normalized_code(chunk, number_parts):
                out.append(i)
            elif len(number_parts) >= 3 and number_parts[0] in chunk and number_parts[-1] in chunk:
                # Relaxed: chunk has 317 and 200 (e.g. code split across line or "Section 317 ... 200")
                relaxed.append(i)
        # Prefer exact matches; if none, use relaxed so we don't return nothing
        return out if out else relaxed

    def _get_selected_indices(self, query: str, top_k: int = TOP_K_CHUNKS) -> list[int]:
        """Return list of chunk indices that would be used as context (for debugging and retrieval)."""
        if not self.chunks or self.embeddings is None:
            return []
        # 1) Brute-force: any chunk whose normalized text contains "317-31-200" (or whatever code is in the query)
        code_scan_indices = self._brute_force_code_chunk_indices(query)
        # 2) Keyword/match logic (document name, regex, etc.)
        keyword_indices = self._keyword_indices(query)
        # 3) Semantic search
        q_embedding = np.array(
            get_embedding(self.client, query), dtype=float
        ).reshape(1, -1)
        sims = np.dot(self.embeddings, q_embedding.T).flatten()
        semantic_indices = list(np.argsort(sims)[::-1][:top_k])
        # Merge: code-scan first (guaranteed to have the code), then keyword, then semantic
        seen: set[int] = set()
        merged: list[int] = []
        for i in code_scan_indices + keyword_indices + semantic_indices:
            if i not in seen:
                seen.add(i)
                merged.append(i)
        max_chunks = min(top_k + 15, len(merged))
        return merged[:max_chunks]

    def get_retrieval_debug(self, query: str, top_k: int = TOP_K_CHUNKS) -> dict:
        """Return what would be retrieved for this query (for debugging)."""
        if not self.chunks or self.embeddings is None:
            return {"error": "No chunks loaded", "chunks_preview": []}
        code_scan = self._brute_force_code_chunk_indices(query)
        keyword_indices = self._keyword_indices(query)
        selected = self._get_selected_indices(query, top_k)
        previews = []
        for i in selected:
            doc = self._doc_name_from_chunk(self.chunks[i])
            excerpt = (self.chunks[i][:500] + "…") if len(self.chunks[i]) > 500 else self.chunks[i]
            previews.append({"index": i, "document": doc, "excerpt": excerpt})
        return {
            "query": query,
            "brute_force_code_scan_count": len(code_scan),
            "keyword_match_count": len(keyword_indices),
            "total_chunks_selected": len(selected),
            "chunks_preview": previews,
        }
