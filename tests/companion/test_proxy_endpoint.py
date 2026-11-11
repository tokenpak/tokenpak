# SPDX-License-Identifier: Apache-2.0
"""The shared proxy address and probe helpers every companion launcher uses."""

from __future__ import annotations

import time

import pytest

from tokenpak.companion import _proxy_endpoint as endpoint

_ENV = (endpoint.PROXY_URL_ENV, endpoint.PROXY_PORT_ENV, endpoint.PROBE_TIMEOUT_ENV)


@pytest.fixture(autouse=True)
def _clean_proxy_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in _ENV:
        monkeypatch.delenv(name, raising=False)


# ---------------------------------------------------------------------------
# Address
# ---------------------------------------------------------------------------


def test_configured_url_wins_over_the_port(monkeypatch):
    monkeypatch.setenv(endpoint.PROXY_URL_ENV, "http://10.0.0.5:54321/")
    monkeypatch.setenv(endpoint.PROXY_PORT_ENV, "9123")
    assert endpoint.proxy_base_url() == "http://10.0.0.5:54321"
    assert endpoint.proxy_port() == 54321


def test_configured_port_is_used_on_the_loopback_address(monkeypatch):
    monkeypatch.setenv(endpoint.PROXY_PORT_ENV, "9123")
    assert endpoint.proxy_base_url() == f"http://{endpoint.DEFAULT_PROXY_HOST}:9123"
    assert endpoint.proxy_port() == 9123


def test_default_address_when_nothing_is_configured():
    expected = f"http://{endpoint.DEFAULT_PROXY_HOST}:{endpoint.DEFAULT_PROXY_PORT}"
    assert endpoint.proxy_base_url() == expected
    assert endpoint.proxy_port() == endpoint.DEFAULT_PROXY_PORT


def test_blank_values_count_as_unset(monkeypatch):
    monkeypatch.setenv(endpoint.PROXY_URL_ENV, "  ")
    monkeypatch.setenv(endpoint.PROXY_PORT_ENV, "")
    assert endpoint.proxy_port() == endpoint.DEFAULT_PROXY_PORT


def test_an_explicit_mapping_is_read_instead_of_the_process_environment(monkeypatch):
    monkeypatch.setenv(endpoint.PROXY_URL_ENV, "http://127.0.0.1:1")
    profile = {endpoint.PROXY_URL_ENV: "http://127.0.0.1:2"}
    assert endpoint.proxy_base_url(profile) == "http://127.0.0.1:2"
    assert endpoint.proxy_port({endpoint.PROXY_PORT_ENV: "3"}) == 3


def test_values_are_read_at_each_call(monkeypatch):
    monkeypatch.setenv(endpoint.PROXY_PORT_ENV, "1111")
    first = endpoint.proxy_base_url()
    monkeypatch.setenv(endpoint.PROXY_PORT_ENV, "2222")
    assert (first, endpoint.proxy_base_url()) == (
        f"http://{endpoint.DEFAULT_PROXY_HOST}:1111",
        f"http://{endpoint.DEFAULT_PROXY_HOST}:2222",
    )


def test_url_without_a_port_starts_on_the_configured_port(monkeypatch):
    monkeypatch.setenv(endpoint.PROXY_URL_ENV, "http://proxy.invalid")
    monkeypatch.setenv(endpoint.PROXY_PORT_ENV, "9124")
    assert endpoint.proxy_port() == 9124


@pytest.mark.parametrize(
    "name,value",
    [
        (endpoint.PROXY_URL_ENV, "http://127.0.0.1:notaport"),
        (endpoint.PROXY_PORT_ENV, "notaport"),
    ],
)
def test_malformed_port_is_an_error(monkeypatch, name, value):
    monkeypatch.setenv(name, value)
    with pytest.raises(ValueError):
        endpoint.proxy_port()


# ---------------------------------------------------------------------------
# Durations
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("raw", ["", "abc", "-1", "nan", "inf"])
def test_invalid_duration_falls_back_to_the_default(monkeypatch, raw):
    monkeypatch.setenv("TOKENPAK_TEST_SECONDS", raw)
    assert endpoint.seconds_setting("TOKENPAK_TEST_SECONDS", 2.5) == 2.5


def test_zero_duration_is_allowed_unless_refused(monkeypatch):
    monkeypatch.setenv("TOKENPAK_TEST_SECONDS", "0")
    assert endpoint.seconds_setting("TOKENPAK_TEST_SECONDS", 2.5) == 0
    assert endpoint.seconds_setting("TOKENPAK_TEST_SECONDS", 2.5, allow_zero=False) == 2.5


def test_probe_timeout_reads_its_setting(monkeypatch):
    assert endpoint.probe_timeout(3.0) == 3.0
    monkeypatch.setenv(endpoint.PROBE_TIMEOUT_ENV, "0.25")
    assert endpoint.probe_timeout(3.0) == 0.25
    monkeypatch.setenv(endpoint.PROBE_TIMEOUT_ENV, "0")
    assert endpoint.probe_timeout(3.0) == 3.0, "a zero timeout could never succeed"
    assert endpoint.probe_timeout(3.0, {endpoint.PROBE_TIMEOUT_ENV: "1.5"}) == 1.5


# ---------------------------------------------------------------------------
# Bounded probe
# ---------------------------------------------------------------------------


def test_bounded_call_returns_a_prompt_result():
    assert endpoint.bounded_call(lambda: "answer", 5.0) == "answer"


def test_bounded_call_treats_an_exception_as_no_answer():
    def fail() -> bool:
        raise OSError("refused")

    assert endpoint.bounded_call(fail, 5.0) is None


def test_bounded_call_returns_at_the_limit_while_the_call_still_runs():
    def slow() -> bool:
        time.sleep(2.0)
        return True

    started = time.monotonic()
    assert endpoint.bounded_call(slow, 0.05) is None
    assert time.monotonic() - started < 1.0
