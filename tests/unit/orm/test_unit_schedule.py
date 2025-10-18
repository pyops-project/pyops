import pytest
import json
from datetime import datetime
from sqlalchemy.orm import Session
from croniter import croniter


@pytest.mark.parametrize("cron_expr, is_valid", [
    ("* * * * *", True),  # Every minute
    ("0 0 * * *", True),  # Midnight daily
    ("invalid", False),   # Malformed
    ("* * * *", False),   # Too few fields
])
def test_schedule_cron_validation(mock_db, cron_expr, is_valid):
    from app.orm.database import get_db_engine
    from app.orm.schedule import Schedule
    from datetime import datetime
    with Session(get_db_engine()) as session:
        param_data = {"key": "value"}
        param_json = json.dumps(param_data)
        schedule = Schedule(
            schedule=cron_expr,  # Field is 'schedule'
            set="test_set",
            name="test_schedule",
            owner="test_user",
            active=True,
            drop_done=False,
            param=param_json,
            last=None
        )
        session.add(schedule)
        session.commit()
        
        # Baseline: Current model allows insert even for invalid cron (no built-in validator)
        fetched = session.query(Schedule).filter_by(id=schedule.id).first()
        assert fetched.schedule == cron_expr
        assert fetched.active == True
        assert json.loads(fetched.param) == param_data
        
        # Explicit validation with croniter
        if is_valid:
            cron = croniter(cron_expr)
            assert cron.get_next(datetime) is not None  # Valid, can get next run
        else:
            with pytest.raises(ValueError):
                croniter(cron_expr)  # Invalid raises ValueError

def test_schedule_active_flag_and_last_run(mock_db):
    from app.orm.database import get_db_engine
    from app.orm.schedule import Schedule
    from datetime import datetime
    with Session(get_db_engine()) as session:
        schedule = Schedule(
            schedule="* * * * *",
            set="test_set",
            name="test_schedule",
            owner="test_user",
            active=False,
            drop_done=False,
            param=json.dumps({}),
            last=None
        )
        session.add(schedule)
        session.commit()
        
        assert schedule.active == False
        assert schedule.last is None
        
        # Update active and last
        now = datetime.now()
        schedule.active = True
        schedule.last = now
        session.commit()
        
        fetched = session.query(Schedule).filter_by(id=schedule.id).first()
        assert fetched.active == True
        assert fetched.last == now

def test_schedule_param_serialization(mock_db):
    from app.orm.database import get_db_engine
    from app.orm.schedule import Schedule
    with Session(get_db_engine()) as session:
        param_data = {"key": "value"}
        param_json = json.dumps(param_data)
        schedule = Schedule(
            schedule="* * * * *",
            set="test_set",
            name="test_schedule",
            owner="test_user",
            active=True,
            drop_done=False,
            param=param_json,
            last=None
        )
        session.add(schedule)
        session.commit()
        fetched = session.query(Schedule).filter_by(id=schedule.id).first()
        assert json.loads(fetched.param) == param_data

def test_schedule_invalid_cron_baseline(mock_db):
    from app.orm.database import get_db_engine
    from app.orm.schedule import Schedule
    with Session(get_db_engine()) as session:
        # Baseline for Insight 3: Model allows invalid cron insert (no Pydantic validator yet)
        schedule = Schedule(
            schedule="invalid_cron",
            set="test_set",
            name="test_schedule",
            owner="test_user",
            active=True,
            drop_done=False,
            param=json.dumps({}),
            last=None
        )
        session.add(schedule)
        session.commit()
        assert schedule.schedule == "invalid_cron"
