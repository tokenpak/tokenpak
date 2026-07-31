"""Keep Docker configuration mounts aligned with the documented runtime path."""

import shlex
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
COMPOSE_PATH = ROOT / "docker-compose.yml"
GUIDE_PATH = ROOT / "docs" / "DOCKER.md"
HOST_CONFIG_PATH = "./config/config.yaml"
CONTAINER_CONFIG_PATH = "/app/config/config.yaml"
CONTAINER_CONFIG_DIRECTORY = "/app/config"
LEGACY_CONTAINER_CONFIG_PATH = "/home/tokenpak/.tokenpak/config.yaml"
GUIDE_FILE_CONFIG_MOUNT = f"$(pwd)/config/config.yaml:{CONTAINER_CONFIG_PATH}:ro"
GUIDE_DIRECTORY_CONFIG_MOUNT = f"$(pwd)/config:{CONTAINER_CONFIG_DIRECTORY}:ro"
GUIDE_CONFIG_MOUNTS = {GUIDE_FILE_CONFIG_MOUNT, GUIDE_DIRECTORY_CONFIG_MOUNT}
GUIDE_CONFIG_SOURCES = {"$(pwd)/config/config.yaml", "$(pwd)/config"}
GUIDE_CONFIG_TARGETS = {CONTAINER_CONFIG_PATH, CONTAINER_CONFIG_DIRECTORY}
DOCKER_OPTIONS_WITH_VALUES = {
    "-e",
    "--env",
    "-m",
    "--memory",
    "--name",
    "-p",
    "--publish",
    "-v",
    "--volume",
}


def _docker_run_commands(guide: str) -> list[str]:
    """Collect complete docker-run commands, including continued lines."""
    commands = []
    lines = guide.splitlines()
    index = 0

    while index < len(lines):
        line = lines[index].strip()
        if not line.startswith("docker run"):
            index += 1
            continue

        command_lines = [line]
        while command_lines[-1].endswith("\\"):
            index += 1
            assert index < len(lines), "unterminated docker run command"
            command_lines.append(lines[index].strip())

        commands.append(" ".join(line.removesuffix("\\").rstrip() for line in command_lines))
        index += 1

    return commands


def _docker_run_options(command: str) -> list[str] | None:
    """Return options for a complete, supported Docker run command."""
    tokens = shlex.split(command)
    if tokens[:2] != ["docker", "run"]:
        return None

    options = []
    index = 2

    while index < len(tokens):
        token = tokens[index]
        if token == "--":
            index += 1
            if index != len(tokens) - 1 or tokens[index].startswith("-"):
                return None
            return options
        if not token.startswith("-"):
            return options if index == len(tokens) - 1 else None

        option, separator, value = token.partition("=")
        if separator:
            if option not in DOCKER_OPTIONS_WITH_VALUES or not value or value.startswith("-"):
                return None
            options.append(token)
            index += 1
            continue

        if token not in DOCKER_OPTIONS_WITH_VALUES or index + 1 >= len(tokens):
            return None
        option_value = tokens[index + 1]
        if option_value.startswith("-"):
            return None
        options.extend((token, option_value))
        index += 2

    return None


def _supported_docker_run_commands(guide: str) -> list[str] | None:
    """Return every command only when the complete guide grammar is supported."""
    commands = _docker_run_commands(guide)
    if not commands or any(_docker_run_options(command) is None for command in commands):
        return None
    return commands


def _tokenpak_config_assignments(command: str) -> list[str]:
    """Return TOKENPAK_CONFIG assignments attached to Docker env options."""
    options = _docker_run_options(command)
    if options is None:
        return []

    assignments = []

    for index, token in enumerate(options):
        if token in {"-e", "--env"}:
            assert index + 1 < len(options), "Docker environment option has no value"
            assignment = options[index + 1]
        elif token.startswith(("-e=", "--env=")):
            assignment = token.partition("=")[2]
        else:
            continue

        if assignment.partition("=")[0] == "TOKENPAK_CONFIG":
            assignments.append(assignment)

    return assignments


def _docker_volume_mounts(command: str) -> list[str]:
    """Return volume mounts attached to Docker options before the image."""
    options = _docker_run_options(command)
    if options is None:
        return []

    mounts = []

    for index, token in enumerate(options):
        if token in {"-v", "--volume"}:
            assert index + 1 < len(options), "Docker volume option has no value"
            mounts.append(options[index + 1])
        elif token.startswith(("-v=", "--volume=")):
            mounts.append(token.partition("=")[2])

    return mounts


def _config_related_mounts(command: str) -> list[str]:
    """Return mounts using a documented config source or container target."""
    mounts = []

    for mount in _docker_volume_mounts(command):
        fields = mount.split(":")
        source = fields[0]
        target = fields[1] if len(fields) > 1 else ""
        if source in GUIDE_CONFIG_SOURCES or target in GUIDE_CONFIG_TARGETS:
            mounts.append(mount)

    return mounts


def _has_config_mount(command: str) -> bool:
    """Return whether a Docker option uses one exact documented config mount."""
    mounts = _config_related_mounts(command)
    return len(mounts) == 1 and mounts[0] in GUIDE_CONFIG_MOUNTS


def _is_config_command(command: str) -> bool:
    """Return whether a command declares a config assignment or related mount."""
    return bool(_tokenpak_config_assignments(command) or _config_related_mounts(command))


def _has_exact_config_binding(command: str) -> bool:
    expected = f"TOKENPAK_CONFIG={CONTAINER_CONFIG_PATH}"
    return _tokenpak_config_assignments(command) == [expected]


def test_docker_config_path_is_coherent():
    compose = yaml.safe_load(COMPOSE_PATH.read_text(encoding="utf-8"))
    service = compose["services"]["tokenpak"]
    expected_mount = f"{HOST_CONFIG_PATH}:{CONTAINER_CONFIG_PATH}:ro"

    assert service["environment"]["TOKENPAK_CONFIG"] == CONTAINER_CONFIG_PATH
    assert service["volumes"].count(expected_mount) == 1

    guide = GUIDE_PATH.read_text(encoding="utf-8")
    assert LEGACY_CONTAINER_CONFIG_PATH not in guide
    assert GUIDE_FILE_CONFIG_MOUNT in guide
    assert GUIDE_DIRECTORY_CONFIG_MOUNT in guide
    assert f"to {CONTAINER_CONFIG_PATH}" in guide

    docker_run_commands = _supported_docker_run_commands(guide)
    assert docker_run_commands is not None
    config_mount_commands = [
        command for command in docker_run_commands if _is_config_command(command)
    ]
    config_mounts = [
        mount for command in config_mount_commands for mount in _config_related_mounts(command)
    ]
    assert len(config_mount_commands) == 3
    assert len(config_mounts) == 3
    assert config_mounts.count(GUIDE_FILE_CONFIG_MOUNT) == 2
    assert config_mounts.count(GUIDE_DIRECTORY_CONFIG_MOUNT) == 1
    assert all(_has_config_mount(command) for command in config_mount_commands)
    assert all(_has_exact_config_binding(command) for command in config_mount_commands)


def test_docker_config_binding_rejects_wrong_or_conflicting_values():
    exact_file_mount = f"-v {GUIDE_FILE_CONFIG_MOUNT}"
    attached_short_exact_binding = (
        "docker run -e=TOKENPAK_CONFIG=/app/config/config.yaml "
        f"-v={GUIDE_FILE_CONFIG_MOUNT} tokenpak"
    )
    attached_short_environment_only = (
        "docker run -e=TOKENPAK_CONFIG=/app/config/config.yaml tokenpak"
    )
    attached_short_volume_only = f"docker run -v={GUIDE_FILE_CONFIG_MOUNT} tokenpak"
    wrong_value = (
        f"docker run -e TOKENPAK_CONFIG=/app/config/config.yaml.bak {exact_file_mount} tokenpak"
    )
    conflicting_values = (
        "docker run -e TOKENPAK_CONFIG=/app/config/config.yaml "
        "-e TOKENPAK_CONFIG=/app/config/config.yaml.bak "
        f"{exact_file_mount} tokenpak"
    )
    attached_short_conflicting_value = (
        "docker run -e TOKENPAK_CONFIG=/app/config/config.yaml "
        "-e=TOKENPAK_CONFIG=/app/config/config.yaml.bak "
        f"{exact_file_mount} tokenpak"
    )
    environment_after_image = (
        f"docker run {exact_file_mount} tokenpak -e TOKENPAK_CONFIG=/app/config/config.yaml"
    )
    volume_after_image = (
        f"docker run -e TOKENPAK_CONFIG=/app/config/config.yaml tokenpak {exact_file_mount}"
    )
    wrong_mount_target = (
        "docker run -e TOKENPAK_CONFIG=/app/config/config.yaml "
        "-v $(pwd)/config/config.yaml:/app/config/config.yaml.bak:ro tokenpak"
    )
    wrong_mount_source = (
        "docker run -e TOKENPAK_CONFIG=/app/config/config.yaml "
        f"-v $(pwd)/config/missing.yaml:{CONTAINER_CONFIG_PATH}:ro tokenpak"
    )
    wrong_mount_mode = (
        "docker run -e TOKENPAK_CONFIG=/app/config/config.yaml "
        f"-v $(pwd)/config/config.yaml:{CONTAINER_CONFIG_PATH}:rw tokenpak"
    )
    conflicting_mount_source = (
        "docker run -e TOKENPAK_CONFIG=/app/config/config.yaml "
        f"{exact_file_mount} "
        f"-v $(pwd)/config/missing.yaml:{CONTAINER_CONFIG_PATH}:ro tokenpak"
    )
    attached_short_conflicting_mount_source = (
        "docker run -e TOKENPAK_CONFIG=/app/config/config.yaml "
        f"{exact_file_mount} "
        f"-v=$(pwd)/config/missing.yaml:{CONTAINER_CONFIG_PATH}:ro tokenpak"
    )
    conflicting_mount_mode = (
        "docker run -e TOKENPAK_CONFIG=/app/config/config.yaml "
        f"{exact_file_mount} "
        f"-v $(pwd)/config/config.yaml:{CONTAINER_CONFIG_PATH}:rw tokenpak"
    )
    duplicate_exact_mount = (
        "docker run -e TOKENPAK_CONFIG=/app/config/config.yaml "
        f"{exact_file_mount} {exact_file_mount} tokenpak"
    )
    config_without_mount = "docker run -e TOKENPAK_CONFIG=/app/config/config.yaml tokenpak"
    post_image_extra_command = (
        f"docker run -e TOKENPAK_CONFIG=/app/config/config.yaml {exact_file_mount} tokenpak extra"
    )
    unknown_option_extra_command = (
        "docker run -e TOKENPAK_CONFIG=/app/config/config.yaml "
        f"{exact_file_mount} --future-option value tokenpak"
    )
    valid_memory_option = (
        f"docker run -e TOKENPAK_CONFIG=/app/config/config.yaml {exact_file_mount} -m 512m tokenpak"
    )
    memory_without_image = (
        f"docker run -e TOKENPAK_CONFIG=/app/config/config.yaml {exact_file_mount} -m 512m"
    )
    name_without_image = (
        "docker run -e TOKENPAK_CONFIG=/app/config/config.yaml "
        f"{exact_file_mount} --name tokenpak-demo"
    )
    unknown_option_without_image = (
        "docker run -e TOKENPAK_CONFIG=/app/config/config.yaml "
        f"{exact_file_mount} --future-option value"
    )
    missing_memory_value = (
        "docker run -e TOKENPAK_CONFIG=/app/config/config.yaml "
        f"{exact_file_mount} -m --name tokenpak"
    )
    missing_environment_value = f"docker run -e {exact_file_mount} tokenpak"
    missing_attached_value = (
        "docker run -e TOKENPAK_CONFIG=/app/config/config.yaml "
        f"{exact_file_mount} --memory= tokenpak"
    )
    trailing_environment_after_valid_options = (
        "docker run -e TOKENPAK_CONFIG=/app/config/config.yaml "
        f"{exact_file_mount} tokenpak "
        "-e TOKENPAK_CONFIG=/app/config/config.yaml"
    )

    assert _has_exact_config_binding(attached_short_exact_binding)
    assert _has_config_mount(attached_short_exact_binding)
    assert _is_config_command(attached_short_environment_only)
    assert _is_config_command(attached_short_volume_only)
    assert not _has_exact_config_binding(wrong_value)
    assert not _has_exact_config_binding(conflicting_values)
    assert not _has_exact_config_binding(attached_short_conflicting_value)
    assert not _has_exact_config_binding(environment_after_image)
    assert not _docker_volume_mounts(volume_after_image)
    assert not _has_config_mount(wrong_mount_target)
    assert not _has_config_mount(wrong_mount_source)
    assert not _has_config_mount(wrong_mount_mode)
    assert len(_config_related_mounts(conflicting_mount_source)) == 2
    assert not _has_config_mount(conflicting_mount_source)
    assert len(_config_related_mounts(attached_short_conflicting_mount_source)) == 2
    assert not _has_config_mount(attached_short_conflicting_mount_source)
    assert len(_config_related_mounts(conflicting_mount_mode)) == 2
    assert not _has_config_mount(conflicting_mount_mode)
    assert len(_config_related_mounts(duplicate_exact_mount)) == 2
    assert not _has_config_mount(duplicate_exact_mount)
    assert _is_config_command(config_without_mount)
    assert not _has_config_mount(config_without_mount)
    assert _supported_docker_run_commands(post_image_extra_command) is None
    assert _supported_docker_run_commands(unknown_option_extra_command) is None
    assert _has_exact_config_binding(valid_memory_option)
    assert _has_config_mount(valid_memory_option)
    assert not _has_exact_config_binding(memory_without_image)
    assert not _has_config_mount(memory_without_image)
    assert not _has_exact_config_binding(name_without_image)
    assert not _has_config_mount(name_without_image)
    assert not _has_exact_config_binding(unknown_option_without_image)
    assert not _has_config_mount(unknown_option_without_image)
    assert not _has_exact_config_binding(missing_memory_value)
    assert not _has_config_mount(missing_memory_value)
    assert not _has_exact_config_binding(missing_environment_value)
    assert not _has_config_mount(missing_environment_value)
    assert not _has_exact_config_binding(missing_attached_value)
    assert not _has_config_mount(missing_attached_value)
    assert not _has_exact_config_binding(trailing_environment_after_valid_options)
    assert not _has_config_mount(trailing_environment_after_valid_options)
