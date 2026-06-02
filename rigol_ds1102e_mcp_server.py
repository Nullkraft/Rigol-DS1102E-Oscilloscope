#!/usr/bin/env python3
# /// 'uv' inline script metadependencies
# dependencies = [
#   "mcp[cli]",
#   "numpy",
# ]
# ///

import base64
from datetime import datetime, timezone
from typing import Any

from mcp.server.fastmcp import FastMCP

from rigol_ds1102e import (
    DEFAULT_GLOB_PATTERNS,
    RigolDS1102E,
    discover_ds1102e_device,
    list_candidate_devices,
)
from rigol_ds1102e_protocol import PROTOCOL, get_command, is_excluded_command, render_command
from rigol_ds1102e_spi_analysis import (
    decode_spi_data_words,
    decode_spi_data_words_windowed,
    detect_rising_edge_sample_indexes,
    normalize_waveform_samples,
    validate_expected_addresses,
)


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

DEFAULT_SCOPE_DELAY = 0.2
DEFAULT_SCOPE_READ_SIZE = 1200000


class ManagedScopeState:
    def __init__(self) -> None:
        self.scope_setup_cache: dict[str, dict[str, Any]] = {}
        self.scope = self.open_discovered_scope()

    def open_discovered_scope(self) -> RigolDS1102E:
        device = discover_ds1102e_device()
        scope = RigolDS1102E(
            device=device,
            query_delay=DEFAULT_SCOPE_DELAY,
            read_size=DEFAULT_SCOPE_READ_SIZE,
        )
        scope.open()
        return scope

    def current_scope(self) -> RigolDS1102E:
        return self.scope

    def reconnect_scope(self, scope: RigolDS1102E) -> RigolDS1102E:
        device = discover_ds1102e_device()
        self.scope.query_delay = scope.query_delay
        self.scope.read_size = scope.read_size
        self.scope.reconnect(device)
        return self.scope

    def scope_write(self, scope: RigolDS1102E, scpi: str) -> None:
        try:
            scope.write(scpi)
        except OSError:
            scope = self.reconnect_scope(scope)
            scope.write(scpi)

    def scope_query_bytes(self, scope: RigolDS1102E, scpi: str) -> bytes:
        try:
            return scope.query_bytes(scpi)
        except OSError:
            scope = self.reconnect_scope(scope)
            return scope.query_bytes(scpi)

    def scope_query(self, scope: RigolDS1102E, scpi: str) -> str:
        response = self.scope_query_bytes(scope, scpi)
        return response.decode("ascii", "replace").replace("\x00", "").strip()

    def protocol_query(
        self,
        scope: RigolDS1102E,
        key: str,
        params: dict[str, Any] | None = None,
    ) -> str:
        scpi = render_command(key, **(params or {}))
        if is_excluded_command(scpi):
            raise ValueError(f"protocol command is excluded: {key}")
        return self.scope_query(scope, scpi)

    def protocol_write(
        self,
        scope: RigolDS1102E,
        key: str,
        params: dict[str, Any] | None = None,
    ) -> str:
        scpi = render_command(key, **(params or {}))
        if is_excluded_command(scpi):
            raise ValueError(f"protocol command is excluded: {key}")
        self.scope_write(scope, scpi)
        return scpi

    def query_waveform_bytes(self, scope: RigolDS1102E, scpi: str) -> bytes:
        data = self.scope_query_bytes(scope, scpi).rstrip(b"\n")
        for _ in range(4):
            if len(data) != 600 or scope.read_size <= 600:
                break
            reread = self.scope_query_bytes(scope, scpi).rstrip(b"\n")
            if len(reread) > len(data):
                data = reread
            elif len(reread) != 600:
                data = reread
                break
        return data

    def require_stopped_scope(self, scope: RigolDS1102E, attempts: int = 20) -> str:
        status = ""
        for _ in range(attempts):
            status = self.protocol_query(scope, "trigger_status").upper()
            if status == "STOP":
                return status
            if status == "WAIT":
                self.protocol_write(scope, "stop")
        raise RuntimeError(f"scope did not enter STOP state after :STOP; last status was {status!r}")

    def capture_waveform_channels(
        self,
        scope: RigolDS1102E,
        selected_channels: list[int],
        freeze: bool,
        points_mode: str,
    ) -> tuple[list[dict[str, Any]], dict[str, bytes]]:
        writes: list[dict[str, Any]] = []

        if freeze:
            scpi = self.protocol_write(scope, "stop")
            writes.append({"key": "stop", "params": {}, "scpi": scpi})
            self.require_stopped_scope(scope)

        params = {"points_mode": points_mode}
        scpi = self.protocol_write(scope, "waveform_points_mode_set", params)
        writes.append({"key": "waveform_points_mode_set", "params": params, "scpi": scpi})

        captured_channels: dict[str, bytes] = {}
        for channel in selected_channels:
            scpi = render_command("waveform_data_get", channel=channel)
            captured_channels[str(channel)] = self.query_waveform_bytes(scope, scpi)

        return writes, captured_channels

    def identify(self) -> dict[str, Any]:
        scope = self.current_scope()
        return {
            "device": scope.device,
            "response": self.scope_query(scope, "*IDN?"),
        }

    def query(self, scpi: str) -> dict[str, Any]:
        scope = self.current_scope()
        return {
            "device": scope.device,
            "scpi": scpi,
            "response": self.scope_query(scope, scpi),
        }

    def write(self, scpi: str) -> dict[str, Any]:
        scope = self.current_scope()
        self.scope_write(scope, scpi)
        device = scope.device
        self.mark_cache_stale(device, f"raw write: {scpi}")
        return {
            "device": device,
            "scpi": scpi,
            "status": "ok",
        }

    def get_cached_snapshot(self) -> tuple[str, dict[str, Any] | None]:
        device = self.scope.device
        return device, self.scope_setup_cache.get(device)

    def build_setup_snapshot(self, channels: list[int],) -> dict[str, Any]:
        scope = self.current_scope()
        device = scope.device
        snapshot: dict[str, Any] = {
            "device": device,
            "identity": self.scope_query(scope, "*IDN?"),
            "channels": {},
            "timebase": {},
            "trigger": {},
            "acquire": {},
            "waveform": {},
            "session": {},
            "cache": {
                "state": "fresh",
                "reason": "snapshot_refresh",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
        }

        visible_channels: list[int] = []
        for channel in channels:
            channel_settings: dict[str, str] = {}
            for setting_name, command_key in SNAPSHOT_CHANNEL_KEYS:
                channel_settings[setting_name] = self.protocol_query(
                    scope,
                    command_key,
                    {"channel": channel},
                )
            if channel_settings.get("display") == "ON":
                visible_channels.append(channel)
            snapshot["channels"][str(channel)] = channel_settings

        for setting_name, command_key in SNAPSHOT_TIMEBASE_KEYS:
            snapshot["timebase"][setting_name] = self.protocol_query(
                scope,
                command_key,
            )

        trigger_mode = self.protocol_query(scope, "trigger_mode_get")
        snapshot["trigger"]["mode"] = trigger_mode
        snapshot["trigger"]["source"] = self.protocol_query(
            scope,
            "trigger_source_get",
            {"mode": trigger_mode},
        )
        snapshot["trigger"]["level"] = self.protocol_query(
            scope,
            "trigger_level_get",
            {"mode": trigger_mode},
        )
        snapshot["trigger"]["sweep"] = self.protocol_query(
            scope,
            "trigger_sweep_get",
            {"mode": trigger_mode},
        )
        snapshot["trigger"]["holdoff"] = self.protocol_query(
            scope,
            "trigger_holdoff_get",
        )

        for setting_name, command_key in SNAPSHOT_ACQUIRE_KEYS:
            snapshot["acquire"][setting_name] = self.protocol_query(
                scope,
                command_key,
            )
        snapshot["acquire"]["sampling_rate"] = {}
        for channel in visible_channels:
            snapshot["acquire"]["sampling_rate"][str(channel)] = self.protocol_query(
                scope,
                "acquire_sampling_rate_get",
                {"channel": channel},
            )

        snapshot["waveform"]["points_mode"] = self.protocol_query(
            scope,
            "waveform_points_mode_get",
        )
        snapshot["session"]["trigger_status"] = self.protocol_query(
            scope,
            "trigger_status",
        )
        self.scope_setup_cache[device] = snapshot
        return snapshot

    def snapshot_get(self, channels: list[int],) -> dict[str, Any]:
        device, cached = self.get_cached_snapshot()
        if cached is not None and cached.get("cache", {}).get("state") == "fresh":
            return {
                "device": device,
                "source": "cache",
                "snapshot": cached,
            }
        return {
            "device": device,
            "source": "scope",
            "snapshot": self.build_setup_snapshot(channels),
        }

    def snapshot_refresh(self, channels: list[int],) -> dict[str, Any]:
        device = self.scope.device
        return {
            "device": device,
            "source": "scope",
            "snapshot": self.build_setup_snapshot(channels),
        }

    def apply_profile(self, profile: dict[str, Any], refresh_after: bool,) -> dict[str, Any]:
        if "snapshot" in profile and isinstance(profile["snapshot"], dict):
            profile = profile["snapshot"]
        scope = self.current_scope()
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
                scpi = self.protocol_write(scope, command_key, params)
                writes.append({"key": command_key, "params": params, "scpi": scpi})

        for setting_name, value in profile.get("timebase", {}).items():
            mapped = PROFILE_TIMEBASE_SETTERS.get(setting_name)
            if mapped is None:
                raise ValueError(f"unsupported timebase setting: {setting_name}")
            command_key, param_name = mapped
            params = {param_name: value}
            scpi = self.protocol_write(scope, command_key, params)
            writes.append({"key": command_key, "params": params, "scpi": scpi})

        trigger_settings = profile.get("trigger", {})
        trigger_mode = trigger_settings.get("mode")
        if trigger_mode is None:
            _, cached = self.get_cached_snapshot()
            trigger_mode = (cached or {}).get("trigger", {}).get("mode", "EDGE")
        if "mode" in trigger_settings:
            params = {"mode": trigger_settings["mode"]}
            scpi = self.protocol_write(scope, "trigger_mode_set", params)
            writes.append({"key": "trigger_mode_set", "params": params, "scpi": scpi})
            trigger_mode = trigger_settings["mode"]
        for setting_name, command_key in (
            ("source", "trigger_source_set"),
            ("level", "trigger_level_set"),
            ("sweep", "trigger_sweep_set"),
        ):
            if setting_name in trigger_settings:
                params = {"mode": trigger_mode, setting_name: trigger_settings[setting_name]}
                scpi = self.protocol_write(scope, command_key, params)
                writes.append({"key": command_key, "params": params, "scpi": scpi})
        if "holdoff" in trigger_settings:
            params = {"holdoff": trigger_settings["holdoff"]}
            scpi = self.protocol_write(scope, "trigger_holdoff_set", params)
            writes.append({"key": "trigger_holdoff_set", "params": params, "scpi": scpi})

        for setting_name, value in profile.get("acquire", {}).items():
            mapped = PROFILE_ACQUIRE_SETTERS.get(setting_name)
            if mapped is None:
                if setting_name == "sampling_rate":
                    continue
                raise ValueError(f"unsupported acquire setting: {setting_name}")
            command_key, param_name = mapped
            params = {param_name: value}
            scpi = self.protocol_write(scope, command_key, params)
            writes.append({"key": command_key, "params": params, "scpi": scpi})

        for setting_name, value in profile.get("waveform", {}).items():
            command_key = PROFILE_WAVEFORM_SETTERS.get(setting_name)
            if command_key is None:
                raise ValueError(f"unsupported waveform setting: {setting_name}")
            params = {setting_name: value}
            scpi = self.protocol_write(scope, command_key, params)
            writes.append({"key": command_key, "params": params, "scpi": scpi})

        session_settings = profile.get("session", {})
        for setting_name, command_key in (("run", "run"), ("stop", "stop"), ("force_trigger", "force_trigger")):
            if session_settings.get(setting_name):
                scpi = self.protocol_write(scope, command_key)
                writes.append({"key": command_key, "params": {}, "scpi": scpi})

        if refresh_after:
            snapshot = self.build_setup_snapshot([1, 2])
            cache_state = "fresh"
        else:
            self.mark_cache_stale(device, "profile applied without refresh")
            _, snapshot = self.get_cached_snapshot()
            cache_state = "stale"

        return {
            "device": device,
            "status": "ok",
            "writes": writes,
            "cache_state": cache_state,
            "snapshot": snapshot,
        }

    def scope_setup(self, channels: list[int], trigger_mode: str, sweep: str, points_mode: str, run: bool,) -> dict[str, Any]:
        scope = self.current_scope()
        device = scope.device
        writes: list[dict[str, Any]] = []

        for channel in channels:
            params = {"channel": channel, "state": "ON"}
            scpi = self.protocol_write(scope, "channel_display_set", params)
            writes.append({"key": "channel_display_set", "params": params, "scpi": scpi})

        for key, params in (
            ("trigger_mode_set", {"mode": trigger_mode}),
            ("trigger_sweep_set", {"mode": trigger_mode, "sweep": sweep}),
            ("waveform_points_mode_set", {"points_mode": points_mode}),
        ):
            scpi = self.protocol_write(scope, key, params)
            writes.append({"key": key, "params": params, "scpi": scpi})

        if run:
            scpi = self.protocol_write(scope, "run")
            writes.append({"key": "run", "params": {}, "scpi": scpi})

        self.mark_cache_stale(device, "scope setup applied")
        return {
            "device": device,
            "status": "ok",
            "writes": writes,
            "cache_state": "stale",
        }

    def data_capture(self, channels: list[int] | None, freeze: bool, points_mode: str, encoding: str,) -> dict[str, Any]:
        scope = self.current_scope()
        device = scope.device
        selected_channels = normalize_channels(channels)
        writes, raw_channels = self.capture_waveform_channels(
            scope,
            selected_channels,
            freeze,
            points_mode,
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

        self.mark_cache_stale(device, "waveform data captured")
        return {
            "device": device,
            "status": "ok",
            "writes": writes,
            "channels": captured_channels,
            "cache_state": "stale",
        }

    def spi_sample_indexes(
        self,
        clock_scope_channel: int,
        data_scope_channel: int,
        freeze: bool,
        points_mode: str,
        threshold: int,
        slope_threshold: int,
    ) -> dict[str, Any]:
        if clock_scope_channel == data_scope_channel:
            raise ValueError("clock_scope_channel and data_scope_channel must be different")

        scope = self.current_scope()
        device = scope.device
        writes, raw_channels = self.capture_waveform_channels(
            scope,
            [clock_scope_channel, data_scope_channel],
            freeze,
            points_mode,
        )

        clock_samples = normalize_waveform_samples(raw_channels[str(clock_scope_channel)])
        data_samples = normalize_waveform_samples(raw_channels[str(data_scope_channel)])
        sample_indexes = detect_rising_edge_sample_indexes(
            clock_samples,
            threshold=threshold,
            slope_threshold=slope_threshold,
        )

        self.mark_cache_stale(device, "SPI sample indexes analyzed")
        return {
            "device": device,
            "status": "ok",
            "writes": writes,
            "clock_scope_channel": clock_scope_channel,
            "data_scope_channel": data_scope_channel,
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

    def spi_decode(
        self,
        clock_scope_channel: int,
        data_scope_channel: int,
        freeze: bool,
        points_mode: str,
        threshold: int,
        slope_threshold: int,
        low_ratio: float,
        high_ratio: float,
        expected_writes: int | None,
        expected_addresses: list[int] | None,
        window_scan: bool,
        max_extra_edges: int,
        time_scale: float | None,
        time_scale_margin: float,
    ) -> dict[str, Any]:
        if clock_scope_channel == data_scope_channel:
            raise ValueError("clock_scope_channel and data_scope_channel must be different")
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

        scope = self.current_scope()
        device = scope.device
        writes: list[dict[str, Any]] = []
        current_time_scale = time_scale
        if current_time_scale is None:
            response = self.protocol_query(scope, "timebase_scale_get")
            current_time_scale = float(response)
        if time_scale is not None:
            params = {"scale": time_scale}
            scpi = self.protocol_write(scope, "timebase_scale_set", params)
            writes.append({"key": "timebase_scale_set", "params": params, "scpi": scpi})

        capture_writes, raw_channels = self.capture_waveform_channels(
            scope,
            [clock_scope_channel, data_scope_channel],
            freeze,
            points_mode,
        )
        writes.extend(capture_writes)

        clock_samples = normalize_waveform_samples(raw_channels[str(clock_scope_channel)])
        data_samples = normalize_waveform_samples(raw_channels[str(data_scope_channel)])
        sample_indexes = detect_rising_edge_sample_indexes(
            clock_samples,
            threshold=threshold,
            slope_threshold=slope_threshold,
        )
        expected_edges = expected_writes * 32 if expected_writes is not None else None

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

        self.mark_cache_stale(device, "SPI data decoded")
        return {
            "device": device,
            "status": "ok",
            "writes": writes,
            "clock_scope_channel": clock_scope_channel,
            "data_scope_channel": data_scope_channel,
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

    def protocol_command(self, key: str, params: dict[str, Any] | None,) -> dict[str, Any]:
        command = get_command(key)
        scpi = render_command(key, **(params or {}))
        if is_excluded_command(scpi):
            raise ValueError(f"protocol command is excluded: {key}")

        scope = self.current_scope()
        device = scope.device
        result: dict[str, Any] = {
            "device": device,
            "key": command.key,
            "family": command.family,
            "kind": command.kind,
            "scpi": scpi,
        }
        if command.kind == "query":
            result["response"] = self.scope_query(scope, scpi)
            return result

        self.scope_write(scope, scpi)
        result["status"] = "ok"
        if command.changes_scope_state:
            self.mark_cache_stale(device, f"protocol write: {command.key}")
            result["cache_state"] = "stale"
        return result

    def scope_io(self, delay: float | None = None, read_size: int | None = None,) -> dict[str, Any]:
        scope = self.current_scope()
        if delay is not None:
            if delay < 0:
                raise ValueError("delay must be non-negative")
            scope.query_delay = delay
        if read_size is not None:
            if read_size < 1:
                raise ValueError("read_size must be at least 1")
            scope.read_size = read_size
        return {
            "device": scope.device,
            "delay": scope.query_delay,
            "read_size": scope.read_size,
        }

    def mark_cache_stale(self, device: str, reason: str) -> None:
        cached = self.scope_setup_cache.get(device)
        if cached is not None:
            cached["cache"] = {
                "state": "stale",
                "reason": reason,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }


_MANAGED_SCOPE = ManagedScopeState()

def is_supported_protocol_command(key: str) -> bool:
    command = PROTOCOL[key]
    return not is_excluded_command(command.template)


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


def normalize_scope_channel(channel: int, name: str) -> int:
    value = int(channel)
    if value not in (1, 2):
        raise ValueError(f"{name} must be 1 or 2, got {value}")
    return value


def resolve_spi_sources(
    chan_1: int | None,
    chan_2: int | None,
    clock_source: str | None,
    data_source: str | None,
) -> tuple[int, int, dict[str, Any]]:
    capture_map = {
        "chan_1": normalize_scope_channel(1 if chan_1 is None else chan_1, "chan_1"),
        "chan_2": normalize_scope_channel(2 if chan_2 is None else chan_2, "chan_2"),
    }
    resolved_clock_source = "chan_1" if clock_source is None else str(clock_source)
    resolved_data_source = "chan_2" if data_source is None else str(data_source)
    if resolved_clock_source not in capture_map:
        raise ValueError("clock_source must be 'chan_1' or 'chan_2'")
    if resolved_data_source not in capture_map:
        raise ValueError("data_source must be 'chan_1' or 'chan_2'")
    resolved_clock = capture_map[resolved_clock_source]
    resolved_data = capture_map[resolved_data_source]
    if resolved_clock == resolved_data:
        raise ValueError("clock_source and data_source must resolve to different scope channels")
    return resolved_clock, resolved_data, {
        "chan_1": capture_map["chan_1"],
        "chan_2": capture_map["chan_2"],
        "clock_source": resolved_clock_source,
        "data_source": resolved_data_source,
    }


@mcp.tool()
def list_ports() -> dict[str, Any]:
    """List likely USBTMC device nodes for the Rigol DS1102E."""
    discovered_ds1102e_device: str | None
    discovery_error: str | None = None
    try:
        discovered_ds1102e_device = discover_ds1102e_device()
    except RuntimeError as exc:
        discovered_ds1102e_device = None
        discovery_error = str(exc)
    return {
        "devices": list_candidate_devices(),
        "discovered_ds1102e_device": discovered_ds1102e_device,
        "discovery_error": discovery_error,
        "patterns": list(DEFAULT_GLOB_PATTERNS),
    }


@mcp.tool()
def rigol_ds1102e_identify() -> dict[str, Any]:
    """Query *IDN? from the scope."""
    return _MANAGED_SCOPE.identify()


@mcp.tool()
def rigol_ds1102e_query(scpi: str,) -> dict[str, Any]:
    """Send a SCPI query and return the response."""
    return _MANAGED_SCOPE.query(scpi)


@mcp.tool()
def rigol_ds1102e_write(scpi: str,) -> dict[str, Any]:
    """Send a SCPI command that does not expect a response."""
    return _MANAGED_SCOPE.write(scpi)


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
        "commands": commands,
    }


@mcp.tool()
def rigol_ds1102e_snapshot_get(
    channels: list[int] | None = None,
) -> dict[str, Any]:
    """Return the cached setup snapshot, refreshing from the scope if missing or stale."""
    return _MANAGED_SCOPE.snapshot_get(normalize_channels(channels))


@mcp.tool()
def rigol_ds1102e_snapshot_refresh(
    channels: list[int] | None = None,
) -> dict[str, Any]:
    """Read a full setup snapshot from the scope and store it in the server cache."""
    return _MANAGED_SCOPE.snapshot_refresh(normalize_channels(channels))


@mcp.tool()
def rigol_ds1102e_apply_profile(
    profile: dict[str, Any],
    refresh_after: bool = True,
) -> dict[str, Any]:
    """Apply multiple setup changes in one request and refresh the setup cache."""
    return _MANAGED_SCOPE.apply_profile(profile, refresh_after)


@mcp.tool()
def rigol_ds1102e_scope_setup(
    channels: list[int] | None = None,
    trigger_mode: str = "EDGE",
    sweep: str = "SINGLE",
    points_mode: str = "RAW",
    run: bool = False,
) -> dict[str, Any]:
    """Prepare the scope for single-trigger RAW waveform capture."""
    return _MANAGED_SCOPE.scope_setup(
        normalize_channels(channels),
        trigger_mode,
        sweep,
        points_mode,
        run,
    )


@mcp.tool()
def rigol_ds1102e_data_capture(
    channels: list[int] | None = None,
    freeze: bool = True,
    points_mode: str = "RAW",
    encoding: str = "list",
) -> dict[str, Any]:
    """Read currently displayed waveform bytes from selected channels."""
    return _MANAGED_SCOPE.data_capture(channels, freeze, points_mode, encoding)


@mcp.tool()
def rigol_ds1102e_spi_sample_indexes(
    chan_1: int | None = None,
    chan_2: int | None = None,
    clock_source: str | None = None,
    data_source: str | None = None,
    freeze: bool = True,
    points_mode: str = "RAW",
    threshold: int = 5,
    slope_threshold: int = 10,
) -> dict[str, Any]:
    """Return clock sample indexes for SPI analysis after normalizing both channels."""
    clock_scope_channel, data_scope_channel, source_map = resolve_spi_sources(
        chan_1,
        chan_2,
        clock_source,
        data_source,
    )
    result = _MANAGED_SCOPE.spi_sample_indexes(
        clock_scope_channel,
        data_scope_channel,
        freeze,
        points_mode,
        threshold,
        slope_threshold,
    )
    result.update(source_map)
    return result


@mcp.tool()
def rigol_ds1102e_spi_decode(
    chan_1: int | None = None,
    chan_2: int | None = None,
    clock_source: str | None = None,
    data_source: str | None = None,
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
) -> dict[str, Any]:
    """Capture, normalize, sample, and decode SPI words from two scope channels."""
    clock_scope_channel, data_scope_channel, source_map = resolve_spi_sources(
        chan_1,
        chan_2,
        clock_source,
        data_source,
    )
    result = _MANAGED_SCOPE.spi_decode(
        clock_scope_channel,
        data_scope_channel,
        freeze,
        points_mode,
        threshold,
        slope_threshold,
        low_ratio,
        high_ratio,
        expected_writes,
        expected_addresses,
        window_scan,
        max_extra_edges,
        time_scale,
        time_scale_margin,
    )
    result.update(source_map)
    return result


@mcp.tool()
def rigol_ds1102e_protocol_command(
    key: str,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Execute a supported protocol command by key using the registry metadata."""
    return _MANAGED_SCOPE.protocol_command(key, params)


@mcp.tool()
def rigol_ds1102e_scope_io(delay: float | None = None, read_size: int | None = None,) -> dict[str, Any]:
    """Get or update the managed scope query delay and read size."""
    return _MANAGED_SCOPE.scope_io(delay, read_size)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
