"""DispatchWorker record (Standards Delta v0 §5.1)."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, field_validator

from .common import DispatchBaseModel, WorkerLoopDefault, _validate_capability_list
from .enums import ModifyFilesPolicy, RunCommandsPolicy


class WorkerPermissionProfile(DispatchBaseModel):
    """DispatchWorker.permission_profile (Standards Delta v0 §5.1).

    ``install_dependencies`` is always ``False`` in v0.1-alpha (external side
    effects are forbidden — §5.5).
    """

    read_files: bool = True
    modify_files: ModifyFilesPolicy = ModifyFilesPolicy.POLICY_CONTROLLED
    run_commands: RunCommandsPolicy = RunCommandsPolicy.POLICY_CONTROLLED
    install_dependencies: bool = Field(
        default=False, description="v0.1-alpha: always false"
    )


class DispatchWorker(DispatchBaseModel):
    """A registry-loaded TIP worker profile (Standards Delta v0 §5.1).

    ``capabilities`` is registry-bound: the loader rejects any string not in
    the §5.2 capability registry at construction time (fail-loud, per §5.2
    governance rule). ``kind`` is the fixed literal ``"tip_worker_profile"``.
    """

    id: str = Field(description='e.g. "worker.builder.default.v1"')
    kind: Literal["tip_worker_profile"] = "tip_worker_profile"

    roles: list[str] = Field(default_factory=list, description='e.g. ["builder"]')
    capabilities: list[str] = Field(
        default_factory=list, description="registry-bound; see §5.2"
    )

    system_directives: list[str] = Field(
        default_factory=list, description="base prompt; overlays additively append"
    )

    allowed_tools: list[str] = Field(
        default_factory=list, description="registry-bound; see §5.3"
    )
    input_schema: str = Field(description='e.g. "station_input.v1"')
    output_schema: str = Field(description='e.g. "station_result.v1"')

    default_loop_policy: WorkerLoopDefault
    permission_profile: WorkerPermissionProfile

    @field_validator("capabilities")
    @classmethod
    def _check_capabilities(cls, value: list[str]) -> list[str]:
        return _validate_capability_list(value)


__all__ = [
    "WorkerPermissionProfile",
    "DispatchWorker",
]
