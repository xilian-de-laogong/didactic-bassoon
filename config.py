"""
Shared configuration for keep-alive scripts.

SITES are stored in sites.json (dynamic). This module provides helpers
to load and save sites atomically. If sites.json is missing it will be
created as an empty mapping.
"""

import json
import time
from pathlib import Path
from typing import Dict, Any

# ─── Paths ────────────────────────────────────────────────────────────
ROOT_DIR = Path(__file__).parent
DATA_DIR = ROOT_DIR / "cookies"
LOG_FILE = ROOT_DIR / "keep_alive.log"
SITES_FILE = ROOT_DIR / "sites.json"
AUTH_FILE = ROOT_DIR / "auth.json"
BACKUP_DIR = ROOT_DIR / "backups"


def _atomic_write(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    try:
        tmp.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
        tmp.replace(path)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except Exception:
                pass


def load_sites() -> Dict[str, Any]:
    """Load sites from sites.json, creating an empty file if missing."""
    if not SITES_FILE.exists():
        try:
            _atomic_write(SITES_FILE, {})
        except Exception:
            pass
        return {}

    try:
        txt = SITES_FILE.read_text(encoding="utf-8")
        data = json.loads(txt)
        if not isinstance(data, dict):
            raise ValueError("sites.json must contain an object mapping site names to configs")
        return data
    except Exception:
        # On error, return empty mapping to avoid crashing
        return {}


def save_sites(sites: Dict[str, Any]) -> None:
    """Save sites atomically and keep a timestamped backup."""
    try:
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        ts = time.strftime("%Y%m%d_%H%M%S")
        backup = BACKUP_DIR / f"sites_{ts}.json"
        if SITES_FILE.exists():
            backup.write_text(SITES_FILE.read_text(encoding="utf-8"), encoding="utf-8")
    except Exception:
        pass

    _atomic_write(SITES_FILE, sites)


# Load SITES at import time (main.py will call load_sites again on startup/reload)
SITES = load_sites()


def cookies_path(site_name: str) -> Path:
    """Return the cookies JSON path for a given site."""
    return DATA_DIR / f"cookies_{site_name}.json"


def session_path(site_name: str) -> Path:
    """Return the session data JSON path for a given site."""
    return DATA_DIR / f"session_{site_name}.json"
