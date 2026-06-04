from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Mapping

import pytest


def _bash_candidates() -> list[str]:
    candidates = [
        shutil.which("bash"),
        r"C:\Program Files\Git\bin\bash.exe",
        r"C:\Program Files\Git\usr\bin\bash.exe",
        r"C:\msys64\usr\bin\bash.exe",
    ]
    out: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        if not candidate:
            continue
        path = Path(candidate)
        if not path.exists():
            continue
        normalized = str(path).lower()
        if normalized in seen:
            continue
        seen.add(normalized)
        if os.name == "nt" and "microsoft\\windowsapps\\bash.exe" in normalized:
            continue
        out.append(str(path))
    return out


def require_usable_bash() -> str:
    candidates = _bash_candidates()
    if not candidates:
        pytest.skip("bash not available")

    for bash in candidates:
        proc = subprocess.run(
            [bash, "-ec", "printf ok"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if proc.returncode == 0 and proc.stdout == "ok":
            return bash
    pytest.skip("bash is not usable in this environment")


def run_bash(
    script: str,
    *,
    env: Mapping[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    bash = require_usable_bash()
    return subprocess.run(
        [bash, "-ec", script],
        env=dict(env) if env is not None else None,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def to_bash_path(path: Path) -> str:
    if os.name != "nt":
        return str(path)

    bash = require_usable_bash()
    raw = str(path)
    converters = (
        'command -v cygpath >/dev/null 2>&1 && cygpath -u "$1"',
        'command -v wslpath >/dev/null 2>&1 && wslpath -u "$1"',
    )
    for converter in converters:
        proc = subprocess.run(
            [bash, "-ec", converter, "path-convert", raw],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if proc.returncode == 0 and proc.stdout.strip():
            return proc.stdout.strip()
    pytest.skip("bash cannot address Windows temp paths")
