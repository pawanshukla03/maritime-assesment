"""
Centralized logging: all logs and errors go to a file and to the console.
Log file path: backend/logs/maritime.log — use "Check the logs" and share this file when debugging.
"""
import logging
import sys
from pathlib import Path

LOG_DIR = Path(__file__).resolve().parent / "logs"
LOG_FILE = LOG_DIR / "maritime.log"
LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def setup_logging(level: int = logging.INFO) -> None:
    """Configure root logger to write to maritime.log and to stderr."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    root = logging.getLogger()
    root.setLevel(level)
    formatter = logging.Formatter(LOG_FORMAT, datefmt=DATE_FORMAT)

    # File: append all logs
    try:
        fh = logging.FileHandler(LOG_FILE, encoding="utf-8")
        fh.setLevel(level)
        fh.setFormatter(formatter)
        root.addHandler(fh)
    except Exception as e:
        sys.stderr.write(f"Could not create log file {LOG_FILE}: {e}\n")

    # Console: so you still see output in the terminal
    ch = logging.StreamHandler(sys.stderr)
    ch.setLevel(level)
    ch.setFormatter(formatter)
    root.addHandler(ch)


def get_log_path() -> Path:
    return LOG_FILE
