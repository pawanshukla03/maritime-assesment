"""
Single-click launcher: starts the backend (FastAPI) and frontend (static server), then opens the app in your browser.
The backend runs in its own window so you can see any startup errors. Close this window or press Enter here to stop both servers.
All launcher errors are written to backend/logs/maritime.log so you can "check the logs" even when the backend never starts.
"""
import os
import signal
import subprocess
import sys
import time
import urllib.request
import webbrowser
from datetime import datetime
from pathlib import Path
from subprocess import Popen

PROJECT_ROOT = Path(__file__).resolve().parent
BACKEND_DIR = PROJECT_ROOT / "backend"
LOG_FILE = BACKEND_DIR / "logs" / "maritime.log"
FRONTEND_PORT = 8080
BACKEND_PORT = 8000
BACKEND_URL = f"http://127.0.0.1:{BACKEND_PORT}"
HEALTH_URL = f"{BACKEND_URL}/health"
FRONTEND_URL = f"http://127.0.0.1:{FRONTEND_PORT}"


def launcher_log(level: str, message: str) -> None:
    """Append a line to backend/logs/maritime.log so launcher errors appear in the same log."""
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"{ts} | {level:8} | launcher | {message}\n")
    except Exception:
        pass


def main():
    os.chdir(PROJECT_ROOT)
    backend_proc = None
    frontend_proc = None

    def cleanup(signum=None, frame=None):
        nonlocal backend_proc, frontend_proc
        if backend_proc and backend_proc.poll() is None:
            backend_proc.terminate()
            backend_proc.wait()
        if frontend_proc and frontend_proc.poll() is None:
            frontend_proc.terminate()
            frontend_proc.wait()
        sys.exit(0)

    signal.signal(signal.SIGINT, cleanup)
    if sys.platform != "win32":
        signal.signal(signal.SIGTERM, cleanup)

    print("Starting Maritime Assessment...")
    print(f"  Backend:  {BACKEND_URL} (runs in a separate window)")
    print(f"  Frontend: {FRONTEND_URL}")
    print()

    # Ensure backend dependencies are installed (uvicorn + python-multipart for FastAPI form data)
    req_file = BACKEND_DIR / "requirements.txt"
    if req_file.exists():
        check = subprocess.run(
            [sys.executable, "-c", "import uvicorn; import python_multipart"],
            cwd=BACKEND_DIR,
            capture_output=True,
            timeout=10,
        )
        if check.returncode != 0:
            print("Installing backend dependencies (pip install -r backend/requirements.txt)...")
            launcher_log("INFO", "Installing backend dependencies (uvicorn or python-multipart missing).")
            install = subprocess.run(
                [sys.executable, "-m", "pip", "install", "-r", "requirements.txt"],
                cwd=BACKEND_DIR,
                capture_output=True,
                text=True,
                timeout=120,
            )
            if install.returncode != 0:
                err = (install.stderr or install.stdout or "").strip()
                print(f"pip install failed: {err}")
                print("Run manually:  cd backend  &&  pip install -r requirements.txt")
                launcher_log("ERROR", f"pip install failed: {err}")
                return
            print("Dependencies installed.")
        print()

    print("If the backend window closes right away, check backend/.env (OPENAI_API_KEY, PDF_PATH or GITHUB_PDF_REPO_URL).")
    print()

    # Ensure backend/.env exists (copy from .env.example if missing)
    env_file = BACKEND_DIR / ".env"
    env_example = BACKEND_DIR / ".env.example"
    if not env_file.exists() and env_example.exists():
        try:
            import shutil
            shutil.copy(env_example, env_file)
            print("Created backend/.env from .env.example. Please edit backend/.env and set OPENAI_API_KEY and PDF_PATH or GITHUB_PDF_REPO_URL.")
            launcher_log("INFO", "Created backend/.env from .env.example (user must edit with real values).")
        except Exception as e:
            print(f"Could not create .env: {e}")
        print()

    # Backend: on Windows run inside "cmd /k" so the window STAYS OPEN when the backend crashes (you can see the error)
    frontend_kw = {"cwd": PROJECT_ROOT}
    if sys.platform == "win32":
        frontend_kw["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)

    try:
        if sys.platform == "win32":
            # Use a helper batch file so paths with spaces (e.g. "Cursor projects") work correctly
            backend_bat = PROJECT_ROOT / "_start_backend.bat"
            backend_dir_str = str(BACKEND_DIR).replace("%", "%%")  # escape for batch
            python_str = str(sys.executable).replace("%", "%%")
            backend_bat.write_text(
                f'@echo off\ncd /d "{backend_dir_str}"\n"{python_str}" -m uvicorn main:app --host 127.0.0.1 --port {BACKEND_PORT}\n',
                encoding="utf-8",
            )
            backend_proc = Popen(
                ["cmd", "/k", str(backend_bat)],
                cwd=PROJECT_ROOT,
                creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0x10),
            )
        else:
            backend_proc = Popen(
                [sys.executable, "-m", "uvicorn", "main:app", "--host", "127.0.0.1", "--port", str(BACKEND_PORT)],
                cwd=BACKEND_DIR,
            )
    except Exception as e:
        msg = f"Error starting backend: {e}"
        print(msg)
        print("Make sure Python and uvicorn are installed:  pip install uvicorn fastapi")
        launcher_log("ERROR", msg)
        return

    # Wait for backend to be ready (or show error)
    print("Waiting for backend to start...", end=" ", flush=True)
    for _ in range(30):
        time.sleep(1)
        if backend_proc.poll() is not None:
            msg = "Backend process exited. Look at the OTHER window (backend) for the exact error."
            print(f"\n\n{msg}")
            print("Common fix: edit backend\\.env and set OPENAI_API_KEY=your-key and either PDF_PATH or GITHUB_PDF_REPO_URL.")
            launcher_log("ERROR", "Backend process exited. Likely cause: missing backend/.env (OPENAI_API_KEY, PDF_PATH or GITHUB_PDF_REPO_URL) or Python/import error. Check the backend window for the traceback.")
            print(f"This error has been written to: {LOG_FILE}")
            return
        try:
            urllib.request.urlopen(HEALTH_URL, timeout=2)
            print("OK")
            launcher_log("INFO", "Backend started successfully.")
            break
        except Exception:
            pass
    else:
        msg = "Backend did not respond in time. Check the BACKEND WINDOW for errors."
        print(f"\n\n{msg}")
        launcher_log("ERROR", "Backend did not respond in time. Check the backend window for startup errors.")
        print(f"This error has been written to: {LOG_FILE}")
        return

    try:
        frontend_proc = Popen(
            [sys.executable, "-m", "http.server", str(FRONTEND_PORT)],
            **frontend_kw,
        )
    except Exception as e:
        msg = f"Error starting frontend: {e}"
        print(msg)
        launcher_log("ERROR", msg)
        cleanup()
        return

    time.sleep(1)
    webbrowser.open(FRONTEND_URL)
    print("Browser opened. Leave this window open while you use the app.")
    print("Press Enter here when you want to stop the servers.\n")
    try:
        input()
    except (EOFError, KeyboardInterrupt):
        pass
    cleanup()

if __name__ == "__main__":
    main()
