"""Consistent online SQLite backups (safe while the server is running)."""

import sqlite3
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from .config import get_config


def backup_database(dest_dir: str | Path, keep: int = 14) -> Path:
    src_path = get_config().database_path
    if not src_path.exists():
        raise FileNotFoundError(f"database not found: {src_path}")
    dest = Path(dest_dir)
    dest.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    target = dest / f"sebastian-{stamp}.db"
    src = sqlite3.connect(src_path)
    out = sqlite3.connect(target)
    try:
        src.backup(out)
    finally:
        out.close()
        src.close()
    backups = sorted(dest.glob("sebastian-*.db"))
    for old in backups[: max(0, len(backups) - keep)]:
        old.unlink()
    return target


DUMP_NAME = "sebastian.sql"


class OffsiteError(RuntimeError):
    pass


def _git(repo: Path, *args: str) -> str:
    done = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, timeout=120
    )
    if done.returncode != 0:
        raise OffsiteError(f"git {' '.join(args)} failed: {(done.stderr or done.stdout).strip()}")
    return done.stdout


def push_offsite(backup_file: Path, repo: str | Path) -> str:
    """Commit a plain-SQL dump of `backup_file` to the git clone at `repo` and push it.

    A text dump (not the binary file) so git stores small deltas and diffs are readable. The dump
    has no timestamps, so a night with no changes makes no commit. Returns a one-line status.
    Does nothing if `repo` is not a git clone (offsite backup not set up).
    """
    repo = Path(repo)
    if not (repo / ".git").exists():
        return f"offsite not configured ({repo} is not a git clone); skipped"
    src = sqlite3.connect(backup_file)
    try:
        dump = "\n".join(src.iterdump()) + "\n"
    finally:
        src.close()
    (repo / DUMP_NAME).write_text(dump, encoding="utf-8")
    _git(repo, "add", DUMP_NAME)
    if not _git(repo, "status", "--porcelain", DUMP_NAME).strip():
        return "offsite: no changes since the last backup"
    _git(repo, "commit", "-q", "-m", f"backup {backup_file.stem.removeprefix('sebastian-')}")
    _git(repo, "push", "-q", "origin", "HEAD")  # HEAD: works for a brand-new empty repo too
    return "offsite: pushed"
