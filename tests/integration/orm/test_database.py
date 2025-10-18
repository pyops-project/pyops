import pytest
import threading
import json
import tempfile
import os
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session
from app.orm.database import Base, get_db_engine
from app.orm.job import Job
from app.orm.schedule import Schedule


def test_engine_and_session_creation(mock_db, test_temp_dir, patch_tempfile, mock_datetime):
    with Session(get_db_engine()) as session:
        # Test session works
        param_json = json.dumps({})
        job = Job(
            id="engine_id",
            owner="test",
            set="test",
            name="engine_test",
            kind="task",
            param=param_json,
            state="todo",
            drop_done=False,
            pid=None,
            detail=None
        )
        session.add(job)
        session.commit()
        fetched = session.query(Job).filter_by(name="engine_test").first()
        assert fetched is not None
        assert fetched.state == "todo"
        assert json.loads(fetched.param) == {}

def test_crud_queries(mock_db, test_temp_dir, patch_tempfile, mock_datetime):
    with Session(get_db_engine()) as session:
        # Create
        param_json = json.dumps({"test": True})
        job = Job(
            id="crud_id",
            owner="test",
            set="test",
            name="crud_job",
            kind="task",
            param=param_json,
            state="todo",
            drop_done=False,
            pid=None,
            detail=None
        )
        session.add(job)
        session.commit()
        
        # Read
        fetched = session.query(Job).filter_by(state="todo").first()
        assert fetched.name == "crud_job"
        assert json.loads(fetched.param) == {"test": True}
        
        # Update
        fetched.state = "done"
        session.commit()
        refreshed = session.query(Job).filter_by(id="crud_id").first()
        assert refreshed.state == "done"
        
        # Delete
        session.delete(fetched)
        session.commit()
        final = session.query(Job).filter_by(id="crud_id").first()
        assert final is None

def test_commit_rollback(mock_db, test_temp_dir, patch_tempfile, mock_datetime):
    with Session(get_db_engine()) as session:
        param_json = json.dumps({})
        job = Job(
            id="rollback_id",
            owner="test",
            set="test",
            name="rollback_test",
            kind="task",
            param=param_json,
            state="todo",
            drop_done=False,
            pid=None,
            detail=None
        )
        session.add(job)
        session.commit()  # Commit 1
        assert session.query(Job).filter_by(name="rollback_test").first() is not None
        
        session.rollback()
        # Fixture rolls back, but explicit test: add another
        another_param = json.dumps({"commit": True})
        another = Job(
            id="commit_id",
            owner="test",
            set="test",
            name="commit_test",
            kind="task",
            param=another_param,
            state="todo",
            drop_done=False,
            pid=None,
            detail=None
        )
        session.add(another)
        session.commit()
        assert session.query(Job).filter_by(name="commit_test").first() is not None

def test_delete_cleanup(mock_db, test_temp_dir, patch_tempfile, mock_datetime):
    with Session(get_db_engine()) as session:
        # Simple delete test (Insight 1: no relationships, just assert gone)
        param_json = json.dumps({})
        job = Job(
            id="cleanup_id",
            owner="test",
            set="test",
            name="cleanup_job",
            kind="task",
            param=param_json,
            state="todo",
            drop_done=False,
            pid=None,
            detail=None
        )
        session.add(job)
        session.commit()
        
        fetched = session.query(Job).filter_by(id="cleanup_id").first()
        assert fetched is not None
        
        session.delete(fetched)
        session.commit()
        
        final = session.query(Job).filter_by(id="cleanup_id").first()
        assert final is None  # Clean delete, no orphans

def test_thread_safety_concurrency(mock_db, test_temp_dir, patch_tempfile, mock_datetime):
    # Simulate concurrent access (Insight 2); use shared engine
    results = []
    num_threads = 5
    import time
    import random

    def thread_task(thread_id):
        with Session(get_db_engine()) as local_session:
            try:
                param_json = json.dumps({})
                job = Job(
                    id=f"thread_{thread_id}",
                    owner=f"thread_{thread_id}",
                    set="concurrent",
                    name=f"job_{thread_id}",
                    kind="task",
                    param=param_json,
                    state="todo",
                    drop_done=False,
                    pid=None,
                    detail=None
                )
                local_session.add(job)
                # Retry commit up to 3 times for transient locks
                for attempt in range(3):
                    try:
                        local_session.commit()
                        break
                    except Exception:
                        local_session.rollback()
                        if attempt == 2:
                            raise
                        time.sleep(0.1)
                # Verify insert within thread
                fetched = local_session.query(Job).filter_by(id=f"thread_{thread_id}").first()
                assert fetched is not None
                results.append(f"Thread {thread_id} success")
            except Exception as e:
                results.append(f"Thread {thread_id} error: {e}")

    threads = []
    for i in range(num_threads):
        t = threading.Thread(target=thread_task, args=(i,))
        threads.append(t)
        t.start()

    for t in threads:
        t.join(timeout=5)  # Increased timeout for retries

    print("Thread results:", results)

    # Assert all succeeded
    assert len([r for r in results if "success" in r]) == num_threads
    assert len([r for r in results if "error" in r]) == 0

    # Query from a new session
    with Session(get_db_engine()) as check_session:
        concurrent_jobs = check_session.query(Job).filter_by(set="concurrent").all()
        assert len(concurrent_jobs) == num_threads
