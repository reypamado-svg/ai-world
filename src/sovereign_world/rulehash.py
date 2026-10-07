"""The rule hash: a fingerprint of the code that decides the world (sealed trial).

It is the sha256 of every Python source file of the ``sovereign_world`` package, by relative
path, except the observer's (``observer/``), which only reads runs. A sealed run records it, and
refuses to go on under code with another hash; a calibration report records it, and the launch
gate checks the report was made under the rules being launched.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

PACKAGE = Path(__file__).resolve().parent
EXCLUDED = ("observer",)
"""Folders of the package that do not decide the world."""


def rule_hash(root: Path = PACKAGE) -> str:
    """The hash of the package's sources under ``root``, in a stable order."""
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*.py")):
        relative = path.relative_to(root)
        if relative.parts and relative.parts[0] in EXCLUDED:
            continue
        digest.update(relative.as_posix().encode("utf-8") + b"\0")
        # Line endings are normalised, so a checkout on Windows hashes as one on Linux.
        digest.update(path.read_bytes().replace(b"\r\n", b"\n") + b"\0")
    return digest.hexdigest()
