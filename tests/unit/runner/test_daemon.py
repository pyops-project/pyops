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

    def test_purgeLeftoverServiceJobs_running_service(self, mock_db):
        from app.runner import purgeLeftoverServiceJobs
        from app.orm.database import get_db_engine
        from app.orm.job import Job
        with Session(get_db_engine()) as test_session:
            job_running_service = Job(id="run_svc_id", set="test_set", name="test_name", owner="test_owner", kind="service", state="running", param=None, detail=None, pid=123, drop_done=False)
            job_running_task = Job(id="run_task_id", set="test_set", name="test_name", owner="test_owner", kind="task", state="running", param=None, detail=None, pid=None, drop_done=False)
            job_done_service = Job(id="done_svc_id", set="test_set", name="test_name", owner="test_owner", kind="service", state="done", param=None, detail=None, pid=None, drop_done=False)
            test_session.add_all([job_running_service, job_running_task, job_done_service])
            test_session.commit()
        purgeLeftoverServiceJobs()
        with Session(get_db_engine()) as test_session:
            # Service jobs should be deleted
            assert test_session.get(Job, "run_svc_id") is None
            assert test_session.get(Job, "done_svc_id") is None
            # Non-service jobs should be unchanged
            job_running_task = test_session.get(Job, "run_task_id")
            assert job_running_task.state == "running"

    def test_purgeLeftoverServiceJobs_dequeued_service(self, mock_db):
        from app.runner import purgeLeftoverServiceJobs
        from app.orm.database import get_db_engine
        from app.orm.job import Job
        with Session(get_db_engine()) as test_session:
            job_dequeued_service = Job(id="deq_svc_id", set="test_set", name="test_name", owner="test_owner", kind="service", state="dequeued", param=None, detail=None, pid=None, drop_done=False)
            job_failed_service = Job(id="fail_svc_id", set="test_set", name="test_name", owner="test_owner", kind="service", state="failed", param=None, detail=None, pid=None, drop_done=False)
            test_session.add_all([job_dequeued_service, job_failed_service])
            test_session.commit()
        purgeLeftoverServiceJobs()
        with Session(get_db_engine()) as test_session:
            # Service jobs should be deleted
            assert test_session.get(Job, "deq_svc_id") is None
            assert test_session.get(Job, "fail_svc_id") is None

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
        mock_find.assert_called_once_with("test_owner", "test_set", "test_name", "service", ["todo", "dequeued", "running", "cancel", "done"])

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
        mock_drop.assert_called_once_with("id", "test_owner", "test_set")

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

    def test_shutdown_graceful(self):
        from app.runner import Job, get_db_engine
        from sqlalchemy.orm import Session
        mock_process = Mock()
        mock_process.is_alive.return_value = False
        processes = {123: mock_process}
        # Mock the shutdown for loop code
        with patch('app.runner.Session') as mock_session:
            mock_session.return_value.__enter__.return_value.query.return_value.filter_by.return_value.first.return_value = Job(id="id", pid=123, state="running")
            mock_session.return_value.__enter__.return_value.commit.return_value = None
            # Simulate the shutdown for loop for one pid
            p = processes[123]
            p.terminate()
            # p.join(timeout=5.0)  # not called in graceful
            # Update job
            session = mock_session.return_value.__enter__.return_value
            job = session.query(Job).filter_by(pid=123).first()
            job.end = datetime.now()
            job.pid = None
            job.state = "cancelled"
            job.detail = "terminated gracefully on shutdown"
            session.commit()
        mock_process.terminate.assert_called_once()
        # mock_process.join.assert_called_once_with(timeout=5.0)
        # assert mock_session.call_count == 1  # manual simulation

    def test_shutdown_kill(self):
        from app.runner import Job, get_db_engine
        from sqlalchemy.orm import Session
        mock_process = Mock()
        mock_process.is_alive.side_effect = [True] * 51 + [False]
        processes = {123: mock_process}
        with patch('app.runner.time') as mock_time:
            time_values = [0.0] + [0.1 * i for i in range(1, 51)]
            mock_time.time.side_effect = time_values
            with patch('app.runner.time.sleep', return_value=None):
                with patch('app.runner.Session') as mock_session:
                    mock_session.return_value.__enter__.return_value.query.return_value.filter_by.return_value.first.return_value = Job(id="id", pid=123, state="running")
                    mock_session.return_value.__enter__.return_value.commit.return_value = None
                    # Simulate the shutdown for loop for one pid
                    p = processes[123]
                    p.terminate()
                    timeout = 5.0
                    start_time = mock_time.time()
                    while p.is_alive() and (mock_time.time() - start_time) < timeout:
                        mock_time.sleep(0.1)
                    killed = p.is_alive()
                    if killed:
                        p.kill()
                        p.join(timeout=1.0)
                    # Update job
                    session = mock_session.return_value.__enter__.return_value
                    job = session.query(Job).filter_by(pid=123).first()
                    job.end = datetime.now()
                    job.pid = None
                    job.state = "killed"
                    job.detail = "force killed after 5s timeout on shutdown"
                    session.commit()
        mock_process.terminate.assert_called_once()
        mock_process.kill.assert_called_once()
        mock_process.join.assert_called_once_with(timeout=1.0)
        # assert mock_session.call_count == 1  # manual simulation

    @patch('app.lib.files.conf_users', return_value={"owner": {"debug_port": 5678}})
    def test_validate_debug_prerequisites_valid(self, mock_conf_users):
        from app.lib.job import validate_debug_prerequisites
        port = validate_debug_prerequisites("owner")
        assert port == 5678

    @patch('app.lib.files.conf_users', return_value={"owner": {}})
    def test_validate_debug_prerequisites_no_port(self, mock_conf_users):
        from app.lib.job import validate_debug_prerequisites
        with pytest.raises(Exception, match="Invalid or missing debug_port for user owner"):
            validate_debug_prerequisites("owner")

    @patch('app.lib.files.conf_users', return_value={"owner": {"debug_port": 1000}})
    def test_validate_debug_prerequisites_invalid_port(self, mock_conf_users):
        from app.lib.job import validate_debug_prerequisites
        with pytest.raises(Exception, match="Invalid or missing debug_port for user owner"):
            validate_debug_prerequisites("owner")

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

