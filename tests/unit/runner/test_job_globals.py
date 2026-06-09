import os
import sys
from unittest.mock import patch, MagicMock


class TestSetupJobGlobals:
    def _call(self, kind):
        from app import job_runner
        with patch.object(job_runner, "_conf_data", {"git": "data/git", "jobs": "data/jobs"}), \
             patch.object(job_runner, "_conf_runner", {"pkg": "data/pkg"}), \
             patch.object(job_runner.pyops, "ServiceListener", return_value=MagicMock()):
            return job_runner.setup_job_globals(
                owner="alice",
                setspace="/tmp/setspace",
                workspace="/tmp/workspace",
                web_bind="localhost:8000",
                job_id="jid",
                kind=kind,
                setname="myops",
                name="myscript",
            )

    def test_task_kind_exposes_setname_not_set(self):
        glob = self._call("task")
        assert "set" not in glob
        assert glob["setname"] == "myops"
        assert glob["name"] == "myscript"
        assert glob["user"] == "alice"
        assert glob["listener"] is None

    def test_kind_global_reflects_job_kind(self):
        # Every scriptlet can introspect its own execution kind, and
        # JobControl reads it to inherit the child kind in create().
        for kind in ("task", "service", "draft", "debug", "test", "testdebug"):
            glob = self._call(kind)
            assert glob["kind"] == kind

    def test_control_receives_kind(self):
        # The kind is threaded into Control so control.job.create can
        # inherit it (working-tree parent -> draft child).
        glob = self._call("draft")
        assert glob["control"].job.parent_kind == "draft"

    def test_draft_kind_has_listener(self):
        glob = self._call("draft")
        assert glob["listener"] is not None

    def test_service_kind_has_listener(self):
        glob = self._call("service")
        assert glob["listener"] is not None

    def test_debug_kind_has_listener(self):
        glob = self._call("debug")
        assert glob["listener"] is not None


class TestPyopsSetExtraGlobals:
    """Scriptlets in the pyops-managed `pyops` set get two extra globals
    so they can introspect the running pyops install without importing from
    `app.*` (forbidden — scriptlets stay self-contained, interfacing with
    pyops only via control/listener/globals)."""

    def _call(self, setname):
        from app import job_runner
        with patch.object(job_runner, "_conf_data", {"git": "data/git", "jobs": "data/jobs"}), \
             patch.object(job_runner, "_conf_runner", {"pkg": "data/pkg", "venv": "data/venv"}), \
             patch.object(job_runner, "_conf_users", {"alice": {"debug_port": 15678}}), \
             patch.object(job_runner, "_conf_web", {"bind": "127.0.0.1:8000"}), \
             patch.object(job_runner, "_conf_log", {"loglevel": "info"}), \
             patch.object(job_runner.pyops, "ServiceListener", return_value=MagicMock()):
            return job_runner.setup_job_globals(
                owner="alice",
                setspace="/tmp/setspace",
                workspace="/tmp/workspace",
                web_bind="localhost:8000",
                job_id="jid",
                kind="task",
                setname=setname,
                name="set_clone",
            )

    def test_user_set_does_not_have_appspace_or_config(self):
        glob = self._call("myops")
        assert "appspace" not in glob
        assert "config" not in glob

    def test_pyops_set_exposes_appspace(self):
        glob = self._call("pyops")
        assert "appspace" in glob
        assert glob["appspace"].endswith("app")

    def test_pyops_set_exposes_full_config(self):
        glob = self._call("pyops")
        assert "config" in glob
        assert set(glob["config"].keys()) == {"data", "runner", "users", "web", "log"}
        assert glob["config"]["data"] == {"git": "data/git", "jobs": "data/jobs"}
        assert glob["config"]["log"] == {"loglevel": "info"}


class TestRedirectJobIo:
    """`_redirect_job_io(stdout_file, stderr_file)` rewires fd1 / fd2
    via `os.dup2` so subprocess and C-level writes also land in the
    job's log files, restores both fds + sys.stdout / sys.stderr on
    exit. Regression: pre-3.3.0 the runner used
    `contextlib.redirect_stdout` / `redirect_stderr`, which only
    swapped the Python objects -- raw fd-level writes (HF tqdm,
    torch native, subprocesses) bypassed it and ended up in the
    gunicorn worker's output instead of the job log."""

    def test_python_sys_stdout_redirected(self, tmp_path):
        from app.job_runner import _redirect_job_io
        out = tmp_path / "out.log"
        err = tmp_path / "err.log"
        with open(out, "a") as o, open(err, "a") as e:
            with _redirect_job_io(o, e):
                print("hello stdout")
                print("hello stderr", file=sys.stderr)
        assert "hello stdout" in out.read_text()
        assert "hello stderr" in err.read_text()

    def test_fd_level_writes_redirected(self, tmp_path):
        """`os.write(1, ...)` / `os.write(2, ...)` -- mimics how
        torch / tqdm / subprocesses write."""
        from app.job_runner import _redirect_job_io
        out = tmp_path / "out.log"
        err = tmp_path / "err.log"
        with open(out, "a") as o, open(err, "a") as e:
            with _redirect_job_io(o, e):
                os.write(1, b"fd1 write\n")
                os.write(2, b"fd2 write\n")
        assert "fd1 write" in out.read_text()
        assert "fd2 write" in err.read_text()

    def test_subprocess_inherits_redirected_fds(self, tmp_path):
        """A subprocess spawned without stdout/stderr override inherits
        the redirected fds + writes into the job log files."""
        from app.job_runner import _redirect_job_io
        import subprocess as sp
        out = tmp_path / "out.log"
        err = tmp_path / "err.log"
        with open(out, "a") as o, open(err, "a") as e:
            with _redirect_job_io(o, e):
                sp.run(["sh", "-c", "echo 'sub-stdout'; echo 'sub-stderr' >&2"],
                       check=True)
        assert "sub-stdout" in out.read_text()
        assert "sub-stderr" in err.read_text()

    def test_fds_restored_on_exit(self, tmp_path, capsys):
        """After the context manager exits, fd1 / fd2 are back to the
        process default and capsys (pytest's capture) sees writes
        again."""
        from app.job_runner import _redirect_job_io
        out = tmp_path / "out.log"
        err = tmp_path / "err.log"
        with open(out, "a") as o, open(err, "a") as e:
            with _redirect_job_io(o, e):
                pass
        # capsys should now see writes (only works if pytest's stdout
        # capture is back).
        print("after-restore-out")
        sys.stderr.write("after-restore-err\n")
        captured = capsys.readouterr()
        assert "after-restore-out" in captured.out
        assert "after-restore-err" in captured.err

    def test_sys_stdout_stderr_restored_on_exit(self, tmp_path):
        """`sys.stdout` / `sys.stderr` must be restored to their
        pre-`with` references even if they were a non-default
        object."""
        from app.job_runner import _redirect_job_io
        out = tmp_path / "out.log"
        err = tmp_path / "err.log"
        before_stdout = sys.stdout
        before_stderr = sys.stderr
        with open(out, "a") as o, open(err, "a") as e:
            with _redirect_job_io(o, e):
                assert sys.stdout is o
                assert sys.stderr is e
        assert sys.stdout is before_stdout
        assert sys.stderr is before_stderr

    def test_streaming_print_visible_mid_block(self, tmp_path):
        """Streaming guarantee for `print()`: each line completes a
        newline, which flushes the line-buffered file
        (`open(..., 'a', 1)` — what setup_execution_environment
        does), so a separate reader sees the bytes immediately —
        operator can `tail -f stdout.log` during a long-running job."""
        from app.job_runner import _redirect_job_io
        out = tmp_path / "out.log"
        out.touch()
        err = tmp_path / "err.log"
        err.touch()
        with open(out, "a", 1) as o, open(err, "a", 1) as e:
            with _redirect_job_io(o, e):
                print("line-A")
                mid_a = out.read_text()
                print("line-B")
                mid_b = out.read_text()
        assert "line-A" in mid_a, (
            f"line-A not flushed mid-block (Python line-buffered "
            f"semantics broken); got {mid_a!r}"
        )
        assert "line-B" in mid_b, f"line-B not flushed mid-block; got {mid_b!r}"

    def test_streaming_fd_writes_visible_mid_block(self, tmp_path):
        """Streaming guarantee for fd-level writes: `os.write(1, ...)`
        / `os.write(2, ...)` go through the kernel directly (no
        Python-level buffer) and are visible to a separate reader
        immediately. This is what makes HF / tqdm / torch download
        progress tail-able in real time."""
        from app.job_runner import _redirect_job_io
        out = tmp_path / "out.log"
        out.touch()
        err = tmp_path / "err.log"
        err.touch()
        with open(out, "a", 1) as o, open(err, "a", 1) as e:
            with _redirect_job_io(o, e):
                os.write(1, b"fd1-mid\n")
                mid_out = out.read_text()
                os.write(2, b"fd2-mid\n")
                mid_err = err.read_text()
        assert "fd1-mid" in mid_out, f"fd1 write not visible mid-block; got {mid_out!r}"
        assert "fd2-mid" in mid_err, f"fd2 write not visible mid-block; got {mid_err!r}"

    def test_streaming_subprocess_visible_mid_block(self, tmp_path):
        """Streaming guarantee for child processes: a subprocess that
        echoes a line and exits has its output flushed to the job log
        files before the parent moves on — its writes go through the
        inherited fd1 / fd2 directly to the kernel, no Python-level
        buffer to wait on."""
        import subprocess as sp
        from app.job_runner import _redirect_job_io
        out = tmp_path / "out.log"
        out.touch()
        err = tmp_path / "err.log"
        err.touch()
        with open(out, "a", 1) as o, open(err, "a", 1) as e:
            with _redirect_job_io(o, e):
                sp.run(["sh", "-c", "echo child-mid-stdout"], check=True)
                mid_out = out.read_text()
                sp.run(["sh", "-c", "echo child-mid-stderr >&2"], check=True)
                mid_err = err.read_text()
        assert "child-mid-stdout" in mid_out, (
            f"subprocess stdout not visible mid-block; got {mid_out!r}"
        )
        assert "child-mid-stderr" in mid_err, (
            f"subprocess stderr not visible mid-block; got {mid_err!r}"
        )

    def test_exception_inside_block_still_restores(self, tmp_path, capsys):
        """An exception inside the `with` must not leak the redirect:
        fds + sys streams restore in `finally`."""
        from app.job_runner import _redirect_job_io
        out = tmp_path / "out.log"
        err = tmp_path / "err.log"
        before_stdout = sys.stdout
        with open(out, "a") as o, open(err, "a") as e:
            try:
                with _redirect_job_io(o, e):
                    print("before-raise")
                    raise RuntimeError("boom")
            except RuntimeError:
                pass
        assert sys.stdout is before_stdout
        # The pre-raise print should have made it into the file before
        # the exception tore the redirect down.
        assert "before-raise" in out.read_text()


class TestRegisterUserModule:
    """`_register_user_module(glob)` snapshots the job's exec dict into
    `sys.modules[glob['__name__']]` so directive-merged classes
    (`@vortex`, `@composite`, ...) round-trip through importlib-based
    deserialisers — `Vortex.from_dict` is the motivating case.

    Skips registration when the name is already taken (`__main__` is
    owned by `job_runner.py` itself in the running interpreter); the
    practical effect today is to register `__test__` (set by
    `kind=test` and by `kind=testdebug`)."""

    def _reset(self, name):
        import sys
        sys.modules.pop(name, None)

    def test_registers_unique_name(self):
        from app.job_runner import _register_user_module
        import sys
        self._reset("__test__")
        try:
            class _UserCls:
                pass
            glob = {"__name__": "__test__", "_UserCls": _UserCls, "x": 7}
            _register_user_module(glob)
            assert "__test__" in sys.modules
            mod = sys.modules["__test__"]
            assert mod._UserCls is _UserCls
            assert mod.x == 7
        finally:
            self._reset("__test__")

    def test_existing_module_kept_in_place(self):
        """When the target name is already in `sys.modules` (the
        `__main__` case for kind=task/service/debug/draft), the
        module object itself stays — we don't replace what the
        running interpreter is using as its own entry point."""
        from app.job_runner import _register_user_module
        import sys
        existing_main = sys.modules["__main__"]
        glob = {"__name__": "__main__", "_UserCls": type("_UserCls", (), {})}
        _register_user_module(glob)
        assert sys.modules["__main__"] is existing_main

    def test_existing_module_gets_new_user_attributes(self):
        """User classes / functions present in `glob` but not in the
        existing module's dict get stamped on so
        `getattr(sys.modules['__main__'], 'UserCls')` resolves —
        which is what `Vortex.from_dict` does via `importlib.import_module`."""
        from app.job_runner import _register_user_module
        import sys
        existing_main = sys.modules["__main__"]
        # Use names unlikely to be on __main__ already.
        class UserClsXyz: pass
        sentinel = object()
        glob = {
            "__name__": "__main__",
            "UserClsXyz": UserClsXyz,
            "user_global_xyz": sentinel,
        }
        # Make sure these aren't already on __main__.
        for k in ("UserClsXyz", "user_global_xyz"):
            existing_main.__dict__.pop(k, None)
        try:
            _register_user_module(glob)
            assert existing_main.UserClsXyz is UserClsXyz
            assert existing_main.user_global_xyz is sentinel
        finally:
            for k in ("UserClsXyz", "user_global_xyz"):
                existing_main.__dict__.pop(k, None)

    def test_existing_module_owned_bindings_not_overwritten(self):
        """Runner-owned bindings (e.g. `logger`) on `sys.modules['__main__']`
        must not be clobbered by same-named user globals — the runner's
        post-exec finalisation code still reads them. Same-name user
        values are dropped on the floor; the practical collisions
        (`os` / `time` stdlib imports) point to the same modules
        either way."""
        from app.job_runner import _register_user_module
        import sys
        existing_main = sys.modules["__main__"]
        # Plant a runner-style binding we want to protect.
        runner_logger = object()
        existing_main.__dict__["runner_logger_xyz"] = runner_logger
        try:
            user_logger = object()
            assert runner_logger is not user_logger
            glob = {"__name__": "__main__", "runner_logger_xyz": user_logger}
            _register_user_module(glob)
            assert existing_main.runner_logger_xyz is runner_logger
        finally:
            existing_main.__dict__.pop("runner_logger_xyz", None)

    def test_skips_when_glob_has_no_name(self):
        from app.job_runner import _register_user_module
        import sys
        size_before = len(sys.modules)
        _register_user_module({"x": 1})
        assert len(sys.modules) == size_before

    def test_skips_when_name_is_empty_string(self):
        from app.job_runner import _register_user_module
        import sys
        size_before = len(sys.modules)
        _register_user_module({"__name__": ""})
        assert len(sys.modules) == size_before

    def test_snapshot_copy_not_aliased(self):
        """The registered module's __dict__ is a snapshot of glob, not
        a reference to it. Mutating glob after registration must not
        change the module — and vice versa. This keeps the contract
        narrow and avoids accidental sharing between the runner's
        exec dict and the user-importable view."""
        from app.job_runner import _register_user_module
        import sys
        self._reset("__test_iso__")
        try:
            glob = {"__name__": "__test_iso__", "x": 1}
            _register_user_module(glob)
            glob["x"] = 999
            sys.modules["__test_iso__"].y = 42
            assert sys.modules["__test_iso__"].x == 1
            assert "y" not in glob
        finally:
            self._reset("__test_iso__")
