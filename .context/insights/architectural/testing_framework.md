# Testing Framework

PyOps uses pytest for testing, configured in pytest.ini (with markers, coverage, etc.). tests/conftest.py provides shared fixtures (e.g., DB sessions, test clients) including comprehensive fixtures: db_session (scoped session), db (SQLite in tmp_path), app_client (FastAPI test client with mocks for get_current_user, conf_data, git ops like list_scriptlets, write/read_content, infer_type). Mocks for croniter, jedi (MockCompletion with docstring), process (subprocess). pytest_configure for config. Tests use these for isolation (sample_data).

Tests organized into unit/ (lib/git.py, pyops.py, runner.py, sites.py, orm/job.py, orm/schedule.py), api/ (endpoints like editor.py, job.py, schedule.py), integration/ (orm/database.py, routers/editor.py/inventory.py/job.py/schedule.py/manager_monitor_scheduler.py, runner/execution.py, web.py). No e2e tests observed; coverage via htmlcov/. Focus on mocking DB/ZMQ, API responses, job states. Run via pytest command.

## Running Tests
Tests with verbose output, no output capture:
pytest -v -s tests/

If really required to identify problem (pollutes context) tests with additional debug logging:
pytest -v -s tests/ --log-cli-level=DEBUG
