#!/usr/bin/env python3
# /// 'uv' inline script metadependencies
# dependencies = [
#   "mcp[cli]",
#   "numpy",
# ]
# ///

import base64
from datetime import datetime, timezone
import glob
import os
from pathlib import Path
import threading
import time
from typing import Any

from mcp.server.fastmcp import FastMCP

from rigol_ds1102e import RigolDS1102E
from rigol_ds1102e_protocol import PROTOCOL, get_command, is_excluded_command, render_command
from rigol_ds1102e_spi_analysis import (
    decode_spi_data_words,
    decode_spi_data_words_windowed,
    detect_rising_edge_sample_indexes,
    normalize_waveform_samples,
    validate_expected_addresses,
)


DEFAULT_GLOB_PATTERNS = (
    "/dev/usbtmc*",
    "/dev/usbmisc/usbtmc*",
    "/dev/usb/usbtmc*",
)
DEFAULT_DEVICE = "/dev/usbtmc0"
RIGOL_VENDOR_ID = "1ab1"

mcp = FastMCP("rigol_ds1102e", json_response=True)

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


def read_sysfs_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="ascii").strip()
    except OSError:
        return None


def find_usb_device_dir(sysfs_path: Path) -> Path | None:
    for parent in (sysfs_path.resolve(), *sysfs_path.resolve().parents):
        if (parent / "idVendor").exists() and (parent / "idProduct").exists():
            return parent
    return None


def build_usbtmc_record(sysfs_path: Path) -> dict[str, Any]:
    device = f"/dev/{sysfs_path.name}"
    record: dict[str, Any] = {
        "device": device,
        "sysfs": str(sysfs_path),
    }
    usb_device_dir = find_usb_device_dir(sysfs_path)
    if usb_device_dir is None:
        return record

    for key in ("idVendor", "idProduct", "manufacturer", "product", "serial"):
        value = read_sysfs_text(usb_device_dir / key)
        if value is not None:
            record[key] = value
    record["usb_path"] = usb_device_dir.name
    return record


def list_candidate_device_records() -> list[dict[str, Any]]:
    records: dict[str, dict[str, Any]] = {}
    for sysfs_path in sorted(Path("/sys/class/usbmisc").glob("usbtmc*")):
        record = build_usbtmc_record(sysfs_path)
        records[record["device"]] = record

    for device in list_candidate_devices():
        records.setdefault(device, {"device": device})

    return [records[device] for device in sorted(records)]


def list_candidate_devices() -> list[str]:
    devices: list[str] = []
    for pattern in DEFAULT_GLOB_PATTERNS:
        devices.extend(glob.glob(pattern))
    return sorted(set(devices))


class ManagedScopeState:
    def __init__(self) -> None:
        self.scope_setup_cache: dict[str, dict[str, Any]] = {}
        self.active_device: str | None = None
        self.active_device_fd: int | None = None
        self.device_fd_lock = threading.Lock()

    def resolve_default_device(self) -> str:
        for record in list_candidate_device_records():
            if record.get("idVendor", "").lower() == RIGOL_VENDOR_ID:
                return str(record["device"])
        return DEFAULT_DEVICE

    def resolve_device(self, device: str) -> str:
        if device == DEFAULT_DEVICE:
            return self.resolve_default_device()
        return device

    def build_scope(self, device: str, delay: float, read_size: int) -> RigolDS1102E:
        device = self.resolve_device(device)
        return RigolDS1102E(device=device, query_delay=delay, read_size=read_size)

    def active_device_fd_for_scope(self, scope: RigolDS1102E) -> int:
        if self.active_device_fd is not None and self.active_device != scope.device:
            os.close(self.active_device_fd)
            self.active_device_fd = None
            self.active_device = None
        if self.active_device_fd is None:
            self.active_device_fd = scope._open_device()
            self.active_device = scope.device
        return self.active_device_fd

    def close_active_device_fd(self) -> None:
        if self.active_device_fd is not None:
            os.close(self.active_device_fd)
        self.active_device_fd = None
        self.active_device = None

    def rebuild_scope(self, scope: RigolDS1102E) -> RigolDS1102E:
        if scope.device == DEFAULT_DEVICE:
            return self.build_scope(DEFAULT_DEVICE, scope.query_delay, scope.read_size)
        return scope

    def scope_write(self, scope: RigolDS1102E, scpi: str) -> None:
        payload = scope._normalize_command(scpi)
        with self.device_fd_lock:
            try:
                os.write(self.active_device_fd_for_scope(scope), payload)
            except OSError:
                self.close_active_device_fd()
                scope = self.rebuild_scope(scope)
                os.write(self.active_device_fd_for_scope(scope), payload)

    def scope_query_bytes(self, scope: RigolDS1102E, scpi: str, delay: float, read_size: int) -> bytes:
        payload = scope._normalize_command(scpi)
        with self.device_fd_lock:
            try:
                fd = self.active_device_fd_for_scope(scope)
                os.write(fd, payload)
                time.sleep(delay)
                return os.read(fd, read_size)
            except OSError:
                self.close_active_device_fd()
                scope = self.rebuild_scope(scope)
                fd = self.active_device_fd_for_scope(scope)
                os.write(fd, payload)
                time.sleep(delay)
                return os.read(fd, read_size)

    def scope_query(self, scope: RigolDS1102E, scpi: str, delay: float, read_size: int) -> str:
        response = self.scope_query_bytes(scope, scpi, delay, read_size)
        return response.decode("ascii", "replace").replace("\x00", "").strip()

    def query_raw_bytes(self, scope: RigolDS1102E, scpi: str, delay: float, read_size: int) -> bytes:
        return self.scope_query_bytes(scope, scpi, delay, read_size).rstrip(b"\n")

    def identify(self, device: str, delay: float, read_size: int) -> dict[str, Any]:
        scope = self.build_scope(device, delay, read_size)
        return {
            "timestamp": utc_timestamp(),
            "device": scope.device,
            "response": self.scope_query(scope, "*IDN?", delay, read_size),
        }

    def query(self, scpi: str, device: str, delay: float, read_size: int) -> dict[str, Any]:
        scope = self.build_scope(device, delay, read_size)
        return {
            "timestamp": utc_timestamp(),
            "device": scope.device,
            "scpi": scpi,
            "response": self.scope_query(scope, scpi, delay, read_size),
        }

    def write(self, scpi: str, device: str) -> dict[str, Any]:
        scope = self.build_scope(device, delay=0.2, read_size=4096)
        self.scope_write(scope, scpi)
        device = scope.device
        self.mark_cache_stale(device, f"raw write: {scpi}")
        return {
            "timestamp": utc_timestamp(),
            "device": device,
            "scpi": scpi,
            "status": "ok",
        }

    def get_cached_snapshot(self, device: str) -> tuple[str, dict[str, Any] | None]:
        device = self.resolve_device(device)
        return device, self.scope_setup_cache.get(device)

    def build_setup_snapshot(
        self,
        device: str,
        delay: float,
        read_size: int,
        channels: list[int],
    ) -> dict[str, Any]:
        scope = self.build_scope(device, delay, read_size)
        device = scope.device
        snapshot: dict[str, Any] = {
            "timestamp": utc_timestamp(),
            "device": device,
            "identity": self.scope_query(scope, "*IDN?", delay, read_size),
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
        self.scope_setup_cache[device] = snapshot
        return snapshot

    def snapshot_get(
        self,
        device: str,
        delay: float,
        read_size: int,
        channels: list[int],
    ) -> dict[str, Any]:
        device, cached = self.get_cached_snapshot(device)
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
            "snapshot": self.build_setup_snapshot(device, delay, read_size, channels),
        }

    def snapshot_cached(self, device: str) -> dict[str, Any]:
        device, cached = self.get_cached_snapshot(device)
        return {
            "timestamp": utc_timestamp(),
            "device": device,
            "found": cached is not None,
            "snapshot": cached,
        }

    def snapshot_refresh(
        self,
        device: str,
        delay: float,
        read_size: int,
        channels: list[int],
    ) -> dict[str, Any]:
        device = self.resolve_device(device)
        return {
            "timestamp": utc_timestamp(),
            "device": device,
            "source": "scope",
            "snapshot": self.build_setup_snapshot(device, delay, read_size, channels),
        }

    def apply_profile(
        self,
        profile: dict[str, Any],
        device: str,
        delay: float,
        read_size: int,
        refresh_after: bool,
    ) -> dict[str, Any]:
        if "snapshot" in profile and isinstance(profile["snapshot"], dict):
            profile = profile["snapshot"]
        scope = self.build_scope(device, delay, read_size)
        device = scope.device
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
            _, cached = self.get_cached_snapshot(device)
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
            snapshot = self.build_setup_snapshot(device, delay, read_size, [1, 2])
            cache_state = "fresh"
        else:
            self.mark_cache_stale(device, "profile applied without refresh")
            _, snapshot = self.get_cached_snapshot(device)
            cache_state = "stale"

        return {
            "timestamp": utc_timestamp(),
            "device": device,
            "status": "ok",
            "writes": writes,
            "cache_state": cache_state,
            "snapshot": snapshot,
        }

    def scope_setup(
        self,
        device: str,
        channels: list[int],
        trigger_mode: str,
        sweep: str,
        points_mode: str,
        run: bool,
        delay: float,
        read_size: int,
    ) -> dict[str, Any]:
        scope = self.build_scope(device, delay, read_size)
        device = scope.device
        writes: list[dict[str, Any]] = []

        for channel in channels:
            params = {"channel": channel, "state": "ON"}
            scpi = write_protocol_value(scope, "channel_display_set", params)
            writes.append({"key": "channel_display_set", "params": params, "scpi": scpi})

        for key, params in (
            ("trigger_mode_set", {"mode": trigger_mode}),
            ("trigger_sweep_set", {"mode": trigger_mode, "sweep": sweep}),
            ("waveform_points_mode_set", {"points_mode": points_mode}),
        ):
            scpi = write_protocol_value(scope, key, params)
            writes.append({"key": key, "params": params, "scpi": scpi})

        if run:
            scpi = write_protocol_value(scope, "run")
            writes.append({"key": "run", "params": {}, "scpi": scpi})

        self.mark_cache_stale(device, "scope setup applied")
        return {
            "timestamp": utc_timestamp(),
            "device": device,
            "status": "ok",
            "writes": writes,
            "cache_state": "stale",
        }

    def protocol_command(
        self,
        key: str,
        params: dict[str, Any] | None,
        device: str,
        delay: float,
        read_size: int,
    ) -> dict[str, Any]:
        command = get_command(key)
        scpi = render_command(key, **(params or {}))
        if is_excluded_command(scpi):
            raise ValueError(f"protocol command is excluded: {key}")

        scope = self.build_scope(device, delay, read_size)
        device = scope.device
        result: dict[str, Any] = {
            "timestamp": utc_timestamp(),
            "device": device,
            "key": command.key,
            "family": command.family,
            "kind": command.kind,
            "scpi": scpi,
        }
        if command.kind == "query":
            result["response"] = self.scope_query(scope, scpi, delay, read_size)
            return result

        self.scope_write(scope, scpi)
        result["status"] = "ok"
        if command.changes_scope_state:
            self.mark_cache_stale(device, f"protocol write: {command.key}")
            result["cache_state"] = "stale"
        return result

    def mark_cache_stale(self, device: str, reason: str) -> None:
        cached = self.scope_setup_cache.get(device)
        if cached is not None:
            cached["cache"] = {
                "state": "stale",
                "reason": reason,
                "timestamp": utc_timestamp(),
            }


_MANAGED_SCOPE = ManagedScopeState()


def resolve_default_device() -> str:
    return _MANAGED_SCOPE.resolve_default_device()


def build_scope(device: str, delay: float, read_size: int) -> RigolDS1102E:
    return _MANAGED_SCOPE.build_scope(device, delay, read_size)


def scope_write(scope: RigolDS1102E, scpi: str) -> None:
    _MANAGED_SCOPE.scope_write(scope, scpi)


def scope_query(scope: RigolDS1102E, scpi: str, delay: float, read_size: int) -> str:
    return _MANAGED_SCOPE.scope_query(scope, scpi, delay, read_size)


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
    return scope_query(scope, scpi, delay, read_size)


def write_protocol_value(
    scope: RigolDS1102E,
    key: str,
    params: dict[str, Any] | None = None,
) -> str:
    scpi = render_command(key, **(params or {}))
    if is_excluded_command(scpi):
        raise ValueError(f"protocol command is excluded: {key}")
    scope_write(scope, scpi)
    return scpi


def query_raw_bytes(
    scope: RigolDS1102E,
    scpi: str,
    delay: float,
    read_size: int,
) -> bytes:
    return _MANAGED_SCOPE.query_raw_bytes(scope, scpi, delay, read_size)


def query_waveform_bytes(
    scope: RigolDS1102E,
    scpi: str,
    delay: float,
    read_size: int,
) -> bytes:
    data = query_raw_bytes(scope, scpi, delay, read_size)
    for _ in range(4):
        if len(data) != 600 or read_size <= 600:
            break
        reread = query_raw_bytes(scope, scpi, delay, read_size)
        if len(reread) > len(data):
            data = reread
        elif len(reread) != 600:
            data = reread
            break
    return data


def require_stopped_scope(scope: RigolDS1102E, delay: float, read_size: int, attempts: int = 20) -> str:
    status = ""
    for _ in range(attempts):
        status = query_protocol_value(scope, "trigger_status", delay=delay, read_size=read_size).upper()
        if status == "STOP":
            return status
        if status == "WAIT":
            write_protocol_value(scope, "stop")
    raise RuntimeError(f"scope did not enter STOP state after :STOP; last status was {status!r}")


def encode_waveform_data(data: bytes, encoding: str) -> Any:
    if encoding == "list":
        return list(data)
    if encoding == "base64":
        return base64.b64encode(data).decode("ascii")
    if encoding == "hex":
        return data.hex()
    if encoding == "none":
        return None
    raise ValueError("encoding must be one of: list, base64, hex, none")


def summarize_waveform_data(data: bytes) -> dict[str, Any]:
    return {
        "length": len(data),
        "minimum": min(data) if data else None,
        "maximum": max(data) if data else None,
        "unique_values": len(set(data)),
        "first_16_hex": data[:16].hex(" "),
    }


def capture_waveform_channels(
    scope: RigolDS1102E,
    selected_channels: list[int],
    freeze: bool,
    points_mode: str,
    delay: float,
    read_size: int,
) -> tuple[list[dict[str, Any]], dict[str, bytes]]:
    writes: list[dict[str, Any]] = []

    if freeze:
        scpi = write_protocol_value(scope, "stop")
        writes.append({"key": "stop", "params": {}, "scpi": scpi})
        require_stopped_scope(scope, delay, read_size)

    params = {"points_mode": points_mode}
    scpi = write_protocol_value(scope, "waveform_points_mode_set", params)
    writes.append({"key": "waveform_points_mode_set", "params": params, "scpi": scpi})

    captured_channels: dict[str, bytes] = {}
    for channel in selected_channels:
        scpi = render_command("waveform_data_get", channel=channel)
        captured_channels[str(channel)] = query_waveform_bytes(scope, scpi, delay, read_size)

    return writes, captured_channels


def mark_cache_stale(device: str, reason: str) -> None:
    _MANAGED_SCOPE.mark_cache_stale(device, reason)


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
def list_ports() -> dict[str, Any]:
    """List likely USBTMC device nodes for the Rigol DS1102E."""
    return {
        "timestamp": utc_timestamp(),
        "devices": list_candidate_devices(),
        "device_records": list_candidate_device_records(),
        "resolved_default_device": resolve_default_device(),
        "patterns": list(DEFAULT_GLOB_PATTERNS),
    }


@mcp.tool()
def rigol_ds1102e_identify(
    device: str = "/dev/usbtmc0",
    delay: float = 0.2,
    read_size: int = 4096,
) -> dict[str, Any]:
    """Query *IDN? from the scope."""
    return _MANAGED_SCOPE.identify(device, delay, read_size)


@mcp.tool()
def rigol_ds1102e_query(
    scpi: str,
    device: str = "/dev/usbtmc0",
    delay: float = 0.2,
    read_size: int = 4096,
) -> dict[str, Any]:
    """Send a SCPI query and return the response."""
    return _MANAGED_SCOPE.query(scpi, device, delay, read_size)


@mcp.tool()
def rigol_ds1102e_write(
    scpi: str,
    device: str = "/dev/usbtmc0",
) -> dict[str, Any]:
    """Send a SCPI command that does not expect a response."""
    return _MANAGED_SCOPE.write(scpi, device)


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
    return _MANAGED_SCOPE.snapshot_get(device, delay, read_size, normalize_channels(channels))


@mcp.tool()
def rigol_ds1102e_snapshot_cached(
    device: str = "/dev/usbtmc0",
) -> dict[str, Any]:
    """Return the last stored setup snapshot without querying the scope."""
    return _MANAGED_SCOPE.snapshot_cached(device)


@mcp.tool()
def rigol_ds1102e_snapshot_refresh(
    device: str = "/dev/usbtmc0",
    delay: float = 0.2,
    read_size: int = 4096,
    channels: list[int] | None = None,
) -> dict[str, Any]:
    """Read a full setup snapshot from the scope and store it in the server cache."""
    return _MANAGED_SCOPE.snapshot_refresh(device, delay, read_size, normalize_channels(channels))


@mcp.tool()
def rigol_ds1102e_apply_profile(
    profile: dict[str, Any],
    device: str = "/dev/usbtmc0",
    delay: float = 0.2,
    read_size: int = 4096,
    refresh_after: bool = True,
) -> dict[str, Any]:
    """Apply multiple setup changes in one request and refresh the setup cache."""
    return _MANAGED_SCOPE.apply_profile(profile, device, delay, read_size, refresh_after)


@mcp.tool()
def rigol_ds1102e_scope_setup(
    device: str = "/dev/usbtmc0",
    channels: list[int] | None = None,
    trigger_mode: str = "EDGE",
    sweep: str = "SINGLE",
    points_mode: str = "RAW",
    run: bool = False,
    delay: float = 0.2,
    read_size: int = 4096,
) -> dict[str, Any]:
    """Prepare the scope for single-trigger RAW waveform capture."""
    return _MANAGED_SCOPE.scope_setup(
        device,
        normalize_channels(channels),
        trigger_mode,
        sweep,
        points_mode,
        run,
        delay,
        read_size,
    )


@mcp.tool()
def rigol_ds1102e_data_capture(
    device: str = "/dev/usbtmc0",
    channels: list[int] | None = None,
    freeze: bool = True,
    points_mode: str = "RAW",
    encoding: str = "list",
    delay: float = 0.2,
    read_size: int = 1200000,
) -> dict[str, Any]:
    """Read currently displayed waveform bytes from selected channels."""
    scope = build_scope(device, delay, read_size)
    device = scope.device
    selected_channels = normalize_channels(channels)
    writes, raw_channels = capture_waveform_channels(
        scope,
        selected_channels,
        freeze,
        points_mode,
        delay,
        read_size,
    )

    captured_channels: dict[str, Any] = {}
    for channel in selected_channels:
        data = raw_channels[str(channel)]
        captured_channels[str(channel)] = {
            "scpi": render_command("waveform_data_get", channel=channel),
            "summary": summarize_waveform_data(data),
            "encoding": encoding,
            "data": encode_waveform_data(data, encoding),
        }

    mark_cache_stale(device, "waveform data captured")
    return {
        "timestamp": utc_timestamp(),
        "device": device,
        "status": "ok",
        "writes": writes,
        "channels": captured_channels,
        "cache_state": "stale",
    }


@mcp.tool()
def rigol_ds1102e_spi_sample_indexes(
    device: str = "/dev/usbtmc0",
    clock_channel: int = 1,
    data_channel: int = 2,
    freeze: bool = True,
    points_mode: str = "RAW",
    threshold: int = 5,
    slope_threshold: int = 10,
    delay: float = 0.2,
    read_size: int = 1200000,
) -> dict[str, Any]:
    """Return clock sample indexes for SPI analysis after normalizing both channels."""
    if clock_channel == data_channel:
        raise ValueError("clock_channel and data_channel must be different")

    scope = build_scope(device, delay, read_size)
    device = scope.device
    writes, raw_channels = capture_waveform_channels(
        scope,
        [clock_channel, data_channel],
        freeze,
        points_mode,
        delay,
        read_size,
    )

    clock_samples = normalize_waveform_samples(raw_channels[str(clock_channel)])
    data_samples = normalize_waveform_samples(raw_channels[str(data_channel)])
    sample_indexes = detect_rising_edge_sample_indexes(
        clock_samples,
        threshold=threshold,
        slope_threshold=slope_threshold,
    )

    mark_cache_stale(device, "SPI sample indexes analyzed")
    return {
        "timestamp": utc_timestamp(),
        "device": device,
        "status": "ok",
        "writes": writes,
        "clock_channel": clock_channel,
        "data_channel": data_channel,
        "threshold": threshold,
        "slope_threshold": slope_threshold,
        "sample_indexes": sample_indexes,
        "clock_samples": {
            "length": len(clock_samples),
            "minimum": min(clock_samples) if clock_samples else None,
            "maximum": max(clock_samples) if clock_samples else None,
        },
        "data_samples": {
            "length": len(data_samples),
            "minimum": min(data_samples) if data_samples else None,
            "maximum": max(data_samples) if data_samples else None,
        },
        "cache_state": "stale",
    }


@mcp.tool()
def rigol_ds1102e_spi_decode(
    device: str = "/dev/usbtmc0",
    clock_channel: int = 1,
    data_channel: int = 2,
    freeze: bool = True,
    points_mode: str = "RAW",
    threshold: int = 5,
    slope_threshold: int = 10,
    low_ratio: float = 0.2,
    high_ratio: float = 0.8,
    expected_writes: int | None = None,
    expected_addresses: list[int] | None = None,
    window_scan: bool = True,
    max_extra_edges: int = 16,
    time_scale: float | None = None,
    time_scale_margin: float = 1.5,
    delay: float = 0.2,
    read_size: int = 1200000,
) -> dict[str, Any]:
    """Capture, normalize, sample, and decode SPI words from two scope channels."""
    if clock_channel == data_channel:
        raise ValueError("clock_channel and data_channel must be different")
    if not 1 <= threshold <= 20:
        raise ValueError("threshold must be between 1 and 20")
    if not 1 <= slope_threshold <= 20:
        raise ValueError("slope_threshold must be between 1 and 20")
    if not 0.05 <= low_ratio <= 0.4:
        raise ValueError("low_ratio must be between 0.05 and 0.4")
    if not 0.6 <= high_ratio <= 0.95:
        raise ValueError("high_ratio must be between 0.6 and 0.95")
    if expected_writes is not None and not 1 <= expected_writes <= 6:
        raise ValueError("expected_writes must be between 1 and 6")
    if expected_addresses is not None:
        if not 1 <= len(expected_addresses) <= 6:
            raise ValueError("expected_addresses must contain between 1 and 6 addresses")
        for address in expected_addresses:
            if not 0 <= int(address) <= 5:
                raise ValueError("expected_addresses values must be between 0 and 5")
        if expected_writes is not None and len(expected_addresses) != expected_writes:
            raise ValueError("expected_addresses length must match expected_writes")
        if expected_writes is None:
            expected_writes = len(expected_addresses)
    if not 0 <= max_extra_edges <= 16:
        raise ValueError("max_extra_edges must be between 0 and 16")
    if time_scale is not None and not 500e-9 <= time_scale <= 20e-6:
        raise ValueError("time_scale must be between 500e-9 and 20e-6 seconds/div")
    if not 1.0 <= time_scale_margin <= 2.0:
        raise ValueError("time_scale_margin must be between 1.0 and 2.0")

    scope = build_scope(device, delay, read_size)
    device = scope.device
    writes: list[dict[str, Any]] = []
    current_time_scale = time_scale
    if current_time_scale is None:
        response = query_protocol_value(scope, "timebase_scale_get", delay=delay, read_size=read_size)
        current_time_scale = float(response)
    if time_scale is not None:
        params = {"scale": time_scale}
        scpi = write_protocol_value(scope, "timebase_scale_set", params)
        writes.append({"key": "timebase_scale_set", "params": params, "scpi": scpi})

    capture_writes, raw_channels = capture_waveform_channels(
        scope,
        [clock_channel, data_channel],
        freeze,
        points_mode,
        delay,
        read_size,
    )
    writes.extend(capture_writes)

    clock_samples = normalize_waveform_samples(raw_channels[str(clock_channel)])
    data_samples = normalize_waveform_samples(raw_channels[str(data_channel)])
    sample_indexes = detect_rising_edge_sample_indexes(
        clock_samples,
        threshold=threshold,
        slope_threshold=slope_threshold,
    )
    expected_edges = expected_writes * 32 if expected_writes is not None else None
    observed_edges = len(sample_indexes)

    if expected_writes is not None and window_scan:
        try:
            decoded = decode_spi_data_words_windowed(
                data_samples,
                sample_indexes,
                expected_writes=expected_writes,
                max_extra_edges=max_extra_edges,
                low_ratio=low_ratio,
                high_ratio=high_ratio,
                expected_addresses=expected_addresses,
            )
        except ValueError:
            if expected_addresses is None or expected_edges is None:
                raise
            raise
        selected_indexes = sample_indexes[
            decoded["window"]["selected_start"] : decoded["window"]["selected_stop"]  # type: ignore[index]
        ]
    else:
        decoded = decode_spi_data_words(
            data_samples,
            sample_indexes,
            low_ratio=low_ratio,
            high_ratio=high_ratio,
        )
        validate_expected_addresses(decoded, expected_addresses)
        selected_indexes = sample_indexes

    mark_cache_stale(device, "SPI data decoded")
    return {
        "timestamp": utc_timestamp(),
        "device": device,
        "status": "ok",
        "writes": writes,
        "clock_channel": clock_channel,
        "data_channel": data_channel,
        "threshold": threshold,
        "slope_threshold": slope_threshold,
        "low_ratio": low_ratio,
        "high_ratio": high_ratio,
        "expected_writes": expected_writes,
        "expected_addresses": expected_addresses,
        "window_scan": window_scan,
        "max_extra_edges": max_extra_edges,
        "time_scale": current_time_scale,
        "time_scale_margin": time_scale_margin,
        "sample_indexes": sample_indexes,
        "selected_sample_indexes": selected_indexes,
        "clock_samples": {
            "length": len(clock_samples),
            "minimum": min(clock_samples) if clock_samples else None,
            "maximum": max(clock_samples) if clock_samples else None,
        },
        "data_samples": {
            "length": len(data_samples),
            "minimum": min(data_samples) if data_samples else None,
            "maximum": max(data_samples) if data_samples else None,
        },
        "decoded": decoded,
        "cache_state": "stale",
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
    return _MANAGED_SCOPE.protocol_command(key, params, device, delay, read_size)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
