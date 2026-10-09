"""E2E: a real server started on a populated 3.3.6 database syncs it at start-up and serves its rows."""

import pytest


@pytest.fixture
def e2e_env(e2e_env, seed_db):
    """conftest's e2e_env with data/app.db pre-seeded as a populated 3.3.6 database."""
    seed_db(e2e_env["data_dir"] / "app.db", "3.3.6")
    return e2e_env


def test_startup_on_v336_db_serves_old_rows(pyops_server, api):
    r = api.get("/scriptlet/job/item", params={"id": "seed-done"})
    assert r.status_code == 200, r.text
    assert (r.json()["state"], r.json()["param"]) == ("done", '{"k": 1}')

    r = api.get("/scriptlet/schedule/list")
    assert r.status_code == 200, r.text
    schedules = {s["id"]: s for s in r.json()}
    assert (schedules[1]["name"], schedules[1]["active"]) == ("task", False)

    pyops_server.assert_no_unexpected_errors()
