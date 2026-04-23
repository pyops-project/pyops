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
