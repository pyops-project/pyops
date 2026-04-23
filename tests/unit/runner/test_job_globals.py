from unittest.mock import patch


class TestSetupJobGlobals:
    def test_task_kind_exposes_setname_not_set(self):
        from app import job_runner
        with patch.object(job_runner, "_conf_data", {"git": "data/git", "jobs": "data/jobs"}), \
             patch.object(job_runner, "_conf_runner", {"pkg": "data/pkg"}):
            glob = job_runner.setup_job_globals(
                owner="alice",
                setspace="/tmp/setspace",
                workspace="/tmp/workspace",
                web_bind="localhost:8000",
                job_id="jid",
                kind="task",
                setname="myops",
                name="myscript",
            )
        assert "set" not in glob
        assert glob["setname"] == "myops"
        assert glob["name"] == "myscript"
        assert glob["user"] == "alice"
        assert glob["listener"] is None
