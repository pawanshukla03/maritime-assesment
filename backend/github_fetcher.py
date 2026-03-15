"""
Clone a GitHub repo to a local directory so PDFs can be loaded from it.
Supports pushing new files back to the remote.
"""
import subprocess
from pathlib import Path


def get_git_repo_root(start_path: Path) -> Path | None:
    """Return the repo root (directory containing .git) or None if not inside a git repo."""
    start_path = Path(start_path).resolve()
    current = start_path
    while current != current.parent:
        if (current / ".git").exists():
            return current
        current = current.parent
    return None


def push_to_github(repo_root: Path, relative_paths: list[str], commit_message: str) -> str | None:
    """
    Run git add, commit, and push for the given paths (relative to repo_root).
    Returns None on success, or an error message string on failure.
    """
    if not relative_paths:
        return None
    repo_root = Path(repo_root).resolve()
    if not (repo_root / ".git").exists():
        return "Not a git repository."
    try:
        for rel in relative_paths:
            subprocess.run(
                ["git", "add", rel],
                cwd=repo_root,
                check=True,
                capture_output=True,
            )
        subprocess.run(
            ["git", "commit", "-m", commit_message],
            cwd=repo_root,
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "push", "origin", "HEAD"],
            cwd=repo_root,
            check=True,
            capture_output=True,
        )
        return None
    except subprocess.CalledProcessError as e:
        err = (e.stderr or e.stdout or b"").decode().strip() or str(e)
        return err
    except FileNotFoundError:
        return "Git is not installed or not on PATH."


def _repo_name_from_url(url: str) -> str:
    """e.g. https://github.com/user/repo -> repo"""
    url = url.rstrip("/")
    if url.endswith(".git"):
        url = url[:-4]
    parts = url.replace("\\", "/").split("/")
    return parts[-1] if parts else "repo"


def clone_or_update_repo(git_url: str, dest_parent: Path) -> Path:
    """
    Clone GitHub repo into dest_parent/<repo_name>. If folder already exists, pull latest.
    Returns path to the cloned repo directory. Requires git to be installed.
    """
    dest_parent = Path(dest_parent)
    dest_parent.mkdir(parents=True, exist_ok=True)
    repo_name = _repo_name_from_url(git_url)
    dest = dest_parent / repo_name

    try:
        if dest.exists() and (dest / ".git").exists():
            print("Git: pulling latest changes...", flush=True)
            subprocess.run(
                ["git", "pull", "--quiet"],
                cwd=dest,
                check=True,
                capture_output=True,
            )
            return dest

        print("Git: cloning repository (may take a moment)...", flush=True)
        subprocess.run(
            ["git", "clone", "--depth", "1", "--quiet", git_url, str(dest)],
            check=True,
            capture_output=True,
        )
        return dest
    except FileNotFoundError:
        raise RuntimeError(
            "Git is not installed or not on PATH. Install Git to use GITHUB_PDF_REPO_URL."
        ) from None
    except subprocess.CalledProcessError as e:
        raise RuntimeError(
            f"Git clone/pull failed for {git_url}. Check URL and network. {e.stderr.decode() if e.stderr else ''}"
        ) from e
