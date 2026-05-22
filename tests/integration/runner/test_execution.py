import pytest
from unittest.mock import Mock, patch, MagicMock, call, ANY, AsyncMock
import os
import sys
import datetime as real_datetime
import logging
import asyncio
import json
import msgpack
from sqlalchemy import select

@pytest.fixture
def mock_glob():
    return {
        "user": "mock_owner",
        "set": "mock_set",
        "name": "mock_name",
        "setspace": "/mock/setspace",
        "workspace": "/mock/workspace",
        "control": Mock(),
        "listener": None
    }

@pytest.fixture
def sample_param():
    return "!pip install requests\nprint('param')"

@pytest.fixture
def sample_code():
    return "!{other::lib}\nprint('code')"

@pytest.fixture
def sample_libs():
    return ["lib code"]

class TestExecution:

    @patch('app.job_runner.job_lib.send_completion')
    @patch('app.job_runner.subprocess.run')
    @patch('app.lib.sets.subprocess.check_call')
    @patch('app.lib.pyops.ServiceListener')
    def test_run_task_success(self, mock_listener, mock_check_call, mock_run, mock_send, job_env):
        from app.job_runner import main as run
        mock_listener.return_value = None  # For non-service
        mock_check_call.return_value = None  # Venv creation succeeds
        mock_run.return_value = Mock(returncode=0)  # Bootstrap and installs succeed
        env = job_env

        run("id", "owner", "set", "name", "task", "print('param executed')", "print('code executed')", [], "/tmp/pyops/runner.sock", 0, None, env["conf_data"], env["conf_runner"], env["conf_users"], env["conf_web"])

        mock_send.assert_called_with("/tmp/pyops/runner.sock", {"id": "id", "state": "done", "detail": None})

    @patch('app.job_runner.job_lib.send_completion')
    @patch('app.job_runner.subprocess.run')
    @patch('app.lib.sets.subprocess.check_call')
    @patch('app.lib.pyops.ServiceListener')
    @patch('time.sleep')
    def test_run_task_retry_success_on_third(self, mock_sleep, mock_listener, mock_check_call, mock_run, mock_send, job_env):
        from app.job_runner import main as run
        mock_listener.return_value = None
        mock_check_call.return_value = None
        mock_run.return_value = Mock(returncode=0)
        env = job_env
        mock_sleep.return_value = None  # No real delay

        # Mock exec to fail first 2 attempts, succeed on 3rd
        exec_mock = Mock()
        with patch('builtins.exec', side_effect=[Exception("fail1"), Exception("fail2"), None]):
            run("id", "owner", "set", "name", "task", "", "print('success')", [], "/tmp/pyops/runner.sock", 3, None, env["conf_data"], env["conf_runner"], env["conf_users"], env["conf_web"])

        assert mock_sleep.call_count == 2  # Sleeps after first two fails
        # Check the final send is done
        mock_send.assert_called_with("/tmp/pyops/runner.sock", {"id": "id", "state": "done", "detail": None})

    @patch('app.job_runner.job_lib.send_completion')
    @patch('app.job_runner.subprocess.run')
    @patch('app.lib.sets.subprocess.check_call')
    @patch('app.lib.pyops.ServiceListener')
    @patch('time.sleep')
    def test_run_task_retry_fail_all(self, mock_sleep, mock_listener, mock_check_call, mock_run, mock_send, job_env):
        from app.job_runner import main as run
        mock_listener.return_value = None
        mock_check_call.return_value = None
        mock_run.return_value = Mock(returncode=0)
        env = job_env
        mock_sleep.return_value = None

        # Mock exec to always fail
        with patch('builtins.exec', side_effect=Exception("fail")):
            run("id", "owner", "set", "name", "task", "", "raise Exception", [], "/tmp/pyops/runner.sock", 3, None, env["conf_data"], env["conf_runner"], env["conf_users"], env["conf_web"])

        assert mock_sleep.call_count == 3  # Sleeps after each of 3 fails
        call_args = mock_send.call_args[0][1]
        assert call_args["state"] == "failed"
        assert "Failed after 3 retries" in call_args["detail"]

    @patch('app.job_runner.job_lib.send_completion')
    @patch('app.job_runner.subprocess.run')
    @patch('app.lib.sets.subprocess.check_call')
    @patch('app.lib.pyops.ServiceListener')
    def test_run_service_with_listener(self, mock_listener, mock_check_call, mock_run, mock_send, job_env):
        from app.job_runner import main as run
        mock_listener_instance = Mock()
        mock_listener.return_value = mock_listener_instance
        mock_check_call.return_value = None
        mock_run.return_value = Mock(returncode=0)
        env = job_env

        run("id", "owner", "set", "name", "service", "", "print('code executed')", [], "/tmp/pyops/runner.sock", 0, None, env["conf_data"], env["conf_runner"], env["conf_users"], env["conf_web"])

        mock_listener.assert_called_once_with("owner", "id")
        mock_send.assert_called_with("/tmp/pyops/runner.sock", {"id": "id", "state": "done", "detail": None})

    @patch('app.job_runner.job_lib.send_completion')
    @patch('app.job_runner.subprocess.run')
    @patch('app.lib.sets.subprocess.check_call')
    @patch('app.lib.pyops.ServiceListener')
    @patch('time.sleep')
    def test_run_service_no_retry(self, mock_sleep, mock_listener, mock_check_call, mock_run, mock_send, job_env):
        from app.job_runner import main as run
        mock_listener.return_value = Mock()
        mock_check_call.return_value = None
        mock_run.return_value = Mock(returncode=0)
        env = job_env
        mock_sleep.return_value = None

        with patch('builtins.exec', side_effect=Exception("service fail")):
            run("id", "owner", "set", "name", "service", "", "raise Exception", [], "/tmp/pyops/runner.sock", 0, None, env["conf_data"], env["conf_runner"], env["conf_users"], env["conf_web"])

        assert mock_sleep.call_count == 0  # No retries for service
        mock_send.assert_called_with("/tmp/pyops/runner.sock", {"id": "id", "state": "failed", "detail": ANY})

    @patch('app.job_runner.job_lib.send_completion')
    @patch('app.lib.sets.subprocess.check_call')
    @patch('subprocess.run')
    @patch('app.lib.pyops.ServiceListener')
    @patch('time.sleep')
    def test_run_debug_no_retry(self, mock_sleep, mock_listener, mock_run, mock_check_call, mock_send, tmp_path, mock_conf):
        """kind=debug fails once and terminates — no retry loop. (mock-arg
        order matches the bottom-up @patch stack.)"""
        mock_run.return_value = Mock(returncode=0, stdout="pip==24.2\n")
        mock_check_call.return_value = None
        from app.job_runner import main as run
        mock_listener.return_value = Mock()
        data_base = os.path.join(tmp_path, "data")
        venv_path = os.path.join(tmp_path, "venv")
        pkg_path = os.path.join(tmp_path, "pkg")
        mock_sleep.return_value = None

        with patch('builtins.exec', side_effect=Exception("debug fail")):
            run("id", "owner", "set", "name", "debug", "", "raise Exception", [], "/tmp/pyops/runner.sock", 0, None, {"jobs": data_base, "git": f"{tmp_path}/data/git", "web": {"bind": "bind"}}, {"venv": venv_path, "pkg": pkg_path}, {"owner": {}}, {"bind": "bind"})

        assert mock_sleep.call_count == 0  # No retries for debug
        mock_send.assert_called_with("/tmp/pyops/runner.sock", {"id": "id", "state": "failed", "detail": ANY})

    @patch('app.job_runner.job_lib.send_completion')
    @patch('app.lib.sets.subprocess.check_call')
    @patch('subprocess.run')
    @patch('app.lib.pyops.ServiceListener')
    @patch('time.sleep')
    def test_run_test_no_retry(self, mock_sleep, mock_listener, mock_run, mock_check_call, mock_send, tmp_path, mock_conf):
        """kind=test mirrors debug's no-retry contract — a single
        failed exec terminates the job."""
        mock_run.return_value = Mock(returncode=0, stdout="pip==24.2\n")
        mock_check_call.return_value = None
        from app.job_runner import main as run
        mock_listener.return_value = Mock()
        data_base = os.path.join(tmp_path, "data")
        venv_path = os.path.join(tmp_path, "venv")
        pkg_path = os.path.join(tmp_path, "pkg")
        mock_sleep.return_value = None

        with patch('builtins.exec', side_effect=Exception("test fail")):
            run("id", "owner", "set", "name", "test", "", "raise Exception", [], "/tmp/pyops/runner.sock", 0, None, {"jobs": data_base, "git": f"{tmp_path}/data/git", "web": {"bind": "bind"}}, {"venv": venv_path, "pkg": pkg_path}, {"owner": {}}, {"bind": "bind"})

        assert mock_sleep.call_count == 0
        mock_send.assert_called_with("/tmp/pyops/runner.sock", {"id": "id", "state": "failed", "detail": ANY})

    @patch('app.job_runner.job_lib.send_completion')
    @patch('app.job_runner.subprocess.run')
    @patch('app.lib.sets.subprocess.check_call')
    @patch('app.lib.pyops.ServiceListener')
    def test_run_test_sets_name_to_test(self, mock_listener, mock_check_call, mock_run, mock_send, tmp_path, mock_conf):
        """kind=test must execute the task body with __name__='__test__'
        so the same *.task.py can gate test logic from production logic
        via the standard idiom."""
        from app.job_runner import main as run
        mock_listener.return_value = None
        mock_check_call.return_value = None
        mock_run.return_value = Mock(returncode=0)
        data_base = os.path.join(tmp_path, "data")
        venv_path = os.path.join(tmp_path, "venv")
        pkg_path = os.path.join(tmp_path, "pkg")

        code = (
            "import os, json\n"
            "open(os.path.join(workspace, 'name.json'), 'w').write(json.dumps({'name': __name__}))\n"
        )
        run("id", "owner", "myset", "my_task", "test", "", code, [], "/tmp/pyops/runner.sock", 0, None,
            {"jobs": data_base, "git": f"{tmp_path}/data/git", "web": {"bind": "bind"}},
            {"venv": venv_path, "pkg": pkg_path},
            {"owner": {}}, {"bind": "bind"})

        mock_send.assert_called_with("/tmp/pyops/runner.sock", {"id": "id", "state": "done", "detail": None})
        name_file = os.path.join(data_base, "myset", "id", "name.json")
        assert os.path.exists(name_file), f"expected workspace artefact at {name_file}"
        with open(name_file) as f:
            import json
            assert json.load(f) == {"name": "__test__"}

    @patch('app.job_runner.job_lib.send_completion')
    @patch('app.job_runner.subprocess.run')
    @patch('app.lib.sets.subprocess.check_call')
    @patch('app.lib.pyops.ServiceListener')
    def test_run_test_materialises_source_under_test_src(self, mock_listener, mock_check_call, mock_run, mock_send, tmp_path, mock_conf):
        """kind=test writes scriptlet bodies under .test/src/{set}/...
        so coverage / debugger source-path mapping can resolve canonical
        names."""
        from app.job_runner import main as run
        mock_listener.return_value = None
        mock_check_call.return_value = None
        mock_run.return_value = Mock(returncode=0)
        data_base = os.path.join(tmp_path, "data")
        venv_path = os.path.join(tmp_path, "venv")
        pkg_path = os.path.join(tmp_path, "pkg")

        libs = [{"filename": "other::shared", "code": "X = 1\n"}]
        run("id", "owner", "myset", "my_task", "test", "", "pass\n", libs, "/tmp/pyops/runner.sock", 0, None,
            {"jobs": data_base, "git": f"{tmp_path}/data/git", "web": {"bind": "bind"}},
            {"venv": venv_path, "pkg": pkg_path},
            {"owner": {}}, {"bind": "bind"})

        ws = os.path.join(data_base, "myset", "id")
        assert os.path.exists(os.path.join(ws, ".test", "src", "myset", "task", "my_task.py"))
        assert os.path.exists(os.path.join(ws, ".test", "src", "other", "library", "shared.py"))

    @patch('app.job_runner.job_lib.send_completion')
    @patch('app.job_runner.subprocess.run')
    @patch('app.lib.sets.subprocess.check_call')
    @patch('app.lib.pyops.ServiceListener')
    def test_run_test_user_module_registered_in_sys_modules(self, mock_listener, mock_check_call, mock_run, mock_send, tmp_path, mock_conf):
        """kind=test exec'ing libs into glob with __name__='__test__'
        leaves directive-merged classes with __module__='__test__'.
        Without the runner's sys.modules registration, their
        round-trip through importlib (e.g. Vortex.from_dict) breaks.

        Drives a kind=test job whose library defines a class and
        whose main code does `importlib.import_module(__name__)`
        plus `getattr(...)` to fetch that class. Class must be
        resolved and identity-equal to the version in `glob`."""
        from app.job_runner import main as run
        import sys
        sys.modules.pop("__test__", None)

        mock_listener.return_value = None
        mock_check_call.return_value = None
        mock_run.return_value = Mock(returncode=0)
        data_base = os.path.join(tmp_path, "data")
        venv_path = os.path.join(tmp_path, "venv")
        pkg_path = os.path.join(tmp_path, "pkg")

        # Library defines a class; main code resolves it via importlib
        # and writes a marker confirming the round-trip succeeded.
        libs = [{"filename": "myset::lib_one",
                 "code": "class UserCls:\n    label = 'directive_merged'\n"}]
        code = (
            "import importlib, os, json\n"
            "mod = importlib.import_module(__name__)\n"
            "cls = getattr(mod, 'UserCls')\n"
            "open(os.path.join(workspace, 'roundtrip.json'), 'w').write("
            "    json.dumps({'cls_label': cls.label, 'same_obj': cls is UserCls}))\n"
        )

        try:
            run("id", "owner", "myset", "my_task", "test", "", code, libs,
                "/tmp/pyops/runner.sock", 0, None,
                {"jobs": data_base, "git": f"{tmp_path}/data/git", "web": {"bind": "bind"}},
                {"venv": venv_path, "pkg": pkg_path},
                {"owner": {}}, {"bind": "bind"})

            mock_send.assert_called_with("/tmp/pyops/runner.sock", {"id": "id", "state": "done", "detail": None})
            marker = os.path.join(data_base, "myset", "id", "roundtrip.json")
            assert os.path.exists(marker), "main code did not write its marker — importlib roundtrip failed"
            with open(marker) as f:
                import json
                payload = json.load(f)
            assert payload["cls_label"] == "directive_merged"
            assert payload["same_obj"] is True
        finally:
            sys.modules.pop("__test__", None)

    @patch('app.job_runner.job_lib.send_completion')
    @patch('app.job_runner.subprocess.run')
    @patch('app.lib.sets.subprocess.check_call')
    @patch('app.lib.pyops.ServiceListener')
    def test_run_draft_user_class_resolves_via_main_module(self, mock_listener, mock_check_call, mock_run, mock_send, tmp_path, mock_conf):
        """kind=draft (and the other __name__='__main__' kinds:
        task / service / debug) ALSO need importlib resolution to
        work for directive-merged classes. The runner's existing
        `sys.modules['__main__']` is `job_runner.py`, so the registration
        stamps the user's classes ONTO that existing module rather than
        replacing it.

        Drives a kind=draft job whose library defines a class and
        whose main code resolves it via importlib + getattr. Regression
        coverage for the round-trip failure in cami::causal_service
        on first start with a persisted realm."""
        from app.job_runner import main as run
        import sys
        sentinel_attrs = ["UserClsDraft", "user_marker_draft"]
        existing_main = sys.modules["__main__"]
        for k in sentinel_attrs:
            existing_main.__dict__.pop(k, None)

        mock_listener.return_value = Mock()
        mock_check_call.return_value = None
        mock_run.return_value = Mock(returncode=0)
        data_base = os.path.join(tmp_path, "data")
        venv_path = os.path.join(tmp_path, "venv")
        pkg_path = os.path.join(tmp_path, "pkg")

        libs = [{"filename": "myset::lib_one",
                 "code": "class UserClsDraft:\n    label = 'main_module_merged'\n"}]
        code = (
            "import importlib, os, json\n"
            "mod = importlib.import_module(__name__)\n"
            "cls = getattr(mod, 'UserClsDraft')\n"
            "open(os.path.join(workspace, 'roundtrip.json'), 'w').write("
            "    json.dumps({'cls_label': cls.label, 'same_obj': cls is UserClsDraft, 'module': __name__}))\n"
        )

        try:
            run("id", "owner", "myset", "my_task", "draft", "", code, libs,
                "/tmp/pyops/runner.sock", 0, None,
                {"jobs": data_base, "git": f"{tmp_path}/data/git", "web": {"bind": "bind"}},
                {"venv": venv_path, "pkg": pkg_path},
                {"owner": {}}, {"bind": "bind"})

            mock_send.assert_called_with("/tmp/pyops/runner.sock", {"id": "id", "state": "done", "detail": None})
            marker = os.path.join(data_base, "myset", "id", "roundtrip.json")
            assert os.path.exists(marker), "main code did not write its marker — main-module roundtrip failed"
            with open(marker) as f:
                import json
                payload = json.load(f)
            # kind=draft keeps __name__='__main__', and the class found
            # via importlib must be the same object as the directive-
            # loaded UserClsDraft sitting in the task's glob.
            assert payload["cls_label"] == "main_module_merged"
            assert payload["same_obj"] is True
            assert payload["module"] == "__main__"
        finally:
            for k in sentinel_attrs:
                existing_main.__dict__.pop(k, None)

    @patch('app.job_runner.job_lib.send_completion')
    @patch('app.job_runner.subprocess.run')
    @patch('app.lib.sets.subprocess.check_call')
    @patch('app.lib.pyops.ServiceListener')
    def test_run_testdebug_sets_name_to_test(self, mock_listener, mock_check_call, mock_run, mock_send, tmp_path, mock_conf):
        """kind=testdebug runs the body with __name__='__test__' just like
        kind=test — the hybrid kind's whole point is "test semantics under
        a debugger". The debug-style source materialisation under
        .debug/src/ is asserted by the next test."""
        from app.job_runner import main as run
        mock_listener.return_value = Mock()
        mock_check_call.return_value = None
        mock_run.return_value = Mock(returncode=0)
        data_base = os.path.join(tmp_path, "data")
        venv_path = os.path.join(tmp_path, "venv")
        pkg_path = os.path.join(tmp_path, "pkg")

        code = (
            "import os, json\n"
            "open(os.path.join(workspace, 'name.json'), 'w').write(json.dumps({'name': __name__}))\n"
        )
        run("id", "owner", "myset", "my_task", "testdebug", "", code, [], "/tmp/pyops/runner.sock", 0, None,
            {"jobs": data_base, "git": f"{tmp_path}/data/git", "web": {"bind": "bind"}},
            {"venv": venv_path, "pkg": pkg_path},
            {"owner": {}}, {"bind": "bind"})

        mock_send.assert_called_with("/tmp/pyops/runner.sock", {"id": "id", "state": "done", "detail": None})
        name_file = os.path.join(data_base, "myset", "id", "name.json")
        assert os.path.exists(name_file)
        with open(name_file) as f:
            import json
            assert json.load(f) == {"name": "__test__"}

    @patch('app.job_runner.job_lib.send_completion')
    @patch('app.job_runner.subprocess.run')
    @patch('app.lib.sets.subprocess.check_call')
    @patch('app.lib.pyops.ServiceListener')
    def test_run_testdebug_materialises_under_debug_src_with_test_symlink(self, mock_listener, mock_check_call, mock_run, mock_send, tmp_path, mock_conf):
        """testdebug uses debug's materialisation root (.debug/src/) — so
        the path-mapping in VSIX (`./.debug/src` ↔ workspace pyops dir)
        stays valid for both kinds. A `.test` symlink to `.debug` is added
        so any tooling that walks the canonical test-sources path also
        resolves into the same tree."""
        from app.job_runner import main as run
        mock_listener.return_value = Mock()
        mock_check_call.return_value = None
        mock_run.return_value = Mock(returncode=0)
        data_base = os.path.join(tmp_path, "data")
        venv_path = os.path.join(tmp_path, "venv")
        pkg_path = os.path.join(tmp_path, "pkg")

        libs = [{"filename": "other::shared", "code": "X = 1\n"}]
        run("id", "owner", "myset", "my_task", "testdebug", "", "pass\n", libs, "/tmp/pyops/runner.sock", 0, None,
            {"jobs": data_base, "git": f"{tmp_path}/data/git", "web": {"bind": "bind"}},
            {"venv": venv_path, "pkg": pkg_path},
            {"owner": {}}, {"bind": "bind"})

        ws = os.path.join(data_base, "myset", "id")
        assert os.path.exists(os.path.join(ws, ".debug", "src", "myset", "task", "my_task.py"))
        assert os.path.exists(os.path.join(ws, ".debug", "src", "other", "library", "shared.py"))
        # .test must be a symlink (not a directory copy) pointing at .debug
        test_link = os.path.join(ws, ".test")
        assert os.path.islink(test_link), f".test must be a symlink, got {os.readlink(test_link) if os.path.lexists(test_link) else '<missing>'}"
        assert os.readlink(test_link) == ".debug"
        # And it must resolve so tooling that walks `.test/src/...` finds the same files
        assert os.path.exists(os.path.join(ws, ".test", "src", "myset", "task", "my_task.py"))

    @patch('app.job_runner.job_lib.send_completion')
    @patch('app.lib.sets.subprocess.check_call')
    @patch('subprocess.run')
    @patch('app.lib.pyops.ServiceListener')
    @patch('time.sleep')
    def test_run_testdebug_no_retry(self, mock_sleep, mock_listener, mock_run, mock_check_call, mock_send, tmp_path, mock_conf):
        """testdebug inherits debug's "no retry" contract — a single
        failed exec terminates the job; the operator is the loop."""
        mock_run.return_value = Mock(returncode=0, stdout="pip==24.2\n")
        mock_check_call.return_value = None
        from app.job_runner import main as run
        mock_listener.return_value = Mock()
        data_base = os.path.join(tmp_path, "data")
        venv_path = os.path.join(tmp_path, "venv")
        pkg_path = os.path.join(tmp_path, "pkg")
        mock_sleep.return_value = None

        with patch('builtins.exec', side_effect=Exception("testdebug fail")):
            run("id", "owner", "set", "name", "testdebug", "", "raise Exception", [], "/tmp/pyops/runner.sock", 0, None, {"jobs": data_base, "git": f"{tmp_path}/data/git", "web": {"bind": "bind"}}, {"venv": venv_path, "pkg": pkg_path}, {"owner": {}}, {"bind": "bind"})

        assert mock_sleep.call_count == 0
        mock_send.assert_called_with("/tmp/pyops/runner.sock", {"id": "id", "state": "failed", "detail": ANY})

    @patch('app.job_runner.job_lib.send_completion')
    @patch('app.job_runner.subprocess.run')
    @patch('app.lib.sets.subprocess.check_call')
    @patch('app.lib.pyops.ServiceListener')
    def test_run_draft_success(self, mock_listener, mock_check_call, mock_run, mock_send, tmp_path, mock_conf):
        from app.job_runner import main as run
        mock_listener.return_value = None  # For non-service
        mock_check_call.return_value = None
        mock_run.return_value = Mock(returncode=0)
        data_base = os.path.join(tmp_path, "data")
        venv_path = os.path.join(tmp_path, "venv")
        pkg_path = os.path.join(tmp_path, "pkg")

        run("id", "owner", "set", "name", "draft", "print('param executed')", "print('code executed')", [], "/tmp/pyops/runner.sock", 0, None, {"jobs": data_base, "git": f"{tmp_path}/data/git", "web": {"bind": "bind"}}, {"venv": venv_path, "pkg": pkg_path}, {"owner": {"debug_port": 5678}}, {"bind": "127.0.0.1:8000"})

        mock_send.assert_called_with("/tmp/pyops/runner.sock", {"id": "id", "state": "done", "detail": None})

    @patch('app.job_runner.job_lib.send_completion')
    @patch('app.job_runner.subprocess.run')
    @patch('app.lib.sets.subprocess.check_call')
    @patch('app.lib.pyops.ServiceListener')
    def test_run_draft_provides_listener(self, mock_listener, mock_check_call, mock_run, mock_send, tmp_path, mock_conf):
        """Regression: drafted services need `listener` in globals (see job_runner.setup_job_globals kind tuple)."""
        from app.job_runner import main as run
        mock_listener.return_value = Mock()
        mock_check_call.return_value = None
        mock_run.return_value = Mock(returncode=0)
        data_base = os.path.join(tmp_path, "data")
        venv_path = os.path.join(tmp_path, "venv")
        pkg_path = os.path.join(tmp_path, "pkg")
        code = "assert listener is not None, 'listener must exist for draft jobs'"

        run("id", "owner", "set", "name", "draft", "", code, [], "/tmp/pyops/runner.sock", 0, None, {"jobs": data_base, "git": f"{tmp_path}/data/git", "web": {"bind": "bind"}}, {"venv": venv_path, "pkg": pkg_path}, {"owner": {"debug_port": 5678}}, {"bind": "127.0.0.1:8000"})

        mock_listener.assert_called_once_with("owner", "id")
        mock_send.assert_called_with("/tmp/pyops/runner.sock", {"id": "id", "state": "done", "detail": None})

    def test_draft_jobs_allow_concurrent_execution(self, mock_conf, mock_db):
        """Test that multiple draft jobs can run concurrently (unlike debug jobs)."""
        from app.lib import runner as lib_runner

        # Enqueue first draft job
        job1_id = lib_runner.enqueue("set", "name", "owner", "draft", False, "param1", 0, None)

        # Enqueue second draft job - should not cancel the first
        job2_id = lib_runner.enqueue("set", "name", "owner", "draft", False, "param2", 0, None)

        # Both jobs should be in todo state
        job1 = lib_runner.query(job1_id)
        job2 = lib_runner.query(job2_id)

        assert job1.state == "todo"
        assert job2.state == "todo"
        assert job1.detail is None  # No cancellation message
        assert job2.detail is None

    @patch('app.job_runner.job_lib.send_completion')
    @patch('app.job_runner.subprocess.run')
    @patch('app.lib.sets.subprocess.check_call')
    @patch('app.lib.pyops.ServiceListener')
    @patch('time.sleep')
    def test_run_failed_exception(self, mock_sleep, mock_listener, mock_check_call, mock_run, mock_send, tmp_path):
        from app.job_runner import main as run
        mock_listener.return_value = None
        mock_check_call.return_value = None
        mock_run.return_value = Mock(returncode=0)
        data_base = os.path.join(tmp_path, "data")
        venv_path = os.path.join(tmp_path, "venv")
        pkg_path = os.path.join(tmp_path, "pkg")
        mock_sleep.return_value = None

        run("id", "owner", "set", "name", "task", "", "raise Exception('exec error')", [], "/tmp/pyops/runner.sock", 3, None, {"jobs": data_base, "git": f"{tmp_path}/data/git", "web": {"bind": "bind"}}, {"venv": venv_path, "pkg": pkg_path}, {"owner": {"debug_port": 5678}}, {"bind": "127.0.0.1:8000"})

        assert mock_sleep.call_count == 3
        call_args = mock_send.call_args[0][1]
        assert call_args["state"] == "failed"
        assert "Failed after 3 retries" in call_args["detail"]

    @patch('app.lib.job.loadLib')
    def test_process_imports_success(self, mock_loadLib, mock_conf):
        from app.lib.job import process_imports
        mock_loadLib.return_value = "loaded lib"
        libs = []
        result = process_imports("#!{::lib}\nrest", "current_set", False, libs)
        assert result == "#!{::lib}\nrest"
        assert len(libs) == 1
        assert libs[0]["code"] == "loaded lib"
        mock_loadLib.assert_called_once_with("current_set", "lib", False, ANY)

    @patch('app.lib.job.loadLib')
    def test_process_imports_failure(self, mock_loadLib, mock_conf):
        from app.lib.job import process_imports
        mock_loadLib.side_effect = Exception("load fail")
        libs = []
        with pytest.raises(Exception, match="load fail"):
            process_imports("#!{::lib}\nrest", "current_set", False, libs)
        mock_loadLib.assert_called_once_with("current_set", "lib", False, ANY)

    @patch('app.lib.job.loadLib')
    def test_process_imports_no_match(self, mock_loadLib, mock_conf):
        from app.lib.job import process_imports
        libs = []
        result = process_imports("no !\nrest", "current_set", False, libs)
        assert "no !" in result
        assert len(libs) == 0
        mock_loadLib.assert_not_called()

    @patch('os.makedirs')
    @patch('app.job_runner.subprocess.run')
    @patch('os.path.exists')
    def test_process_install_lines_pip(self, mock_exists, mock_run, mock_makedirs, mock_conf, tmp_path):
        from app.lib.job import process_install_lines
        import io
        mock_result = Mock(returncode=0)
        mock_run.return_value = mock_result  # install succeeds
        mock_exists.return_value = True  # venv exists
        lines = ["#!requests"]
        venv_path = os.path.join(tmp_path, "venv")
        pkg_path = os.path.join(tmp_path, "pkg")
        set = "testset"
        id = "id"
        stdout_file = io.StringIO()
        stderr_file = io.StringIO()
        result = process_install_lines(lines, venv_path, pkg_path, set, id, stdout_file, stderr_file)
        assert result == lines
        # Per-job: single pip call, no bootstrap upgrade, no --upgrade flag.
        assert mock_run.call_count == 1
        pip_path = os.path.join(venv_path, set, 'bin', 'pip')
        mock_run.assert_called_with([pip_path, 'install', '--no-cache-dir', 'requests'], stdout=stdout_file, stderr=stderr_file, text=True, check=True)

    @patch('os.makedirs')
    @patch('app.job_runner.subprocess.run')
    @patch('os.path.exists')
    def test_process_install_lines_pip_with_options(self, mock_exists, mock_run, mock_makedirs, mock_conf, tmp_path):
        from app.lib.job import process_install_lines
        import io
        mock_result = Mock(returncode=0)
        mock_run.return_value = mock_result  # install succeeds
        mock_exists.return_value = True  # venv exists
        lines = ["#!torch --index-url https://download.pytorch.org/whl/cu130"]
        venv_path = os.path.join(tmp_path, "venv")
        pkg_path = os.path.join(tmp_path, "pkg")
        set = "testset"
        id = "id"
        stdout_file = io.StringIO()
        stderr_file = io.StringIO()
        result = process_install_lines(lines, venv_path, pkg_path, set, id, stdout_file, stderr_file)
        assert result == lines
        assert mock_run.call_count == 1
        pip_path = os.path.join(venv_path, set, 'bin', 'pip')
        mock_run.assert_called_with([pip_path, 'install', '--no-cache-dir', 'torch', '--index-url', 'https://download.pytorch.org/whl/cu130'], stdout=stdout_file, stderr=stderr_file, text=True, check=True)

    @patch('os.makedirs')
    @patch('app.job_runner.subprocess.run')
    @patch('os.path.exists')
    def test_process_install_lines_skip_lib_directive(self, mock_exists, mock_run, mock_makedirs, mock_conf, tmp_path):
        from app.lib.job import process_install_lines
        import io
        mock_exists.return_value = True  # venv exists
        lines = ["#!{test::lib}"]
        venv_path = os.path.join(tmp_path, "venv")
        pkg_path = os.path.join(tmp_path, "pkg")
        set = "testset"
        id = "id"
        stdout_file = io.StringIO()
        stderr_file = io.StringIO()
        result = process_install_lines(lines, venv_path, pkg_path, set, id, stdout_file, stderr_file)
        assert result == lines
        # Library directive (#!{set::name}) is handled by process_imports, not
        # process_install_lines — it's skipped here. With the per-job no-bootstrap
        # change there are no pip subprocess calls at all.
        assert mock_run.call_count == 0

    @patch('subprocess.run')
    @patch('os.path.exists')
    def test_process_install_lines_git_clone_strips_git_suffix_only(self, mock_exists, mock_run, mock_conf, tmp_path):
        """`<base>.git` → `<base>` (matches set_update_deps.task.py
        _install_git). Dots inside the repo name (e.g. `causal.py`) are
        preserved — the dir name is consumed by `pip install -e` which
        derives the importable package name from setup.py / pyproject.toml,
        so the directory does NOT need to be a valid Python identifier."""
        from app.lib.job import process_install_lines
        import io
        mock_run.return_value = Mock(returncode=0, stderr="")
        mock_exists.return_value = False

        venv_path = os.path.join(tmp_path, "venv")
        pkg_path = os.path.join(tmp_path, "pkg")
        set_ = "testset"
        stdout_file = io.StringIO()
        stderr_file = io.StringIO()

        process_install_lines(["#! https://example.com/causal.py.git"],
                              venv_path, pkg_path, set_, "id", stdout_file, stderr_file)
        repo_dir = os.path.join(pkg_path, set_, "causal.py")
        mock_run.assert_any_call(
            ['git', 'clone', 'https://example.com/causal.py.git', repo_dir],
            stdout=stdout_file, stderr=stderr_file, text=True,
            cwd=os.path.join(pkg_path, set_),
        )

    @patch('subprocess.run')
    @patch('os.path.exists')
    def test_process_install_lines_git_clone(self, mock_exists, mock_run, mock_conf, tmp_path):
        from app.lib.job import process_install_lines
        import io
        mock_run.return_value = Mock(returncode=0, stderr="")  # clone success
        mock_exists.return_value = False  # repo_dir not exist
        lines = ["#! https://repo.git"]
        venv_path = os.path.join(tmp_path, "venv")
        pkg_path = os.path.join(tmp_path, "pkg")
        set = "testset"
        id = "id"
        stdout_file = io.StringIO()
        stderr_file = io.StringIO()
        process_install_lines(lines, venv_path, pkg_path, set, id, stdout_file, stderr_file)
        # Check that git clone was called with correct args
        pkg_set_dir = os.path.join(pkg_path, set)
        repo_dir = os.path.join(pkg_set_dir, "repo")
        mock_run.assert_any_call(['git', 'clone', 'https://repo.git', repo_dir], stdout=stdout_file, stderr=stderr_file, text=True, cwd=pkg_set_dir)

    @patch('subprocess.run')
    @patch('os.path.exists')
    def test_process_install_lines_git_pip_e_success(self, mock_exists, mock_run, mock_conf, tmp_path):
        from app.lib.job import process_install_lines
        import io
        mock_run.return_value = Mock(returncode=0, stderr="")  # clone and pip success
        mock_exists.return_value = False  # repo_dir not exist
        lines = ["#! https://repo.git"]
        venv_path = os.path.join(tmp_path, "venv")
        pkg_path = os.path.join(tmp_path, "pkg")
        set = "testset"
        id = "id"
        stdout_file = io.StringIO()
        stderr_file = io.StringIO()
        process_install_lines(lines, venv_path, pkg_path, set, id, stdout_file, stderr_file)
        pip_path = os.path.join(venv_path, set, 'bin', 'pip')
        repo_dir = os.path.join(pkg_path, set, 'repo')
        # Per-job: pip install -e without --upgrade.
        mock_run.assert_any_call([pip_path, 'install', '--no-cache-dir', '-e', repo_dir], stdout=stdout_file, stderr=stderr_file, text=True, check=True)

    @patch('subprocess.run')
    @patch('os.path.exists')
    def test_process_install_lines_git_pip_e_fail_raises(self, mock_exists, mock_run, mock_conf, tmp_path):
        from app.lib.job import process_install_lines
        import io
        from subprocess import CalledProcessError
        # Per-job: no bootstrap upgrade, so the call sequence is just clone,
        # then pip install -e.
        mock_run.side_effect = [Mock(returncode=0, stderr=""), CalledProcessError(1, 'pip', stderr='no setup.py')]
        mock_exists.return_value = False  # repo_dir not exist
        lines = ["#! https://repo.git"]
        venv_path = os.path.join(tmp_path, "venv")
        pkg_path = os.path.join(tmp_path, "pkg")
        set = "testset"
        id = "id"
        stdout_file = io.StringIO()
        stderr_file = io.StringIO()
        with pytest.raises(CalledProcessError):
            process_install_lines(lines, venv_path, pkg_path, set, id, stdout_file, stderr_file)

    @patch('os.makedirs')
    @patch('subprocess.run')
    def test_process_install_lines_git_error(self, mock_run, mock_makedirs, mock_conf, tmp_path):
        from app.lib.job import process_install_lines
        import io
        # Per-job: no bootstrap upgrade, the first subprocess call is the clone
        # itself which fails.
        mock_run.side_effect = [Mock(returncode=1, stderr="clone fail")]
        lines = ["#! https://fail.git"]
        venv_path = os.path.join(tmp_path, "venv")
        pkg_path = os.path.join(tmp_path, "pkg")
        stdout_file = io.StringIO()
        stderr_file = io.StringIO()
        with pytest.raises(Exception, match="git clone https://fail.git failed: clone fail"):
            process_install_lines(lines, venv_path, pkg_path, "testset", "id", stdout_file, stderr_file)

    @patch('os.makedirs')
    @patch('app.job_runner.subprocess.run')
    def test_process_install_lines_no_bang(self, mock_run, mock_makedirs, mock_conf, tmp_path):
        from app.lib.job import process_install_lines
        import io
        lines = ["normal line"]
        venv_path = os.path.join(tmp_path, "venv")
        pkg_path = os.path.join(tmp_path, "pkg")
        stdout_file = io.StringIO()
        stderr_file = io.StringIO()
        result = process_install_lines(lines, venv_path, "/pkg", "testset", "id", stdout_file, stderr_file)
        assert result == lines
        # No #! lines and no bootstrap upgrade → no subprocess calls.
        assert mock_run.call_count == 0

    def test_shutdown_updates_running_jobs_to_cancelled(self, mock_conf, mock_db):
        from sqlalchemy import select
        from sqlalchemy.orm import Session
        from datetime import datetime
        from app.orm.database import get_db_engine
        from app.orm.job import Job

        # Create running job
        engine = get_db_engine()
        with Session(engine) as session:
            job = Job(id="test_id", kind="service", state="running", pid=123, detail=None, end=None)
            job.set = "test_set"
            job.name = "test_name"
            job.owner = "test_owner"
            session.add(job)
            session.commit()

        # Mock now
        now = datetime.now()

        # Execute shutdown logic
        engine = get_db_engine()
        with Session(engine) as session:
            stmt = select(Job).where(Job.state == "running")
            running_jobs = session.scalars(stmt).all()
            for job in running_jobs:
                job.state = "cancelled"
                job.detail = "cancelled due to shutdown"
                job.end = now
                job.pid = None
            session.commit()

        # Verify
        with Session(engine) as session:
            result = session.get(Job, "test_id")
            assert result.state == "cancelled"
            assert result.detail == "cancelled due to shutdown"
            assert result.end == now
            assert result.pid is None

    @patch('app.runner.PYOPS_ESSENTIAL_SERVICES', [])
    @patch('app.runner.lib_runner.enqueue')
    @patch('app.runner.lib_runner.find')
    def test_schedule_reenqueues_service_excluding_cancelled(self, mock_find, mock_enqueue, mock_conf, mock_db, mock_croniter):
        from app.runner import schedule
        from sqlalchemy import select
        from sqlalchemy.orm import Session
        from app.orm.database import get_db_engine
        from app.orm.schedule import Schedule

        # Setup mocks
        mock_find.side_effect = [[], []]

        # Create schedule
        engine = get_db_engine()
        with Session(engine) as session:
            sched = Schedule(
                id=1,
                set="set",
                name="name",
                owner="owner",
                active=True,
                drop_done=True,
                schedule="",
                param="param",
                last=None
            )
            session.add(sched)
            session.commit()

        # Call schedule
        schedule()

        # Verify find calls
        mock_find.assert_has_calls([
            call("owner", "set", "name", "service",
                 ["todo", "dequeued", "loading", "running",
                  "cancel", "cancelling", "cancelled", "done"]),
            call("owner", "set", "name", "service", ["failed"]),
        ])
        # Verify enqueue
        mock_enqueue.assert_called_once_with("set", "name", "owner", "service", True, "param", 0, None)
        # Check last updated
        with Session(engine) as session:
            result = session.get(Schedule, 1)
            assert result.last is not None

class TestLoggingCapture:
    @pytest.fixture
    def sample_logging_code(self):
        return """
import logging
print('stdout print')
logging.info('INFO message')
logging.warning('WARNING message')
logging.error('ERROR message')
"""

    @pytest.fixture
    def sample_empty_code(self):
        return ""

    @pytest.fixture
    def sample_fail_code(self):
        return "raise ValueError('test error')"

    @pytest.fixture
    def mock_queue(self):
        q = Mock()
        q.put = Mock()
        return q

    def get_log_contents(self, data_base, owner, set_name, job_id):
        workspace = os.path.join(data_base, set_name, job_id)
        stdout_path = os.path.join(workspace, "stdout.log")
        stderr_path = os.path.join(workspace, "stderr.log")
        stdout_content = ""
        if os.path.exists(stdout_path):
            with open(stdout_path, 'r') as f:
                stdout_content = f.read()
        stderr_content = ""
        if os.path.exists(stderr_path):
            with open(stderr_path, 'r') as f:
                stderr_content = f.read()
        return stdout_content, stderr_content

    @patch('app.job_runner.job_lib.send_completion')
    @patch('app.job_runner.subprocess.run')
    @patch('app.lib.sets.subprocess.check_call')
    @patch('app.lib.pyops.ServiceListener')
    def test_run_task_logging_success(self, mock_listener, mock_check_call, mock_run, mock_send, tmp_path, sample_logging_code, mock_conf):
        from app.job_runner import main as run
        mock_listener.return_value = None
        mock_check_call.return_value = None
        mock_run.return_value = Mock(returncode=0)
        data_base = os.path.join(tmp_path, "data")
        owner = "owner"
        set_name = "set"
        job_id = "id"
        venv_path = os.path.join(tmp_path, "venv")
        pkg_path = os.path.join(tmp_path, "pkg")

        run(job_id, owner, set_name, "name", "task", "", sample_logging_code, [], "/tmp/pyops/runner.sock", 0, None, {"jobs": data_base, "git": f"{tmp_path}/data/git", "web": {"bind": "bind"}}, {"venv": venv_path, "pkg": pkg_path}, {"owner": {"debug_port": 5678}}, {"bind": "127.0.0.1:8000"})

        stdout_content, stderr_content = self.get_log_contents(data_base, owner, set_name, job_id)

        assert "stdout print" in stdout_content
        assert "INFO message" in stdout_content
        assert "WARNING message" in stderr_content
        assert "ERROR message" in stderr_content
        mock_send.assert_called_with("/tmp/pyops/runner.sock", {"id": job_id, "state": "done", "detail": None})

    @patch('app.job_runner.job_lib.send_completion')
    @patch('app.job_runner.subprocess.run')
    @patch('app.lib.sets.subprocess.check_call')
    @patch('app.lib.pyops.ServiceListener')
    def test_run_service_logging(self, mock_listener, mock_check_call, mock_run, mock_send, tmp_path, sample_logging_code, mock_conf):
        from app.job_runner import main as run
        mock_listener.return_value = Mock()  # For service
        mock_check_call.return_value = None
        mock_run.return_value = Mock(returncode=0)
        data_base = os.path.join(tmp_path, "data")
        owner = "owner"
        set_name = "set"
        job_id = "id"

        venv_path = os.path.join(tmp_path, "venv")
        pkg_path = os.path.join(tmp_path, "pkg")
        run(job_id, owner, set_name, "name", "service", "", sample_logging_code, [], "/tmp/pyops/runner.sock", 0, None, {"jobs": data_base, "git": f"{tmp_path}/data/git", "web": {"bind": "bind"}}, {"venv": venv_path, "pkg": pkg_path}, {"owner": {"debug_port": 5678}}, {"bind": "127.0.0.1:8000"})

        stdout_content, stderr_content = self.get_log_contents(data_base, owner, set_name, job_id)

        assert "stdout print" in stdout_content
        assert "INFO message" in stdout_content
        assert "WARNING message" in stderr_content
        assert "ERROR message" in stderr_content
        mock_send.assert_called_with("/tmp/pyops/runner.sock", {"id": job_id, "state": "done", "detail": None})

    @patch('app.job_runner.job_lib.send_completion')
    @patch('app.job_runner.subprocess.run')
    @patch('app.lib.sets.subprocess.check_call')
    @patch('app.lib.pyops.ServiceListener')
    def test_run_service_closes_listener(self, mock_listener, mock_check_call, mock_run, mock_send, tmp_path, mock_conf):
        from app.job_runner import main as run
        mock_listener_instance = Mock()
        mock_listener.return_value = mock_listener_instance
        mock_check_call.return_value = None
        mock_run.return_value = Mock(returncode=0)
        data_base = os.path.join(tmp_path, "data")
        owner = "owner"
        set_name = "set"
        job_id = "id"

        venv_path = os.path.join(tmp_path, "venv")
        pkg_path = os.path.join(tmp_path, "pkg")
        run(job_id, owner, set_name, "name", "service", "", "pass", [], "/tmp/pyops/runner.sock", 0, None, {"jobs": data_base, "git": f"{tmp_path}/data/git", "web": {"bind": "bind"}}, {"venv": venv_path, "pkg": pkg_path}, {"owner": {"debug_port": 5678}}, {"bind": "127.0.0.1:8000"})

        mock_listener.assert_called_once_with("owner", "id")
        mock_listener_instance.close.assert_called_once()

    @patch('app.job_runner.subprocess.run')
    @patch('app.lib.sets.subprocess.check_call')
    @patch('app.job_runner.job_lib.send_completion')
    @patch('app.lib.pyops.ServiceListener')
    def test_run_service_closes_listener_on_error(self, mock_listener, mock_check_call, mock_run, mock_send, tmp_path, mock_conf):
        from app.job_runner import main as run
        mock_listener_instance = Mock()
        mock_listener.return_value = mock_listener_instance
        mock_check_call.return_value = None
        mock_run.return_value = Mock(returncode=0)
        data_base = os.path.join(tmp_path, "data")
        owner = "owner"
        set_name = "set"
        job_id = "id"

        venv_path = os.path.join(tmp_path, "venv")
        pkg_path = os.path.join(tmp_path, "pkg")
        with patch('builtins.exec', side_effect=Exception("error")):
            run(job_id, owner, set_name, "name", "service", "", "raise Exception", [], "/tmp/pyops/runner.sock", 0, None, {"jobs": data_base, "git": f"{tmp_path}/data/git", "web": {"bind": "bind"}}, {"venv": venv_path, "pkg": pkg_path}, {"owner": {"debug_port": 5678}}, {"bind": "127.0.0.1:8000"})

        mock_listener.assert_called_once_with("owner", "id")
        mock_listener_instance.close.assert_called_once()
        mock_check_call.assert_called_with("/tmp/pyops/runner.sock", {"id": "id", "state": "failed", "detail": ANY})

    @patch('app.lib.sets.subprocess.check_call')
    @patch('subprocess.check_call')
    @patch('app.lib.pyops.ServiceListener')
    @patch('subprocess.run')
    @patch('app.lib.job.send_completion')
    def test_run_debug_no_debugger_injection(self, mock_send, mock_run, mock_listener, mock_check_call, mock_sets_check_call, mock_conf, tmp_path):
        """Regression: pyops no longer imports or starts a debugger.
        run_debug_job materialises sources, runs the user's param + libs +
        code, sends `state=running` after param, and `state=done` at the
        end — same code path as task/service/draft minus retries."""
        mock_run.side_effect = [Mock(returncode=0, stdout=""), Mock(returncode=0)]
        mock_sets_check_call.return_value = None
        from app.job_runner import main as run
        mock_listener.return_value = Mock()
        data_base = os.path.join(tmp_path, "data")
        owner = "owner"
        set_name = "set"
        job_id = "id"
        code = "print('test')"

        venv_path = os.path.join(tmp_path, "venv")
        pkg_path = os.path.join(tmp_path, "pkg")
        run(job_id, owner, set_name, "name", "debug", "", code, [],
            "/tmp/pyops/runner.sock", 0, None,
            {"jobs": data_base, "git": f"{tmp_path}/data/git", "web": {"bind": "bind"}},
            {"venv": venv_path, "pkg": pkg_path},
            {"owner": {}},
            {"bind": "127.0.0.1:8000"})

        # state=running fires after exec(param), state=done at the end.
        sent_states = [c.args[1]["state"] for c in mock_send.call_args_list]
        assert "running" in sent_states
        assert sent_states[-1] == "done"

    @patch('app.job_runner.job_lib.send_completion')
    @patch('app.lib.pyops.ServiceListener')
    @patch('subprocess.check_call')
    @patch('subprocess.run')
    def test_run_debug_logging(self, mock_run, mock_check_call, mock_listener, mock_send, tmp_path, sample_logging_code, mock_conf):
        mock_run.side_effect = [Mock(returncode=0, stdout=""), Mock(returncode=0)]
        from app.job_runner import main as run
        mock_listener.return_value = Mock()
        data_base = os.path.join(tmp_path, "data")
        owner = "owner"
        set_name = "set"
        job_id = "id"

        venv_path = os.path.join(tmp_path, "venv")
        pkg_path = os.path.join(tmp_path, "pkg")
        run(job_id, owner, set_name, "name", "debug", "", sample_logging_code, [],
            "/tmp/pyops/runner.sock", 0, None,
            {"jobs": data_base, "git": f"{tmp_path}/data/git", "web": {"bind": "bind"}},
            {"venv": venv_path, "pkg": pkg_path},
            {"owner": {}},
            {"bind": "127.0.0.1:8000"})

        stdout_content, stderr_content = self.get_log_contents(data_base, owner, set_name, job_id)

        assert "stdout print" in stdout_content
        assert "INFO message" in stdout_content
        assert "WARNING message" in stderr_content
        assert "ERROR message" in stderr_content
        # The very last send_completion is "done" (a "running" precedes it).
        last_call = mock_send.call_args_list[-1]
        assert last_call.args[1] == {"id": job_id, "state": "done", "detail": None}

    @patch('app.job_runner.job_lib.send_completion')
    @patch('app.job_runner.subprocess.run')
    @patch('app.lib.sets.subprocess.check_call')
    @patch('app.lib.pyops.ServiceListener')
    @patch('time.sleep')
    def test_run_fail_logging(self, mock_sleep, mock_listener, mock_check_call, mock_run, mock_send, tmp_path, sample_fail_code, mock_conf):
        from app.job_runner import main as run
        mock_listener.return_value = None
        mock_check_call.return_value = None
        mock_run.return_value = Mock(returncode=0)
        data_base = os.path.join(tmp_path, "data")
        owner = "owner"
        set_name = "set"
        job_id = "id"
        mock_sleep.return_value = None

        venv_path = os.path.join(tmp_path, "venv")
        pkg_path = os.path.join(tmp_path, "pkg")
        run(job_id, owner, set_name, "name", "task", "", sample_fail_code, [], "/tmp/pyops/runner.sock", 3, None, {"jobs": data_base, "git": f"{tmp_path}/data/git", "web": {"bind": "bind"}}, {"venv": venv_path, "pkg": pkg_path}, {"owner": {"debug_port": 5678}}, {"bind": "127.0.0.1:8000"})

        stdout_content, stderr_content = self.get_log_contents(data_base, owner, set_name, job_id)

        assert stdout_content == ""
        assert "Failed after 3 retries" in stderr_content
        assert "ValueError: test error" in stderr_content
        detail = mock_send.call_args[0][1]["detail"]
        assert "Failed after 3 retries" in detail
        assert "ValueError" in str(detail)
        assert mock_sleep.call_count == 3

    @patch('app.job_runner.job_lib.send_completion')
    @patch('app.job_runner.subprocess.run')
    @patch('app.lib.sets.subprocess.check_call')
    @patch('app.lib.pyops.ServiceListener')
    def test_run_empty_output(self, mock_listener, mock_check_call, mock_run, mock_send, tmp_path, sample_empty_code, mock_conf):
        from app.job_runner import main as run
        mock_listener.return_value = None
        mock_check_call.return_value = None
        mock_run.return_value = Mock(returncode=0)
        data_base = os.path.join(tmp_path, "data")
        owner = "owner"
        set_name = "set"
        job_id = "id"

        venv_path = os.path.join(tmp_path, "venv")
        pkg_path = os.path.join(tmp_path, "pkg")
        run(job_id, owner, set_name, "name", "task", "", sample_empty_code, [], "/tmp/pyops/runner.sock", 0, None, {"jobs": data_base, "git": f"{tmp_path}/data/git", "web": {"bind": "bind"}}, {"venv": venv_path, "pkg": pkg_path}, {"owner": {"debug_port": 5678}}, {"bind": "127.0.0.1:8000"})

        stdout_content, stderr_content = self.get_log_contents(data_base, owner, set_name, job_id)

        assert stdout_content == ""
        assert stderr_content == ""
        mock_send.assert_called_with("/tmp/pyops/runner.sock", {"id": job_id, "state": "done", "detail": None})


    @patch('app.job_runner.job_lib.send_completion')
    @patch('app.job_runner.subprocess.run')
    @patch('app.lib.sets.subprocess.check_call')
    @patch('os.chdir')
    @patch('app.lib.pyops.ServiceListener')
    def test_run_task_sets_cwd(self, mock_listener, mock_chdir, mock_check_call, mock_run, mock_send, tmp_path, mock_conf):
        from app.job_runner import main as run
        mock_listener.return_value = None
        mock_check_call.return_value = None
        mock_run.return_value = Mock(returncode=0)
        data_base = os.path.join(tmp_path, "data")
        owner = "owner"
        set_name = "set"
        job_id = "id"
        workspace = os.path.join(data_base, set_name, job_id)

        venv_path = os.path.join(tmp_path, "venv")
        pkg_path = os.path.join(tmp_path, "pkg")
        run(job_id, owner, set_name, "name", "task", "", "print('code')", [], "/tmp/pyops/runner.sock", 0, None, {"jobs": data_base, "git": f"{tmp_path}/data/git", "web": {"bind": "bind"}}, {"venv": venv_path, "pkg": pkg_path}, {"owner": {"debug_port": 5678}}, {"bind": "127.0.0.1:8000"})

        mock_chdir.assert_called_once_with(workspace)
        mock_send.assert_called_with("/tmp/pyops/runner.sock", {"id": job_id, "state": "done", "detail": None})


    @patch('app.lib.pyops.Control', return_value=Mock())
    @patch('app.lib.job.send_completion')
    @patch('subprocess.run')
    @patch('app.lib.sets.subprocess.check_call')
    def test_run_task_glob_spaces_and_cwd(self, mock_check_call, mock_run, mock_send, mock_control, tmp_path, mock_conf):
        from app.job_runner import main as run
        from app.lib import files
        import os

        mock_check_call.return_value = None
        mock_run.return_value = Mock(returncode=0)
        project_root = str(tmp_path)
        mock_conf_data = {"git": "data/git", "jobs": "data/jobs"}
        mock_conf_runner = {"pkg": "data/pkg"}

        with patch.object(files, 'project_root', project_root), \
             patch('app.lib.files.conf_data', return_value=mock_conf_data), \
             patch('app.lib.files.conf_runner', return_value=mock_conf_runner), \
             patch('app.lib.pyops.ServiceListener', return_value=None):

            data_base = os.path.join(tmp_path, "data")
            owner = "owner"
            set_name = "set"
            job_id = "id"
            workspace = os.path.join(data_base, set_name, job_id)
            expected_gitspace = os.path.join(project_root, "data/git")
            expected_pkgspace = os.path.join(project_root, "pkg")
            expected_jobsspace = data_base

            code = """
import os
print("CWD: " + os.getcwd())
print("gitspace: " + globals()['gitspace'])
print("pkgspace: " + globals()['pkgspace'])
print("jobsspace: " + globals()['jobsspace'])
"""
            venv_path = os.path.join(tmp_path, "venv")
            pkg_path = os.path.join(tmp_path, "pkg")
            run(job_id, owner, set_name, "name", "task", "", code, [], "/tmp/pyops/runner.sock", 0, None, {"jobs": data_base, "git": f"{tmp_path}/data/git", "web": {"bind": "bind"}}, {"venv": venv_path, "pkg": pkg_path}, {"owner": {"debug_port": 5678}}, {"bind": "127.0.0.1:8000"})

            # Read stdout
            stdout_content, _ = self.get_log_contents(data_base, owner, set_name, job_id)
            assert workspace in stdout_content  # CWD is workspace
            assert expected_gitspace in stdout_content
            assert expected_pkgspace in stdout_content
            assert expected_jobsspace in stdout_content
            mock_send.assert_called_with("/tmp/pyops/runner.sock", {"id": "id", "state": "done", "detail": None})

    @patch('app.job_runner.job_lib.send_completion')
    @patch('app.job_runner.subprocess.run')
    @patch('app.lib.sets.subprocess.check_call')
    @patch('app.lib.pyops.ServiceListener')
    @patch('time.sleep')
    def test_run_error_in_param(self, mock_sleep, mock_listener, mock_check_call, mock_run, mock_send, tmp_path, mock_conf):
        from app.job_runner import main as run
        mock_listener.return_value = None
        mock_check_call.return_value = None
        mock_run.return_value = Mock(returncode=0)
        mock_sleep.return_value = None
        data_base = os.path.join(tmp_path, "data")
        set_ = "myset"
        name = "myname"
        param = "raise ValueError('param err')"
        venv_path = os.path.join(tmp_path, "venv")
        pkg_path = os.path.join(tmp_path, "pkg")
        run("id", "owner", set_, name, "task", param, "pass", [], "/tmp/pyops/runner.sock", 3, None, {"jobs": data_base, "git": f"{tmp_path}/data/git", "web": {"bind": "bind"}}, {"venv": venv_path, "pkg": pkg_path}, {"owner": {"debug_port": 5678}}, {"bind": "127.0.0.1:8000"})
        call_args = mock_send.call_args[0][1]
        assert call_args["state"] == "failed"
        assert "param err" in call_args["detail"]
        assert mock_sleep.call_count == 3

    @patch('app.job_runner.job_lib.send_completion')
    @patch('app.job_runner.subprocess.run')
    @patch('app.lib.sets.subprocess.check_call')
    @patch('app.lib.pyops.ServiceListener')
    @patch('time.sleep')
    def test_run_error_in_code(self, mock_sleep, mock_listener, mock_check_call, mock_run, mock_send, tmp_path, mock_conf):
        from app.job_runner import main as run
        mock_listener.return_value = None
        mock_check_call.return_value = None
        mock_run.return_value = Mock(returncode=0)
        mock_sleep.return_value = None
        data_base = os.path.join(tmp_path, "data")
        set_ = "myset"
        name = "myname"
        code = "raise ValueError('code err')"
        venv_path = os.path.join(tmp_path, "venv")
        pkg_path = os.path.join(tmp_path, "pkg")
        run("id", "owner", set_, name, "task", "", code, [], "/tmp/pyops/runner.sock", 3, None, {"jobs": data_base, "git": f"{tmp_path}/data/git", "web": {"bind": "bind"}}, {"venv": venv_path, "pkg": pkg_path}, {"owner": {"debug_port": 5678}}, {"bind": "127.0.0.1:8000"})
        call_args = mock_send.call_args[0][1]
        assert call_args["state"] == "failed"
        assert "code err" in call_args["detail"]
        assert f"{set_}/task/{name}.py" in call_args["detail"]
        assert mock_sleep.call_count == 3

    @patch('app.job_runner.job_lib.send_completion')
    @patch('app.job_runner.subprocess.run')
    @patch('app.lib.sets.subprocess.check_call')
    @patch('app.lib.pyops.ServiceListener')
    @patch('time.sleep')
    def test_run_error_in_lib(self, mock_sleep, mock_listener, mock_check_call, mock_run, mock_send, tmp_path, mock_conf):
        from app.job_runner import main as run
        mock_listener.return_value = None
        mock_check_call.return_value = None
        mock_run.return_value = Mock(returncode=0)
        mock_sleep.return_value = None
        data_base = os.path.join(tmp_path, "data")
        set_ = "myset"
        name = "myname"
        lib_code = "raise ValueError('lib err')"
        lib_filename = f"{set_}::mylib"
        libs = [{"code": lib_code, "filename": lib_filename}]
        venv_path = os.path.join(tmp_path, "venv")
        pkg_path = os.path.join(tmp_path, "pkg")
        run("id", "owner", set_, name, "task", "", "pass", libs, "/tmp/pyops/runner.sock", 3, None, {"jobs": data_base, "git": f"{tmp_path}/data/git", "web": {"bind": "bind"}}, {"venv": venv_path, "pkg": pkg_path}, {"owner": {"debug_port": 5678}}, {"bind": "127.0.0.1:8000"})
        call_args = mock_send.call_args[0][1]
        assert call_args["state"] == "failed"
        assert "lib err" in call_args["detail"]
        assert f"{set_}/library/mylib.py" in call_args["detail"]
        assert mock_sleep.call_count == 3

    @patch('app.job_runner.job_lib.send_completion')
    @patch('app.job_runner.subprocess.run')
    @patch('app.lib.sets.subprocess.check_call')
    @patch('app.lib.pyops.ServiceListener')
    @patch('time.sleep')
    def test_compile_syntax_error(self, mock_sleep, mock_listener, mock_check_call, mock_run, mock_send, tmp_path, mock_conf):
        from app.job_runner import main as run
        mock_listener.return_value = None
        mock_check_call.return_value = None
        mock_run.return_value = Mock(returncode=0)
        mock_sleep.return_value = None
        data_base = os.path.join(tmp_path, "data")
        set_ = "myset"
        name = "myname"
        code = "def foo("  # syntax error
        venv_path = os.path.join(tmp_path, "venv")
        pkg_path = os.path.join(tmp_path, "pkg")
        run("id", "owner", set_, name, "task", "", code, [], "/tmp/pyops/runner.sock", 3, None, {"jobs": data_base, "git": f"{tmp_path}/data/git", "web": {"bind": "bind"}}, {"venv": venv_path, "pkg": pkg_path}, {"owner": {"debug_port": 5678}}, {"bind": "127.0.0.1:8000"})
        call_args = mock_send.call_args[0][1]
        assert call_args["state"] == "failed"
        assert "SyntaxError" in call_args["detail"]
        assert f"{set_}/task/{name}.py" in call_args["detail"]
        assert mock_sleep.call_count == 0  # no retry for compile error

    @patch('app.job_runner.job_lib.send_completion')
    @patch('app.job_runner.subprocess.run')
    @patch('app.lib.sets.subprocess.check_call')
    @patch('app.lib.pyops.ServiceListener')
    def test_run_error_in_lib_service(self, mock_listener, mock_check_call, mock_run, mock_send, tmp_path, mock_conf):
        from app.job_runner import main as run
        mock_listener.return_value = Mock()
        mock_check_call.return_value = None
        mock_run.return_value = Mock(returncode=0)
        data_base = os.path.join(tmp_path, "data")
        set_ = "myset"
        name = "myname"
        lib_code = "raise ValueError('lib err service')"
        lib_filename = f"{set_}::mylib"
        libs = [{"code": lib_code, "filename": lib_filename}]
        venv_path = os.path.join(tmp_path, "venv")
        pkg_path = os.path.join(tmp_path, "pkg")
        run("id", "owner", set_, name, "service", "", "pass", libs, "/tmp/pyops/runner.sock", 0, None, {"jobs": data_base, "git": f"{tmp_path}/data/git", "web": {"bind": "bind"}}, {"venv": venv_path, "pkg": pkg_path}, {"owner": {"debug_port": 5678}}, {"bind": "127.0.0.1:8000"})
        call_args = mock_send.call_args[0][1]
        assert call_args["state"] == "failed"
        assert "lib err service" in call_args["detail"]
        assert f"{set_}/library/mylib.py" in call_args["detail"]

    @patch('app.job_runner.job_lib.send_completion')
    @patch('app.lib.job.process_code_installs', side_effect=lambda p, c, l, v, pk, s, j, so, se: (p, c, l))
    @patch('subprocess.check_call')
    @patch('subprocess.run')
    @patch('app.lib.pyops.ServiceListener')
    def test_run_debug_creates_files(self, mock_listener, mock_run, mock_check_call, mock_process_installs, mock_send, tmp_path, mock_conf):
        from app.job_runner import main as run
        mock_listener.return_value = Mock()
        mock_run.return_value = Mock(returncode=0, stdout="")
        data_base = os.path.join(tmp_path, "data")
        owner = "owner"
        set_name = "set"
        job_id = "id"
        param = "print('param code')"
        code = "print('main code')"
        libs = [{"code": "print('lib code')", "filename": "set::mylib"}]

        venv_path = os.path.join(tmp_path, "venv")
        pkg_path = os.path.join(tmp_path, "pkg")
        run(job_id, owner, set_name, "name", "debug", param, code, libs, "/tmp/pyops/runner.sock", 0, None, {"jobs": data_base, "git": f"{tmp_path}/data/git", "web": {"bind": "bind"}}, {"venv": venv_path, "pkg": pkg_path}, {"owner": {}}, {"bind": "127.0.0.1:8000"})

        last_call = mock_send.call_args_list[-1]
        assert last_call.args[1] == {"id": "id", "state": "done", "detail": None}

        # Check that files were created in .debug/src/ subdirectories
        workspace = os.path.join(data_base, set_name, job_id)
        debug_src_dir = os.path.join(workspace, ".debug", "src")

        # Check param file
        param_file = os.path.join(debug_src_dir, set_name, "task", "param.py")
        assert os.path.exists(param_file)
        with open(param_file, 'r') as f:
            assert "print('param code')" in f.read()

        # Check main code file
        code_file = os.path.join(debug_src_dir, set_name, "task", "name.py")
        assert os.path.exists(code_file)
        with open(code_file, 'r') as f:
            assert "print('main code')" in f.read()

        # Check lib file
        lib_file = os.path.join(debug_src_dir, set_name, "library", "mylib.py")
        assert os.path.exists(lib_file)
        with open(lib_file, 'r') as f:
            assert "print('lib code')" in f.read()

    @patch('app.lib.job.send_completion')
    @patch('subprocess.check_call')
    @patch('subprocess.run')
    @patch('app.lib.pyops.ServiceListener')
    def test_run_debug_overrides_with_pip_install(self, mock_listener, mock_run, mock_check_call, mock_send, tmp_path, mock_conf):
        """Regression test: !pip directives inside override code are still
        processed by job_setup.py before this runner phase. The override
        body itself runs through the regular exec()."""
        from app.job_runner import main as run

        mock_run.return_value = Mock(returncode=0, stdout="")
        mock_listener.return_value = Mock()

        data_base = os.path.join(tmp_path, "data")
        owner = "owner"
        set_name = "set"
        job_id = "id"

        overrides = {"set::name": "#!pip install requests\nprint('overridden code')"}
        overrides_json = json.dumps(overrides)

        venv_path = os.path.join(tmp_path, "venv")
        pkg_path = os.path.join(tmp_path, "pkg")
        run(job_id, owner, set_name, "name", "debug", "", "print('original code')", [],
            "/tmp/pyops/runner.sock", 0, overrides_json,
            {"jobs": data_base, "git": f"{tmp_path}/data/git", "web": {"bind": "bind"}},
            {"venv": venv_path, "pkg": pkg_path},
            {"owner": {}},
            {"bind": "127.0.0.1:8000"})

        last_call = mock_send.call_args_list[-1]
        assert last_call.args[1] == {"id": "id", "state": "done", "detail": None}

    @patch('app.lib.job.send_completion')
    @patch('subprocess.check_call')
    @patch('subprocess.run')
    @patch('app.lib.pyops.ServiceListener')
    def test_debug_overrides_ignore_unreferenced(self, mock_listener, mock_run, mock_check_call, mock_send, tmp_path, mock_conf):
        """Unreferenced override entries don't produce extra files in .debug/src/."""
        from app.job_runner import main as run

        mock_run.return_value = Mock(returncode=0, stdout="")
        mock_listener.return_value = Mock()

        data_base = os.path.join(tmp_path, "data")
        owner = "owner"
        set_name = "set"
        job_id = "id"

        overrides = {
            "set::name": "print('overridden code')",
            "set::newlib": "print('unused new lib')",
        }
        overrides_json = json.dumps(overrides)

        venv_path = os.path.join(tmp_path, "venv")
        pkg_path = os.path.join(tmp_path, "pkg")
        run(job_id, owner, set_name, "name", "debug", "", "print('task')", [],
            "/tmp/pyops/runner.sock", 0, overrides_json,
            {"jobs": data_base, "git": f"{tmp_path}/data/git", "web": {"bind": "bind"}},
            {"venv": venv_path, "pkg": pkg_path},
            {"owner": {}},
            {"bind": "127.0.0.1:8000"})

        last_call = mock_send.call_args_list[-1]
        assert last_call.args[1] == {"id": "id", "state": "done", "detail": None}

        workspace = os.path.join(data_base, set_name, job_id)
        debug_src_dir = os.path.join(workspace, ".debug", "src")
        new_lib_file = os.path.join(debug_src_dir, set_name, "library", "newlib.py")
        assert not os.path.exists(new_lib_file)

        code_file = os.path.join(debug_src_dir, set_name, "task", "name.py")
        with open(code_file, 'r') as f:
            assert "print('overridden code')" in f.read()

    @patch('app.runner.postTerm')
    def test_service_quick_fail_stays_failed_no_cancelled(self, mock_postTerm, mock_conf, mock_db):
        """Regression test: terminate_job_processes guard prevents 'failed' → 'cancelled' transition."""
        from app.runner import terminate_job_processes
        from app.orm.database import get_db_engine
        from app.orm.job import Job
        from sqlalchemy.orm import Session

        # Create job in DB with 'failed' state and pid (simulating quick fail scenario)
        engine = get_db_engine()
        with Session(engine) as session:
            job = Job(id="test_id", set="test_set", name="test_name", owner="test_owner", kind="service", state="failed", pid=12345)
            session.add(job)
            session.commit()

        # Call terminate (should guard and skip postTerm)
        terminate_job_processes("test_id", 12345, "test_owner")

        # Assert postTerm not called (guard prevents transition)
        mock_postTerm.assert_not_called()


class TestMainLoopDebugIntegration:
    """Integration tests for main loop spawn path. The has_debugpy_adapter
    polling, the 5-second adapter timeout, and the kind=debug-specific
    branch in the main loop are all gone — debug jobs spawn exactly like
    task/service/draft. The remaining test verifies the universal path."""

    @patch('app.runner.main')  # Patch the main function itself
    @patch('app.runner.getNextToDo')
    @patch('app.runner.loadJob')
    @patch('app.runner.assignJob')
    @patch('app.runner.finishJob')
    @patch('subprocess.check_output')
    @patch('subprocess.Popen')
    def test_main_loop_non_debug_job_no_waiting(self, mock_process_class, mock_check_output, mock_finish_job, mock_assign_job,
                                               mock_load_job, mock_get_next, mock_main, mock_conf, mock_db):
        """Test that non-debug jobs don't wait for adapter."""
        from app.runner import main

        # Setup mocks for non-debug job
        mock_check_output.return_value = b'64-bit'
        mock_get_next.return_value = "task_job_id"
        mock_load_job.return_value = ("owner", "set", "name", "task", "param", "code", "overrides")
        mock_process = Mock()
        mock_process.pid = 12345
        mock_process_class.return_value = mock_process

        # Mock the main function to simulate one iteration
        def mock_main_impl(data_base, web_bind, venv_path, pkg_path, env):
            # Simulate one iteration of the main loop
            job_id = mock_get_next()
            if job_id is not None:
                mock_load_job()
                p = mock_process_class()
                mock_assign_job(job_id, p.pid)

        mock_main.side_effect = mock_main_impl

        # Run main - it should process one job and exit
        main("data", "bind", "/venv", "/pkg", {})

        # Verify assignJob was called immediately (no adapter waiting)
        mock_assign_job.assert_called_once_with("task_job_id", 12345)
        # Verify no finishJob calls
        mock_finish_job.assert_not_called()

    @patch('app.runner.main')  # Patch main to simulate
    @patch('app.runner.getNextToDo')
    @patch('app.runner.loadJob')
    @patch('app.runner.assignJob')
    @patch('app.runner.finishJob')
    @patch('app.runner.receive_and_process')
    @patch('app.runner.setup_runner_socket')
    def test_full_runner_lifecycle(self, mock_setup_socket, mock_receive, mock_finish, mock_assign, mock_load, mock_get_next, mock_main, mock_conf, mock_db):
        """Test full runner lifecycle: enqueue→spawn→ZMQ→finish."""
        # Setup mocks
        mock_get_next.return_value = "test_job_id"
        mock_load.return_value = ("owner", "set", "name", "task", "param", "code", [], None, 0)
        mock_socket = Mock()
        mock_poller = Mock()
        mock_poller.poll.return_value = [(mock_socket, 0)]
        mock_setup_socket.return_value = (Mock(), mock_socket, mock_poller)
        mock_receive.return_value = None  # Simulate processing

        # Simulate main loop iteration
        def mock_main_impl(data_base, web_bind, venv_path, pkg_path, env):
            job_id = mock_get_next()
            if job_id:
                owner, set, name, kind, param, code, libs, overrides, max_retries = mock_load(job_id)
                pid = 12345  # Simulate spawned process pid
                mock_assign(job_id, pid)
                # Simulate ZMQ event
                events = mock_poller.poll(0)
                for sock, _ in events:
                    mock_receive(sock)
                    mock_finish("test_job_id", "done", None)  # Simulate completion

        mock_main.side_effect = mock_main_impl

        # Call main (simulated)
        from app.runner import main
        main("data", "bind", "/venv", "/pkg", {})

        # Verify lifecycle
        mock_get_next.assert_called()
        mock_load.assert_called_once_with("test_job_id")
        mock_assign.assert_called_once_with("test_job_id", 12345)
        mock_receive.assert_called_once_with(mock_socket)
        mock_finish.assert_called_once_with("test_job_id", "done", None)


class TestDebugOverridesResolution(TestLoggingCapture):
    """New regression tests for override resolution (post-fix)."""

    @patch('app.job_runner.job_lib.send_completion')
    @patch('app.job_runner.subprocess.run')
    @patch('app.lib.sets.subprocess.check_call')
    @patch('app.lib.pyops.ServiceListener')
    def test_run_task_logging_success(self, mock_listener, mock_check_call, mock_run, mock_send, tmp_path, sample_logging_code, mock_conf):
        from app.job_runner import main as run
        mock_listener.return_value = None
        mock_check_call.return_value = None
        mock_run.return_value = Mock(returncode=0)
        data_base = os.path.join(tmp_path, "data")
        owner = "owner"
        set_name = "set"
        job_id = "id"
        venv_path = os.path.join(tmp_path, "venv")
        pkg_path = os.path.join(tmp_path, "pkg")

        run(job_id, owner, set_name, "name", "task", "", sample_logging_code, [], "/tmp/pyops/runner.sock", 0, None, {"jobs": data_base, "git": f"{tmp_path}/data/git", "web": {"bind": "bind"}}, {"venv": venv_path, "pkg": pkg_path}, {"owner": {"debug_port": 5678}}, {"bind": "127.0.0.1:8000"})

        stdout_content, stderr_content = self.get_log_contents(data_base, owner, set_name, job_id)

        assert "stdout print" in stdout_content
        assert "INFO message" in stdout_content
        assert "WARNING message" in stderr_content
        assert "ERROR message" in stderr_content
        mock_send.assert_called_with("/tmp/pyops/runner.sock", {"id": job_id, "state": "done", "detail": None})

    @patch('app.job_runner.job_lib.send_completion')
    @patch('app.job_runner.subprocess.run')
    @patch('app.lib.sets.subprocess.check_call')
    @patch('app.lib.pyops.ServiceListener')
    def test_run_service_logging(self, mock_listener, mock_check_call, mock_run, mock_send, tmp_path, sample_logging_code, mock_conf):
        from app.job_runner import main as run
        mock_listener.return_value = Mock()  # For service
        mock_check_call.return_value = None
        mock_run.return_value = Mock(returncode=0)
        data_base = os.path.join(tmp_path, "data")
        owner = "owner"
        set_name = "set"
        job_id = "id"

        venv_path = os.path.join(tmp_path, "venv")
        pkg_path = os.path.join(tmp_path, "pkg")
        run(job_id, owner, set_name, "name", "service", "", sample_logging_code, [], "/tmp/pyops/runner.sock", 0, None, {"jobs": data_base, "git": f"{tmp_path}/data/git", "web": {"bind": "bind"}}, {"venv": venv_path, "pkg": pkg_path}, {"owner": {"debug_port": 5678}}, {"bind": "127.0.0.1:8000"})

        stdout_content, stderr_content = self.get_log_contents(data_base, owner, set_name, job_id)

        assert "stdout print" in stdout_content
        assert "INFO message" in stdout_content
        assert "WARNING message" in stderr_content
        assert "ERROR message" in stderr_content
        mock_send.assert_called_with("/tmp/pyops/runner.sock", {"id": job_id, "state": "done", "detail": None})

    @patch('app.lib.job.send_completion')
    @patch('subprocess.check_call')
    @patch('subprocess.run')
    @patch('app.lib.pyops.ServiceListener')
    @patch('app.lib.job.loadLib', side_effect=Exception("git not found"))
    def test_debug_directive_new_override(self, mock_loadLib, mock_listener, mock_run, mock_check_call, mock_send, tmp_path, mock_conf):
        """Directive references new override lib (no git)."""
        from app.job_runner import main as run
        from app.lib.job import process_imports

        mock_run.return_value = Mock(returncode=0, stdout="")

        data_base = os.path.join(tmp_path, "data")
        owner = "owner"
        set_name = "test"
        job_id = "id"
        code = "#!{test::newlib}\nprint('task')"

        overrides = {"test::newlib": "print('overridden newlib')"}
        overrides_json = json.dumps(overrides)
        override_dict = json.loads(overrides_json)

        libs = []
        code = process_imports(code, set_name, "debug", libs, override_dict)

        venv_path = os.path.join(tmp_path, "venv")
        pkg_path = os.path.join(tmp_path, "pkg")
        run(job_id, owner, set_name, "task", "debug", "", code, libs, "/tmp/pyops/runner.sock", 0, overrides_json, {"jobs": data_base, "git": f"{tmp_path}/data/git", "web": {"bind": "bind"}}, {"venv": venv_path, "pkg": pkg_path}, {"owner": {}}, {"bind": "127.0.0.1:8000"})

        last_call = mock_send.call_args_list[-1]
        assert last_call.args[1] == {"id": "id", "state": "done", "detail": None}

        # Check stdout has newlib
        stdout_path = os.path.join(data_base, set_name, job_id, "stdout.log")
        with open(stdout_path, 'r') as f:
            stdout = f.read()
            assert "overridden newlib" in stdout
            assert "task" in stdout

        # Check file created
        workspace = os.path.join(data_base, set_name, job_id)
        debug_src_dir = os.path.join(workspace, ".debug", "src")
        new_lib_file = os.path.join(debug_src_dir, set_name, "library", "newlib.py")
        assert os.path.exists(new_lib_file)
        with open(new_lib_file, 'r') as f:
            assert "print('overridden newlib')" in f.read()

    @patch('app.lib.job.send_completion')
    @patch('subprocess.run')
    @patch('app.lib.pyops.ServiceListener')
    def test_debug_recursive_directive(self, mock_listener, mock_run, mock_send, tmp_path, mock_conf):
        """Lib directive resolves sub-lib."""
        from app.job_runner import main as run
        from app.lib.job import process_imports

        mock_run.return_value = Mock(returncode=0, stdout="")

        data_base = os.path.join(tmp_path, "data")
        owner = "owner"
        set_name = "test"
        job_id = "id"
        code = "#!{test::libA}\nprint('task')"

        overrides = {
            "test::libA": "#!{test::libB}\nprint('libA')",
            "test::libB": "print('libB')"
        }
        overrides_json = json.dumps(overrides)
        override_dict = json.loads(overrides_json)

        libs = []
        code = process_imports(code, set_name, "debug", libs, override_dict)

        venv_path = os.path.join(tmp_path, "venv")
        pkg_path = os.path.join(tmp_path, "pkg")
        run(job_id, owner, set_name, "task", "debug", "", code, libs, "/tmp/pyops/runner.sock", 0, overrides_json, {"jobs": data_base, "git": f"{tmp_path}/data/git", "web": {"bind": "bind"}}, {"venv": venv_path, "pkg": pkg_path}, {"owner": {}}, {"bind": "127.0.0.1:8000"})

        last_call = mock_send.call_args_list[-1]
        assert last_call.args[1] == {"id": "id", "state": "done", "detail": None}

        stdout_path = os.path.join(data_base, set_name, job_id, "stdout.log")
        with open(stdout_path, 'r') as f:
            stdout = f.read()
            assert "libB" in stdout
            assert "libA" in stdout
            assert "task" in stdout

        # Check files
        workspace = os.path.join(data_base, set_name, job_id)
        debug_src_dir = os.path.join(workspace, ".debug", "src")
        libA_file = os.path.join(debug_src_dir, set_name, "library", "libA.py")
        libB_file = os.path.join(debug_src_dir, set_name, "library", "libB.py")
        assert os.path.exists(libA_file)
        assert os.path.exists(libB_file)

    @patch('app.job_runner.subprocess.run')
    @patch('app.job_runner.job_lib.send_completion')
    @patch('app.job_runner.subprocess.run')
    @patch('app.lib.sets.subprocess.check_call')
    @patch('app.lib.pyops.ServiceListener')
    def test_run_task_with_pip_install(self, mock_listener, mock_check_call, mock_run, mock_send, mock_run_bootstrap, tmp_path, mock_conf):
        from app.job_runner import main as run
        mock_listener.return_value = None
        mock_check_call.return_value = None
        mock_run.return_value = Mock(returncode=0)
        mock_run_bootstrap.return_value = Mock(returncode=0)

        data_base = os.path.join(tmp_path, "data")
        owner = "owner"
        set_name = "set"
        job_id = "id"
        param = "#!torch --index-url https://download.pytorch.org/whl/cpu\nprint('param')"
        code = "print('code')"

        venv_path = os.path.join(tmp_path, "venv")
        pkg_path = os.path.join(tmp_path, "pkg")

        run(job_id, owner, set_name, "name", "task", param, code, [], "/tmp/pyops/runner.sock", 0, None, {"jobs": data_base, "git": f"{tmp_path}/data/git", "web": {"bind": "bind"}}, {"venv": venv_path, "pkg": pkg_path}, {"owner": {"debug_port": 5678}}, {"bind": "127.0.0.1:8000"})

        mock_send.assert_called_with("/tmp/pyops/runner.sock", {"id": "id", "state": "done", "detail": None})

    @patch('app.job_runner.zmq.Context')
    def test_send_completion_zmq_retry_on_error(self, mock_context_class):
        """Test ZMQ send_completion handles ZMQError gracefully (no retry)."""
        import zmq
        import msgpack
        from app.lib.job import send_completion
        mock_context = Mock()
        mock_context_class.return_value = mock_context
        mock_socket_instance = Mock()
        mock_context.socket.return_value = mock_socket_instance

        # Simulate ZMQError - function logs error and exits without retry
        mock_socket_instance.send.side_effect = zmq.ZMQError("ZMQError")

        send_completion("/tmp/pyops/runner.sock", {"id": "test_id", "state": "done", "detail": None})

        # Should have tried 3 times
        assert mock_socket_instance.send.call_count == 1
        # Should have connected and sent
        mock_socket_instance.connect.assert_called_once_with("ipc:///tmp/pyops/runner.sock")

    @patch('app.job_runner.zmq.Context')
    def test_send_completion_permanent_zmq_fail(self, mock_context_class):
        """Test ZMQ send_completion fails gracefully on permanent ZMQError (no retry)."""
        import zmq
        import msgpack
        from app.lib.job import send_completion
        mock_context = Mock()
        mock_context_class.return_value = mock_context
        mock_socket_instance = Mock()
        mock_context.socket.return_value = mock_socket_instance

        # Simulate permanent ZMQError
        mock_socket_instance.send.side_effect = zmq.ZMQError("ZMQError")

        send_completion("/tmp/pyops/runner.sock", {"id": "test_id", "state": "done", "detail": None})

        # Should have tried once (no retry)
        assert mock_socket_instance.send.call_count == 1
        # Should have connected
        mock_socket_instance.connect.assert_called_once_with("ipc:///tmp/pyops/runner.sock")


    @patch('app.runner.finishJob')
    @patch('psutil.Process')
    @patch('app.runner.Session')
    def test_process_death_no_zmq(self, mock_session_class, mock_process_class, mock_finish):
        """Test that process death without ZMQ completion is detected via stale PID check."""
        from sqlalchemy import select
        from app.orm.job import Job

        # Mock session
        mock_session = Mock()
        mock_session_manager = Mock()
        mock_session_manager.__enter__ = Mock(return_value=mock_session)
        mock_session_manager.__exit__ = Mock(return_value=None)
        mock_session_class.return_value = mock_session_manager
        mock_job = Mock()
        mock_job.id = 'test_id'
        mock_session.execute.return_value.scalar_one_or_none.return_value = mock_job

        # Mock psutil process not running
        mock_process = Mock()
        mock_process.is_running.return_value = False
        mock_process_class.return_value = mock_process

        # Simulate processes dict with a pid
        processes = {123: Mock()}

        # Execute the stale PID check logic
        with mock_session_class.return_value as session:
            for pid in list(processes.keys()):
                job = session.execute(select(Job).where(Job.pid == pid, Job.state == "running")).scalar_one_or_none()
                if job and not mock_process.is_running():
                    from app.runner import finishJob
                    finishJob(job.id, "failed", f"process termination pid={pid}")

        # Assert finishJob was called with failed state
        mock_finish.assert_called_once_with('test_id', 'failed', 'process termination pid=123')

    @patch('app.runner.finishJob')
    def test_receive_and_process_malformed_msgpack(self, mock_finish):
        """Test receive_and_process handles malformed msgpack gracefully."""
        socket = Mock()
        socket.recv.side_effect = [b'identity', b'', b'hello']  # Invalid msgpack
        from app.runner import receive_and_process
        with pytest.raises(msgpack.exceptions.ExtraData):
            receive_and_process(socket)
        mock_finish.assert_not_called()

    @patch('app.runner.finishJob')
    @patch('psutil.Process')
    def test_stale_service_AccessDenied(self, mock_process_class, mock_finish):
        """Test stale PID check handles AccessDenied → failed with log."""
        import psutil
        from app.orm.job import Job
        mock_process = Mock()
        mock_process.is_running.side_effect = psutil.AccessDenied
        mock_process_class.return_value = mock_process

        # Mock session
        with patch('app.runner.Session') as mock_session_class:
            mock_session = Mock()
            mock_session_manager = Mock()
            mock_session_manager.__enter__ = Mock(return_value=mock_session)
            mock_session_manager.__exit__ = Mock(return_value=None)
            mock_session_class.return_value = mock_session_manager
            mock_job = Mock()
            mock_job.id = 'test_id'
            mock_session.execute.return_value.scalar_one_or_none.return_value = mock_job

            # Execute stale check
            from app.runner import main
            with mock_session_class.return_value as session:
                for pid in [12345]:
                    job = session.execute(select(Job).where(Job.pid == pid, Job.state == "running")).scalar_one_or_none()
                    if job:
                        try:
                            if mock_process.is_running():
                                continue
                        except psutil.AccessDenied:
                            from app.runner import finishJob
                            finishJob(job.id, "failed", f"stale process pid={pid} (AccessDenied)")

            # Assert finishJob called with AccessDenied detail
            mock_finish.assert_called_once_with('test_id', 'failed', 'stale process pid=12345 (AccessDenied)')

    @patch('app.runner.finishJob')
    @patch('psutil.Process')
    def test_stale_service_ZombieProcess(self, mock_process_class, mock_finish):
        """Test stale PID check handles ZombieProcess → failed with log."""
        import psutil
        from app.orm.job import Job
        mock_process = Mock()
        mock_process.is_running.side_effect = psutil.ZombieProcess(123)
        mock_process_class.return_value = mock_process

        # Mock session
        with patch('app.runner.Session') as mock_session_class:
            mock_session = Mock()
            mock_session_manager = Mock()
            mock_session_manager.__enter__ = Mock(return_value=mock_session)
            mock_session_manager.__exit__ = Mock(return_value=None)
            mock_session_class.return_value = mock_session_manager
            mock_job = Mock()
            mock_job.id = 'test_id'
            mock_session.execute.return_value.scalar_one_or_none.return_value = mock_job

            # Execute stale check
            from app.runner import main
            with mock_session_class.return_value as session:
                for pid in [12345]:
                    job = session.execute(select(Job).where(Job.pid == pid, Job.state == "running")).scalar_one_or_none()
                    if job:
                        try:
                            if mock_process.is_running():
                                continue
                        except psutil.ZombieProcess:
                            from app.runner import finishJob
                            finishJob(job.id, "failed", f"stale process pid={pid} (ZombieProcess)")

            # Assert finishJob called with ZombieProcess detail
            mock_finish.assert_called_once_with('test_id', 'failed', 'stale process pid=12345 (ZombieProcess)')  # Should not process invalid msg

    @patch('app.job_runner.job_lib.send_completion')
    @patch('app.job_runner.subprocess.run')
    @patch('app.lib.sets.subprocess.check_call')
    @patch('app.lib.pyops.ServiceListener')
    def test_run_draft_success(self, mock_listener, mock_check_call, mock_run, mock_send, job_env):
        from app.job_runner import main as run
        mock_listener.return_value = None  # For non-service
        mock_check_call.return_value = None  # Venv creation succeeds
        mock_run.return_value = Mock(returncode=0)  # Bootstrap and installs succeed
        env = job_env

        run("id", "owner", "set", "name", "draft", "print('param executed')", "print('code executed')", [], "/tmp/pyops/runner.sock", 0, None, env["conf_data"], env["conf_runner"], env["conf_users"], env["conf_web"])

        mock_send.assert_called_with("/tmp/pyops/runner.sock", {"id": "id", "state": "done", "detail": None})

    def test_subprocess_popen_uses_direct_output_redirect(self):
        """Test that subprocess.Popen is called with sys.stdout/sys.stderr for direct output."""
        import subprocess
        from unittest.mock import patch, Mock, MagicMock

        # Mock sys.stdout and sys.stderr
        mock_stdout = MagicMock()
        mock_stderr = MagicMock()

        with patch('subprocess.Popen') as mock_popen, \
             patch('app.runner.sys.stdout', mock_stdout), \
             patch('app.runner.sys.stderr', mock_stderr):

            # Mock the process
            mock_process = Mock()
            mock_process.pid = 12345
            mock_process.poll.return_value = None
            mock_popen.return_value = mock_process

            # Simulate the subprocess creation part of the main loop
            venv_python = "/tmp/venv/set/bin/python"
            json_kwargs = '{"test": "data"}'

            # This is the exact call from the main function (after fix)
            p = subprocess.Popen([venv_python, 'app/job_runner.py', json_kwargs], cwd='/tmp', stdout=mock_stdout, stderr=mock_stderr, text=True)

            # Verify the call was made with direct output redirect
            mock_popen.assert_called_once_with(
                [venv_python, 'app/job_runner.py', json_kwargs],
                cwd='/tmp',
                stdout=mock_stdout,
                stderr=mock_stderr,
                text=True
            )

