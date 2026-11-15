"""Tests for the TEMPORARY proxy-header probe: shapes only, never an address."""

import logging

from fastapi.testclient import TestClient

from app.main import app
from app.proxy_probe import kind

client = TestClient(app)
# A public address standing in for a visitor (Google's DNS). Python counts
# the documentation ranges as private, so one of those would read "private".
REAL = "8.8.4.4"


def probe_lines(caplog):
    return [r.getMessage() for r in caplog.records if r.name == "app.proxy_probe"]


def test_nothing_is_logged_without_the_probe_header(caplog):
    with caplog.at_level(logging.INFO):
        client.get("/privacy", headers={"X-Forwarded-For": f"203.0.113.7, {REAL}"})

    assert probe_lines(caplog) == []


def test_the_probe_logs_kinds_and_positions_but_no_address(caplog):
    headers = {
        "X-DoPolitics-Probe": "1",
        "X-Forwarded-For": f"203.0.113.7, {REAL}, 162.158.1.1",
        "CF-Connecting-IP": REAL,
    }
    with caplog.at_level(logging.INFO):
        client.get("/privacy", headers=headers)

    [line] = probe_lines(caplog)
    assert "xff=['probe-fake', 'public', 'cloudflare']" in line
    assert "cf_connecting_ip=xff[1]" in line
    assert "cf-connecting-ip" in line
    for address in ("203.0.113.7", REAL, "162.158.1.1"):
        assert address not in line


def test_kinds():
    assert kind("10.0.0.5") == "private"
    assert kind("104.16.0.1") == "cloudflare"
    assert kind("8.8.8.8") == "public"
    assert kind("::1") == "ipv6"
    assert kind("nonsense") == "not-an-ip"
