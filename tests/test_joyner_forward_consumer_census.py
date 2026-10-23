"""Synthetic socket snapshots only; never opens sockets or generates inference."""
from __future__ import annotations

import json
import pytest

from scripts import joyner_forward_consumer_census as c


SAMPLE = """
ESTAB 0 0 192.168.1.79:1234 192.168.1.222:53000
ESTAB 0 0 100.83.215.83:1234 100.64.11.22:53001
ESTAB 0 0 100.83.215.83:1236 100.64.11.22:53002
ESTAB 0 0 127.0.0.1:1234 127.0.0.1:58500
ESTAB 0 0 192.168.1.79:9876 192.168.1.222:53000
LISTEN 0 128 100.83.215.83:1236 0.0.0.0:*
"""


def test_socket_allowlist_drops_unrelated_routes_and_localhost():
    rows = c.parse_ss(SAMPLE)
    assert rows == {
        ("minicpm_lan", "192.168.1.222"),
        ("minicpm_tailnet", "100.64.11.22"),
        ("ling_tailnet", "100.64.11.22"),
    }


def test_only_literal_ipv4_accepted():
    for invalid in ("127.0.0.1", "localhost:1234", "https://127.0.0.1:1234",
                    "[::1]:1234", "100.83.215.83:0", "100.83.215.83:65536",
                    "127.0.0.1:notaport"):
        assert c.address(invalid) is None
    assert c.address("100.83.215.83:1236") == ("100.83.215.83", 1236)


def test_forged_x_forwarded_for_never_parsed():
    rows = c.parse_ss(SAMPLE + "X-Forwarded-For: 1.2.3.4\n")
    assert not any(peer == "1.2.3.4" for _,peer in rows)


def fake_clock():
    times = [0.0]
    def now():
        return times[0]
    def sleep(delay):
        times[0] += delay
    return now, sleep


def test_bounded_census_counts_same_client_once_across_routes_and_samples():
    now,sleep = fake_clock()
    values = [SAMPLE, SAMPLE, ""]
    def reader():
        return values.pop(0)
    result = c.census(duration=1.0, interval=0.5, hostname="joyner",
                      reader=reader,clock=now,sleeper=sleep)
    assert result["status"] == "BOUNDED_SAMPLE_COMPLETE_NOT_CUSTODY_PROOF"
    assert result["samples"] == 3
    assert result["unique_clients_observed"] == 2
    assert result["routes"]["minicpm_lan"]["samples_with_connections"] == 2
    assert result["routes"]["ling_lan"]["unique_client_ips_seen"] == 0
    assert result["routes"]["ling_tailnet"]["connection_observations"] == 2
    assert not result["client_inventory_complete"]


def test_no_connections_never_misreported_as_unused():
    now,sleep = fake_clock()
    result = c.census(duration=1.0, interval=0.5, hostname="joyner",
                      reader=lambda:"",clock=now,sleeper=sleep)
    assert result["samples"] == 3
    assert result["unique_clients_observed"] == 0
    assert result["client_inventory_complete"] is False
    assert result["may_disable_forward"] is False


def test_read_failure_not_a_successful_census():
    now,sleep=fake_clock()
    def fail():
        raise OSError("denied")
    result=c.census(duration=0.5,interval=0.5,hostname="joyner",
                    reader=fail,clock=now,sleeper=sleep)
    assert result["socket_read_errors"] == 2
    assert result["status"] == "OBSERVATIONS_INCOMPLETE"
    assert not result["may_promote_router"]


def test_wrong_host_does_not_poll_or_sleep():
    def forbid(*args):
        raise AssertionError("wrong host must not inspect sockets")
    result=c.census(duration=2.0,interval=0.5,hostname="x1-370",
                    reader=forbid,clock=forbid,sleeper=forbid)
    assert result["status"] == "OBSERVER_NOT_JOYNER"
    assert result["unique_clients_observed"] is None
    assert result["samples"] == 0


@pytest.mark.parametrize("seconds,interval",[
    (0,0.5),(-1,1),(61,0.5),(5,0),(5,6),
])
def test_unbounded_or_invalid_samples_rejected(seconds,interval):
    with pytest.raises(ValueError):
        c.census(duration=seconds,interval=interval,hostname="joyner")


def test_peer_address_never_emitted_in_report(monkeypatch):
    monkeypatch.setattr(c.os,"urandom",lambda count:b"x"*count)
    now,sleep=fake_clock()
    report=c.census(duration=0.1,interval=0.1,hostname="joyner",
                    reader=lambda:SAMPLE,clock=now,sleeper=sleep)
    encoded=json.dumps(report,sort_keys=True)
    assert "192.168.1.222" not in encoded
    assert "100.64.11.22" not in encoded
    assert "client_tags" not in encoded
    assert "x-forwarded" not in encoded.lower()
    assert report["raw_peer_addresses_emitted"] is False
    assert report["model_requests_sent"] is False


def test_cli_returns_denial_even_with_observed_client(monkeypatch,capsys):
    monkeypatch.setattr(c,"census",lambda **args: {"status":"BOUNDED_SAMPLE_COMPLETE_NOT_CUSTODY_PROOF",
                "unique_clients_observed":1,"may_disable_forward":False})
    monkeypatch.setattr("sys.argv",["joyner_forward_consumer_census.py",
                                     "--duration","1.5","--interval","0.5"])
    assert c.main()==78
    body=json.loads(capsys.readouterr().out)
    assert body["may_disable_forward"] is False
