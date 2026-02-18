from __future__ import annotations

import subprocess
from pathlib import Path


def _run_git(args: list[str]) -> str | None:
    repo_root = Path(__file__).resolve().parent.parent
    try:
        out = subprocess.check_output(
            ["git", *args],
            cwd=repo_root,
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
        return out
    except Exception:
        return None


def get_version_label() -> str:
    short = _run_git(["rev-parse", "--short", "HEAD"])
    if not short:
        return "v-dev"
    dirty = _run_git(["status", "--porcelain"])
    return f"v-{short}-dirty" if dirty else f"v-{short}"
