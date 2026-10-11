import subprocess

import pytest
from unittest.mock import Mock, patch, call
from datetime import datetime, timedelta
import croniter

from sqlalchemy import select
from sqlalchemy.orm import Session


class _Killer:
    """GracefulKiller stand-in: main() keeps looping until the test calls stop()."""

    def __init__(self):
        self.kill_now = False

    def stop(self, *_args):
        self.kill_now = True


"""Runs runner.main() on the test DB with its process-wide side effects stubbed:
signal handlers (GracefulKiller → the given killer), the ZMQ ROUTER socket,
logging/os.environ/config set-up, start-up housekeeping, schedule(), sleeps and
the terminate threads finishJob starts. Keyword arguments replace further
app.runner attributes (dotted paths allowed) or override these defaults; main()
returns once the test's patches call killer.stop(). Returns the applied patches."""
@pytest.fixture
def run_main(mock_db):
    import contextlib

    def _run(killer, **patches):
        from app import runner
        applied = {
            "GracefulKiller": Mock(return_value=killer),
            "setup_runner_socket": Mock(return_value=(Mock(), Mock(), Mock(**{"poll.return_value": []}))),
            "handleOddJobs": Mock(),
            "purgeLeftoverServiceJobs": Mock(),
            "cleanup_drop_done_jobs": Mock(),
            "cleanup_failed_jobs": Mock(),
            "schedule": Mock(),
            "ensure_set_environment": Mock(),
            "threading.Thread": Mock(),
            "time.sleep": Mock(),
            "os.environ": Mock(),
            "logging.basicConfig": Mock(),
            "files.Config.init": Mock(),
        }
        applied.update(patches)
        with contextlib.ExitStack() as stack:
            for target, value in applied.items():
                stack.enter_context(patch(f"app.runner.{target}", value))
            runner.main("data", "127.0.0.1:0", "/venv", "/pkg", {})
        return applied

    return _run


"""A real runner ROUTER socket (setup_runner_socket) at tmp_path/runner.sock, for
run_main(setup_runner_socket=...); yields (context, socket, poller, path). main()
closes it on exit; teardown closes whatever a failing test left open."""
@pytest.fixture
def runner_socket(tmp_path):
    from app.runner import setup_runner_socket
    path = str(tmp_path / "runner.sock")
    context, socket, poller = setup_runner_socket(path)
    try:
        yield context, socket, poller, path
    finally:
        if not socket.closed:
            socket.close(linger=0)
        if not context.closed:
            context.term()


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

    @pytest.mark.parametrize("state, detail", [("done", None), ("failed", "Traceback: boom")])
    def test_handleOddJobs_running_job_with_result_file_gets_its_result(self, mock_db, state, detail):
        """A running row whose job wrote its result file finished while the
        previous runner never recorded it: the row gets that result and its
        finish time instead of the incomplete-on-startup marker."""
        import json
        import os
        from datetime import datetime
        from app.runner import handleOddJobs
        from app.lib import files
        from app.orm.database import get_db_engine
        from app.orm.job import Job
        workspace = os.path.join(files.conf_data()["jobs"], "s", "fin")
        os.makedirs(workspace)
        with open(os.path.join(workspace, "result.json"), "w") as f:
            json.dump({"state": state, "detail": detail, "time": 1700000000.5}, f)
        with Session(get_db_engine()) as test_session:
            test_session.add(Job(id="fin", set="s", name="n", owner="o", kind="task", state="running",
                                 detail=None, param=None, pid=4242, drop_done=False))
            test_session.commit()
        handleOddJobs()
        with Session(get_db_engine()) as test_session:
            row = test_session.get(Job, "fin")
            assert (row.state, row.detail, row.end) == (state, detail, datetime.fromtimestamp(1700000000.5))

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
        so the schedule's failed-count gate still permits restart."""
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
        never overwritten — the per-service failed-count gate and the
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

    def test_finishJob_without_row_is_noop(self, mock_db):
        """A completion for a job whose row is already gone (a drop_done
        service disposed before its second completion arrived) changes
        nothing and starts no terminate thread."""
        from app.runner import finishJob
        with patch('app.runner.threading.Thread') as mock_thread:
            finishJob("disposed_id", "done", None)
        mock_thread.assert_not_called()

    def test_disposeJobs_deletes_only_removed_rows(self, mock_db):
        """Per-iteration housekeeping: rows whose workspace is gone
        (`removed`) are deleted; every other state stays."""
        from app.runner import disposeJobs
        from app.orm.database import get_db_engine
        from app.orm.job import Job
        with Session(get_db_engine()) as session:
            session.add_all(Job(id=job_id, set="s", name="n", owner="o", kind="task", state=state)
                            for job_id, state in [("rmd1", "removed"), ("rmd2", "removed"),
                                                  ("rm", "remove"), ("done", "done")])
            session.commit()

        disposeJobs()

        with Session(get_db_engine()) as session:
            remaining = {j.id: j.state for j in session.scalars(select(Job))}
        assert remaining == {"rm": "remove", "done": "done"}

    def test_receive_and_process_acks_when_processing_fails(self):
        """The job's REQ socket waits for the runner's reply: the ACK goes
        out even if recording the completion fails (e.g. a locked DB), so
        the job process never blocks on it."""
        import time
        import msgpack
        import zmq
        from app.runner import receive_and_process
        sock = Mock()
        sock.recv.side_effect = [b"job-identity", b"",
                                 msgpack.packb({"id": "x", "state": "done", "detail": None,
                                                "time": time.time()})]
        with patch('app.runner.finishJob', side_effect=RuntimeError("database is locked")) as finish:
            receive_and_process(sock)
        finish.assert_called_once()
        assert sock.send.call_args_list == [call(b"job-identity", zmq.SNDMORE), call(b"", zmq.SNDMORE), call(b"ack")]

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

        The stale-PID check fails a job whose process is gone; finishJob
        computes candidate='failed' from the stale 'running' snapshot, but
        the atomic CASE evaluates the row at UPDATE time: the now-committed
        'cancel' and its reason stay, the runner's message is appended — the
        runner term-loop can then clean up. Without CAS, the commit
        overwrites cancel with failed and the cancel request is silently lost.
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
                    row = cs.get(Job, "rj")
                    row.state, row.detail = "cancel", "cancelled by operator"
                    cs.commit()
            return result

        with patch.object(Session, 'get', get_then_cancel):
            finishJob("rj", "failed", "dead (pid=4242)")

        with Session(get_db_engine()) as test_session:
            fetched = test_session.get(Job, "rj")
            assert fetched.state == "cancel"
            assert fetched.detail == "cancelled by operator\ndead (pid=4242)"

    @patch('app.runner.PYOPS_ESSENTIAL_SERVICES', [])
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
        mock_enqueue.assert_called_once_with("test_set", "test_name", "test_owner", "task", False, None, 0, None, schedule_id=sched_id)
        with Session(get_db_engine()) as test_session:
            sched = test_session.get(Schedule, sched_id)
            test_session.refresh(sched)
            assert sched.last == datetime(2023, 1, 1, 12, 0)

    @patch('app.runner.PYOPS_ESSENTIAL_SERVICES', [])
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

    @patch('app.runner.PYOPS_ESSENTIAL_SERVICES', [])
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

    @patch('app.runner.PYOPS_ESSENTIAL_SERVICES', [])
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
        mock_enqueue.assert_called_once_with("test_set", "test_name", "test_owner", "task", False, None, 0, None, schedule_id=sched_id)
        with Session(get_db_engine()) as test_session:
            sched = test_session.get(Schedule, sched_id)
            test_session.refresh(sched)
            assert sched.last == datetime(2023, 1, 1, 12, 2)

    @patch('app.runner.PYOPS_ESSENTIAL_SERVICES', [])
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
        mock_enqueue.assert_called_once_with("test_set", "test_name", "test_owner", "service", False, None, 0, None, schedule_id=sched_id)
        with Session(get_db_engine()) as test_session:
            sched = test_session.get(Schedule, sched_id)
            test_session.refresh(sched)
            assert sched.last == mock_dt.now.return_value

    @patch('app.runner.PYOPS_ESSENTIAL_SERVICES', [])
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

    def test_shutdown_job_processes_skips_live_pid_without_row(self, mock_db):
        """A live tracked process whose job row is gone gets no signal —
        only processes the DB still knows as jobs are shut down."""
        from app.runner import _shutdown_job_processes

        mock_p = Mock()
        mock_p.poll.return_value = None  # alive
        with patch('app.runner.job_lib.kill_proc_group') as mock_kill:
            _shutdown_job_processes({7001: mock_p}, timeout=2.0,
                                    runner_socket=Mock(), runner_poller=Mock())

        mock_kill.assert_not_called()

    def test_shutdown_job_processes_keeps_row_failed_during_drain(self, mock_db):
        """A service whose drained completion reports `failed` keeps its
        row (state and detail) — the failure counts toward the schedule's
        max_fail_n on the next boot instead of being swept away."""
        from app.runner import _shutdown_job_processes
        from app.orm.database import get_db_engine
        from app.orm.job import Job

        with Session(get_db_engine()) as session:
            session.add(Job(id="failing_svc", set="s", name="n", owner="o",
                            kind="service", state="running", pid=805, drop_done=False))
            session.commit()

        mock_p = Mock()
        mock_p.poll.side_effect = [None, None, 1, 1]
        sock = Mock()
        poller = Mock()
        poller.poll.side_effect = [[(sock, None)], [], []]

        def fake_receive(_socket):
            from app.runner import finishJob
            finishJob("failing_svc", "failed", "ConnectionError: broker gone")
        with patch('app.runner.receive_and_process', side_effect=fake_receive), \
             patch('app.runner.job_lib.kill_proc_group'), \
             patch('app.runner.job_lib.proc_group_alive', return_value=False), \
             patch('app.runner.threading.Thread'), \
             patch('app.runner.time.sleep'):
            _shutdown_job_processes({805: mock_p}, timeout=2.0,
                                    runner_socket=sock, runner_poller=poller)

        with Session(get_db_engine()) as session:
            row = session.get(Job, "failing_svc")
            assert row is not None
            assert row.state == "failed"
            assert row.detail == "ConnectionError: broker gone"

    def test_shutdown_job_processes_completion_after_sigterm_keeps_operator_cancel(self, mock_db):
        """An operator cancel the runner had not acted on yet when shutdown
        started: the service, SIGTERM'd by the shutdown, reports `done` while
        the runner drains. It finished after its termination started, so the
        cancel stands — `cancelled` (blocks the restart), not `done` (row
        deleted, service restarted on the next boot)."""
        import time
        import msgpack
        from app.runner import _shutdown_job_processes
        from app.orm.database import get_db_engine
        from app.orm.job import Job

        with Session(get_db_engine()) as session:
            session.add(Job(id="op_cancel", set="s", name="n", owner="o", kind="service",
                            state="cancel", pid=806, drop_done=False, detail="cancelled by operator"))
            session.commit()

        def frames():  # the report is stamped when it is read, i.e. after the SIGTERM
            yield b"job-identity"
            yield b""
            yield msgpack.packb({"id": "op_cancel", "state": "done", "detail": None, "time": time.time()})
        sent = frames()
        sock = Mock(**{"recv.side_effect": lambda *_: next(sent)})
        poller = Mock(**{"poll.side_effect": [[(sock, None)], [], []]})
        service = Mock(**{"poll.side_effect": [None, None, 0, 0]})

        with patch('app.runner.job_lib.kill_proc_group'), \
             patch('app.runner.job_lib.proc_group_alive', return_value=False), \
             patch('app.runner.threading.Thread'), \
             patch('app.runner.time.sleep'):
            _shutdown_job_processes({806: service}, timeout=2.0,
                                    runner_socket=sock, runner_poller=poller)

        with Session(get_db_engine()) as session:
            row = session.get(Job, "op_cancel")
            assert row is not None
            assert (row.state, row.detail) == ("cancelled", "cancelled by operator")

    def test_shutdown_job_processes_sigkill_keeps_reported_failure(self, mock_db):
        """A service reports `failed` while the runner drains (its shutdown
        raised) but its process does not exit (e.g. a non-daemon thread) and
        is SIGKILL'd: it stays `failed` with the reported traceback, the kill
        is appended to it."""
        import signal
        import time
        import msgpack
        from app.runner import _shutdown_job_processes
        from app.orm.database import get_db_engine
        from app.orm.job import Job

        with Session(get_db_engine()) as session:
            session.add(Job(id="hung_svc", set="s", name="n", owner="o", kind="service",
                            state="running", pid=807, drop_done=False))
            session.commit()

        traceback_ = "Error executing service job hung_svc\nTraceback ...\nConnectionError: broker gone"
        sock = Mock(**{"recv.side_effect": [b"job-identity", b"", msgpack.packb(
            {"id": "hung_svc", "state": "failed", "detail": traceback_, "time": time.time()})]})
        events = [[(sock, None)]]
        poller = Mock(**{"poll.side_effect": lambda *_: events.pop(0) if events else []})
        signals = []
        service = Mock(**{"poll.side_effect": lambda: 0 if signal.SIGKILL in signals else None})

        with patch('app.runner.job_lib.kill_proc_group', side_effect=lambda _pid, sig: signals.append(sig)), \
             patch('app.runner.job_lib.proc_group_alive', side_effect=lambda _pid: signal.SIGKILL not in signals), \
             patch('app.runner.threading.Thread'), \
             patch('app.runner.time.sleep'):
            _shutdown_job_processes({807: service}, timeout=0.1,
                                    runner_socket=sock, runner_poller=poller)

        assert signals == [signal.SIGTERM, signal.SIGKILL]
        with Session(get_db_engine()) as session:
            row = session.get(Job, "hung_svc")
            assert (row.state, row.pid) == ("failed", None)
            assert row.detail == traceback_ + "\ndid not respond to shutdown SIGTERM"

    def test_shutdown_job_processes_sigkill_wait_ends_when_group_is_gone(self, mock_db):
        """Real process group that ignores SIGTERM: after the graceful
        timeout it is SIGKILL'd, and the sweep returns as soon as the group
        is gone instead of waiting out the 2 s SIGKILL grace period."""
        import signal
        import sys
        import time
        from app.runner import _shutdown_job_processes
        from app.orm.database import get_db_engine
        from app.orm.job import Job

        script = ("import signal, time\n"
                  "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
                  "print('ready', flush=True)\n"
                  "time.sleep(60)\n")
        with subprocess.Popen([sys.executable, "-c", script], stdout=subprocess.PIPE,
                              text=True, start_new_session=True) as p:
            try:
                assert p.stdout.readline().strip() == "ready"
                with Session(get_db_engine()) as session:
                    session.add(Job(id="deaf_svc", set="s", name="n", owner="o",
                                    kind="service", state="running", pid=p.pid, drop_done=False))
                    session.commit()

                started = time.monotonic()
                _shutdown_job_processes({p.pid: p}, timeout=0.2)
                elapsed = time.monotonic() - started
            finally:
                if p.poll() is None:
                    p.kill()
                    p.wait()

        assert p.returncode == -signal.SIGKILL
        assert elapsed < 1.5, f"sweep waited {elapsed:.2f}s after the group was gone"
        with Session(get_db_engine()) as session:
            row = session.get(Job, "deaf_svc")
            assert row.state == "failed"
            assert row.detail == "did not respond to shutdown SIGTERM"


class TestCompletionPrecedence:
    """finishJob with a completion the job process reported (`finished` = the
    job's own finish time) against the runner's cancel: the job's outcome is
    recorded when the job finished before the runner started terminating it
    (term_start); otherwise the cancel stands and the job's message is
    appended. An outcome already recorded is never replaced. A recorded
    outcome ends at the job's own finish time, not when the runner
    processed the report."""

    TERM_START = datetime(2026, 10, 11, 12, 0, 0)
    FINISHED = TERM_START - timedelta(milliseconds=5)
    CANCEL_END = TERM_START + timedelta(seconds=2)
    REASON = "cancelled by operator"
    TRACEBACK = "Error executing service job j\nTraceback ...\nConnectionError: broker gone"

    @staticmethod
    def _add(**columns):
        from app.orm.database import get_db_engine
        from app.orm.job import Job
        with Session(get_db_engine()) as session:
            session.add(Job(id="j", set="s", name="n", owner="o", kind="service", drop_done=False, **columns))
            session.commit()

    @staticmethod
    def _row():
        from app.orm.database import get_db_engine
        from app.orm.job import Job
        with Session(get_db_engine()) as session:
            return session.get(Job, "j")

    def test_reported_outcome_ends_at_the_jobs_finish_time(self, mock_db):
        """A running job reported its outcome; the runner processes the
        report later (its loop was busy). The row ends at the time the job
        finished, not at the processing time."""
        from app.runner import finishJob
        self._add(state="running", pid=4242)

        with patch('app.runner.threading.Thread'):
            finishJob("j", "done", None, finished=self.FINISHED)

        row = self._row()
        assert (row.state, row.end, row.pid) == ("done", self.FINISHED, None)

    def test_completion_beats_a_cancel_the_runner_has_not_acted_on(self, mock_db):
        """Cancel requested (`cancel`, the runner has not started terminating)
        when the job's completion arrives: the job finished first, so its
        state, message and finish time are recorded — the cancel came too
        late."""
        from app.runner import finishJob
        self._add(state="cancel", pid=4242, detail=self.REASON)

        with patch('app.runner.threading.Thread'):
            finishJob("j", "failed", self.TRACEBACK, finished=self.FINISHED)

        row = self._row()
        assert (row.state, row.end, row.detail, row.pid) == ("failed", self.FINISHED, self.TRACEBACK, None)

    @pytest.mark.parametrize("state", ["cancel", "cancelled"])
    def test_completion_finished_before_termination_started_wins(self, mock_db, state):
        """The job finished just before the runner started terminating it,
        but its report is processed only afterwards — with the termination
        under way (`cancel`) or already finalised (`cancelled`): the job's
        state and message are recorded, and it ends when it finished —
        before the termination started, not at the cancel's end."""
        from app.runner import finishJob
        self._add(state=state, term_start=self.TERM_START, end=self.CANCEL_END, detail=self.REASON)

        finishJob("j", "failed", self.TRACEBACK, finished=self.FINISHED)

        row = self._row()
        assert (row.state, row.end, row.detail) == ("failed", self.FINISHED, self.TRACEBACK)

    @pytest.mark.parametrize("state", ["cancel", "cancelled"])
    @pytest.mark.parametrize("report, message, detail", [
        ("done", None, REASON),
        ("failed", TRACEBACK, REASON + "\n" + TRACEBACK),
    ], ids=["done-without-message", "failed-with-traceback"])
    def test_completion_finished_after_termination_started_keeps_cancel(self, mock_db, state, report, message, detail):
        """The job finished only after the runner started terminating it —
        it was cancelled. State and end stay; a message the job reported is
        appended to the cancel reason, a report without one leaves it."""
        from app.runner import finishJob
        self._add(state=state, term_start=self.TERM_START, end=self.CANCEL_END, detail=self.REASON)

        finishJob("j", report, message, finished=self.TERM_START + timedelta(milliseconds=5))

        row = self._row()
        assert (row.state, row.end, row.detail) == (state, self.CANCEL_END, detail)

    @pytest.mark.parametrize("state, recorded, detail", [
        ("done", None, "Traceback ...\nRuntimeError: close failed"),
        ("failed", TRACEBACK, TRACEBACK + "\nTraceback ...\nRuntimeError: close failed"),
    ], ids=["done", "failed"])
    def test_later_report_keeps_recorded_outcome_and_appends_message(self, mock_db, state, recorded, detail):
        """A second report for a job whose outcome is recorded (the job's
        result was reported, then its finally-block listener.close() raised
        and run_*_job reported `failed` again): the first state and end
        stand, the later message is appended."""
        from app.runner import finishJob
        self._add(state=state, end=self.CANCEL_END, detail=recorded)

        finishJob("j", "failed", "Traceback ...\nRuntimeError: close failed", finished=datetime.now())

        row = self._row()
        assert (row.state, row.end, row.detail) == (state, self.CANCEL_END, detail)


class TestCleanupDropDoneKeepN:
    """cleanup_drop_done_jobs keeps the newest keep_n done jobs of a cron schedule with drop_done; the delay is 3s."""

    @staticmethod
    def _schedule(**kw):
        from app.orm.database import get_db_engine
        from app.orm.schedule import Schedule
        with Session(get_db_engine()) as session:
            sched = Schedule(set="test_set", name="test_name", owner="test_owner", active=False, param=None, **kw)
            session.add(sched)
            session.commit()
            return sched.id

    @staticmethod
    def _done_jobs(schedule_id, ages):
        """Done drop_done jobs of `schedule_id` that ended `ages` seconds ago; returns their ids."""
        from datetime import timedelta
        from app.orm.database import get_db_engine
        from app.orm.job import Job
        now = datetime.now()
        ids = [f"{schedule_id}_{age}" for age in ages]
        with Session(get_db_engine()) as session:
            session.add_all(Job(id=job_id, set="test_set", name="test_name", owner="test_owner", kind="task", state="done",
                                drop_done=True, end=now - timedelta(seconds=age), schedule_id=schedule_id)
                            for job_id, age in zip(ids, ages))
            session.commit()
        return ids

    @staticmethod
    def _states_after_cleanup(ids):
        from app.runner import cleanup_drop_done_jobs
        from app.orm.database import get_db_engine
        from app.orm.job import Job
        with patch('app.runner.files.conf_runner', return_value={"drop_done_delay_seconds": 3}):
            cleanup_drop_done_jobs()
        with Session(get_db_engine()) as session:
            return [session.get(Job, job_id).state for job_id in ids]

    def test_keeps_newest_keep_n_done_jobs(self, mock_db):
        sched_id = self._schedule(schedule="* * * * *", drop_done=True, keep_n=2)
        jobs = self._done_jobs(sched_id, [40, 30, 20, 10])

        assert self._states_after_cleanup(jobs) == ["remove", "remove", "done", "done"]

    def test_failed_jobs_take_no_keep_slot(self, mock_db):
        """A newer failed run of the schedule does not push its last successful runs out of keep_n."""
        sched_id = self._schedule(schedule="* * * * *", drop_done=True, keep_n=2)
        jobs = self._done_jobs(sched_id, [40, 30]) + TestCleanupFailedMaxFailN._failed_jobs(sched_id, [10])

        assert self._states_after_cleanup(jobs) == ["done", "done", "failed"]

    @pytest.mark.parametrize("schedule_kw", [
        {"schedule": "* * * * *", "drop_done": True, "keep_n": 0},
        {"schedule": "* * * * *", "drop_done": False, "keep_n": 2},
        {"schedule": "", "drop_done": True, "keep_n": 2},
    ], ids=["keep_n_0", "drop_done_false", "service"])
    def test_inactive_keep_n_removes_all(self, mock_db, schedule_kw):
        sched_id = self._schedule(**schedule_kw)
        jobs = self._done_jobs(sched_id, [30, 20, 10])

        assert self._states_after_cleanup(jobs) == ["remove"] * 3

    def test_jobs_without_schedule_row_removed(self, mock_db):
        sched_id = self._schedule(schedule="* * * * *", drop_done=True, keep_n=2)
        # no schedule_id (job API, control.job.create) and a deleted schedule
        jobs = self._done_jobs(None, [20, 10]) + self._done_jobs(sched_id + 1, [20, 10])

        assert self._states_after_cleanup(jobs) == ["remove"] * 4

    def test_keep_n_per_schedule(self, mock_db):
        """Two schedules of the same task each keep their own newest job."""
        first = self._schedule(schedule="0 * * * *", drop_done=True, keep_n=1)
        second = self._schedule(schedule="30 * * * *", drop_done=True, keep_n=1)
        jobs = self._done_jobs(first, [50, 40]) + self._done_jobs(second, [30, 10])

        assert self._states_after_cleanup(jobs) == ["remove", "done", "remove", "done"]


class TestCleanupFailedMaxFailN:
    """cleanup_failed_jobs keeps the newest max_fail_n failed jobs of a cron schedule, without delay."""

    @staticmethod
    def _failed_jobs(schedule_id, ages, kind="task"):
        """Failed jobs of `schedule_id` that ended `ages` seconds ago; returns their ids."""
        from datetime import timedelta
        from app.orm.database import get_db_engine
        from app.orm.job import Job
        now = datetime.now()
        ids = [f"{schedule_id}_{age}" for age in ages]
        with Session(get_db_engine()) as session:
            session.add_all(Job(id=job_id, set="test_set", name="test_name", owner="test_owner", kind=kind, state="failed",
                                drop_done=False, end=now - timedelta(seconds=age), schedule_id=schedule_id)
                            for job_id, age in zip(ids, ages))
            session.commit()
        return ids

    @staticmethod
    def _states_after_cleanup(ids):
        from app.runner import cleanup_failed_jobs
        from app.orm.database import get_db_engine
        from app.orm.job import Job
        cleanup_failed_jobs()
        with Session(get_db_engine()) as session:
            return [session.get(Job, job_id).state for job_id in ids]

    @pytest.mark.parametrize("drop_done", [True, False])
    def test_keeps_newest_max_fail_n_failed_jobs(self, mock_db, drop_done):
        sched_id = TestCleanupDropDoneKeepN._schedule(schedule="* * * * *", drop_done=drop_done, max_fail_n=2)
        jobs = self._failed_jobs(sched_id, [4, 3, 2, 1])

        assert self._states_after_cleanup(jobs) == ["remove", "remove", "failed", "failed"]

    def test_incomplete_on_startup_kept_as_newest(self, mock_db):
        """A cron task still running at an unclean stop is the schedule's newest failure after start-up."""
        from app.runner import handleOddJobs
        from app.orm.database import get_db_engine
        from app.orm.job import Job
        sched_id = TestCleanupDropDoneKeepN._schedule(schedule="* * * * *", drop_done=False, max_fail_n=2)
        jobs = self._failed_jobs(sched_id, [2, 1])
        with Session(get_db_engine()) as session:
            session.add(Job(id="incomplete", set="test_set", name="test_name", owner="test_owner", kind="task",
                            state="running", drop_done=False, schedule_id=sched_id))
            session.commit()
        handleOddJobs()

        assert self._states_after_cleanup(jobs + ["incomplete"]) == ["remove", "failed", "failed"]

    def test_max_fail_n_per_schedule(self, mock_db):
        """Two schedules of the same task each keep their own newest failed job."""
        first = TestCleanupDropDoneKeepN._schedule(schedule="0 * * * *", drop_done=False, max_fail_n=1)
        second = TestCleanupDropDoneKeepN._schedule(schedule="30 * * * *", drop_done=False, max_fail_n=1)
        jobs = self._failed_jobs(first, [4, 3]) + self._failed_jobs(second, [2, 1])

        assert self._states_after_cleanup(jobs) == ["remove", "failed", "remove", "failed"]

    def test_only_failed_jobs_trimmed(self, mock_db):
        """Done history and in-flight jobs (no end yet) of the schedule are left alone."""
        from app.orm.database import get_db_engine
        from app.orm.job import Job
        sched_id = TestCleanupDropDoneKeepN._schedule(schedule="* * * * *", drop_done=False, max_fail_n=1)
        jobs = self._failed_jobs(sched_id, [4, 2]) + TestCleanupDropDoneKeepN._done_jobs(sched_id, [3, 1])
        with Session(get_db_engine()) as session:
            session.add_all(Job(id=state, set="test_set", name="test_name", owner="test_owner", kind="task",
                                state=state, drop_done=False, schedule_id=sched_id) for state in ("todo", "running"))
            session.commit()

        assert self._states_after_cleanup(jobs + ["todo", "running"]) == \
            ["remove", "failed", "done", "done", "todo", "running"]

    def test_service_and_unscheduled_failed_jobs_kept(self, mock_db):
        service = TestCleanupDropDoneKeepN._schedule(schedule="", drop_done=False, max_fail_n=1)
        jobs = self._failed_jobs(service, [3, 2, 1], kind="service") + self._failed_jobs(None, [3, 2, 1])

        assert self._states_after_cleanup(jobs) == ["failed"] * 6


class TestScheduleShutdownPending:
    """Once the shutdown flag exists, enqueue raises ShutdownPending: schedule()
    returns at the first refused enqueue — no further enqueue attempts in that
    call, and a cron schedule's `last` is not advanced, so the due run is
    still due after the restart."""

    @pytest.mark.parametrize("schedule_expr", ["* * * * *", ""], ids=["cron_task", "service"])
    def test_schedule_row_stops_scheduling(self, mock_db, schedule_expr):
        import os
        from app.runner import schedule
        from app.lib import runner as lib_runner_mod
        from app.orm.database import get_db_engine
        from app.orm.job import Job
        from app.orm.schedule import Schedule
        os.makedirs(os.path.dirname(lib_runner_mod.SHUTDOWN_FLAG_PATH), exist_ok=True)
        with open(lib_runner_mod.SHUTDOWN_FLAG_PATH, "w") as f:
            f.write("1")
        with Session(get_db_engine()) as session:
            session.add_all(Schedule(set="s", name=name, owner="o", schedule=schedule_expr, active=True,
                                     drop_done=False, param=None, last=None) for name in ("first", "second"))
            session.commit()

        with patch('app.runner.lib_runner.enqueue', wraps=lib_runner_mod.enqueue) as spy:
            schedule()

        assert spy.call_count == 1  # neither the second schedule nor the essential service
        with Session(get_db_engine()) as session:
            assert session.scalars(select(Job)).all() == []
            assert [s.last for s in session.scalars(select(Schedule))] == [None, None]

    @patch('app.runner.PYOPS_ESSENTIAL_SERVICES', [{"set": "pyops", "name": "data", "owner": "system"},
                                                   {"set": "pyops", "name": "other", "owner": "system"}])
    def test_essential_service_stops_scheduling(self, mock_db):
        import os
        from app.runner import schedule
        from app.lib import runner as lib_runner_mod
        from app.orm.database import get_db_engine
        from app.orm.job import Job
        os.makedirs(os.path.dirname(lib_runner_mod.SHUTDOWN_FLAG_PATH), exist_ok=True)
        with open(lib_runner_mod.SHUTDOWN_FLAG_PATH, "w") as f:
            f.write("1")

        with patch('app.runner.lib_runner.enqueue', wraps=lib_runner_mod.enqueue) as spy:
            schedule()

        assert spy.call_count == 1
        with Session(get_db_engine()) as session:
            assert session.scalars(select(Job)).all() == []


class TestScheduleInvalidStoredName:
    """A schedule whose set or task name is outside the name charset
    [A-Za-z0-9_-]+ cannot run: schedule() skips it — no job, no change to
    the schedule — and carries on with the other schedules."""

    @pytest.mark.parametrize("schedule_expr", ["* * * * *", ""], ids=["cron_task", "service"])
    def test_invalid_name_skipped(self, mock_db, schedule_expr):
        from app.runner import schedule
        from app.orm.database import get_db_engine
        from app.orm.job import Job
        from app.orm.schedule import Schedule
        with Session(get_db_engine()) as session:
            session.add_all([
                Schedule(set="ops.tools", name="job", owner="o", schedule=schedule_expr, active=True,
                         drop_done=False, param=None, last=None),
                Schedule(set="s1", name="my.job", owner="o", schedule=schedule_expr, active=True,
                         drop_done=False, param=None, last=None),
                Schedule(set="s1", name="job", owner="o", schedule=schedule_expr, active=True,
                         drop_done=False, param=None, last=None),
            ])
            session.commit()

        with patch('app.runner.PYOPS_ESSENTIAL_SERVICES', []):
            schedule()

        with Session(get_db_engine()) as session:
            assert [(s.set, s.name, s.active, s.last) for s in session.scalars(select(Schedule).order_by(Schedule.id))][:2] == [
                ("ops.tools", "job", True, None), ("s1", "my.job", True, None)]
            assert [(j.set, j.name) for j in session.scalars(select(Job))] == [("s1", "job")]


class TestServiceMaxFailN:
    """A service schedule is re-enqueued while fewer than max_fail_n of its own jobs (by schedule_id) failed."""

    @pytest.mark.parametrize("own_failed, enqueued", [(3, True), (4, False)])
    @patch('app.runner.PYOPS_ESSENTIAL_SERVICES', [])
    @patch('app.runner.lib_runner.enqueue')
    def test_gate_counts_own_failed_jobs(self, mock_enqueue, mock_db, own_failed, enqueued):
        from app.runner import schedule
        from app.orm.database import get_db_engine
        from app.orm.job import Job
        from app.orm.schedule import Schedule
        with Session(get_db_engine()) as session:
            sched = Schedule(set="test_set", name="test_name", owner="test_owner", schedule="", active=True,
                             drop_done=False, max_fail_n=4, param=None, last=None)
            session.add(sched)
            session.commit()
            sched_id = sched.id
            # Failed jobs of another schedule and without a schedule do not count.
            session.add_all(Job(id=f"{schedule_id}_{i}", set="test_set", name="test_name", owner="test_owner",
                                kind="service", state="failed", schedule_id=schedule_id)
                            for schedule_id, count in ((sched_id, own_failed), (sched_id + 1, 4), (None, 4))
                            for i in range(count))
            session.commit()

        schedule()

        assert mock_enqueue.call_count == (1 if enqueued else 0)


class TestShutdownFlag:
    """The shutdown-flag mechanism gates lib_runner.enqueue across process
    boundaries — so gunicorn workers (HTTP /scriptlet/job/item, /scriptlet/
    schedule/run/{id}) and the pyops data service (control.job.create →
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

    def test_graceful_killer_unwritable_flag_still_flips_kill_now(self, tmp_path, monkeypatch):
        """The flag is best-effort: when it cannot be written, the handler
        does not raise and kill_now still stops the main loop."""
        import signal
        from app.runner import GracefulKiller
        from app.lib import runner as lib_runner_mod
        not_a_dir = tmp_path / "afile"
        not_a_dir.write_text("")
        monkeypatch.setattr(lib_runner_mod, "SHUTDOWN_FLAG_PATH", str(not_a_dir / "shutdown.flag"))
        with patch('app.runner.signal.signal'):
            killer = GracefulKiller()
        killer.exit_gracefully(signal.SIGTERM, None)
        assert killer.kill_now is True

    def test_completion_after_shutdown_wait_is_processed(self, run_main):
        """A completion that arrives after the shutdown wait is still
        recorded and ACKed before main() exits."""
        import time
        import msgpack
        import zmq
        from app.orm.database import get_db_engine
        from app.orm.job import Job
        with Session(get_db_engine()) as session:
            session.add(Job(id="late", set="s", name="n", owner="o", kind="task", state="running"))
            session.commit()
        sock = Mock()
        sock.recv.side_effect = [b"job-identity", b"",
                                 msgpack.packb({"id": "late", "state": "done", "detail": None, "time": time.time()})]
        poller = Mock()
        poller.poll.side_effect = [[(sock, zmq.POLLIN)], []]
        killer = _Killer()
        killer.stop()

        run_main(killer, setup_runner_socket=Mock(return_value=(Mock(), sock, poller)))

        with Session(get_db_engine()) as session:
            assert session.get(Job, "late").state == "done"
        assert sock.send.call_args_list[-1] == call(b"ack")

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


class TestMainLoop:
    """One or a few main() iterations against the test DB (see run_main)."""

    @staticmethod
    def _add_jobs(*jobs):
        from app.orm.database import get_db_engine
        with Session(get_db_engine()) as session:
            session.add_all(jobs)
            session.commit()

    @staticmethod
    def _job(job_id):
        from app.orm.database import get_db_engine
        from app.orm.job import Job
        with Session(get_db_engine()) as session:
            return session.get(Job, job_id)

    def test_dead_rows_are_failed(self, run_main):
        """A loading/running row whose process no longer exists and that
        wrote no result file (crash in job_setup's install phase or in the
        job itself) is finished as failed "dead (pid=N)"; a row with a live
        pid is left alone."""
        import os
        from app.orm.job import Job
        gone = subprocess.Popen(["true"])
        gone.wait()
        self._add_jobs(
            Job(id="stale_loading", set="s", name="n", owner="o", kind="task", state="loading", pid=gone.pid),
            Job(id="stale_running", set="s", name="n", owner="o", kind="task", state="running", pid=gone.pid),
            Job(id="alive", set="s", name="n", owner="o", kind="task", state="running", pid=os.getpid()),
        )
        killer = _Killer()

        run_main(killer, getNextToDo=Mock(side_effect=killer.stop))

        for job_id in ("stale_loading", "stale_running"):
            row = self._job(job_id)
            assert (row.state, row.detail, row.pid) == ("failed", f"dead (pid={gone.pid})", None), job_id
        assert (self._job("alive").state, self._job("alive").pid) == ("running", os.getpid())

    @pytest.mark.parametrize("state, detail", [("done", None), ("failed", "Traceback: boom")])
    def test_dead_row_takes_the_result_file(self, run_main, state, detail):
        """A job that finished but whose report never reached the runner
        (no ACK, runner busy/restarting) left its result in result.json:
        the row gets that result and its finish time, not "dead"."""
        import json
        import os
        from datetime import datetime
        from app.lib import files
        from app.orm.job import Job
        gone = subprocess.Popen(["true"])
        gone.wait()
        workspace = os.path.join(files.conf_data()["jobs"], "s", "finished")
        os.makedirs(workspace)
        with open(os.path.join(workspace, "result.json"), "w") as f:
            json.dump({"state": state, "detail": detail, "time": 1700000000.5}, f)
        self._add_jobs(Job(id="finished", set="s", name="n", owner="o", kind="task", state="running", pid=gone.pid))
        killer = _Killer()

        run_main(killer, getNextToDo=Mock(side_effect=killer.stop))

        row = self._job("finished")
        assert (row.state, row.detail, row.end) == (state, detail, datetime.fromtimestamp(1700000000.5))

    def test_cancel_of_tracked_process_starts_termination_and_keeps_detail(self, run_main):
        """A cancel whose pid is one of the runner's processes is handed to
        a terminate thread and the time the termination started is recorded;
        the operator's cancel detail survives."""
        from app import runner
        from app.orm.job import Job
        self._add_jobs(Job(id="cj", set="s", name="n", owner="o", kind="debug", state="cancel", pid=4242,
                           detail="Cancelled by new debug job start for o"))
        killer = _Killer()
        tracked = Mock(**{"poll.return_value": 0})

        applied = run_main(killer, getNextToDo=Mock(side_effect=killer.stop),
                           cleanProcesses=Mock(return_value={4242: tracked}))

        applied["threading.Thread"].assert_called_once_with(
            target=runner.terminate_job_processes, args=("cj", 4242, "o"), daemon=True)
        applied["threading.Thread"].return_value.start.assert_called_once_with()
        row = self._job("cj")
        assert row.detail == "Cancelled by new debug job start for o"
        assert row.pid is None
        assert row.state == "cancel"  # postTerm finalises it on a later iteration
        assert row.term_start is not None

    def test_cancel_of_untracked_job_is_finalised(self, run_main):
        """A cancel for a job the runner has no process for (claimed but not
        spawned, or spawned by a previous runner) is finalised directly."""
        from app.orm.job import Job
        self._add_jobs(Job(id="cu", set="s", name="n", owner="o", kind="task", state="cancel", pid=4243))
        killer = _Killer()

        applied = run_main(killer, getNextToDo=Mock(side_effect=killer.stop))

        row = self._job("cu")
        assert (row.state, row.pid) == ("cancelled", None)
        assert row.end is not None
        applied["threading.Thread"].assert_not_called()

    @staticmethod
    def _claim(killer, job_id):
        def claim():
            killer.stop()
            return job_id
        return Mock(side_effect=claim)

    def test_launch_failure_fails_job(self, run_main):
        """If the job process cannot be started (e.g. the per-set venv's
        python is missing), the job is failed with 'Launch failed: ...'
        and no pid is assigned."""
        from app.orm.job import Job
        self._add_jobs(Job(id="lf", set="myset", name="t", owner="o", kind="task", state="loading"))
        killer = _Killer()
        missing = FileNotFoundError(2, "No such file or directory", "/venv/myset/bin/python")

        run_main(killer, getNextToDo=self._claim(killer, "lf"),
                 loadJob=Mock(return_value=("o", "myset", "t", "task", "", "pass", None, 0)),
                 **{"subprocess.Popen": Mock(side_effect=missing)})

        row = self._job("lf")
        assert row.state == "failed"
        assert row.detail == "Launch failed: [Errno 2] No such file or directory: '/venv/myset/bin/python'"
        assert row.pid is None

    def test_child_dying_before_reading_stdin_is_still_tracked(self, run_main):
        """BrokenPipeError while piping the kwargs (child died at start-up):
        the spawn is still assigned and tracked — the stale-PID check reaps
        it later — and the runner does not fail or crash."""
        from app.orm.job import Job
        self._add_jobs(Job(id="bp", set="myset", name="t", owner="o", kind="task", state="loading"))
        killer = _Killer()
        proc = Mock(pid=99002, **{"poll.return_value": 1})
        proc.stdin.write.side_effect = BrokenPipeError()

        applied = run_main(killer, getNextToDo=self._claim(killer, "bp"),
                           loadJob=Mock(return_value=("o", "myset", "t", "task", "", "pass", None, 0)),
                           _shutdown_job_processes=Mock(),
                           **{"subprocess.Popen": Mock(return_value=proc)})

        row = self._job("bp")
        assert (row.state, row.pid) == ("loading", 99002)
        assert row.detail is None
        assert applied["_shutdown_job_processes"].call_args.args[0] == {99002: proc}

    def test_cancel_before_load_is_not_an_error(self, run_main, caplog):
        """A cancel that lands between the claim and loadJob is a normal
        outcome: nothing is spawned, nothing is logged as an error, and the
        row keeps its cancel state and detail for the cancel path."""
        import logging
        from sqlalchemy.orm import Session
        from app.orm.database import get_db_engine
        from app.runner import getNextToDo
        from app.orm.job import Job
        self._add_jobs(Job(id="raced", set="s", name="t", owner="o", kind="task", state="todo"))
        killer = _Killer()

        def claim_then_cancel():
            killer.stop()
            job_id = getNextToDo()
            with Session(get_db_engine()) as session:  # the cancel API, concurrently
                row = session.get(Job, job_id)
                row.state, row.detail = "cancel", "cancelled by operator"
                session.commit()
            return job_id

        with caplog.at_level(logging.INFO, logger="app.runner"):
            applied = run_main(killer, getNextToDo=Mock(side_effect=claim_then_cancel), **{"subprocess.Popen": Mock()})

        row = self._job("raced")
        assert (row.state, row.detail) == ("cancel", "cancelled by operator")
        applied["subprocess.Popen"].assert_not_called()
        assert not [r for r in caplog.records if r.levelno >= logging.ERROR]

    def test_load_failure_fails_job_with_traceback(self, run_main):
        """A claimed job that cannot be loaded (here: its set was removed
        after enqueue) is failed with the traceback; nothing is spawned."""
        from app.runner import getNextToDo
        from app.orm.job import Job
        self._add_jobs(Job(id="gone", set="removed_set", name="t", owner="o", kind="task", state="todo"))
        killer = _Killer()

        def claim():
            killer.stop()
            return getNextToDo()

        applied = run_main(killer, getNextToDo=Mock(side_effect=claim), **{"subprocess.Popen": Mock()})

        row = self._job("gone")
        assert row.state == "failed"
        assert "Traceback" in row.detail
        assert "Set 'removed_set' does not exist" in row.detail
        applied["subprocess.Popen"].assert_not_called()

    @staticmethod
    def _job_reports(path, msg):
        """The job side, unstarted: send_completion in its own thread (it blocks
        until the runner ACKs). Built here so the real Thread class is used even
        when started inside run_main, which patches threading.Thread."""
        import threading
        from app.lib import job as job_lib
        return threading.Thread(target=job_lib.send_completion, args=(path, msg), daemon=True)

    def test_completion_reported_before_termination_started_wins(self, run_main, runner_socket):
        """Real wire: the job finished and sent its completion; in the same
        iteration the runner first acts on the cancel (starts terminating),
        then reads the socket. The finish time the job stamped precedes the
        termination start, so the job's outcome is recorded, ending at that
        finish time."""
        from app.orm.job import Job
        context, socket, poller, path = runner_socket
        self._add_jobs(Job(id="cj", set="s", name="n", owner="o", kind="task", state="cancel", pid=4242,
                           detail="cancelled by operator"))
        job = self._job_reports(path, {"id": "cj", "state": "done", "detail": None})
        job.start()
        assert poller.poll(5000), "completion not queued at the runner"
        killer = _Killer()

        run_main(killer, setup_runner_socket=Mock(return_value=(context, socket, poller)),
                 getNextToDo=Mock(side_effect=killer.stop),
                 cleanProcesses=Mock(return_value={4242: Mock(**{"poll.return_value": 0})}))
        job.join(5)

        assert not job.is_alive(), "the job got no ACK"
        row = self._job("cj")
        assert (row.state, row.detail) == ("done", None)
        assert row.term_start is not None
        assert row.end < row.term_start

    def test_completion_winning_keeps_its_finish_time_when_termination_completes(self, run_main, runner_socket):
        """Real wire and a real terminate thread: the thread starts terminating
        the cancelled job (the row still `cancel`), the runner then records the
        job's earlier completion, and only afterwards does the process group
        drain. The termination completing leaves the recorded outcome and the
        job's own finish time as they are."""
        import threading
        from app import runner
        from app.orm.job import Job
        context, socket, poller, path = runner_socket
        self._add_jobs(Job(id="cj", set="s", name="n", owner="o", kind="task", state="cancel", pid=4244,
                           detail="cancelled by operator"))
        job = self._job_reports(path, {"id": "cj", "state": "done", "detail": None})
        job.start()
        assert poller.poll(5000), "completion not queued at the runner"
        killer = _Killer()
        sigterm_sent, completion_recorded = threading.Event(), threading.Event()
        recorded = {}
        terminators = []
        real_thread = threading.Thread  # run_main patches threading.Thread itself

        def terminator(target, args, daemon):  # the real thread finishJob starts
            terminators.append(real_thread(target=target, args=args, daemon=daemon))
            return terminators[-1]

        def read_completion(sock):  # once the thread passed its state check and sent SIGTERM
            assert sigterm_sent.wait(5), "the terminate thread sent no SIGTERM"
            runner_receive(sock)
            recorded["end"] = self._job("cj").end
            completion_recorded.set()

        def group_alive(pid):  # the job's group drains only after the completion was recorded
            assert completion_recorded.wait(5)
            return False

        runner_receive = runner.receive_and_process
        with patch("app.runner.job_lib.kill_proc_group", side_effect=lambda *_: sigterm_sent.set()), \
                patch("app.runner.job_lib.proc_group_alive", side_effect=group_alive):
            run_main(killer, setup_runner_socket=Mock(return_value=(context, socket, poller)),
                     getNextToDo=Mock(side_effect=killer.stop),
                     cleanProcesses=Mock(return_value={4244: Mock(**{"poll.return_value": 0})}),
                     receive_and_process=Mock(side_effect=read_completion),
                     **{"threading.Thread": Mock(side_effect=terminator)})
            assert len(terminators) == 1
            terminators[0].join(5)
        job.join(5)

        assert not terminators[0].is_alive(), "the termination did not complete"
        assert not job.is_alive(), "the job got no ACK"
        row = self._job("cj")
        assert (row.state, row.detail, row.pid) == ("done", None, None)
        assert row.end == recorded["end"]
        assert row.end < row.term_start

    def test_completion_reported_after_termination_started_keeps_cancel(self, run_main, runner_socket):
        """Real wire: the runner starts terminating the cancelled job, then the
        job reports `failed` (its shutdown raised). It finished after the
        termination started: the row ends `cancelled`, the job's message is
        appended to the cancel reason."""
        from app.orm.job import Job
        context, socket, poller, path = runner_socket
        self._add_jobs(Job(id="cj", set="s", name="n", owner="o", kind="task", state="cancel", pid=4243,
                           detail="cancelled by operator"))
        traceback_ = "Error executing task job cj\nTraceback ...\nConnectionError: broker gone"
        job = self._job_reports(path, {"id": "cj", "state": "failed", "detail": traceback_})
        killer = _Killer()
        queued = []

        def next_todo():
            if not queued:  # iteration 1 started the termination; now the job reports
                job.start()
                queued.append(bool(poller.poll(5000)))
            else:  # iteration 2 finalised the cancel and read the report
                killer.stop()

        run_main(killer, setup_runner_socket=Mock(return_value=(context, socket, poller)),
                 getNextToDo=Mock(side_effect=next_todo),
                 cleanProcesses=Mock(return_value={4243: Mock(**{"poll.return_value": 0})}))
        job.join(5)

        assert queued == [True]
        assert not job.is_alive(), "the job got no ACK"
        row = self._job("cj")
        assert row.state == "cancelled"
        assert row.detail == "cancelled by operator\n" + traceback_

    def test_loop_survives_errors(self, run_main):
        """Errors in the iteration (e.g. a locked DB) are logged and the
        loop goes on: disposeJobs failing pauses 1 s before the next
        iteration; getNextToTerm / getNextToDo failing skip their block."""
        killer = _Killer()
        locked = RuntimeError("database is locked")
        todo_calls = []

        def next_todo():
            todo_calls.append(1)
            if len(todo_calls) == 1:
                raise locked
            killer.stop()

        applied = run_main(killer,
                           disposeJobs=Mock(side_effect=[locked, None, None, None]),
                           getNextToTerm=Mock(side_effect=[locked, None]),
                           getNextToDo=Mock(side_effect=next_todo))

        assert call(1) in applied["time.sleep"].call_args_list
        assert len(todo_calls) == 2
        applied["schedule"].assert_called_once_with()


class TestPyopsEssentialServices:
    """Persistent pyops-managed services run without a Schedule row.
    The schedule loop iterates PYOPS_ESSENTIAL_SERVICES with a fixed
    `<3 failed` gate (schedule services use their max_fail_n)."""

    @patch('app.runner.PYOPS_ESSENTIAL_SERVICES', [{"set": "pyops", "name": "data", "owner": "system"}])
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
        mock_enqueue.assert_called_once_with("pyops", "data", "system", "service", True, None, 0, None)

    @patch('app.runner.PYOPS_ESSENTIAL_SERVICES', [{"set": "pyops", "name": "data", "owner": "system"}])
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

    @patch('app.runner.PYOPS_ESSENTIAL_SERVICES', [{"set": "pyops", "name": "data", "owner": "system"}])
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

    @patch('app.runner.PYOPS_ESSENTIAL_SERVICES', [{"set": "pyops", "name": "data", "owner": "system"}])
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
        mock_enqueue.assert_called_once_with("pyops", "data", "system", "service", True, None, 0, None)

    @patch('app.runner.PYOPS_ESSENTIAL_SERVICES', [{"set": "pyops", "name": "data", "owner": "system"}])
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
    cancel-gate semantics as PYOPS_ESSENTIAL_SERVICES — a cancelled
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


class TestPyopsSetRunsInHostVenv:
    """Pyops-set scriptlets are pyops-native code and execute in the
    pyops process's own Python — pyzmq / msgpack / psutil / requests are
    already importable there. The runner skips ensure_set_environment for
    set=='pyops' and uses sys.executable instead of a per-set venv
    path. User sets keep the standard per-set-venv flow."""

    def test_main_no_pyops_bootstrap_at_startup(self, mock_db):
        """main() must NOT call ensure_set_environment('pyops') at
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
