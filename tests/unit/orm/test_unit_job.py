import pytest
import json
from sqlalchemy.orm import Session


@pytest.mark.parametrize("state", ["todo", "dequeued", "running", "done", "failed", "canceled", "removed"])
def test_job_creation_with_states(mock_db, state, test_temp_dir, patch_tempfile, mock_conf):
    from app.orm.database import get_db_engine
    from app.orm.job import Job
    with Session(get_db_engine()) as session:
        param_data = {"key": "value"}
        param_json = json.dumps(param_data)
        job = Job(
            id=f"test_id_{state}",  # Unique per state to avoid duplicates
            owner="test_user",
            set="test_set",
            name="test_job",
            kind="task",
            param=param_json,
            state=state,
            drop_done=False,
            pid=None,
            detail=None
        )
        session.add(job)
        session.commit()
        assert job.state == state
        assert json.loads(job.param) == param_data  # JSON as str
        fetched = session.query(Job).filter_by(id=f"test_id_{state}").first()
        assert fetched.state == state

def test_job_detail_traceback(mock_db, test_temp_dir, patch_tempfile, mock_conf):
    from app.orm.database import get_db_engine
    from app.orm.job import Job
    with Session(get_db_engine()) as session:
        job = Job(
            id="detail_id",
            owner="test_user",
            set="test_set",
            name="test_job",
            kind="task",
            param=json.dumps({}),
            state="failed",
            drop_done=False,
            pid=None,
            detail="Simulated traceback: Error occurred"
        )
        session.add(job)
        session.commit()
        assert job.detail == "Simulated traceback: Error occurred"
        fetched = session.query(Job).filter_by(id="detail_id").first()
        assert fetched.detail == job.detail

def test_job_param_serialization_roundtrip(mock_db, test_temp_dir, patch_tempfile, mock_conf):
    from app.orm.database import get_db_engine
    from app.orm.job import Job
    with Session(get_db_engine()) as session:
        param_data = {"nested": {"list": [1, 2, 3]}}
        param_json = json.dumps(param_data)
        job = Job(
            id="param_id",
            owner="test_user",
            set="test_set",
            name="test_job",
            kind="task",
            param=param_json,
            state="todo",
            drop_done=False,
            pid=None,
            detail=None
        )
        session.add(job)
        session.commit()
        fetched = session.query(Job).filter_by(id="param_id").first()
        assert json.loads(fetched.param) == param_data
        # Test update
        updated_data = {"updated": True}
        updated_json = json.dumps(updated_data)
        fetched.param = updated_json
        session.commit()
        refreshed = session.query(Job).filter_by(id="param_id").first()
        assert json.loads(refreshed.param) == updated_data

# No model validation for state; allows any string
def test_job_allows_invalid_state(mock_db, test_temp_dir, patch_tempfile, mock_conf):
    from app.orm.database import get_db_engine
    from app.orm.job import Job
    with Session(get_db_engine()) as session:
        job = Job(
            id="invalid_state_id",
            owner="test_user",
            set="test_set",
            name="test_job",
            kind="task",
            param=json.dumps({}),
            state="invalid_state",
            drop_done=False,
            pid=None,
            detail=None
        )
        session.add(job)
        session.commit()
        assert job.state == "invalid_state"  # Allowed
