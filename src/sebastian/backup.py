"""Consistent online SQLite backups (safe while the server is running)."""

import sqlite3
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
