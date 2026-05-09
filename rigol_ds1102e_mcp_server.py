#!/usr/bin/env python3
# /// script
# dependencies = [
#   "mcp[cli]",
# ]
# ///

from __future__ import annotations

from datetime import datetime, timezone
import glob
from typing import Any

from mcp.server.fastmcp import FastMCP

from rigol_ds1102e import RigolDS1102E
from rigol_ds1102e_protocol import PROTOCOL, get_command, is_excluded_command, render_command


DEFAULT_GLOB_PATTERNS = (
    "/dev/usbtmc*",
    "/dev/usbmisc/usbtmc*",
    "/dev/usb/usbtmc*",
)

mcp = FastMCP("rigol_ds1102e", json_response=True)
_SCOPE_SETUP_CACHE: dict[str, dict[str, Any]] = {}

SNAPSHOT_CHANNEL_KEYS = (
    ("display", "channel_display_get"),
    ("coupling", "channel_coupling_get"),
    ("probe", "channel_probe_get"),
    ("scale", "channel_scale_get"),
    ("offset", "channel_offset_get"),
)

SNAPSHOT_TIMEBASE_KEYS = (
    ("scale", "timebase_scale_get"),
    ("offset", "timebase_offset_get"),
)

SNAPSHOT_ACQUIRE_KEYS = (
    ("type", "acquire_type_get"),
    ("mode", "acquire_mode_get"),
    ("averages", "acquire_averages_get"),
    ("memory_depth", "acquire_memory_depth_get"),
)

PROFILE_CHANNEL_SETTERS = {
    "display": ("channel_display_set", "state"),
    "coupling": ("channel_coupling_set", "coupling"),
    "probe": ("channel_probe_set", "probe"),
    "scale": ("channel_scale_set", "scale"),
    "offset": ("channel_offset_set", "offset"),
}

PROFILE_TIMEBASE_SETTERS = {
    "scale": ("timebase_scale_set", "scale"),
    "offset": ("timebase_offset_set", "offset"),
}

PROFILE_ACQUIRE_SETTERS = {
    "type": ("acquire_type_set", "acquire_type"),
    "mode": ("acquire_mode_set", "mode"),
    "averages": ("acquire_averages_set", "count"),
    "memory_depth": ("acquire_memory_depth_set", "depth"),
}

PROFILE_WAVEFORM_SETTERS = {
    "points_mode": "waveform_points_mode_set",
}


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def list_candidate_devices() -> list[str]:
    devices: list[str] = []
    for pattern in DEFAULT_GLOB_PATTERNS:
        devices.extend(glob.glob(pattern))
    return sorted(set(devices))


def build_scope(device: str, delay: float, read_size: int) -> RigolDS1102E:
    return RigolDS1102E(device=device, query_delay=delay, read_size=read_size)


def is_supported_protocol_command(key: str) -> bool:
    command = PROTOCOL[key]
    return not is_excluded_command(command.template)


def query_protocol_value(
    scope: RigolDS1102E,
    key: str,
    params: dict[str, Any] | None = None,
    delay: float = 0.2,
    read_size: int = 4096,
) -> str:
    scpi = render_command(key, **(params or {}))
    if is_excluded_command(scpi):
        raise ValueError(f"protocol command is excluded: {key}")
    return scope.query(scpi, delay=delay, read_size=read_size)


def write_protocol_value(
    scope: RigolDS1102E,
    key: str,
    params: dict[str, Any] | None = None,
) -> str:
    scpi = render_command(key, **(params or {}))
    if is_excluded_command(scpi):
        raise ValueError(f"protocol command is excluded: {key}")
    scope.write(scpi)
    return scpi


def mark_cache_stale(device: str, reason: str) -> None:
    cached = _SCOPE_SETUP_CACHE.get(device)
    if cached is not None:
        cached["cache"] = {
            "state": "stale",
            "reason": reason,
            "timestamp": utc_timestamp(),
        }


def build_setup_snapshot(
    device: str,
    delay: float,
    read_size: int,
    channels: list[int],
) -> dict[str, Any]:
    scope = build_scope(device, delay, read_size)
    snapshot: dict[str, Any] = {
        "timestamp": utc_timestamp(),
        "device": device,
        "identity": scope.identify(),
        "channels": {},
        "timebase": {},
        "trigger": {},
        "acquire": {},
        "waveform": {},
        "session": {},
        "cache": {
            "state": "fresh",
            "reason": "snapshot_refresh",
            "timestamp": utc_timestamp(),
        },
    }

    visible_channels: list[int] = []
    for channel in channels:
        channel_settings: dict[str, str] = {}
        for setting_name, command_key in SNAPSHOT_CHANNEL_KEYS:
            channel_settings[setting_name] = query_protocol_value(
                scope,
                command_key,
                {"channel": channel},
                delay,
                read_size,
            )
        if channel_settings.get("display") == "ON":
            visible_channels.append(channel)
        snapshot["channels"][str(channel)] = channel_settings

    for setting_name, command_key in SNAPSHOT_TIMEBASE_KEYS:
        snapshot["timebase"][setting_name] = query_protocol_value(
            scope,
            command_key,
            delay=delay,
            read_size=read_size,
        )

    trigger_mode = query_protocol_value(scope, "trigger_mode_get", delay=delay, read_size=read_size)
    snapshot["trigger"]["mode"] = trigger_mode
    snapshot["trigger"]["source"] = query_protocol_value(
        scope,
        "trigger_source_get",
        {"mode": trigger_mode},
        delay,
        read_size,
    )
    snapshot["trigger"]["level"] = query_protocol_value(
        scope,
        "trigger_level_get",
        {"mode": trigger_mode},
        delay,
        read_size,
    )
    snapshot["trigger"]["sweep"] = query_protocol_value(
        scope,
        "trigger_sweep_get",
        {"mode": trigger_mode},
        delay,
        read_size,
    )
    snapshot["trigger"]["holdoff"] = query_protocol_value(
        scope,
        "trigger_holdoff_get",
        delay=delay,
        read_size=read_size,
    )

    for setting_name, command_key in SNAPSHOT_ACQUIRE_KEYS:
        snapshot["acquire"][setting_name] = query_protocol_value(
            scope,
            command_key,
            delay=delay,
            read_size=read_size,
        )
    snapshot["acquire"]["sampling_rate"] = {}
    for channel in visible_channels:
        snapshot["acquire"]["sampling_rate"][str(channel)] = query_protocol_value(
            scope,
            "acquire_sampling_rate_get",
            {"channel": channel},
            delay,
            read_size,
        )

    snapshot["waveform"]["points_mode"] = query_protocol_value(
        scope,
        "waveform_points_mode_get",
        delay=delay,
        read_size=read_size,
    )
    snapshot["session"]["trigger_status"] = query_protocol_value(
        scope,
        "trigger_status",
        delay=delay,
        read_size=read_size,
    )
    _SCOPE_SETUP_CACHE[device] = snapshot
    return snapshot


def normalize_channels(channels: list[int] | None) -> list[int]:
    selected = channels or [1, 2]
    normalized = []
    for channel in selected:
        value = int(channel)
        if value not in (1, 2):
            raise ValueError(f"channel must be 1 or 2, got {value}")
        if value not in normalized:
            normalized.append(value)
    return normalized


@mcp.tool()
def rigol_ds1102e_list_devices() -> dict[str, Any]:
    """List likely USBTMC device nodes for the Rigol DS1102E."""
    return {
        "timestamp": utc_timestamp(),
        "devices": list_candidate_devices(),
        "patterns": list(DEFAULT_GLOB_PATTERNS),
    }


@mcp.tool()
def rigol_ds1102e_identify(
    device: str = "/dev/usbtmc0",
    delay: float = 0.2,
    read_size: int = 4096,
) -> dict[str, Any]:
    """Query *IDN? from the scope."""
    scope = build_scope(device, delay, read_size)
    return {
        "timestamp": utc_timestamp(),
        "device": device,
        "response": scope.identify(),
    }


@mcp.tool()
def rigol_ds1102e_query(
    scpi: str,
    device: str = "/dev/usbtmc0",
    delay: float = 0.2,
    read_size: int = 4096,
) -> dict[str, Any]:
    """Send a SCPI query and return the response."""
    scope = build_scope(device, delay, read_size)
    return {
        "timestamp": utc_timestamp(),
        "device": device,
        "scpi": scpi,
        "response": scope.query(scpi, delay=delay, read_size=read_size),
    }


@mcp.tool()
def rigol_ds1102e_write(
    scpi: str,
    device: str = "/dev/usbtmc0",
) -> dict[str, Any]:
    """Send a SCPI command that does not expect a response."""
    scope = build_scope(device, delay=0.2, read_size=4096)
    scope.write(scpi)
    mark_cache_stale(device, f"raw write: {scpi}")
    return {
        "timestamp": utc_timestamp(),
        "device": device,
        "scpi": scpi,
        "status": "ok",
    }


@mcp.tool()
def rigol_ds1102e_list_protocol_commands() -> dict[str, Any]:
    """List supported protocol command keys from the existing registry."""
    commands = []
    for key, command in sorted(PROTOCOL.items()):
        if not is_supported_protocol_command(key):
            continue
        commands.append(
            {
                "key": command.key,
                "family": command.family,
                "kind": command.kind,
                "description": command.description,
                "args": list(command.args),
                "response_hint": command.response_hint,
                "requires_visible_channel": command.requires_visible_channel,
                "changes_scope_state": command.changes_scope_state,
            }
        )
    return {
        "timestamp": utc_timestamp(),
        "commands": commands,
    }


@mcp.tool()
def rigol_ds1102e_snapshot_get(
    device: str = "/dev/usbtmc0",
    delay: float = 0.2,
    read_size: int = 4096,
    channels: list[int] | None = None,
) -> dict[str, Any]:
    """Return the cached setup snapshot, refreshing from the scope if missing or stale."""
    cached = _SCOPE_SETUP_CACHE.get(device)
    if cached is not None and cached.get("cache", {}).get("state") == "fresh":
        return {
            "timestamp": utc_timestamp(),
            "device": device,
            "source": "cache",
            "snapshot": cached,
        }
    return {
        "timestamp": utc_timestamp(),
        "device": device,
        "source": "scope",
        "snapshot": build_setup_snapshot(device, delay, read_size, normalize_channels(channels)),
    }


@mcp.tool()
def rigol_ds1102e_snapshot_cached(
    device: str = "/dev/usbtmc0",
) -> dict[str, Any]:
    """Return the last stored setup snapshot without querying the scope."""
    cached = _SCOPE_SETUP_CACHE.get(device)
    return {
        "timestamp": utc_timestamp(),
        "device": device,
        "found": cached is not None,
        "snapshot": cached,
    }


@mcp.tool()
def rigol_ds1102e_snapshot_refresh(
    device: str = "/dev/usbtmc0",
    delay: float = 0.2,
    read_size: int = 4096,
    channels: list[int] | None = None,
) -> dict[str, Any]:
    """Read a full setup snapshot from the scope and store it in the server cache."""
    return {
        "timestamp": utc_timestamp(),
        "device": device,
        "source": "scope",
        "snapshot": build_setup_snapshot(device, delay, read_size, normalize_channels(channels)),
    }


@mcp.tool()
def rigol_ds1102e_apply_profile(
    profile: dict[str, Any],
    device: str = "/dev/usbtmc0",
    delay: float = 0.2,
    read_size: int = 4096,
    refresh_after: bool = True,
) -> dict[str, Any]:
    """Apply multiple setup changes in one request and refresh the setup cache."""
    if "snapshot" in profile and isinstance(profile["snapshot"], dict):
        profile = profile["snapshot"]
    scope = build_scope(device, delay, read_size)
    writes: list[dict[str, Any]] = []

    for channel_key, settings in profile.get("channels", {}).items():
        channel = int(channel_key)
        if channel not in (1, 2):
            raise ValueError(f"channel must be 1 or 2, got {channel}")
        for setting_name, value in settings.items():
            mapped = PROFILE_CHANNEL_SETTERS.get(setting_name)
            if mapped is None:
                raise ValueError(f"unsupported channel setting: {setting_name}")
            command_key, param_name = mapped
            params = {"channel": channel, param_name: value}
            scpi = write_protocol_value(scope, command_key, params)
            writes.append({"key": command_key, "params": params, "scpi": scpi})

    for setting_name, value in profile.get("timebase", {}).items():
        mapped = PROFILE_TIMEBASE_SETTERS.get(setting_name)
        if mapped is None:
            raise ValueError(f"unsupported timebase setting: {setting_name}")
        command_key, param_name = mapped
        params = {param_name: value}
        scpi = write_protocol_value(scope, command_key, params)
        writes.append({"key": command_key, "params": params, "scpi": scpi})

    trigger_settings = profile.get("trigger", {})
    trigger_mode = trigger_settings.get("mode")
    if trigger_mode is None:
        cached = _SCOPE_SETUP_CACHE.get(device)
        trigger_mode = (cached or {}).get("trigger", {}).get("mode", "EDGE")
    if "mode" in trigger_settings:
        params = {"mode": trigger_settings["mode"]}
        scpi = write_protocol_value(scope, "trigger_mode_set", params)
        writes.append({"key": "trigger_mode_set", "params": params, "scpi": scpi})
        trigger_mode = trigger_settings["mode"]
    for setting_name, command_key in (
        ("source", "trigger_source_set"),
        ("level", "trigger_level_set"),
        ("sweep", "trigger_sweep_set"),
    ):
        if setting_name in trigger_settings:
            params = {"mode": trigger_mode, setting_name: trigger_settings[setting_name]}
            scpi = write_protocol_value(scope, command_key, params)
            writes.append({"key": command_key, "params": params, "scpi": scpi})
    if "holdoff" in trigger_settings:
        params = {"holdoff": trigger_settings["holdoff"]}
        scpi = write_protocol_value(scope, "trigger_holdoff_set", params)
        writes.append({"key": "trigger_holdoff_set", "params": params, "scpi": scpi})

    for setting_name, value in profile.get("acquire", {}).items():
        mapped = PROFILE_ACQUIRE_SETTERS.get(setting_name)
        if mapped is None:
            if setting_name == "sampling_rate":
                continue
            raise ValueError(f"unsupported acquire setting: {setting_name}")
        command_key, param_name = mapped
        params = {param_name: value}
        scpi = write_protocol_value(scope, command_key, params)
        writes.append({"key": command_key, "params": params, "scpi": scpi})

    for setting_name, value in profile.get("waveform", {}).items():
        command_key = PROFILE_WAVEFORM_SETTERS.get(setting_name)
        if command_key is None:
            raise ValueError(f"unsupported waveform setting: {setting_name}")
        params = {setting_name: value}
        scpi = write_protocol_value(scope, command_key, params)
        writes.append({"key": command_key, "params": params, "scpi": scpi})

    session_settings = profile.get("session", {})
    for setting_name, command_key in (("run", "run"), ("stop", "stop"), ("force_trigger", "force_trigger")):
        if session_settings.get(setting_name):
            scpi = write_protocol_value(scope, command_key)
            writes.append({"key": command_key, "params": {}, "scpi": scpi})

    if refresh_after:
        snapshot = build_setup_snapshot(device, delay, read_size, [1, 2])
        cache_state = "fresh"
    else:
        mark_cache_stale(device, "profile applied without refresh")
        snapshot = _SCOPE_SETUP_CACHE.get(device)
        cache_state = "stale"

    return {
        "timestamp": utc_timestamp(),
        "device": device,
        "status": "ok",
        "writes": writes,
        "cache_state": cache_state,
        "snapshot": snapshot,
    }


@mcp.tool()
def rigol_ds1102e_protocol_command(
    key: str,
    params: dict[str, Any] | None = None,
    device: str = "/dev/usbtmc0",
    delay: float = 0.2,
    read_size: int = 4096,
) -> dict[str, Any]:
    """Execute a supported protocol command by key using the registry metadata."""
    command = get_command(key)
    scpi = render_command(key, **(params or {}))
    if is_excluded_command(scpi):
        raise ValueError(f"protocol command is excluded: {key}")

    scope = build_scope(device, delay, read_size)
    result: dict[str, Any] = {
        "timestamp": utc_timestamp(),
        "device": device,
        "key": command.key,
        "family": command.family,
        "kind": command.kind,
        "scpi": scpi,
    }
    if command.kind == "query":
        result["response"] = scope.query(scpi, delay=delay, read_size=read_size)
        return result

    scope.write(scpi)
    result["status"] = "ok"
    if command.changes_scope_state:
        mark_cache_stale(device, f"protocol write: {command.key}")
        result["cache_state"] = "stale"
    return result


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
