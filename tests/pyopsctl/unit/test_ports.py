"""Unit coverage for ctl/pyopsctl/ports.py.

The lease store lives at $PYOPS_DEBUG_PORTS_DIR (set per-test to a
tmp_path) → <profile>/<port>.lease, contents = job_id. The contract:
  - first-in wins (O_CREAT|O_EXCL semantics)
  - pool walked in order; first free port leased
  - stale leases (is_stale(holder) → True) are reclaimed
  - release / release_for_job / find_port_for_job are idempotent and
    do not crash on missing files
  - NoPortsAvailable when pool is empty or fully booked
"""
import os
from unittest.mock import patch

import pytest


@pytest.fixture
def leases(tmp_path, monkeypatch):
    monkeypatch.setenv("PYOPS_DEBUG_PORTS_DIR", str(tmp_path))
    from pyopsctl import ports as ports_mod  # imported under env
    return ports_mod


def test_lease_picks_first_free_port(leases):
    l = leases.lease("p", (45679, 45680), "JOB_A")
    assert l.port == 45679
    assert l.job_id == "JOB_A"


def test_lease_skips_occupied_ports(leases):
    leases.lease("p", (45679, 45680), "JOB_A")
    l2 = leases.lease("p", (45679, 45680), "JOB_B")
    assert l2.port == 45680


def test_lease_raises_when_pool_full(leases):
    leases.lease("p", (45679,), "JOB_A")
    with pytest.raises(leases.NoPortsAvailable):
        leases.lease("p", (45679,), "JOB_B")


def test_lease_raises_when_pool_empty(leases):
    with pytest.raises(leases.NoPortsAvailable):
        leases.lease("p", (), "JOB_A")


def test_lease_reclaims_stale_leases(leases):
    leases.lease("p", (45679,), "OLD_JOB")
    # Caller declares OLD_JOB stale → port is reclaimed.
    l = leases.lease("p", (45679,), "NEW_JOB",
                     is_stale=lambda jid: jid == "OLD_JOB")
    assert l.port == 45679
    assert l.job_id == "NEW_JOB"


def test_release_is_idempotent(leases):
    l = leases.lease("p", (45679,), "JOB_A")
    leases.release(l)
    leases.release(l)  # second call is a no-op
    # Port is free again.
    l2 = leases.lease("p", (45679,), "JOB_B")
    assert l2.port == 45679


def test_release_for_job_returns_port_and_unlinks(leases):
    leases.lease("p", (45679, 45680), "JOB_A")
    leases.lease("p", (45679, 45680), "JOB_B")
    released = leases.release_for_job("p", "JOB_A")
    assert released == 45679
    # JOB_B's lease intact.
    found = leases.find_port_for_job("p", "JOB_B")
    assert found == 45680


def test_release_for_job_returns_minus1_when_no_match(leases):
    assert leases.release_for_job("p", "NO_SUCH_JOB") == -1


def test_find_port_for_job_returns_none_when_no_profile_dir(leases):
    assert leases.find_port_for_job("never_created", "JOB_A") is None


def test_find_port_for_job_returns_port_when_leased(leases):
    leases.lease("p", (45680, 45681), "JOB_A")
    assert leases.find_port_for_job("p", "JOB_A") == 45680


def test_list_leases_returns_known_holders_sorted(leases):
    leases.lease("p", (45679, 45680), "JOB_A")
    leases.lease("p", (45679, 45680), "JOB_B")
    rows = leases.list_leases("p")
    assert sorted((r.port, r.job_id) for r in rows) == [
        (45679, "JOB_A"), (45680, "JOB_B"),
    ]


def test_profiles_dont_share_namespace(leases):
    leases.lease("alice", (45679,), "JOB_A")
    # Same port number under a different profile is independently free.
    l = leases.lease("bob", (45679,), "JOB_B")
    assert l.port == 45679
