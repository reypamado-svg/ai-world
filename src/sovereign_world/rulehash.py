"""Fingerprints of the code (sealed trial).

Two hashes, each the sha256 of the ``sovereign_world`` package's Python sources by relative path,
line endings normalised:

- ``rule_hash`` covers everything but the observer (``observer/``), which only reads runs. It is
  the seal's code hash: a sealed run refuses to go on under code with another one, because the
  runner, the spending cap, the gateway and the seal's own checks must not move under a live
  world.
- ``engine_hash`` covers only the code that decides the world and the scripted policies the
  balance calibration plays: it leaves out the observer, the model gateway, running, saving,
  replaying, spending, sealing and the calibration's own batch and report. A calibration report
  records it, and the launch gate checks the report was made under the engine being launched,
  so a fix to the runner does not make a finished calibration stale. A new file is in by default.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

PACKAGE = Path(__file__).resolve().parent
EXCLUDED = ("observer",)
"""Folders of the package left out of the rule hash: they do not decide or run the world."""
ENGINE_EXCLUDED = (
    "observer",
    "gateway",
    "calibration/batch.py",
    "calibration/report.py",
    "calibration/__main__.py",
    "cli.py",
    "runner.py",
    "persistence.py",
    "journal.py",
    "replay.py",
    "spend.py",
    "rulehash.py",
    "seal.py",
    "preflight.py",
)
"""Folders and files left out of the engine hash: they run, save, show or pay for a world, but
decide nothing in it."""


def _excluded(relative: Path, excluded: tuple[str, ...]) -> bool:
    text = relative.as_posix()
    return any(text == entry or text.startswith(f"{entry}/") for entry in excluded)


def _hash_sources(root: Path, excluded: tuple[str, ...]) -> str:
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*.py")):
        relative = path.relative_to(root)
        if _excluded(relative, excluded):
            continue
        digest.update(relative.as_posix().encode("utf-8") + b"\0")
        # Line endings are normalised, so a checkout on Windows hashes as one on Linux.
        digest.update(path.read_bytes().replace(b"\r\n", b"\n") + b"\0")
    return digest.hexdigest()


def rule_hash(root: Path = PACKAGE) -> str:
    """The hash of the package's sources under ``root``, but the observer's."""
    return _hash_sources(root, EXCLUDED)


def engine_hash(root: Path = PACKAGE) -> str:
    """The hash of the sources under ``root`` that decide the world."""
    return _hash_sources(root, ENGINE_EXCLUDED)
