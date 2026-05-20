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
