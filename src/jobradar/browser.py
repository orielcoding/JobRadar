"""Finding the local Chrome / Edge, shared by the inbox window and the CV PDF printer."""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path


def chrome_exe() -> Path | None:
    candidates: list[Path] = []
    if sys.platform == "win32":
        for base in (os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)"),
                     os.environ.get("LocalAppData")):
            if base:
                candidates.append(Path(base) / "Google/Chrome/Application/chrome.exe")
    elif sys.platform == "darwin":
        candidates.append(Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"))
    for name in ("google-chrome", "chrome", "chromium"):
        found = shutil.which(name)
        if found:
            candidates.append(Path(found))
    return next((p for p in candidates if p.exists()), None)


def edge_exe() -> Path | None:
    candidates: list[Path] = []
    if sys.platform == "win32":
        for base in (os.environ.get("ProgramFiles(x86)"), os.environ.get("ProgramFiles")):
            if base:
                candidates.append(Path(base) / "Microsoft/Edge/Application/msedge.exe")
    elif sys.platform == "darwin":
        candidates.append(Path("/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"))
    found = shutil.which("microsoft-edge")
    if found:
        candidates.append(Path(found))
    return next((p for p in candidates if p.exists()), None)


def any_chromium() -> Path | None:
    return chrome_exe() or edge_exe()
