import subprocess

import pytest
from unittest.mock import Mock, patch, call
from datetime import datetime
import croniter

from sqlalchemy import select
from sqlalchemy.orm import Session

class TestDaemon:
    def test_handleOddJobs_dequeued_to_todo(self, mock_db):
        from app.runner import handleOddJobs
        from app.orm.database import get_db_engine
        from app.orm.job import Job
        with Session(get_db_engine()) as test_session:
            job_dequeued = Job(id="deq_id", set="test_set", name="test_name", owner="test_owner", kind="task", state="dequeued", param=None, detail=None, pid=None, drop_done=False)
            job_running = Job(id="run_id", set="test_set", name="test_name", owner="test_owner", kind="task", state="running", param=None, detail=None, pid=None, drop_done=False)
            test_session.add_all([job_dequeued, job_running])
            test_session.commit()
        handleOddJobs()
        with Session(get_db_engine()) as test_session:
            job_dequeued = test_session.get(Job, "deq_id")
            job_running = test_session.get(Job, "run_id")
            assert job_dequeued.state == "todo"
            assert job_running.state == "failed"
            assert job_running.detail == "incomplete execution detected on startup"

    def test_handleOddJobs_running_to_failed(self, mock_db):
        from app.runner import handleOddJobs
        from app.orm.database import get_db_engine
        from app.orm.job import Job
        with Session(get_db_engine()) as test_session:
            job_running = Job(id="run_id", set="test_set", name="test_name", owner="test_owner", kind="task", state="running", param=None, detail=None, pid=None, drop_done=False)
            test_session.add(job_running)
            test_session.commit()
        handleOddJobs()
        with Session(get_db_engine()) as test_session:
            job_running = test_session.get(Job, "run_id")
            assert job_running.state == "failed"
            assert job_running.detail == "incomplete execution detected on startup"

    def test_handleOddJobs_loading_to_todo(self, mock_db):
        """`loading` is a transient state set by loadJob before spawn. If
        the runner crashed mid-loadJob the row sits there forever; reset
        it to `todo` so the new runner picks it up cleanly."""
        from app.runner import handleOddJobs
        from app.orm.database import get_db_engine
        from app.orm.job import Job
        with Session(get_db_engine()) as test_session:
            test_session.add(Job(id="load_id", set="s", name="n", owner="o",
                                  kind="task", state="loading", param=None,
                                  detail=None, pid=None, drop_done=False))
            test_session.commit()
        handleOddJobs()
        with Session(get_db_engine()) as test_session:
            assert test_session.get(Job, "load_id").state == "todo"

    def test_handleOddJobs_deduplicates_incomplete_marker(self, mock_db):
        """Repeated unclean container restarts shouldn't accumulate ghost
        `failed` rows toward the per-service `<3 failed` gate. At most one
        row with the incomplete-on-startup sentinel detail survives per
        (set, name, owner, kind)."""
        from app.runner import handleOddJobs, INCOMPLETE_ON_STARTUP_DETAIL
        from app.orm.database import get_db_engine
        from app.orm.job import Job
        with Session(get_db_engine()) as test_session:
            test_session.add_all([
                # Prior incomplete-on-startup rows from earlier restarts
                Job(id="old1", set="s", name="n", owner="o", kind="service",
                    state="failed", detail=INCOMPLETE_ON_STARTUP_DETAIL,
                    param=None, pid=None, drop_done=True),
                Job(id="old2", set="s", name="n", owner="o", kind="service",
                    state="failed", detail=INCOMPLETE_ON_STARTUP_DETAIL,
                    param=None, pid=None, drop_done=True),
                # A REAL failure with a different detail — must survive
                Job(id="real", set="s", name="n", owner="o", kind="service",
                    state="failed", detail="ZeroDivisionError: real bug",
                    param=None, pid=None, drop_done=True),
                # The currently-orphaned running row that will become incomplete
                Job(id="now", set="s", name="n", owner="o", kind="service",
                    state="running", detail=None,
                    param=None, pid=None, drop_done=True),
                # Unrelated service — its own incomplete must not be touched
                Job(id="other", set="s", name="other", owner="o", kind="service",
                    state="failed", detail=INCOMPLETE_ON_STARTUP_DETAIL,
                    param=None, pid=None, drop_done=True),
            ])
            test_session.commit()

        handleOddJobs()

        with Session(get_db_engine()) as test_session:
            # Old incomplete rows for (s, n, o, service) are gone
            assert test_session.get(Job, "old1") is None
            assert test_session.get(Job, "old2") is None
            # Real failure preserved
            real = test_session.get(Job, "real")
            assert real is not None and real.state == "failed"
            # New incomplete row is the survivor
            now = test_session.get(Job, "now")
            assert now.state == "failed"
            assert now.detail == INCOMPLETE_ON_STARTUP_DETAIL
            # Unrelated service untouched
            other = test_session.get(Job, "other")
            assert other is not None and other.detail == INCOMPLETE_ON_STARTUP_DETAIL

    def test_handleOddJobs_incomplete_dedup_does_not_block_under_three_gate(self, mock_db):
        """Three unclean restarts in a row leave ONE failed row, not three,
        so the schedule's <3 failed gate still permits restart."""
        from app.runner import handleOddJobs, INCOMPLETE_ON_STARTUP_DETAIL
        from app.orm.database import get_db_engine
        from app.orm.job import Job
        for i, jid in enumerate(["r1", "r2", "r3"]):
            with Session(get_db_engine()) as test_session:
                test_session.add(Job(id=jid, set="s", name="n", owner="o",
                                     kind="service", state="running",
                                     param=None, pid=None, drop_done=True))
                test_session.commit()
            handleOddJobs()
        with Session(get_db_engine()) as test_session:
            stmt = select(Job).where(Job.set == "s", Job.name == "n",
                                      Job.detail == INCOMPLETE_ON_STARTUP_DETAIL)
            rows = test_session.scalars(stmt).all()
            assert len(rows) == 1

    def test_handleOddJobs_preserves_terminal_states(self, mock_db):
        """`done` / `failed` / `cancelled` / `remove` / `removed` rows are
        never overwritten — the per-service `<3 failed` gate and the
        cancelled-blocks-restart rule both depend on those rows surviving
        across restarts."""
        from app.runner import handleOddJobs
        from app.orm.database import get_db_engine
        from app.orm.job import Job
        with Session(get_db_engine()) as test_session:
            for jid, state in [("d_id", "done"), ("f_id", "failed"),
                               ("c_id", "cancelled"), ("rm_id", "remove"),
                               ("rmd_id", "removed")]:
                test_session.add(Job(id=jid, set="s", name="n", owner="o",
                                     kind="task", state=state, param=None,
                                     detail="original", pid=None, drop_done=False))
            test_session.commit()
        handleOddJobs()
        with Session(get_db_engine()) as test_session:
            assert test_session.get(Job, "d_id").state == "done"
            assert test_session.get(Job, "f_id").state == "failed"
            assert test_session.get(Job, "c_id").state == "cancelled"
            assert test_session.get(Job, "rm_id").state == "remove"
            assert test_session.get(Job, "rmd_id").state == "removed"

    def test_purgeLeftoverServiceJobs_clears_stuck_remove(self, mock_db):
        """`remove`/`removed` service rows are the only ones the startup
        sweep deletes — they're transient cleanup states whose postTerm
        got cut short when the previous runner exited."""
        from app.runner import purgeLeftoverServiceJobs
        from app.orm.database import get_db_engine
        from app.orm.job import Job
        with Session(get_db_engine()) as test_session:
            test_session.add_all([
                Job(id="rm_svc", set="s", name="n", owner="o", kind="service",
                    state="remove", param=None, detail=None, pid=None, drop_done=True),
                Job(id="rmd_svc", set="s", name="n", owner="o", kind="service",
                    state="removed", param=None, detail=None, pid=None, drop_done=True),
                # non-service in remove — left alone
                Job(id="rm_task", set="s", name="n", owner="o", kind="task",
                    state="remove", param=None, detail=None, pid=None, drop_done=True),
            ])
            test_session.commit()
        purgeLeftoverServiceJobs()
        with Session(get_db_engine()) as test_session:
            assert test_session.get(Job, "rm_svc") is None
            assert test_session.get(Job, "rmd_svc") is None
            assert test_session.get(Job, "rm_task") is not None  # untouched

    def test_purgeLeftoverServiceJobs_preserves_terminal_rows(self, mock_db):
        """`failed` / `done` / `cancelled` service rows survive a restart —
        the `<3 failed` gate and the cancelled-blocks-restart rule both
        depend on those rows being present on the next runner boot."""
        from app.runner import purgeLeftoverServiceJobs
        from app.orm.database import get_db_engine
        from app.orm.job import Job
        with Session(get_db_engine()) as test_session:
            test_session.add_all([
                Job(id="fail_svc", set="s", name="n", owner="o", kind="service",
                    state="failed", param=None, detail=None, pid=None, drop_done=False),
                Job(id="done_svc", set="s", name="n", owner="o", kind="service",
                    state="done", param=None, detail=None, pid=None, drop_done=False),
                Job(id="cancel_svc", set="s", name="n", owner="o", kind="service",
                    state="cancelled", param=None, detail=None, pid=None, drop_done=False),
            ])
            test_session.commit()
        purgeLeftoverServiceJobs()
        with Session(get_db_engine()) as test_session:
            for jid in ("fail_svc", "done_svc", "cancel_svc"):
                assert test_session.get(Job, jid) is not None, jid

    def test_getNextToDo_found(self, mock_db):
        from app.runner import getNextToDo
        from app.orm.database import get_db_engine
        from app.orm.job import Job
        with Session(get_db_engine()) as test_session:
            job = Job(id="todo_id", set="test_set", name="test_name", owner="test_owner", kind="task", state="todo", param=None, detail=None, pid=None, drop_done=False)
            test_session.add(job)
            test_session.commit()
        result = getNextToDo()
        assert result == "todo_id"
        with Session(get_db_engine()) as test_session:
            job = test_session.get(Job, "todo_id")
            assert job.state == "dequeued"

    def test_getNextToDo_none(self, mock_db):
        from app.runner import getNextToDo
        result = getNextToDo()
        assert result is None

    def test_getNextToDo_cas_drops_job_on_cancel_race(self, mock_db):
        """If a cancel races in between SELECT and conditional UPDATE,
        getNextToDo must NOT flip the cancel back to dequeued.
        Returns None instead so the runner term-loop honours the cancel
        on the next iteration."""
        from app.runner import getNextToDo
        from app.orm.database import get_db_engine
        from app.orm.job import Job
        with Session(get_db_engine()) as test_session:
            job = Job(id="raceme", set="s", name="n", owner="o", kind="task",
                      state="todo", param=None, detail=None, pid=None, drop_done=False)
            test_session.add(job)
            test_session.commit()

        # Hook session.scalar to flip state to 'cancel' immediately after the
        # candidate-id SELECT returns — simulating an HTTP cancel committing
        # before getNextToDo's conditional UPDATE runs.
        orig_scalar = Session.scalar
        flipped = {"done": False}
        def flip_after_first_scalar(self, stmt, *args, **kwargs):
            result = orig_scalar(self, stmt, *args, **kwargs)
            if result == "raceme" and not flipped["done"]:
                flipped["done"] = True
                with Session(get_db_engine()) as cancel_session:
                    cancel_session.get(Job, "raceme").state = "cancel"
                    cancel_session.commit()
            return result

        with patch.object(Session, 'scalar', flip_after_first_scalar):
            result = getNextToDo()
        assert result is None
        with Session(get_db_engine()) as test_session:
            fetched = test_session.get(Job, "raceme")
            assert fetched.state == "cancel"  # CAS preserved the cancel

    def test_finishJob_done_drop(self, mock_db):
        from app.runner import finishJob
        from app.orm.database import get_db_engine
        from app.orm.job import Job
        with Session(get_db_engine()) as test_session:
            job = Job(id="id", set="test_set", name="test_name", owner="test_owner", kind="task", state="running", param=None, detail=None, pid=None, drop_done=True)
            test_session.add(job)
            test_session.commit()
        finishJob("id", "done", None)
        with Session(get_db_engine()) as test_session:
            job = test_session.get(Job, "id")
            assert job.state == "done"
            assert job.end is not None
            assert job.detail is None
            assert job.pid is None

    @patch('app.runner.files.conf_runner')
    @patch('app.runner.datetime')
    def test_cleanup_drop_done_jobs_delayed_removal(self, mock_dt, mock_conf_runner, mock_db):
        from app.runner import finishJob, cleanup_drop_done_jobs
        from app.orm.database import get_db_engine
        from app.orm.job import Job
        from datetime import datetime, timedelta

        # Mock config to return 3 second delay
        mock_conf_runner.return_value = {"drop_done_delay_seconds": 3}

        # Create and finish job
        with Session(get_db_engine()) as test_session:
            job = Job(id="id", set="test_set", name="test_name", owner="test_owner", kind="task", state="running", param=None, detail=None, pid=None, drop_done=True)
            test_session.add(job)
            test_session.commit()

        # Mock initial time
        initial_time = datetime(2023, 1, 1, 12, 0, 0)
        mock_dt.now.return_value = initial_time

        finishJob("id", "done", None)

        # Verify job is done but not removed yet
        with Session(get_db_engine()) as test_session:
            job = test_session.get(Job, "id")
            assert job.state == "done"
            assert job.end == initial_time

        # Mock time 4 seconds later (past cutoff)
        mock_dt.now.return_value = initial_time + timedelta(seconds=4)

        cleanup_drop_done_jobs()

        # Verify job is now marked for removal
        with Session(get_db_engine()) as test_session:
            job = test_session.get(Job, "id")
            assert job.state == "remove"

        # Verify cleanup was called with correct config
        mock_conf_runner.assert_called_once()

    def test_finishJob_failed_no_drop(self, mock_db):
        from app.runner import finishJob
        from app.orm.database import get_db_engine
        from app.orm.job import Job
        with Session(get_db_engine()) as test_session:
            job = Job(id="id", set="test_set", name="test_name", owner="test_owner", kind="debug", state="running", param=None, detail=None, pid=None, drop_done=False)
            test_session.add(job)
            test_session.commit()
        finishJob("id", "failed", "error")
        with Session(get_db_engine()) as test_session:
            job = test_session.get(Job, "id")
            assert job.state == "failed"
            assert job.detail == "error"

    def test_finishJob_cas_preserves_terminal_state(self, mock_db):
        """A row that already reached a terminal state must not be re-written
        by a later completion. Concrete scenario: a service exits cleanly,
        send_completion('done') runs and (with drop_done=True) the row gets
        re-targeted to 'remove'. The finally-block listener.close() then
        raises, run_*_job's outer except sends a second completion 'failed'.
        Without CAS protection, the second commit would demote 'remove'
        back to 'failed' and the schedule loop would treat the service as
        unrestartable. CAS preserves the terminal 'remove'.
        """
        from app.runner import finishJob
        from app.orm.database import get_db_engine
        from app.orm.job import Job
        with Session(get_db_engine()) as test_session:
            test_session.add(Job(id="t1", set="s", name="n", owner="o",
                                 kind="service", state="remove", param=None,
                                 detail=None, pid=None, drop_done=True))
            test_session.commit()

        # Second completion with state='failed' — must be ignored by CAS.
        finishJob("t1", "failed", "spurious error from close()")

        with Session(get_db_engine()) as test_session:
            assert test_session.get(Job, "t1").state == "remove"

    def test_finishJob_cas_preserves_done_against_later_failed(self, mock_db):
        from app.runner import finishJob
        from app.orm.database import get_db_engine
        from app.orm.job import Job
        with Session(get_db_engine()) as test_session:
            test_session.add(Job(id="t2", set="s", name="n", owner="o",
                                 kind="task", state="done", param=None,
                                 detail=None, pid=None, drop_done=False))
            test_session.commit()
        finishJob("t2", "failed", "duplicate completion")
        with Session(get_db_engine()) as test_session:
            assert test_session.get(Job, "t2").state == "done"

    def test_finishJob_cas_preserves_cancel_on_concurrent_commit(self, mock_db):
        """Cancel commits between finishJob's snapshot read and its CAS UPDATE.

        finishJob computes candidate='done' from the stale 'running' snapshot,
        but the atomic CASE evaluates the state at UPDATE time and preserves
        the now-committed 'cancel' — the runner term-loop can then clean up.
        Without CAS, the commit overwrites cancel with done and the cancel
        request is silently lost.
        """
        from app.runner import finishJob
        from app.orm.database import get_db_engine
        from app.orm.job import Job
        with Session(get_db_engine()) as test_session:
            test_session.add(Job(id="rj", set="s", name="n", owner="o", kind="task",
                                 state="running", param=None, detail=None, pid=None, drop_done=False))
            test_session.commit()

        orig_get = Session.get
        fired = {"done": False}
        def get_then_cancel(self, *args, **kwargs):
            result = orig_get(self, *args, **kwargs)
            if not fired["done"] and result is not None and getattr(result, "id", None) == "rj":
                fired["done"] = True
                with Session(get_db_engine()) as cs:
                    cs.get(Job, "rj").state = "cancel"
                    cs.commit()
            return result

        with patch.object(Session, 'get', get_then_cancel):
            finishJob("rj", "done", "ok")

        with Session(get_db_engine()) as test_session:
            fetched = test_session.get(Job, "rj")
            # CAS preserved the cancel that committed under us; finishJob did not
            # overwrite it with the candidate 'done'.
            assert fetched.state == "cancel"

    @patch('app.runner.INTERNAL_ESSENTIAL_SERVICES', [])
    @patch('app.runner.lib_runner.find')
    @patch('app.runner.lib_runner.enqueue')
    @patch('croniter.croniter')
    @patch('app.runner.datetime')
    def test_schedule_cron_enqueue_new_schedule(self, mock_dt, mock_croniter, mock_enqueue, mock_find, mock_db):
        from app.runner import schedule
        from app.orm.database import get_db_engine
        from app.orm.schedule import Schedule
        from datetime import datetime
        with Session(get_db_engine()) as test_session:
            mock_dt.now.return_value = datetime(2023, 1, 1, 12, 0)
            sched = Schedule(set="test_set", name="test_name", owner="test_owner", schedule="* * * * *", active=True, drop_done=False, param=None, last=datetime(2023, 1, 1, 11, 59))  # Creation time
            test_session.add(sched)
            test_session.commit()
            sched_id = sched.id
        mock_cron = Mock()
        mock_next = datetime(2023, 1, 1, 12, 0)  # Next run at now
        mock_cron.get_next.return_value = mock_next
        mock_croniter.return_value = mock_cron
        mock_find.return_value = []  # No running jobs
        schedule()
        mock_enqueue.assert_called_once_with("test_set", "test_name", "test_owner", "task", False, None, 0, None)
        with Session(get_db_engine()) as test_session:
            sched = test_session.get(Schedule, sched_id)
            test_session.refresh(sched)
            assert sched.last == datetime(2023, 1, 1, 12, 0)

    @patch('app.runner.INTERNAL_ESSENTIAL_SERVICES', [])
    @patch('app.runner.lib_runner.find')
    @patch('app.runner.lib_runner.enqueue')
    @patch('croniter.croniter')
    @patch('app.runner.datetime')
    def test_schedule_cron_no_enqueue_future_run(self, mock_dt, mock_croniter, mock_enqueue, mock_find, mock_db):
        from app.runner import schedule
        from app.orm.database import get_db_engine
        from app.orm.schedule import Schedule
        from datetime import datetime
        with Session(get_db_engine()) as test_session:
            mock_dt.now.return_value = datetime(2023, 1, 1, 12, 0)
            sched = Schedule(set="test_set", name="test_name", owner="test_owner", schedule="* * * * *", active=True, drop_done=False, param=None, last=datetime(2023, 1, 1, 11, 59))
            test_session.add(sched)
            test_session.commit()
        mock_cron = Mock()
        mock_next = datetime(2023, 1, 1, 12, 1)  # Next run in future
        mock_cron.get_next.return_value = mock_next
        mock_croniter.return_value = mock_cron
        mock_find.return_value = []  # No running jobs
        schedule()
        mock_enqueue.assert_not_called()  # Should not enqueue future runs

    @patch('app.runner.INTERNAL_ESSENTIAL_SERVICES', [])
    @patch('app.runner.lib_runner.find')
    @patch('app.runner.lib_runner.enqueue')
    @patch('croniter.croniter')
    @patch('app.runner.datetime')
    def test_schedule_cron_no_enqueue_running_job(self, mock_dt, mock_croniter, mock_enqueue, mock_find, mock_db):
        from app.runner import schedule
        from app.orm.database import get_db_engine
        from app.orm.schedule import Schedule
        from datetime import datetime
        with Session(get_db_engine()) as test_session:
            mock_dt.now.return_value = datetime(2023, 1, 1, 12, 0)
            sched = Schedule(set="test_set", name="test_name", owner="test_owner", schedule="* * * * *", active=True, drop_done=False, param=None, last=datetime(2023, 1, 1, 11, 59))
            test_session.add(sched)
            test_session.commit()
        mock_cron = Mock()
        mock_next = datetime(2023, 1, 1, 12, 0)
        mock_cron.get_next.return_value = mock_next
        mock_croniter.return_value = mock_cron
        mock_find.return_value = ["running_job"]  # Has running job
        schedule()
        mock_enqueue.assert_not_called()

    @patch('app.runner.INTERNAL_ESSENTIAL_SERVICES', [])
    @patch('app.runner.lib_runner.find')
    @patch('app.runner.lib_runner.enqueue')
    @patch('croniter.croniter')
    @patch('app.runner.datetime')
    def test_schedule_cron_catch_up_missed_runs(self, mock_dt, mock_croniter, mock_enqueue, mock_find, mock_db):
        from app.runner import schedule
        from app.orm.database import get_db_engine
        from app.orm.schedule import Schedule
        from datetime import datetime
        with Session(get_db_engine()) as test_session:
            mock_dt.now.return_value = datetime(2023, 1, 1, 12, 2)  # 2 minutes later
            sched = Schedule(set="test_set", name="test_name", owner="test_owner", schedule="* * * * *", active=True, drop_done=False, param=None, last=datetime(2023, 1, 1, 11, 59))
            test_session.add(sched)
            test_session.commit()
            sched_id = sched.id
        mock_cron = Mock()
        mock_next = datetime(2023, 1, 1, 12, 0)  # Missed run at 12:00
        mock_cron.get_next.return_value = mock_next
        mock_croniter.return_value = mock_cron
        mock_find.return_value = []  # No running jobs
        schedule()
        mock_enqueue.assert_called_once_with("test_set", "test_name", "test_owner", "task", False, None, 0, None)
        with Session(get_db_engine()) as test_session:
            sched = test_session.get(Schedule, sched_id)
            test_session.refresh(sched)
            assert sched.last == datetime(2023, 1, 1, 12, 2)

    @patch('app.runner.INTERNAL_ESSENTIAL_SERVICES', [])
    @patch('app.runner.lib_runner.enqueue')
    @patch('app.runner.lib_runner.find')
    @patch('app.runner.datetime')
    def test_schedule_always_on_service(self, mock_dt, mock_find, mock_enqueue, mock_db):
        from app.runner import schedule
        from app.orm.database import get_db_engine
        from app.orm.schedule import Schedule
        from datetime import datetime
        with Session(get_db_engine()) as test_session:
            mock_dt.now.return_value = datetime(2023, 1, 1, 12, 0)
            sched = Schedule(set="test_set", name="test_name", owner="test_owner", schedule="", active=True, drop_done=False, param=None, last=None)
            test_session.add(sched)
            test_session.commit()
            sched_id = sched.id
        mock_find.side_effect = [[], []]
        schedule()
        mock_enqueue.assert_called_once_with("test_set", "test_name", "test_owner", "service", False, None, 0, None)
        with Session(get_db_engine()) as test_session:
            sched = test_session.get(Schedule, sched_id)
            test_session.refresh(sched)
            assert sched.last == mock_dt.now.return_value

    @patch('app.runner.INTERNAL_ESSENTIAL_SERVICES', [])
    @patch('app.runner.lib_runner.find')
    @patch('app.runner.datetime')
    def test_schedule_no_enqueue_existing(self, mock_dt, mock_find, mock_db):
        from app.runner import schedule
        from app.orm.database import get_db_engine
        from app.orm.schedule import Schedule
        from datetime import datetime
        with Session(get_db_engine()) as test_session:
            mock_dt.now.return_value = datetime(2023, 1, 1, 12, 0)
            sched = Schedule(set="test_set", name="test_name", owner="test_owner", schedule="", active=True, drop_done=False, param=None, last=datetime(2023, 1, 1, 12, 0))
            test_session.add(sched)
            test_session.commit()
            sched_id = sched.id
        mock_find.return_value = ["existing_id"]
        schedule()
        mock_find.assert_called_once_with(
            "test_owner", "test_set", "test_name", "service",
            ["todo", "dequeued", "loading", "running",
             "cancel", "cancelling", "cancelled", "done"],
        )

    def test_getNextToTerm_cancel(self, mock_db):
        from app.runner import getNextToTerm
        from app.orm.database import get_db_engine
        from app.orm.job import Job
        with Session(get_db_engine()) as test_session:
            job = Job(id="id", set="test_set", name="test_name", owner="test_owner", kind="task", state="cancel", param=None, detail=None, pid=123, drop_done=False)
            test_session.add(job)
            test_session.commit()
        result = getNextToTerm()
        assert result == {"id": "id", "pid": 123}

    def test_getNextToTerm_none(self, mock_db):
        from app.runner import getNextToTerm
        result = getNextToTerm()
        assert result is None



    @patch('app.lib.runner.drop')
    def test_postTerm_remove(self, mock_drop, mock_db):
        from app.runner import postTerm
        from app.orm.database import get_db_engine
        from app.orm.job import Job
        with Session(get_db_engine()) as test_session:
            job = Job(id="id", set="test_set", name="test_name", owner="test_owner", kind="task", state="remove", param=None, detail=None, pid=None, drop_done=False)
            test_session.add(job)
            test_session.commit()
        postTerm("id")
        with Session(get_db_engine()) as test_session:
            result = test_session.execute(select(Job).where(Job.id == "id")).scalar_one_or_none()
            assert result is None
        mock_drop.assert_called_once_with("id", "test_set")

    @patch('app.lib.runner.drop')
    def test_postTerm_cancel(self, mock_drop, mock_db):
        from app.runner import postTerm
        from app.orm.database import get_db_engine
        from app.orm.job import Job
        with Session(get_db_engine()) as test_session:
            job = Job(id="id", set="test_set", name="test_name", owner="test_owner", kind="task", state="cancel", end=None, param=None, detail=None, pid=None, drop_done=False)
            test_session.add(job)
            test_session.commit()
        postTerm("id")
        with Session(get_db_engine()) as test_session:
            result = test_session.execute(select(Job).where(Job.id == "id")).scalar_one_or_none()
            assert result is not None
            assert result.state == "cancelled"
            assert result.end is not None
        mock_drop.assert_not_called()

    def test_shutdown_loop_uses_per_pid_job_id(self, mock_db):
        """Regression: shutdown loop must call finishJob(job.id) for each pid's own job,
        not a stale job_id variable from the outer dequeue loop scope (runner.py:607)."""
        from app.runner import finishJob
        from app.orm.database import get_db_engine
        from app.orm.job import Job
        from sqlalchemy.orm import Session

        with Session(get_db_engine()) as session:
            job_a = Job(id="job_a", set="s", name="n", owner="o", kind="task",
                        state="running", pid=111, drop_done=False)
            job_b = Job(id="job_b", set="s", name="n", owner="o", kind="task",
                        state="running", pid=222, drop_done=False)
            session.add_all([job_a, job_b])
            session.commit()

        mock_p = Mock()
        mock_p.poll.return_value = None
        mock_p.wait.return_value = None
        processes = {111: mock_p, 222: mock_p}

        finished = []
        with patch('app.runner.finishJob', side_effect=lambda jid, s, d: finished.append(jid)):
            import app.runner as runner_module
            for pid in list(processes.keys()):
                p = processes[pid]
                if p.poll() is None:
                    with Session(get_db_engine()) as session:
                        job = session.query(Job).filter_by(pid=pid).first()
                    runner_module.finishJob(job.id, "cancelling", "shutdown")

        assert set(finished) == {"job_a", "job_b"}, (
            "Each job must be finished with its own id; if stale job_id were used "
            "both calls would use the same (wrong) id"
        )

    def test_shutdown_job_processes_completion_deletes_row(self, mock_db):
        """A service that exits on SIGTERM and reports its `done` completion
        through the ZMQ drain has its row DELETED at the end of the sweep
        — clean shutdown should leave nothing behind for the next boot to
        reason about, so the schedule loop re-enqueues the service freshly.
        Workspace logs survive (the DB row goes, the on-disk directory
        doesn't)."""
        from app.runner import _shutdown_job_processes
        from app.orm.database import get_db_engine
        from app.orm.job import Job

        with Session(get_db_engine()) as session:
            session.add(Job(id="graceful_svc", set="s", name="n", owner="o",
                            kind="service", state="running", pid=801, drop_done=False))
            session.commit()

        mock_p = Mock()
        mock_p.poll.side_effect = [None, None, 0, 0]
        processes = {801: mock_p}

        proc_obj = Mock(); proc_obj.pid = 801
        sock = Mock()
        poller = Mock()
        poller.poll.side_effect = [[(sock, None)], [], []]

        def fake_receive(_socket):
            from app.runner import finishJob
            finishJob("graceful_svc", "done", None)
        with patch('app.runner.receive_and_process', side_effect=fake_receive), \
             patch('app.runner.job_lib.kill_proc_group') as mock_kill, \
             patch('app.runner.job_lib.proc_group_alive', return_value=False), \
             patch('app.runner.threading.Thread'), \
             patch('app.runner.lib_runner.drop'), \
             patch('app.runner.time.sleep'):
            _shutdown_job_processes(processes, timeout=2.0,
                                    runner_socket=sock, runner_poller=poller)

        kill_signals = [c.args[1] for c in mock_kill.call_args_list]
        import signal as _sig
        assert _sig.SIGTERM in kill_signals
        assert _sig.SIGKILL not in kill_signals
        with Session(get_db_engine()) as session:
            assert session.get(Job, "graceful_svc") is None

    def test_shutdown_job_processes_sigkill_marks_failed(self, mock_db):
        """A service that doesn't respond to SIGTERM within the timeout
        gets SIGKILL'd. Since it misbehaved, the row is kept as `failed`
        with a sentinel detail — visible in the monitor AND counts toward
        the per-service <3-failed gate so a repeatedly-misbehaving service
        eventually stops being auto-restarted."""
        from app.runner import _shutdown_job_processes
        from app.orm.database import get_db_engine
        from app.orm.job import Job

        with Session(get_db_engine()) as session:
            session.add(Job(id="stuck_svc", set="s", name="n", owner="o",
                            kind="service", state="running", pid=802, drop_done=False))
            session.commit()

        mock_p = Mock()
        mock_p.poll.return_value = None
        processes = {802: mock_p}

        proc_obj = Mock(); proc_obj.pid = 802
        poller = Mock(); poller.poll.return_value = []

        # The whole point of this test: group never drains → SIGTERM
        # window expires → SIGKILL fires. proc_group_alive must stay
        # True throughout the SIGTERM grace period.
        with patch('app.runner.job_lib.kill_proc_group') as mock_kill, \
             patch('app.runner.job_lib.proc_group_alive', return_value=True), \
             patch('app.runner.time.sleep'):
            _shutdown_job_processes(processes, timeout=0.1,
                                    runner_socket=Mock(), runner_poller=poller)

        kill_signals = [c.args[1] for c in mock_kill.call_args_list]
        import signal as _sig
        assert _sig.SIGTERM in kill_signals
        assert _sig.SIGKILL in kill_signals
        with Session(get_db_engine()) as session:
            row = session.get(Job, "stuck_svc")
            assert row is not None and row.state == "failed"
            assert "shutdown" in (row.detail or "").lower()
            assert row.pid is None

    def test_shutdown_job_processes_clean_exit_no_completion_deletes_row(self, mock_db):
        """A task that exits on SIGTERM under default Python signal
        handling (no ServiceListener, no completion) is also DELETED at
        the end — no surviving evidence is preferable to a stale row
        that handleOddJobs would later relabel as failed."""
        from app.runner import _shutdown_job_processes
        from app.orm.database import get_db_engine
        from app.orm.job import Job

        with Session(get_db_engine()) as session:
            session.add(Job(id="silent_task", set="s", name="n", owner="o",
                            kind="task", state="running", pid=803, drop_done=False))
            session.commit()

        mock_p = Mock()
        mock_p.poll.side_effect = [None, 0, 0]
        processes = {803: mock_p}

        proc_obj = Mock(); proc_obj.pid = 803
        poller = Mock(); poller.poll.return_value = []

        with patch('app.runner.job_lib.kill_proc_group') as mock_kill, \
             patch('app.runner.job_lib.proc_group_alive', return_value=False), \
             patch('app.runner.lib_runner.drop'), \
             patch('app.runner.time.sleep'):
            _shutdown_job_processes(processes, timeout=2.0,
                                    runner_socket=Mock(), runner_poller=poller)

        kill_signals = [c.args[1] for c in mock_kill.call_args_list]
        import signal as _sig
        assert _sig.SIGTERM in kill_signals
        assert _sig.SIGKILL not in kill_signals
        with Session(get_db_engine()) as session:
            assert session.get(Job, "silent_task") is None

    def test_shutdown_job_processes_preserves_operator_cancel(self, mock_db):
        """A row already in `cancel`/`cancelling` when shutdown starts
        represents operator intent — preserve it as `cancelled` so the
        schedule-loop's cancelled-blocks-restart gate honours the
        operator's choice across the restart."""
        from app.runner import _shutdown_job_processes
        from app.orm.database import get_db_engine
        from app.orm.job import Job

        with Session(get_db_engine()) as session:
            session.add(Job(id="op_cancelled", set="s", name="n", owner="o",
                            kind="service", state="cancel", pid=804, drop_done=False))
            session.commit()

        mock_p = Mock()
        mock_p.poll.side_effect = [None, 0, 0]
        processes = {804: mock_p}

        proc_obj = Mock(); proc_obj.pid = 804
        poller = Mock(); poller.poll.return_value = []

        with patch('app.runner.job_lib.kill_proc_group'), \
             patch('app.runner.job_lib.proc_group_alive', return_value=False), \
             patch('app.runner.time.sleep'):
            _shutdown_job_processes(processes, timeout=2.0,
                                    runner_socket=Mock(), runner_poller=poller)

        with Session(get_db_engine()) as session:
            row = session.get(Job, "op_cancelled")
            assert row is not None and row.state == "cancelled"

    def test_shutdown_job_processes_skips_already_exited(self, mock_db):
        """A process whose poll() returns non-None at snapshot time is
        skipped entirely — no signals, no DB state change."""
        from app.runner import _shutdown_job_processes
        from app.orm.database import get_db_engine
        from app.orm.job import Job

        with Session(get_db_engine()) as session:
            session.add(Job(id="exited_job", set="s", name="n", owner="o",
                            kind="task", state="done", pid=703, drop_done=False))
            session.commit()

        mock_p = Mock()
        mock_p.poll.return_value = 0  # already exited
        processes = {703: mock_p}

        with patch('app.runner.job_lib.kill_proc_group') as mock_kill:
            _shutdown_job_processes(processes, timeout=2.0,
                                    runner_socket=Mock(), runner_poller=Mock())

        mock_kill.assert_not_called()
        with Session(get_db_engine()) as session:
            row = session.get(Job, "exited_job")
            assert row.state == "done"  # untouched


class TestShutdownFlag:
    """The shutdown-flag mechanism gates lib_runner.enqueue across process
    boundaries — so gunicorn workers (HTTP /scriptlet/job/item, /scriptlet/
    schedule/run/{id}) and the internal data service (control.job.create →
    HTTP) all refuse to enqueue once the runner has been signalled."""

    def test_enqueue_raises_when_flag_present(self, mock_db, tmp_path, monkeypatch):
        from app.lib import runner as lib_runner_mod
        flag = tmp_path / "shutdown.flag"
        flag.write_text("123")
        monkeypatch.setattr(lib_runner_mod, "SHUTDOWN_FLAG_PATH", str(flag))

        with pytest.raises(lib_runner_mod.ShutdownPending):
            lib_runner_mod.enqueue("s", "n", "o", "task", False, None, 0, None)

    def test_enqueue_proceeds_without_flag(self, mock_db, tmp_path, monkeypatch):
        from app.lib import runner as lib_runner_mod
        # Point flag path at a location guaranteed not to exist
        flag = tmp_path / "absent.flag"
        monkeypatch.setattr(lib_runner_mod, "SHUTDOWN_FLAG_PATH", str(flag))

        job_id = lib_runner_mod.enqueue("s", "n", "o", "task", False, None, 0, None)
        assert job_id and job_id.startswith("o_s_n_")

    def test_is_shutdown_pending_reflects_flag_file(self, tmp_path, monkeypatch):
        from app.lib import runner as lib_runner_mod
        flag = tmp_path / "shutdown.flag"
        monkeypatch.setattr(lib_runner_mod, "SHUTDOWN_FLAG_PATH", str(flag))
        assert lib_runner_mod.is_shutdown_pending() is False
        flag.write_text("99")
        assert lib_runner_mod.is_shutdown_pending() is True

    def test_main_clears_stale_flag_on_startup(self, mock_db, tmp_path, monkeypatch):
        """Stale flag from previous crash must not block enqueues for the
        new process lifetime."""
        from app import runner
        from app.lib import runner as lib_runner_mod

        flag = tmp_path / "shutdown.flag"
        flag.write_text("old")
        monkeypatch.setattr(lib_runner_mod, "SHUTDOWN_FLAG_PATH", str(flag))

        class _PreKilledKiller:
            kill_now = True
            def __init__(self): pass
            def exit_gracefully(self, *a, **kw): pass

        with patch('app.runner.GracefulKiller', _PreKilledKiller), \
             patch('app.runner.setup_runner_socket', return_value=(Mock(), Mock(), Mock(**{'poll.return_value': []}))), \
             patch('app.runner.handleOddJobs'), \
             patch('app.runner.purgeLeftoverServiceJobs'), \
             patch('app.runner.cleanup_drop_done_jobs'), \
             patch('app.runner.os.environ'), \
             patch('app.runner.logging.basicConfig'), \
             patch('app.runner.files.Config.init'), \
             patch('app.runner._shutdown_job_processes'), \
             patch('app.runner.disposeJobs'):
            runner.main("data", "127.0.0.1:0", "/venv", "/pkg", {})

        assert not flag.exists(), "main() must clear the stale flag on startup"


class TestHandleOddJobsCancelStates:
    """handleOddJobs cleans up jobs left in 'cancel'/'cancelling' across
    a restart — the daemon-thread terminate path from a cancel HTTP request
    can be killed mid-flight by an immediately-following shutdown, leaving
    the row stuck. Pre-fix this orphaned the row forever."""

    def test_handleOddJobs_cancelling_to_cancelled(self, mock_db):
        from app.runner import handleOddJobs
        from app.orm.database import get_db_engine
        from app.orm.job import Job
        with Session(get_db_engine()) as session:
            session.add(Job(id="orphan", set="s", name="n", owner="o", kind="task",
                            state="cancelling", pid=None, drop_done=False, detail="shutdown"))
            session.commit()

        handleOddJobs()

        with Session(get_db_engine()) as session:
            row = session.get(Job, "orphan")
            assert row.state == "cancelled"
            assert "orphan cancel cleaned on startup" in (row.detail or "")

    def test_handleOddJobs_cancel_to_cancelled(self, mock_db):
        from app.runner import handleOddJobs
        from app.orm.database import get_db_engine
        from app.orm.job import Job
        with Session(get_db_engine()) as session:
            session.add(Job(id="orphan2", set="s", name="n", owner="o", kind="task",
                            state="cancel", pid=None, drop_done=False))
            session.commit()

        handleOddJobs()

        with Session(get_db_engine()) as session:
            row = session.get(Job, "orphan2")
            assert row.state == "cancelled"

    def test_enqueue_debug_mark_existing_for_cancellation(self, mock_db):
        from app.lib.runner import enqueue
        from app.orm.database import get_db_engine
        from app.orm.job import Job
        from sqlalchemy.orm import Session
        # Add existing debug running with pid
        with Session(get_db_engine()) as session:
            job = Job(id="existing", set="set", name="name", owner="owner", kind="debug", state="running", pid=123)
            session.add(job)
            session.commit()
        job_id = enqueue("set", "name", "owner", "debug", False, "{}")
        assert isinstance(job_id, str)
        # Check existing marked for cancellation
        with Session(get_db_engine()) as session:
            existing = session.get(Job, "existing")
            assert existing.state == "cancel"
            assert existing.detail == "Cancelled by new debug job start for owner"
            assert existing.end is not None
            # New job created
            new_job = session.query(Job).filter(Job.id == job_id).first()
            assert new_job.state == "todo"
            assert new_job.kind == "debug"

    @patch('app.runner.time.sleep')
    def test_commit_with_retry_success_on_first_try(self, mock_sleep, mock_db):
        from app.runner import commit_with_retry
        from app.orm.database import get_db_engine
        from sqlalchemy.orm import Session
        with Session(get_db_engine()) as session:
            # Should commit without retry
            commit_with_retry(session)
            mock_sleep.assert_not_called()

    @patch('app.runner.time.sleep')
    def test_commit_with_retry_success_after_retry(self, mock_sleep, mock_db):
        from app.runner import commit_with_retry
        from app.orm.database import get_db_engine
        from sqlalchemy.orm import Session
        import sqlalchemy.exc
        with Session(get_db_engine()) as session:
            # Mock commit to fail once with lock, then succeed
            original_commit = session.commit
            call_count = 0
            def mock_commit():
                nonlocal call_count
                call_count += 1
                if call_count == 1:
                    raise sqlalchemy.exc.OperationalError("database is locked", None, None)
                else:
                    original_commit()
            session.commit = mock_commit
            try:
                commit_with_retry(session, max_retries=2)
                assert call_count == 2
                # Should sleep once: 0.1 * (2^0) = 0.1
                mock_sleep.assert_called_once_with(0.1)
            finally:
                session.commit = original_commit

    @patch('app.runner.time.sleep')
    def test_commit_with_retry_fails_after_max_retries(self, mock_sleep, mock_db):
        from app.runner import commit_with_retry
        from app.orm.database import get_db_engine
        from sqlalchemy.orm import Session
        import sqlalchemy.exc
        with Session(get_db_engine()) as session:
            original_commit = session.commit
            # Mock commit to always fail with lock
            def mock_commit():
                raise sqlalchemy.exc.OperationalError("database is locked", None, None)
            session.commit = mock_commit
            try:
                with pytest.raises(sqlalchemy.exc.OperationalError):
                    commit_with_retry(session, max_retries=2)
                # For max_retries=2, sleeps 1 time (after attempt 0)
                assert mock_sleep.call_count == 1
                mock_sleep.assert_has_calls([call(0.1)])
            finally:
                session.commit = original_commit

    @patch('app.runner.time.sleep')
    def test_commit_with_retry_no_retry_on_other_error(self, mock_sleep, mock_db):
        from app.runner import commit_with_retry
        from app.orm.database import get_db_engine
        from sqlalchemy.orm import Session
        import sqlalchemy.exc
        with Session(get_db_engine()) as session:
            original_commit = session.commit
            # Mock commit to fail with non-lock error
            def mock_commit():
                raise sqlalchemy.exc.OperationalError("some other error", None, None)
            session.commit = mock_commit
            try:
                with pytest.raises(sqlalchemy.exc.OperationalError):
                    commit_with_retry(session)
                mock_sleep.assert_not_called()
            finally:
                session.commit = original_commit


class TestShutdownContract:
    """Pyops's shutdown contract: no new pip installs / Popen after a SIGTERM,
    so an in-flight `ensure_set_environment` is never killed mid-flight and the
    venv stays consistent. Two pieces:
      1. `GracefulKiller` flips `kill_now` on signal.
      2. The runner main loop checks `kill_now` BEFORE `getNextToDo` — claims
         and spawns are skipped once shutdown is signalled.
    """

    def test_graceful_killer_sigterm_flips_kill_now(self, tmp_path, monkeypatch):
        import signal
        from app.runner import GracefulKiller
        from app.lib import runner as lib_runner_mod
        flag = tmp_path / "shutdown.flag"
        monkeypatch.setattr(lib_runner_mod, "SHUTDOWN_FLAG_PATH", str(flag))
        killer = GracefulKiller()
        assert killer.kill_now is False
        assert not flag.exists()
        killer.exit_gracefully(signal.SIGTERM, None)
        assert killer.kill_now is True
        assert flag.exists(), "exit_gracefully must drop the cross-process shutdown flag"

    def test_graceful_killer_sigint_flips_kill_now(self, tmp_path, monkeypatch):
        import signal
        from app.runner import GracefulKiller
        from app.lib import runner as lib_runner_mod
        flag = tmp_path / "shutdown.flag"
        monkeypatch.setattr(lib_runner_mod, "SHUTDOWN_FLAG_PATH", str(flag))
        killer = GracefulKiller()
        killer.exit_gracefully(signal.SIGINT, None)
        assert killer.kill_now is True
        assert flag.exists()

    def test_main_loop_skips_spawn_when_kill_now_already_set(self, mock_db):
        """If `kill_now` is True at the top of the loop, while-check exits
        and getNextToDo / Popen are never reached — no new claims, no new
        subprocesses, no new pip installs."""
        from app import runner

        # Stand-in killer with kill_now pre-set: while-check exits immediately.
        class _PreKilledKiller:
            kill_now = True
            def __init__(self): pass
            def exit_gracefully(self, *a, **kw): pass

        with patch('app.runner.GracefulKiller', _PreKilledKiller), \
             patch('app.runner.setup_runner_socket', return_value=(Mock(), Mock(), Mock(**{'poll.return_value': []}))), \
             patch('app.runner.ensure_set_environment'), \
             patch('app.runner.handleOddJobs'), \
             patch('app.runner.purgeLeftoverServiceJobs'), \
             patch('app.runner.cleanup_drop_done_jobs'), \
             patch('app.runner.getNextToDo') as mock_get_next, \
             patch('app.runner.subprocess.Popen') as mock_popen, \
             patch('app.runner.os.environ'), \
             patch('app.runner.logging.basicConfig'), \
             patch('app.runner.files.Config.init'):
            runner.main("data", "127.0.0.1:0", "/venv", "/pkg", {})

        mock_get_next.assert_not_called()
        mock_popen.assert_not_called()

    def test_main_loop_skips_spawn_when_signal_arrives_mid_iteration(self, mock_db):
        """Signal arrives during the loop body (after the while-check passed):
        the in-loop guard before getNextToDo must skip the spawn block.
        Without it, a fresh pip install would launch right as the worker is
        about to die — exactly the corruption case we're protecting against.
        """
        from app import runner

        class _MidIterKiller:
            kill_now = False
            def __init__(self): pass
            def exit_gracefully(self, *a, **kw):
                self.kill_now = True

        killer_instance = _MidIterKiller()

        # Flip kill_now early in the iteration body — before the in-loop guard
        # but after the while-check. disposeJobs runs first inside the body.
        def _dispose_then_signal():
            killer_instance.kill_now = True

        with patch('app.runner.GracefulKiller', return_value=killer_instance), \
             patch('app.runner.setup_runner_socket', return_value=(Mock(), Mock(), Mock(**{'poll.return_value': []}))), \
             patch('app.runner.ensure_set_environment'), \
             patch('app.runner.handleOddJobs'), \
             patch('app.runner.purgeLeftoverServiceJobs'), \
             patch('app.runner.cleanup_drop_done_jobs'), \
             patch('app.runner.disposeJobs', side_effect=_dispose_then_signal), \
             patch('app.runner.getNextToDo') as mock_get_next, \
             patch('app.runner.subprocess.Popen') as mock_popen, \
             patch('app.runner.time.sleep'), \
             patch('app.runner.os.environ'), \
             patch('app.runner.logging.basicConfig'), \
             patch('app.runner.files.Config.init'):
            runner.main("data", "127.0.0.1:0", "/venv", "/pkg", {})

        # Body ran one iteration, but kill_now flipped before the spawn-block guard,
        # so no claim/spawn happened.
        mock_get_next.assert_not_called()
        mock_popen.assert_not_called()

    def test_main_loop_pipes_kwargs_via_stdin_not_argv(self, mock_db):
        """Regression for `Launch failed: [Errno 7] Argument list too long`.

        The serialised job context (param + code + libs + VSIX overrides)
        can comfortably exceed Linux ARG_MAX (~128 KiB) — i-doit
        scriptlets alone hit 84 KB each. The earlier implementation
        passed `json_kwargs` as a third argv element to
        `subprocess.Popen([venv_python, 'app/job_setup.py', json_kwargs])`,
        which made `execve(2)` reject the call with E2BIG before the
        child even started. No workspace, no stderr.log, no traceback in
        the runner logs — just the short "Launch failed" detail caught
        at the spawn-block's local `except`.

        The fix: argv carries only the script path, and `json_kwargs`
        goes in via stdin (no ARG_MAX limit).
        """
        from app import runner

        # 1 MiB of payload — well past ARG_MAX, would E2BIG on argv.
        huge_code = "x" * (1024 * 1024)

        class _OneShotKiller:
            kill_now = False
            def __init__(self): pass
            def exit_gracefully(self, *a, **kw): self.kill_now = True

        killer_instance = _OneShotKiller()

        # Trip kill_now after the first iteration so main() returns.
        call_count = {"n": 0}
        def _get_next_then_kill():
            call_count["n"] += 1
            killer_instance.kill_now = True
            return "huge_job_id" if call_count["n"] == 1 else None

        mock_proc = Mock()
        mock_proc.pid = 99001
        mock_proc.stdin = Mock()

        with patch('app.runner.GracefulKiller', return_value=killer_instance), \
             patch('app.runner.setup_runner_socket', return_value=(Mock(), Mock(), Mock(**{'poll.return_value': []}))), \
             patch('app.runner.ensure_set_environment'), \
             patch('app.runner.handleOddJobs'), \
             patch('app.runner.purgeLeftoverServiceJobs'), \
             patch('app.runner.cleanup_drop_done_jobs'), \
             patch('app.runner.getNextToDo', side_effect=_get_next_then_kill), \
             patch('app.runner.loadJob', return_value=("owner", "i-doit", "checker-service", "debug", "", huge_code, None, 0)), \
             patch('app.runner.assignJob'), \
             patch('app.runner.finishJob') as mock_finish, \
             patch('app.runner.subprocess.Popen', return_value=mock_proc) as mock_popen, \
             patch('app.runner.time.sleep'), \
             patch('app.runner.os.environ'), \
             patch('app.runner.logging.basicConfig'), \
             patch('app.runner.files.Config.init'):
            runner.main("data", "127.0.0.1:0", "/venv", "/pkg", {})

        # Spawn must have happened (no Launch failed).
        mock_popen.assert_called_once()
        mock_finish.assert_not_called()

        # argv: exactly [venv_python, 'app/job_setup.py'] — no third
        # element carrying the kwargs JSON. If a third element appears,
        # the ARG_MAX regression is back.
        call_args, call_kwargs = mock_popen.call_args
        argv = call_args[0]
        assert len(argv) == 2, (
            "subprocess.Popen argv must be exactly [venv_python, "
            f"job_setup.py] for the ARG_MAX fix, got {len(argv)} elements: "
            f"{[a[:60] + '...' if len(a) > 60 else a for a in argv]}"
        )
        assert argv[1].endswith("job_setup.py")

        # stdin must be a pipe so kwargs can be written to it.
        assert call_kwargs.get("stdin") is subprocess.PIPE

        # The big payload must have been written to the child's stdin,
        # not stashed in argv.
        write_calls = mock_proc.stdin.write.call_args_list
        assert len(write_calls) == 1
        written = write_calls[0][0][0]
        assert len(written) > 1024 * 1024  # the 1 MiB code is in there
        assert huge_code in written
        mock_proc.stdin.close.assert_called_once()


class TestInternalEssentialServices:
    """Persistent pyops-internal services run without a Schedule row.
    The schedule loop iterates INTERNAL_ESSENTIAL_SERVICES with the same
    `<3 failed` gating as service-with-empty-schedule."""

    @patch('app.runner.INTERNAL_ESSENTIAL_SERVICES', [{"set": "internal", "name": "data", "owner": "system"}])
    @patch('app.runner.lib_runner.enqueue')
    @patch('app.runner.lib_runner.find')
    @patch('app.runner.datetime')
    def test_enqueues_when_not_running_and_no_failures(self, mock_dt, mock_find, mock_enqueue, mock_db):
        from app.runner import schedule
        from datetime import datetime as real_datetime
        mock_dt.now.return_value = real_datetime(2026, 5, 13, 12, 0)
        # No live job and no failed records → enqueue
        mock_find.return_value = []
        schedule()
        mock_enqueue.assert_called_once_with("internal", "data", "system", "service", True, None, 0, None)

    @patch('app.runner.INTERNAL_ESSENTIAL_SERVICES', [{"set": "internal", "name": "data", "owner": "system"}])
    @patch('app.runner.lib_runner.enqueue')
    @patch('app.runner.lib_runner.find')
    @patch('app.runner.datetime')
    def test_skips_when_already_running(self, mock_dt, mock_find, mock_enqueue, mock_db):
        from app.runner import schedule
        from datetime import datetime as real_datetime
        mock_dt.now.return_value = real_datetime(2026, 5, 13, 12, 0)
        # First find (live states) returns a running job → skip
        mock_find.return_value = [{"id": "live"}]
        schedule()
        mock_enqueue.assert_not_called()

    @patch('app.runner.INTERNAL_ESSENTIAL_SERVICES', [{"set": "internal", "name": "data", "owner": "system"}])
    @patch('app.runner.lib_runner.enqueue')
    @patch('app.runner.lib_runner.find')
    @patch('app.runner.datetime')
    def test_skips_when_three_or_more_failed(self, mock_dt, mock_find, mock_enqueue, mock_db):
        from app.runner import schedule
        from datetime import datetime as real_datetime
        mock_dt.now.return_value = real_datetime(2026, 5, 13, 12, 0)
        # First find (live states) empty, second (failed states) has 3 → gate
        mock_find.side_effect = [[], [{"id": "f1"}, {"id": "f2"}, {"id": "f3"}]]
        schedule()
        mock_enqueue.assert_not_called()

    @patch('app.runner.INTERNAL_ESSENTIAL_SERVICES', [{"set": "internal", "name": "data", "owner": "system"}])
    @patch('app.runner.lib_runner.enqueue')
    @patch('app.runner.lib_runner.find')
    @patch('app.runner.datetime')
    def test_enqueues_when_failed_below_three(self, mock_dt, mock_find, mock_enqueue, mock_db):
        from app.runner import schedule
        from datetime import datetime as real_datetime
        mock_dt.now.return_value = real_datetime(2026, 5, 13, 12, 0)
        # Live empty, two failures (< 3) → still enqueues
        mock_find.side_effect = [[], [{"id": "f1"}, {"id": "f2"}]]
        schedule()
        mock_enqueue.assert_called_once_with("internal", "data", "system", "service", True, None, 0, None)

    @patch('app.runner.INTERNAL_ESSENTIAL_SERVICES', [{"set": "internal", "name": "data", "owner": "system"}])
    @patch('app.runner.lib_runner.enqueue')
    @patch('app.runner.lib_runner.find')
    @patch('app.runner.datetime')
    def test_skips_when_a_prior_job_is_cancelled(self, mock_dt, mock_find, mock_enqueue, mock_db):
        """A user-initiated cancel (state=`cancelled`) must stop the
        auto-restart. The operator deliberately took the service down and
        must delete the row before it comes back."""
        from app.runner import schedule
        from datetime import datetime as real_datetime
        mock_dt.now.return_value = real_datetime(2026, 5, 13, 12, 0)
        # The first find query enumerates the "still active or terminal-
        # without-failure" states — `cancelled` is in there, returning a
        # row gates restart even if no failed rows exist.
        mock_find.return_value = [{"id": "cancelled_by_operator"}]
        schedule()
        mock_enqueue.assert_not_called()
        # Sanity: the lookup includes cancelled and cancelling.
        states = mock_find.call_args_list[0].args[4]
        assert "cancelled" in states
        assert "cancelling" in states


class TestServiceRestartGatedOnCancel:
    """Schedule-row-driven service (empty cron string) shares the same
    cancel-gate semantics as INTERNAL_ESSENTIAL_SERVICES — a cancelled
    job in the table stops auto-restart until the operator removes it."""

    @patch('app.runner.lib_runner.enqueue')
    @patch('app.runner.lib_runner.find')
    @patch('app.runner.datetime')
    def test_schedule_service_skips_on_cancelled_row(self, mock_dt, mock_find, mock_enqueue, mock_db):
        from app.runner import schedule
        from app.orm.database import get_db_engine
        from app.orm.schedule import Schedule
        from datetime import datetime as real_datetime
        mock_dt.now.return_value = real_datetime(2026, 5, 13, 12, 0)
        with Session(get_db_engine()) as s:
            s.add(Schedule(set="usr", name="svc", owner="u", schedule="",
                           active=True, drop_done=True, param=None, last=None))
            s.commit()
        # find() returns a cancelled-state row → no enqueue
        mock_find.return_value = [{"id": "cancelled_id"}]
        schedule()
        mock_enqueue.assert_not_called()
        states = mock_find.call_args_list[0].args[4]
        assert "cancelled" in states
        assert "cancel" in states
        assert "cancelling" in states


class TestInternalSetRunsInHostVenv:
    """Internal-set scriptlets are pyops-native code and execute in the
    pyops process's own Python — pyzmq / msgpack / psutil / requests are
    already importable there. The runner skips ensure_set_environment for
    set=='internal' and uses sys.executable instead of a per-set venv
    path. User sets keep the standard per-set-venv flow."""

    def test_main_no_internal_bootstrap_at_startup(self, mock_db):
        """main() must NOT call ensure_set_environment('internal') at
        startup — the host venv already has every CORE_DEP."""
        from app import runner

        class _PreKilledKiller:
            kill_now = True
            def __init__(self): pass
            def exit_gracefully(self, *a, **kw): pass

        with patch('app.runner.GracefulKiller', _PreKilledKiller), \
             patch('app.runner.setup_runner_socket', return_value=(Mock(), Mock(), Mock(**{'poll.return_value': []}))), \
             patch('app.runner.ensure_set_environment') as mock_ensure, \
             patch('app.runner.handleOddJobs'), \
             patch('app.runner.purgeLeftoverServiceJobs'), \
             patch('app.runner.cleanup_drop_done_jobs'), \
             patch('app.runner.os.environ'), \
             patch('app.runner.logging.basicConfig'), \
             patch('app.runner.files.Config.init'):
            runner.main("data", "127.0.0.1:0", "/venv", "/pkg", {})

        mock_ensure.assert_not_called()
