# SPDX-License-Identifier: Apache-2.0
"""Load the installed TokenPak release metadata from package resources."""

from __future__ import annotations

import json
import re
from importlib import resources
from typing import Any, Literal, NamedTuple

from tokenpak import __version__

_METADATA_FILE = "version.json"
_MAX_METADATA_BYTES = 4_096
_MAX_VERSION_CHARS = 512
_MAX_PIN_CHARS = 512
_METADATA_KEYS = {
    "tokenpak_version",
    "asserted_tip_version",
    "pinned_registry_schema_tag",
    "pinned_docs_revision",
}
_SEMVER_RE = re.compile(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\Z", re.ASCII)
_TIP_RE = re.compile(r"TIP-(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\Z", re.ASCII)

MetadataFailureReason = Literal["oss_metadata_missing", "oss_metadata_malformed"]


class ReleaseMetadata(NamedTuple):
    """Validated versions asserted by the installed OSS artifact."""

    tokenpak_version: tuple[int, int, int]
    asserted_tip_version: tuple[int, int]


class ReleaseMetadataError(ValueError):
    """A closed-vocabulary failure while reading installed metadata."""

    def __init__(self, reason: MetadataFailureReason) -> None:
        super().__init__(reason)
        self.reason = reason


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _reject_json_constant(_value: str) -> None:
    raise ValueError("non-finite JSON number")


def _strict_json_loads(raw: bytes) -> Any:
    return json.loads(
        raw.decode("utf-8"),
        object_pairs_hook=_reject_duplicate_keys,
        parse_constant=_reject_json_constant,
    )


def _parse_semver(value: object) -> tuple[int, int, int] | None:
    if not isinstance(value, str) or len(value) > _MAX_VERSION_CHARS:
        return None
    match = _SEMVER_RE.fullmatch(value)
    if match is None:
        return None
    return tuple(int(part) for part in match.groups())  # type: ignore[return-value]


def _parse_tip_version(value: object) -> tuple[int, int] | None:
    if not isinstance(value, str) or len(value) > _MAX_VERSION_CHARS:
        return None
    match = _TIP_RE.fullmatch(value)
    if match is None:
        return None
    return tuple(int(part) for part in match.groups())  # type: ignore[return-value]


def load_release_metadata() -> ReleaseMetadata:
    """Return validated metadata from the installed ``tokenpak`` package.

    The package resource is the only runtime source. Missing, malformed, or
    version-disagreeing metadata fails closed with a typed reason.
    """

    try:
        resource = resources.files("tokenpak").joinpath(_METADATA_FILE)
        with resource.open("rb") as handle:
            raw = handle.read(_MAX_METADATA_BYTES + 1)
    except (FileNotFoundError, ModuleNotFoundError):
        raise ReleaseMetadataError("oss_metadata_missing") from None
    except Exception:
        raise ReleaseMetadataError("oss_metadata_malformed") from None

    if len(raw) > _MAX_METADATA_BYTES:
        raise ReleaseMetadataError("oss_metadata_malformed")

    try:
        payload = _strict_json_loads(raw)
    except Exception:
        raise ReleaseMetadataError("oss_metadata_malformed") from None

    if not isinstance(payload, dict) or set(payload) != _METADATA_KEYS:
        raise ReleaseMetadataError("oss_metadata_malformed")

    tokenpak_version = _parse_semver(payload.get("tokenpak_version"))
    asserted_tip_version = _parse_tip_version(payload.get("asserted_tip_version"))
    installed_version = _parse_semver(__version__)
    registry_pin = payload.get("pinned_registry_schema_tag")
    docs_pin = payload.get("pinned_docs_revision")
    if (
        tokenpak_version is None
        or asserted_tip_version is None
        or installed_version is None
        or tokenpak_version != installed_version
        or not isinstance(registry_pin, str)
        or not (1 <= len(registry_pin) <= _MAX_PIN_CHARS)
        or not isinstance(docs_pin, str)
        or not (1 <= len(docs_pin) <= _MAX_PIN_CHARS)
    ):
        raise ReleaseMetadataError("oss_metadata_malformed")

    return ReleaseMetadata(tokenpak_version, asserted_tip_version)


__all__ = ["ReleaseMetadata", "ReleaseMetadataError", "load_release_metadata"]
