"""Regression tests for fail-closed Pro source connectors in the OSS package."""

from __future__ import annotations

import socket

import pytest

from tokenpak.sources.base import ConnectorConfig
from tokenpak.sources.github import GitHubConnector
from tokenpak.sources.google_drive import GoogleDriveConnector
from tokenpak.sources.notion import NotionConnector


@pytest.mark.parametrize(
    ("connector_type", "source_path"),
    [
        (GitHubConnector, "tokenpak/tokenpak"),
        (GoogleDriveConnector, "drive-root"),
        (NotionConnector, "workspace"),
    ],
)
def test_oss_pro_connector_fails_closed_before_network(
    connector_type: type[GitHubConnector | GoogleDriveConnector | NotionConnector],
    source_path: str,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    network_calls: list[tuple[object, ...]] = []

    def unexpected_network(*args: object, **kwargs: object) -> None:
        network_calls.append((*args, kwargs))
        raise AssertionError("Pro connector attempted network access from the OSS package")

    monkeypatch.setattr(socket, "create_connection", unexpected_network)
    connector = connector_type(
        ConnectorConfig(
            name=connector_type.name,
            source_path=source_path,
            auth_token="not-a-real-token",
        )
    )

    assert connector.tier == "pro"
    assert connector.connect() is False
    assert capsys.readouterr().out == "Pro tier required\n"
    assert network_calls == []
