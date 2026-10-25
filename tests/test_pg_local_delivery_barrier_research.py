"""Reproduce local delivery ordering AND the unsolved remote-revoke race."""
from __future__ import annotations

import threading
import pytest
from research.fenced_authority.local_delivery_barrier import (
    LocalSnapshot, LocalDeliveryBarrier, LocalDeliveryDenied,
)


def make_gate(*, witness=None, deliver=None, abort=None, revision=1):
    received, stopped = [], []
    witness = witness or (lambda: LocalSnapshot(1, revision, True))
    gate = LocalDeliveryBarrier(
        epoch=1,
        read_current=witness,
        fake_deliver=deliver or received.append,
        fake_abort=abort or (lambda: stopped.append(True)),
    )
    return gate, received, stopped


def test_stage_does_not_release_data_without_step():
    gate, out, aborts = make_gate()
    gate.stage(b"secret-synthetic")
    assert out == []
    assert gate.evidence["queued"] == 1
    assert gate.deliver_one() is True
    assert out == [b"secret-synthetic"]
    assert gate.evidence["provider_calls"] == 0


def test_local_revocation_drops_already_authorized_queued_output():
    gate, out, aborts = make_gate()
    gate.stage(b"buffered-before-revocation")
    gate.revoke_local(2)
    with pytest.raises(LocalDeliveryDenied):
        gate.deliver_one()
    assert out == [] and len(aborts) == 1
    assert gate.evidence["queued"] == 0
    gate.revoke_local(3)
    assert len(aborts) == 1
    with pytest.raises(LocalDeliveryDenied):
        gate.stage(b"after-revoke")


@pytest.mark.parametrize("snapshot", [
    LocalSnapshot(2, 1, True),
    LocalSnapshot(1, 2, False),
    LocalSnapshot(1, 0, True),
    None,
])
def test_wrong_epoch_revoked_or_invalid_observation_denies(snapshot):
    gate, out, aborted = make_gate(witness=lambda: snapshot)
    gate.stage(b"denied")
    with pytest.raises(LocalDeliveryDenied):
        gate.deliver_one()
    assert out == [] and aborted == [True]


def test_read_failure_drops_queued_bytes_and_fails_closed():
    def unavailable():
        raise ConnectionError("isolated synthetic partition")
    gate, out, aborted = make_gate(witness=unavailable)
    gate.stage(b"no-output")
    with pytest.raises(LocalDeliveryDenied):
        gate.deliver_one()
    assert out == [] and aborted == [True]
    assert gate.evidence["possibly_accepted"] is False


def test_rollback_after_observing_higher_revision_denies():
    latest = [LocalSnapshot(1, 4, True)]
    gate, out, aborted = make_gate(witness=lambda: latest[0])
    gate.stage(b"one")
    assert gate.deliver_one() is True
    latest[0] = LocalSnapshot(1, 3, True)
    gate.stage(b"stale")
    with pytest.raises(LocalDeliveryDenied):
        gate.deliver_one()
    assert out == [b"one"]
    assert gate.evidence["noncopyable_highwater_proven"] is False


def test_transport_may_accept_then_raise_no_replay():
    accepted = []
    def fail_after_receipt(data):
        accepted.append(data)
        raise OSError("fake transport accepted then disconnected")
    gate, out, aborted = make_gate(deliver=fail_after_receipt)
    gate.stage(b"maybe-delivered")
    with pytest.raises(LocalDeliveryDenied):
        gate.deliver_one()
    assert accepted == [b"maybe-delivered"]
    assert gate.evidence["possibly_accepted"] is True
    assert gate.evidence["closed"] is True
    with pytest.raises(LocalDeliveryDenied):
        gate.deliver_one()
    assert accepted == [b"maybe-delivered"]


def test_bounded_buffer_fail_closed():
    gate, out, aborted = make_gate()
    for _ in range(4):
        gate.stage(b"x")
    with pytest.raises(LocalDeliveryDenied):
        gate.stage(b"excess")
    assert out == [] and aborted == [True]


def test_local_revoke_ack_orders_with_inflight_fake_sink():
    entered, release, revoke_done = (
        threading.Event(), threading.Event(), threading.Event()
    )
    got = []
    def blocking_fake_sink(data):
        entered.set()
        assert release.wait(timeout=3), "test fake sink release missing"
        got.append(data)

    gate, _, aborted = make_gate(deliver=blocking_fake_sink)
    gate.stage(b"first")
    gate.stage(b"queued-second")
    results = []
    sender = threading.Thread(target=lambda: results.append(gate.deliver_one()))
    sender.start()
    assert entered.wait(timeout=3)
    def revoke():
        gate.revoke_local(2)
        revoke_done.set()
    killer = threading.Thread(target=revoke)
    killer.start()
    # Ack cannot happen while the local sink is still inside its lock.
    assert not revoke_done.wait(timeout=0.03)
    release.set()
    sender.join(timeout=3)
    killer.join(timeout=3)
    assert not sender.is_alive() and not killer.is_alive()
    assert revoke_done.is_set() and results == [True]
    assert got == [b"first"] and gate.evidence["queued"] == 0
    with pytest.raises(LocalDeliveryDenied):
        gate.deliver_one()
    assert got == [b"first"]
    assert aborted == [True]


def test_remote_revocation_after_snapshot_before_fake_sink_still_leaks():
    """INTENTIONAL counterexample: a local lock cannot fence a remote effect."""
    authority = {"active": True}
    delivered = []
    def stale_snapshot_then_remote_revoke():
        old = LocalSnapshot(1, 7, authority["active"])
        authority["active"] = False  # remote revoke after old snapshot
        return old
    gate, _, _ = make_gate(
        witness=stale_snapshot_then_remote_revoke, deliver=delivered.append
    )
    gate.stage(b"REMOTE_REVOKE_RACE_SYNTHETIC")
    assert gate.deliver_one() is True
    assert authority["active"] is False
    assert delivered == [b"REMOTE_REVOKE_RACE_SYNTHETIC"]
    assert gate.evidence["remote_revocation_atomicity_proven"] is False
    assert gate.evidence["provider_execution_authorized"] is False


def test_independent_cloned_local_highwaters_allow_split_brain():
    """INTENTIONAL failure mode: one resource's revocation isn't the other's."""
    same_stale_view = lambda: LocalSnapshot(1, 5, True)
    a, output_a, _ = make_gate(witness=same_stale_view)
    b, output_b, _ = make_gate(witness=same_stale_view)
    a.stage(b"quarantined")
    b.stage(b"still-deliverable")
    a.revoke_local(6)
    assert b.deliver_one() is True
    assert output_a == [] and output_b == [b"still-deliverable"]
    assert b.evidence["noncopyable_highwater_proven"] is False


def test_no_real_provider_or_database_code():
    from pathlib import Path
    source = (Path(__file__).parents[1] / "research" /
              "fenced_authority" / "local_delivery_barrier.py").read_text()
    for denied in ("requests.post(", "httpx.", "psycopg", "subprocess.run(", "docker run"):
        assert denied not in source
