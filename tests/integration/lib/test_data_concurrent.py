"""Cross-process locking semantics for app/lib/data.py.

Spawns multiple Python subprocesses that each open `data.write("counter")`
and increment a counter. With the exclusive RW flock in place, the
final counter equals the number of subprocesses; without the lock,
overlapping read-modify-write cycles would lose increments.

Uses real subprocesses (not threads) because `fcntl.flock` is a
per-process advisory lock — pthread-style locks within one process
would mask the contention this test exists to catch.
"""
import os
import subprocess
import sys
import textwrap

import pytest


WORKER_TEMPLATE = textwrap.dedent("""\
    import sys
    sys.path.insert(0, {project_root!r})
    from app.lib.data import SetData
    sd = SetData("myset", {repo!r})
    # Race window: each worker reads, sleeps a bit, then writes.
    import time
    for _ in range({iters}):
        with sd.write("counter", format="json") as c:
            current = c.get("n", 0)
            time.sleep(0.005)
            c["n"] = current + 1
""")


@pytest.fixture
def repo(tmp_path):
    p = tmp_path / "myset"
    p.mkdir()
    (p / ".gitignore").write_text("*.bck\n")
    return str(p)


def test_concurrent_writes_serialise(repo, tmp_path):
    project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
    workers = 6
    iters_per_worker = 8
    expected = workers * iters_per_worker

    # Seed the file so each worker only contests on the same path.
    from app.lib.data import SetData
    sd = SetData("myset", repo)
    with sd.write("counter", format="json") as c:
        c["n"] = 0

    worker_script = WORKER_TEMPLATE.format(
        project_root=project_root, repo=repo, iters=iters_per_worker
    )
    procs = [
        subprocess.Popen(
            [sys.executable, "-c", worker_script],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        for _ in range(workers)
    ]
    failures = []
    for p in procs:
        out, err = p.communicate(timeout=60)
        if p.returncode != 0:
            failures.append(err.decode(errors="replace"))

    assert not failures, "worker(s) crashed:\n" + "\n---\n".join(failures)

    with sd.read("counter") as c:
        assert c["n"] == expected, (
            f"Lost updates: got {c['n']}, expected {expected}. "
            f"Locking is broken (writes are not exclusive)."
        )


def test_shared_read_does_not_block_other_reads(repo):
    """Two concurrent `read` calls both hold LOCK_SH and proceed in
    parallel. Verify by holding one read open and confirming a second
    read can acquire and return."""
    import threading
    from app.lib.data import SetData
    sd = SetData("myset", repo)
    with sd.write("cfg", format="yaml") as c:
        c["x"] = 1

    second_read_completed = threading.Event()

    def hold_first_read():
        with sd.read("cfg") as _:
            second_read_completed.wait(timeout=5)

    t = threading.Thread(target=hold_first_read)
    t.start()
    try:
        with sd.read("cfg") as c:
            assert c == {"x": 1}
        second_read_completed.set()
    finally:
        t.join(timeout=5)


def test_exclusive_write_blocks_read(repo):
    """A write holding LOCK_EX must block a concurrent read until it
    releases. We approximate this by running the writer in a subprocess
    that sleeps inside its `with` block, then asserting the reader saw
    the post-write state."""
    import threading
    import time
    from app.lib.data import SetData
    sd = SetData("myset", repo)
    with sd.write("state", format="json") as s:
        s["v"] = 0

    project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
    writer_script = textwrap.dedent(f"""\
        import sys, time
        sys.path.insert(0, {project_root!r})
        from app.lib.data import SetData
        sd = SetData("myset", {repo!r})
        with sd.write("state", format="json") as s:
            time.sleep(0.5)
            s["v"] = 42
    """)
    writer = subprocess.Popen([sys.executable, "-c", writer_script])
    # Brief delay so the writer is inside its with-block before we try to read.
    time.sleep(0.1)
    read_value = None

    def read_after_writer():
        nonlocal read_value
        with sd.read("state") as s:
            read_value = s["v"]

    t = threading.Thread(target=read_after_writer)
    t.start()
    writer.wait(timeout=10)
    t.join(timeout=10)
    assert read_value == 42, (
        f"Reader saw {read_value!r}; expected 42. Read must have blocked "
        "until the writer released its exclusive lock."
    )
