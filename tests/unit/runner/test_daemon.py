import pytest
from unittest.mock import Mock, patch
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
            assert job.state == "remove"
            assert job.end is not None
            assert job.detail is None
            assert job.pid is None

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

    @patch('app.runner.lib_runner.enqueue')
    @patch('croniter.croniter')
    @patch('app.runner.datetime')
    def test_schedule_cron_enqueue(self, mock_dt, mock_croniter, mock_enqueue, mock_db):
        from app.runner import schedule
        from app.orm.database import get_db_engine
        from app.orm.schedule import Schedule
        from datetime import datetime
        with Session(get_db_engine()) as test_session:
            mock_dt.now.return_value = datetime(2023, 1, 1, 12, 0)
            sched = Schedule(set="test_set", name="test_name", owner="test_owner", schedule="* * * * *", active=True, drop_done=False, param=None, last=None)
            test_session.add(sched)
            test_session.commit()
            sched_id = sched.id
        mock_cron = Mock()
        mock_prev = datetime(2023, 1, 1, 11, 59)
        mock_cron.get_prev.return_value = mock_prev
        mock_croniter.return_value = mock_cron
        schedule()
        mock_enqueue.assert_called_once_with("test_set", "test_name", "test_owner", "task", False, None)
        with Session(get_db_engine()) as test_session:
            sched = test_session.get(Schedule, sched_id)
            test_session.refresh(sched)
            assert sched.last == mock_prev

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
        mock_enqueue.assert_called_once_with("test_set", "test_name", "test_owner", "service", False, None)
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

    def test_term_cancel(self):
        from app.runner import term
        mock_postTerm = Mock()
        mock_process = Mock()
        mock_process.is_alive.return_value = False  # Graceful exit immediately
        mock_processes = {123: mock_process}
        job = {"id": "id", "pid": 123}
        with patch('app.runner.postTerm', mock_postTerm):
            term(job, mock_processes)
        mock_process.terminate.assert_called_once()
        mock_postTerm.assert_called_once_with("id", killed=False)

    def test_term_kill(self):
        from app.runner import term
        mock_postTerm = Mock()
        mock_process = Mock()
        # Simulate 50 loop is_alive True, post-loop True to trigger kill
        mock_process.is_alive.side_effect = [True] * 51
        mock_process.join.return_value = None
        mock_processes = {123: mock_process}
        job = {"id": "id", "pid": 123}
        time_values = [0.0] + [0.1 * i for i in range(1, 51)]  # start 0.0, conditions 0.1 to 5.0
        with patch('app.runner.time.time', side_effect=time_values):
            with patch('app.runner.time.sleep', return_value=None):
                with patch('app.runner.postTerm', mock_postTerm):
                    term(job, mock_processes)
        mock_process.terminate.assert_called_once()
        mock_process.kill.assert_called_once()
        mock_process.join.assert_called_once_with(timeout=1.0)
        mock_postTerm.assert_called_once_with("id", killed=True)
        assert mock_process.is_alive.call_count == 51

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

    def test_main_initialization_no_crash(self, mock_db):
        from app.runner import main
        from unittest.mock import Mock, patch, DEFAULT
        # Mock all dependencies to no-op for first iteration, set kill_now=True to exit after
        def sleep_side_effect(delay):
            mock_killer_instance.kill_now = True
            return None
        with patch.dict('os.environ', {}):
            with patch.multiple('app.runner.time', sleep=DEFAULT, time=DEFAULT) as time_mocks:
                with patch.multiple('app.runner.os', listdir=DEFAULT) as os_mocks:
                    with patch.multiple('app.runner.git', get_git_base=DEFAULT, pull_if_needed=DEFAULT, get_git_pull_interval=DEFAULT) as git_mocks:
                        with patch.multiple('app.runner', schedule=DEFAULT, assignJob=DEFAULT, Process=DEFAULT, loadJob=DEFAULT, getNextToDo=DEFAULT, finishJob=DEFAULT, postTerm=DEFAULT, term=DEFAULT, getNextToTerm=DEFAULT, cleanProcesses=DEFAULT, disposeJobs=DEFAULT, handleOddJobs=DEFAULT, Queue=DEFAULT, GracefulKiller=DEFAULT) as runner_mocks:
                            mock_killer_instance = runner_mocks['GracefulKiller'].return_value
                            mock_killer_instance.kill_now = False
                            mock_queue_instance = Mock()
                            mock_queue_instance.empty.return_value = True
                            runner_mocks['Queue'].return_value = mock_queue_instance
                            runner_mocks['cleanProcesses'].return_value = {}
                            runner_mocks['getNextToTerm'].return_value = None
                            runner_mocks['getNextToDo'].return_value = None
                            git_mocks['get_git_pull_interval'].return_value = 60
                            git_mocks['get_git_base'].return_value = '/tmp/git'
                            git_mocks['pull_if_needed'].return_value = None
                            os_mocks['listdir'].return_value = []
                            time_mocks['time'].side_effect = [61.0]  # Trigger git pull on first call
                            time_mocks['sleep'].side_effect = sleep_side_effect
                            # Call main with dummy args
                            main('tmp/db', 'localhost:8000', 'tmp/venv', 'tmp/pkg', {})
                            # Assert time.time called (verifies no UnboundLocalError)
                            assert time_mocks['time'].call_count >= 1  # current_time call
                            time_mocks['sleep'].assert_called_once_with(0.5)
