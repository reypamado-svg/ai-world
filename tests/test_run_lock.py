"""One writer per run (sealed trial, slice J): whatever saves days or seals the run holds its
writer lock, a second writer is refused with exit code 5 before it reads anything, the lock
goes when its process goes, and readers never take it or make its file."""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
from typer.testing import CliRunner

from sovereign_world.cli import app
from sovereign_world.observer.runner_link import EXITED, RunnerLink
from sovereign_world.persistence import (
    LOCK_FILE,
    JournalCorruption,
    RunLocked,
    WorldStore,
    WriterLock,
    writer_held,
)
from sovereign_world.runner import run_days

cli = CliRunner()
SOVEREIGN_WORLD = Path(sys.executable).parent / "sovereign-world"
HOLDER = """
import sys, time
from pathlib import Path
from sovereign_world.persistence import WriterLock
lock = WriterLock(Path(sys.argv[1]))
lock.acquire()
print("held", flush=True)
time.sleep(120)
"""


def _init(root: Path) -> WorldStore:
    result = cli.invoke(app, ["init", str(root), "--seed", "21", "--width", "24", "--height", "24"])
    assert result.exit_code == 0, result.output
    return WorldStore(root)


def _stamps(root: Path) -> dict[str, tuple[bytes, int]]:
    return {
        path.name: (path.read_bytes(), path.stat().st_mtime_ns)
        for path in sorted(root.iterdir())
        if path.is_file() and not path.name.endswith("-shm")
    }


def _holder(root: Path) -> subprocess.Popen[str]:
    holder = subprocess.Popen(
        [sys.executable, "-c", HOLDER, str(root)], stdout=subprocess.PIPE, text=True
    )
    assert holder.stdout is not None
    assert holder.stdout.readline().strip() == "held"
    return holder


def test_a_second_writer_is_refused_before_it_reads_anything(tmp_path: Path) -> None:
    store = _init(tmp_path / "run")
    journal = store.journal_path.read_bytes()
    with WriterLock(store.root):
        refused = cli.invoke(app, ["run", str(store.root), "--days", "2"])
        assert refused.exit_code == 5, refused.output
        assert "another writer holds this run" in refused.output
        with pytest.raises(RunLocked):
            run_days(WorldStore(store.root), 1)
        with pytest.raises(RunLocked):
            WorldStore(store.root).write_seal({"sealed": True})
    assert store.journal_path.read_bytes() == journal
    assert (store.root / LOCK_FILE).stat().st_size == 0
    # Free again: the run goes on, and the empty file stays for the next writer.
    assert cli.invoke(app, ["run", str(store.root), "--days", "2"]).exit_code == 0
    assert cli.invoke(app, ["run", str(store.root), "--days", "1"]).exit_code == 0
    assert (store.root / LOCK_FILE).stat().st_size == 0


def test_the_lock_goes_with_the_process_that_held_it(tmp_path: Path) -> None:
    store = _init(tmp_path / "run")
    holder = _holder(store.root)
    try:
        assert writer_held(store.root)
        refused = subprocess.run(
            [SOVEREIGN_WORLD, "run", store.root, "--days", "1"], capture_output=True, text=True
        )
        assert refused.returncode == 5, refused.stderr
        assert "another writer holds this run" in refused.stderr
    finally:
        holder.kill()
        holder.wait()
    assert not writer_held(store.root)
    done = subprocess.run(
        [SOVEREIGN_WORLD, "run", store.root, "--days", "1"], capture_output=True, text=True
    )
    assert done.returncode == 0, done.stderr


def test_a_runner_the_observer_starts_is_refused_too(tmp_path: Path) -> None:
    store = _init(tmp_path / "run")
    holder = _holder(store.root)
    link = RunnerLink.spawn(store.root, 2)
    try:
        until = time.monotonic() + 60
        while link.state().exit_code is None:
            assert time.monotonic() < until, "the runner did not stop"
            time.sleep(0.05)
        assert link.state().phase == EXITED and link.state().exit_code == 5
    finally:
        link.close(timeout=30)
        holder.kill()
        holder.wait()


def test_readers_never_take_the_lock_or_make_its_file(tmp_path: Path) -> None:
    store = _init(tmp_path / "run")
    before = _stamps(store.root)
    for command in (
        ["verify", str(store.root)],
        ["inspect", str(store.root)],
        ["spend", str(store.root)],
        ["councils", str(store.root)],
        ["replay", str(store.root)],
        ["preflight", str(store.root)],
    ):
        cli.invoke(app, command)
    assert not writer_held(store.root)
    assert _stamps(store.root) == before
    assert not (store.root / LOCK_FILE).exists()
    # Probing a lock another process holds changes nothing either.
    assert cli.invoke(app, ["run", str(store.root), "--days", "1"]).exit_code == 0
    holder = _holder(store.root)
    try:
        held = _stamps(store.root)
        assert writer_held(store.root)
        assert _stamps(store.root) == held
    finally:
        holder.kill()
        holder.wait()


def test_a_journal_cut_back_under_a_live_store_is_not_extended(tmp_path: Path) -> None:
    store = _init(tmp_path / "run")
    assert cli.invoke(app, ["run", str(store.root), "--days", "3"]).exit_code == 0
    live = WorldStore(store.root)
    live.read_records()
    lines = live.journal_path.read_bytes().splitlines(keepends=True)
    cut = b"".join(lines[:-2])
    live.journal_path.write_bytes(cut)
    with pytest.raises(JournalCorruption, match="shortened"):
        live.append_record("note", {"day": 99})
    assert live.journal_path.read_bytes() == cut
    assert b"\x00" not in live.journal_path.read_bytes()


def test_the_lock_file_is_not_inherited_by_children(tmp_path: Path) -> None:
    store = _init(tmp_path / "run")
    with WriterLock(store.root) as lock:
        fd = lock._fd
        assert fd is not None and not os.get_inheritable(fd)
