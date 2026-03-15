# Maritime Assessment

Simple chat interface where users ask questions and receive answers powered by RAG (Retrieval-Augmented Generation). The backend can use **a single local PDF** or **a set of PDFs from a GitHub repo** as the knowledge base.

## Prerequisites

- Python 3.10+
- OpenAI API key (set as `OPENAI_API_KEY`)
- **Knowledge base:** either a local PDF (or set `PDF_PATH`) **or** a GitHub repo URL (set `GITHUB_PDF_REPO_URL`) containing PDF files
- If using GitHub: **Git** must be installed and on your PATH

## Backend (Python)

1. Create a virtual environment and install dependencies:

```bash
cd backend
python -m venv venv
.\venv\Scripts\activate   # Windows (use .\ so path is relative to backend folder)
# source venv/bin/activate   # macOS/Linux
pip install -r requirements.txt
```

2. Set your OpenAI API key:

```bash
set OPENAI_API_KEY=sk-your-key-here
# export OPENAI_API_KEY=sk-your-key-here   # macOS/Linux
```

3. Set the knowledge-base source (one of):

   - **GitHub repo** (PDFs from the repo are used). You can use the full browser URL including a folder:
   ```bash
   set GITHUB_PDF_REPO_URL=https://github.com/Beto22/acai/tree/main/WAC
   ```
   The repo is cloned into `backend/data/pdf_repos/<repo_name>`; if the URL contains `/tree/.../Folder`, only that folder (e.g. `WAC`) is used. Later runs pull the latest.

   - **Single local PDF** (when not using GitHub, set `PDF_PATH` to your file):
   ```bash
   set PDF_PATH=C:\path\to\your\file.pdf
   ```
   If `GITHUB_PDF_REPO_URL` is set, it takes priority and `PDF_PATH` is ignored. You must set one or the other.

4. Run the API (localhost only):

```bash
uvicorn main:app --reload --host 127.0.0.1 --port 8000
```

Check health: [http://127.0.0.1:8000/health](http://127.0.0.1:8000/health)

## Frontend

The UI must be served over HTTP (not opened as `file://`) so it can call the backend.

From the **project folder** (parent of `backend`, e.g. `Maritime Assesment`):

```bash
# Python
python -m http.server 8080

# Or Node
npx serve . -p 8080
```

Open [http://127.0.0.1:8080](http://127.0.0.1:8080) (or the port you used). CORS is allowed for `localhost` and `127.0.0.1` on common dev ports.

## Flow

1. User types a question and sends.
2. Backend loads the knowledge base (one PDF or all PDFs from the GitHub repo), chunks and embeds them.
3. For each question, the backend retrieves relevant chunks, builds a prompt with context + chat history, and streams the reply from OpenAI.
4. The chat UI shows the answer; conversation history is sent with each request for follow-up questions.

## Project layout

- `index.html`, `styles.css`, `app.js` — chat UI (Maritime Assessment).
- `backend/` — FastAPI app:
  - `main.py` — `/chat` (streaming) and `/health`.
  - `pdf_loader.py` — load one PDF or all PDFs from a directory.
  - `retriever.py` — embed chunks (OpenAI), in-memory vector search.
  - `github_fetcher.py` — clone/pull GitHub repo when `GITHUB_PDF_REPO_URL` is set.
  - `config.py` — `GITHUB_PDF_REPO_URL`, `PDF_PATH`, `OPENAI_API_KEY`, chunk/API settings.
