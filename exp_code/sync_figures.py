"""Optionally sync generated figures to a paper directory."""
from __future__ import annotations

import shutil
from pathlib import Path

from config import FIGURE_DIR, PAPER_EXP, ensure_dirs

OBSOLETE = ("fig_eval_2x4.pdf", "fig_eval_2x4.png")


def sync() -> list[Path]:
    ensure_dirs()
    if PAPER_EXP is None:
        return []
    for name in OBSOLETE:
        stale = PAPER_EXP / name
        if stale.exists():
            stale.unlink()
    copied: list[Path] = []
    for src in FIGURE_DIR.glob("*"):
        if src.is_file() and src.suffix.lower() in {".pdf", ".png", ".svg"}:
            dst = PAPER_EXP / src.name
            shutil.copy2(src, dst)
            copied.append(dst)
    return copied


if __name__ == "__main__":
    files = sync()
    if PAPER_EXP is None:
        print("CASTLE_PAPER_EXP is unset; figures remain in code/figure")
    else:
        print(f"synced {len(files)} files -> {PAPER_EXP}")
    for f in files:
        print(" ", f.name)
