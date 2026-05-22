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
