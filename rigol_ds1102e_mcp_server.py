#!/usr/bin/env python3
# /// 'uv' inline script metadependencies
# dependencies = [
#   "mcp[cli]",
#   "numpy",
# ]
# ///

import base64
import functools
import os
import re
import threading
import time
from typing import Any

from mcp.server.fastmcp import FastMCP

from rigol_ds1102e_acceptable_scpi_commands import ACCEPTABLE_SCPI_COMMANDS
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


mcp = FastMCP(
    "rigol_ds1102e",
    json_response=True,
    instructions="Call one Rigol MCP tool at a time. Parallel Rigol MCP calls are not supported.",
)

SCOPE_CONFIG_CHANNEL_KEYS = (
    ("display", "channel_display_get"),
    ("coupling", "channel_coupling_get"),
    ("probe", "channel_probe_get"),
    ("scale", "channel_scale_get"),
    ("offset", "channel_offset_get"),
)

SCOPE_CONFIG_TIMEBASE_KEYS = (
    ("scale", "timebase_scale_get"),
    ("offset", "timebase_offset_get"),
)

SCOPE_CONFIG_ACQUIRE_KEYS = (
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
DEFAULT_SPI_CLOCK_CHANNEL = 1
DEFAULT_SPI_DATA_CHANNEL = 2
DEFAULT_SPI_POINTS_MODE = "RAW"
DEFAULT_SPI_CLOCK_LOW_RATIO = 0.3
DEFAULT_SPI_CLOCK_HIGH_RATIO = 0.6
DEFAULT_SPI_LOW_RATIO = 0.2
DEFAULT_SPI_HIGH_RATIO = 0.8
DEFAULT_SPI_MAX_EXTRA_EDGES = 16
DEFAULT_SPI_SETUP_DELAY = 0.15
DEFAULT_SPI_CLOCK_VERTICAL_SCALE = 2.0
DEFAULT_SPI_TRIGGER_LEVEL = 1.28
DEFAULT_SPI_TIMEBASE_SCALE = "5.0us"
PARALLEL_CALL_ERROR = "Rigol MCP calls must be made one at a time; parallel Rigol calls will not work. Wait for the current call to finish and retry."


def _build_scpi_mnemonic_pattern(text: str) -> str:
    parts = re.split(r"([A-Za-z]+)", text)
    pattern = ""
    for part in parts:
        if not part:
            continue
        if not part.isalpha():
            pattern += re.escape(part)
            continue
        if any(char.islower() for char in part):
            mandatory = "".join(char for char in part if char.isupper())
            optional = "".join(char for char in part if char.islower())
            pattern += re.escape(mandatory)
            if optional:
                pattern += "(?:" + "".join(f"[{char.lower()}{char.upper()}]" for char in optional) + ")?"
            continue
        pattern += re.escape(part) + r"(?:[a-z]+)?"
    return pattern


def _build_scpi_template_pattern(template: str) -> re.Pattern[str]:
    parts = re.split(r"(\{[a-zA-Z_][a-zA-Z0-9_]*\})", template)
    pattern = ""
    for part in parts:
        if not part:
            continue
        if part.startswith("{") and part.endswith("}"):
            pattern += r"[^ ]+"
        else:
            pattern += _build_scpi_mnemonic_pattern(part)
    return re.compile(rf"^{pattern}$", re.IGNORECASE)


ACCEPTABLE_SCPI_PATTERNS = tuple(
    (template, _build_scpi_template_pattern(template))
    for template in ACCEPTABLE_SCPI_COMMANDS
)
MCP_TOOL_CALL_LOCK = threading.Lock()


def normalize_scpi_command_text(scpi: str) -> str:
    return " ".join(scpi.strip().split())


def validate_acceptable_scpi_command(scpi: str) -> str:
    normalized = normalize_scpi_command_text(scpi)
    for template, pattern in ACCEPTABLE_SCPI_PATTERNS:
        if pattern.fullmatch(normalized):
            return normalized
    raise ValueError(
        "unsupported SCPI command for Rigol MCP: "
        f"{normalized!r}. Use a supported command template."
    )


def require_single_tool_call(func):
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        if not MCP_TOOL_CALL_LOCK.acquire(blocking=False):
            raise RuntimeError(PARALLEL_CALL_ERROR)
        try:
            return func(*args, **kwargs)
        finally:
            MCP_TOOL_CALL_LOCK.release()

    return wrapper


class ManagedScopeState:
    def __init__(self) -> None:
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

    def reconnect_scope(self) -> RigolDS1102E:
        device = discover_ds1102e_device()
        self.scope.reconnect(device)
        return self.scope

    def scope_write(self, scpi: str) -> None:
        scpi = validate_acceptable_scpi_command(scpi)
        try:
            self.scope.write(scpi)
        except OSError:
            self.reconnect_scope()
            self.scope.write(scpi)

    def scope_query_bytes(self, scpi: str) -> bytes:
        scpi = validate_acceptable_scpi_command(scpi)
        try:
            return self.scope.query_bytes(scpi)
        except OSError:
            self.reconnect_scope()
            return self.scope.query_bytes(scpi)

    def scope_query(self, scpi: str) -> str:
        response = self.scope_query_bytes(scpi)
        return response.decode("ascii", "replace").replace("\x00", "").strip()

    def protocol_query(self, key: str, params: dict[str, Any] | None = None,) -> str:
        scpi = render_command(key, **(params or {}))
        if is_excluded_command(scpi):
            raise ValueError(f"protocol command is excluded: {key}")
        return self.scope_query(scpi)

    def protocol_write(self, key: str, params: dict[str, Any] | None = None,) -> str:
        scpi = render_command(key, **(params or {}))
        if is_excluded_command(scpi):
            raise ValueError(f"protocol command is excluded: {key}")
        self.scope_write(scpi)
        return scpi

    def query_waveform_bytes(self, scpi: str) -> bytes:
        data = self.scope_query_bytes(scpi).rstrip(b"\n")
        for _ in range(4):
            if len(data) != 600 or self.scope.read_size <= 600:
                break
            reread = self.scope_query_bytes(scpi).rstrip(b"\n")
            if len(reread) > len(data):
                data = reread
            elif len(reread) != 600:
                data = reread
                break
        return data

    def require_stopped_scope(self, attempts: int = 20) -> str:
        status = ""
        for _ in range(attempts):
            status = self.protocol_query("trigger_status").upper()
            if status == "STOP":
                return status
        raise RuntimeError(f"scope did not enter STOP state after :STOP; last status was {status!r}")

    def require_waiting_scope(self, attempts: int = 20) -> str:
        status = ""
        for _ in range(attempts):
            status = self.protocol_query("trigger_status").upper()
            if status == "WAIT":
                return status
        raise RuntimeError(f"scope did not enter WAIT state after :RUN; last status was {status!r}")

    def capture_waveform_channels(
        self,
        selected_channels: list[int],
        freeze: bool,
        points_mode: str,
    ) -> tuple[list[dict[str, Any]], dict[str, bytes]]:
        writes: list[dict[str, Any]] = []

        if freeze:
            scpi = self.protocol_write("stop")
            writes.append({"key": "stop", "params": {}, "scpi": scpi})
            self.require_stopped_scope()

        params = {"points_mode": points_mode}
        scpi = self.protocol_write("waveform_points_mode_set", params)
        writes.append({"key": "waveform_points_mode_set", "params": params, "scpi": scpi})

        captured_channels: dict[str, bytes] = {}
        for channel in selected_channels:
            scpi = render_command("waveform_data_get", channel=channel)
            captured_channels[str(channel)] = self.query_waveform_bytes(scpi)

        return writes, captured_channels

    def identify(self) -> dict[str, Any]:
        return {
            "device": self.scope.device,
            "response": self.scope_query("*IDN?"),
        }

    def query(self, scpi: str) -> dict[str, Any]:
        return {
            "device": self.scope.device,
            "scpi": scpi,
            "response": self.scope_query(scpi),
        }

    def write(self, scpi: str) -> dict[str, Any]:
        self.scope_write(scpi)
        device = self.scope.device
        return {
            "device": device,
            "scpi": scpi,
            "status": "ok",
        }

    def build_scope_config(self, channels: list[int],) -> dict[str, Any]:
        device = self.scope.device
        normalized_scope_data: dict[str, Any] = {
            "device": device,
            "identity": self.scope_query("*IDN?"),
            "channels": {},
            "timebase": {},
            "trigger": {},
            "acquire": {},
            "waveform": {},
            "session": {},
        }

        visible_channels: list[int] = []
        for channel in channels:
            channel_settings: dict[str, str] = {}
            for setting_name, command_key in SCOPE_CONFIG_CHANNEL_KEYS:
                channel_settings[setting_name] = self.protocol_query(
                    command_key,
                    {"channel": channel},
                )
            if channel_settings.get("display") == "ON":
                visible_channels.append(channel)
            normalized_scope_data["channels"][str(channel)] = channel_settings

        for setting_name, command_key in SCOPE_CONFIG_TIMEBASE_KEYS:
            normalized_scope_data["timebase"][setting_name] = self.protocol_query(
                command_key,
            )

        trigger_mode = self.protocol_query("trigger_mode_get")
        normalized_scope_data["trigger"]["mode"] = trigger_mode
        normalized_scope_data["trigger"]["source"] = self.protocol_query(
            "trigger_source_get",
            {"mode": trigger_mode},
        )
        normalized_scope_data["trigger"]["level"] = self.protocol_query(
            "trigger_level_get",
            {"mode": trigger_mode},
        )
        normalized_scope_data["trigger"]["sweep"] = self.protocol_query(
            "trigger_sweep_get",
            {"mode": trigger_mode},
        )
        normalized_scope_data["trigger"]["holdoff"] = self.protocol_query(
            "trigger_holdoff_get",
        )

        for setting_name, command_key in SCOPE_CONFIG_ACQUIRE_KEYS:
            normalized_scope_data["acquire"][setting_name] = self.protocol_query(
                command_key,
            )
        normalized_scope_data["acquire"]["sampling_rate"] = {}
        for channel in visible_channels:
            normalized_scope_data["acquire"]["sampling_rate"][str(channel)] = self.protocol_query(
                "acquire_sampling_rate_get",
                {"channel": channel},
            )

        normalized_scope_data["waveform"]["points_mode"] = self.protocol_query(
            "waveform_points_mode_get",
        )
        normalized_scope_data["session"]["trigger_status"] = self.protocol_query(
            "trigger_status",
        )
        return normalized_scope_data

    def get_scope_config(self, channels: list[int],) -> dict[str, Any]:
        device = self.scope.device
        return {
            "device": device,
            "source": "scope",
            "normalized_scope_data": self.build_scope_config(channels),
        }

    def apply_profile(self, profile: dict[str, Any],) -> dict[str, Any]:
        device = self.scope.device
        writes: list[dict[str, Any]] = []

        for channel_key, settings in profile.get("channels", {}).items():
            channel = int(channel_key)
            for setting_name, value in settings.items():
                mapped = PROFILE_CHANNEL_SETTERS[setting_name]
                command_key, param_name = mapped
                params = {"channel": channel, param_name: value}
                scpi = self.protocol_write(command_key, params)
                writes.append({"key": command_key, "params": params, "scpi": scpi})

        for setting_name, value in profile.get("timebase", {}).items():
            mapped = PROFILE_TIMEBASE_SETTERS[setting_name]
            command_key, param_name = mapped
            params = {param_name: value}
            scpi = self.protocol_write(command_key, params)
            writes.append({"key": command_key, "params": params, "scpi": scpi})

        trigger_settings = profile.get("trigger", {})
        trigger_mode = trigger_settings.get("mode")
        if trigger_mode is None:
            trigger_mode = self.protocol_query("trigger_mode_get")
        if "mode" in trigger_settings:
            params = {"mode": trigger_settings["mode"]}
            scpi = self.protocol_write("trigger_mode_set", params)
            writes.append({"key": "trigger_mode_set", "params": params, "scpi": scpi})
            trigger_mode = trigger_settings["mode"]
        for setting_name, command_key in (
            ("source", "trigger_source_set"),
            ("level", "trigger_level_set"),
            ("sweep", "trigger_sweep_set"),
        ):
            if setting_name in trigger_settings:
                params = {"mode": trigger_mode, setting_name: trigger_settings[setting_name]}
                scpi = self.protocol_write(command_key, params)
                writes.append({"key": command_key, "params": params, "scpi": scpi})
        if "holdoff" in trigger_settings:
            params = {"holdoff": trigger_settings["holdoff"]}
            scpi = self.protocol_write("trigger_holdoff_set", params)
            writes.append({"key": "trigger_holdoff_set", "params": params, "scpi": scpi})

        for setting_name, value in profile.get("acquire", {}).items():
            if setting_name == "sampling_rate":
                continue
            mapped = PROFILE_ACQUIRE_SETTERS[setting_name]
            command_key, param_name = mapped
            params = {param_name: value}
            scpi = self.protocol_write(command_key, params)
            writes.append({"key": command_key, "params": params, "scpi": scpi})

        for setting_name, value in profile.get("waveform", {}).items():
            command_key = PROFILE_WAVEFORM_SETTERS[setting_name]
            params = {setting_name: value}
            scpi = self.protocol_write(command_key, params)
            writes.append({"key": command_key, "params": params, "scpi": scpi})

        session_settings = profile.get("session", {})
        for setting_name, command_key in (("run", "run"), ("stop", "stop"), ("force_trigger", "force_trigger")):
            if session_settings.get(setting_name):
                scpi = self.protocol_write(command_key)
                writes.append({"key": command_key, "params": {}, "scpi": scpi})

        return {
            "device": device,
            "status": "ok",
            "writes": writes,
        }

    def prepare_to_capture_spi_bus(self, channels: list[int], trigger_mode: str, sweep: str, points_mode: str, run: bool,) -> dict[str, Any]:
        device = self.scope.device
        writes: list[dict[str, Any]] = []

        for channel in channels:
            params = {"channel": channel, "state": "ON"}
            scpi = self.protocol_write("channel_display_set", params)
            writes.append({"key": "channel_display_set", "params": params, "scpi": scpi})

        for key, params in (
            ("trigger_mode_set", {"mode": trigger_mode}),
            ("trigger_sweep_set", {"mode": trigger_mode, "sweep": sweep}),
            ("waveform_points_mode_set", {"points_mode": points_mode}),
        ):
            scpi = self.protocol_write(key, params)
            writes.append({"key": key, "params": params, "scpi": scpi})

        if run:
            scpi = self.protocol_write("run")
            writes.append({"key": "run", "params": {}, "scpi": scpi})

        return {
            "device": device,
            "status": "ok",
            "writes": writes,
        }

    def arm_spi_capture(self) -> dict[str, Any]:
        device = self.scope.device
        scpi = self.protocol_write("run")
        status = self.require_waiting_scope()
        return {
            "device": device,
            "status": "ok",
            "writes": [{"key": "run", "params": {}, "scpi": scpi}],
            "trigger_status": status,
        }

    def scope_setup_for_spi_bus_analysis(
        self,
        clock_channel: int,
        data_channel: int,
        delay: float,
        clock_vertical_scale: float,
        trigger_level: float,
        timebase_scale: str,
        verify: bool,
    ) -> dict[str, Any]:
        device = self.scope.device
        setup_commands = (
            ":STOP",
            f":CHAN{clock_channel}:DISP ON",
            f":CHAN{data_channel}:DISP ON",
            f":CHAN{clock_channel}:SCALe {clock_vertical_scale:.1f}",
            ":TRIGger:MODE EDGE",
            f":TRIGger:EDGE:SOURce CHAN{clock_channel}",
            f":TRIGger:EDGE:LEVel {trigger_level:.2f}",
            ":TRIGger:EDGE:SWEep SING",
            ":WAVeform:POINts:MODE RAW",
            f":TIMebase:SCALe {timebase_scale}",
        )

        writes: list[dict[str, Any]] = []
        with self.scope._io_lock:
            fd = self.scope.open()      # Creates or returns already open port
            for scpi in setup_commands:
                normalized_scpi = validate_acceptable_scpi_command(scpi)
                os.write(fd, RigolDS1102E._normalize_command(normalized_scpi))
                time.sleep(delay)
                writes.append({"scpi": normalized_scpi, "status": "ok"})

        result: dict[str, Any] = {
            "device": device,
            "status": "ok",
            "writes": writes,
        }
        if verify:
            result["verification"] = self.build_scope_config([clock_channel, data_channel])
        return result

    def data_capture(self, channels: list[int], freeze: bool, points_mode: str, encoding: str,) -> dict[str, Any]:
        device = self.scope.device
        writes, raw_channels = self.capture_waveform_channels(
            channels,
            freeze,
            points_mode,
        )

        captured_channels: dict[str, Any] = {}
        for channel in channels:
            data = raw_channels[str(channel)]
            captured_channels[str(channel)] = {
                "scpi": render_command("waveform_data_get", channel=channel),
                "summary": summarize_waveform_data(data),
                "encoding": encoding,
                "data": encode_waveform_data(data, encoding),
            }

        return {
            "device": device,
            "status": "ok",
            "writes": writes,
            "channels": captured_channels,
        }

    def spi_sample_indexes(
        self,
        clock_scope_channel: int,
        data_scope_channel: int,
        freeze: bool,
        points_mode: str,
        clock_low_ratio: float,
        clock_high_ratio: float,
    ) -> dict[str, Any]:
        device = self.scope.device
        writes, raw_channels = self.capture_waveform_channels(
            [clock_scope_channel, data_scope_channel],
            freeze,
            points_mode,
        )

        clock_samples = normalize_waveform_samples(raw_channels[str(clock_scope_channel)])
        data_samples = normalize_waveform_samples(raw_channels[str(data_scope_channel)])
        sample_indexes = detect_rising_edge_sample_indexes(
            clock_samples,
            low_ratio=clock_low_ratio,
            high_ratio=clock_high_ratio,
        )

        return {
            "device": device,
            "status": "ok",
            "writes": writes,
            "clock_scope_channel": clock_scope_channel,
            "data_scope_channel": data_scope_channel,
            "clock_low_ratio": clock_low_ratio,
            "clock_high_ratio": clock_high_ratio,
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
        }

    def spi_decode(
        self,
        clock_scope_channel: int,
        data_scope_channel: int,
        freeze: bool,
        points_mode: str,
        clock_low_ratio: float,
        clock_high_ratio: float,
        low_ratio: float,
        high_ratio: float,
        expected_writes: int | None,
        expected_addresses: list[int] | None,
        window_scan: bool,
        max_extra_edges: int,
        time_scale: float | None,
        time_scale_margin: float,
    ) -> dict[str, Any]:
        device = self.scope.device
        writes: list[dict[str, Any]] = []
        current_time_scale = time_scale
        if current_time_scale is None:
            response = self.protocol_query("timebase_scale_get")
            current_time_scale = float(response)
        if time_scale is not None:
            params = {"scale": time_scale}
            scpi = self.protocol_write("timebase_scale_set", params)
            writes.append({"key": "timebase_scale_set", "params": params, "scpi": scpi})

        capture_writes, raw_channels = self.capture_waveform_channels(
            [clock_scope_channel, data_scope_channel],
            freeze,
            points_mode,
        )
        writes.extend(capture_writes)

        clock_samples = normalize_waveform_samples(raw_channels[str(clock_scope_channel)])
        data_samples = normalize_waveform_samples(raw_channels[str(data_scope_channel)])
        sample_indexes = detect_rising_edge_sample_indexes(
            clock_samples,
            low_ratio=clock_low_ratio,
            high_ratio=clock_high_ratio,
        )

        if expected_writes is not None and window_scan:
            decoded = decode_spi_data_words_windowed(
                data_samples,
                sample_indexes,
                expected_writes=expected_writes,
                max_extra_edges=max_extra_edges,
                low_ratio=low_ratio,
                high_ratio=high_ratio,
                expected_addresses=expected_addresses,
            )
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

        return {
            "device": device,
            "status": "ok",
            "writes": writes,
            "clock_scope_channel": clock_scope_channel,
            "data_scope_channel": data_scope_channel,
            "clock_low_ratio": clock_low_ratio,
            "clock_high_ratio": clock_high_ratio,
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
        }

    def protocol_command(self, key: str, params: dict[str, Any] | None,) -> dict[str, Any]:
        command = get_command(key)
        scpi = render_command(key, **(params or {}))

        device = self.scope.device
        result: dict[str, Any] = {
            "device": device,
            "key": command.key,
            "family": command.family,
            "kind": command.kind,
            "scpi": scpi,
        }
        if command.kind == "query":
            result["response"] = self.scope_query(scpi)
            return result

        self.scope_write(scpi)
        result["status"] = "ok"
        return result

    def scope_io(self, delay: float | None = None, read_size: int | None = None,) -> dict[str, Any]:
        scope = self.scope
        if delay is not None:
            scope.query_delay = delay
        if read_size is not None:
            scope.read_size = read_size
        return {
            "device": scope.device,
            "delay": scope.query_delay,
            "read_size": scope.read_size,
        }

_MANAGED_SCOPE: ManagedScopeState | None = None


def managed_scope() -> ManagedScopeState:
    """Return the cached scope connection, opening it on first use.

    The MCP server can start before the Rigol is connected because this delays
    device discovery until a scope-dependent tool is called.
    """
    global _MANAGED_SCOPE
    if _MANAGED_SCOPE is None:
        _MANAGED_SCOPE = ManagedScopeState()
    return _MANAGED_SCOPE

VALID_SCOPE_CHANNELS = (1, 2)
VALID_WAVEFORM_ENCODINGS = ("list", "base64", "hex", "none")
VALID_SPI_SOURCE_NAMES = ("chan_1", "chan_2")
WRITABLE_PROFILE_TOP_LEVEL_KEYS = ("channels", "timebase", "trigger", "acquire", "waveform", "session")
READBACK_ONLY_PROFILE_TOP_LEVEL_KEYS = ("device", "identity")
VALID_PROFILE_TRIGGER_KEYS = ("mode", "source", "level", "sweep", "holdoff")
VALID_PROFILE_SESSION_KEYS = ("run", "stop", "force_trigger")
READBACK_ONLY_PROFILE_ACQUIRE_KEYS = ("sampling_rate",)
READBACK_ONLY_PROFILE_SESSION_KEYS = ("trigger_status",)


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


def validate_integer_range(name: str, value: int, minimum: int, maximum: int,) -> None:
    if not minimum <= int(value) <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")


def validate_float_range(name: str, value: float, minimum: float, maximum: float, suffix: str = "",) -> None:
    numeric = float(value)
    if not minimum <= numeric <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}{suffix}")


def validate_choice(name: str, value: str, allowed: tuple[str, ...],) -> None:
    if value not in allowed:
        allowed_text = ", ".join(allowed)
        if len(allowed) == 2:
            allowed_text = f"{allowed[0]!r} or {allowed[1]!r}"
        raise ValueError(f"{name} must be {allowed_text}")


def resolve_scope_channels(channels: list[int] | None) -> list[int]:
    selected = channels or [1, 2]
    resolved = []
    for channel in selected:
        value = resolve_scope_channel(channel, "channel")
        if value not in resolved:
            resolved.append(value)
    return resolved


def extract_writable_profile(profile: dict[str, Any],) -> dict[str, Any]:
    from_readback = "normalized_scope_data" in profile and isinstance(profile["normalized_scope_data"], dict)
    candidate = profile
    if from_readback:
        candidate = profile["normalized_scope_data"]
    allowed_top_level_keys = set(WRITABLE_PROFILE_TOP_LEVEL_KEYS)
    if from_readback:
        allowed_top_level_keys.update(READBACK_ONLY_PROFILE_TOP_LEVEL_KEYS)
    for section_name in candidate:
        if section_name not in allowed_top_level_keys:
            raise ValueError(f"unsupported profile section: {section_name}")

    writable_profile = {
        section_name: candidate[section_name]
        for section_name in WRITABLE_PROFILE_TOP_LEVEL_KEYS
        if section_name in candidate
    }

    resolve_scope_channels(list(writable_profile.get("channels", {}).keys()))
    for settings in writable_profile.get("channels", {}).values():
        for setting_name in settings:
            if setting_name not in PROFILE_CHANNEL_SETTERS:
                raise ValueError(f"unsupported channel setting: {setting_name}")
    for setting_name in writable_profile.get("timebase", {}):
        if setting_name not in PROFILE_TIMEBASE_SETTERS:
            raise ValueError(f"unsupported timebase setting: {setting_name}")
    for setting_name in writable_profile.get("trigger", {}):
        if setting_name not in VALID_PROFILE_TRIGGER_KEYS:
            raise ValueError(f"unsupported trigger setting: {setting_name}")
    acquire_settings = dict(writable_profile.get("acquire", {}))
    for setting_name in list(acquire_settings):
        if setting_name in READBACK_ONLY_PROFILE_ACQUIRE_KEYS:
            if not from_readback:
                raise ValueError(f"readback-only acquire setting: {setting_name}")
            acquire_settings.pop(setting_name)
            continue
        if setting_name not in PROFILE_ACQUIRE_SETTERS:
            raise ValueError(f"unsupported acquire setting: {setting_name}")
    if acquire_settings:
        writable_profile["acquire"] = acquire_settings
    else:
        writable_profile.pop("acquire", None)
    for setting_name in writable_profile.get("waveform", {}):
        if setting_name not in PROFILE_WAVEFORM_SETTERS:
            raise ValueError(f"unsupported waveform setting: {setting_name}")
    session_settings = dict(writable_profile.get("session", {}))
    for setting_name in list(session_settings):
        if setting_name in READBACK_ONLY_PROFILE_SESSION_KEYS:
            if not from_readback:
                raise ValueError(f"readback-only session setting: {setting_name}")
            session_settings.pop(setting_name)
            continue
        if setting_name not in VALID_PROFILE_SESSION_KEYS:
            raise ValueError(f"unsupported session setting: {setting_name}")
    if session_settings:
        writable_profile["session"] = session_settings
    else:
        writable_profile.pop("session", None)
    return writable_profile


def validate_waveform_encoding(encoding: str,) -> None:
    if encoding not in VALID_WAVEFORM_ENCODINGS:
        raise ValueError("encoding must be one of: list, base64, hex, none")


def resolve_scope_channel(channel: int, name: str) -> int:
    value = int(channel)
    if value not in VALID_SCOPE_CHANNELS:
        raise ValueError(f"{name} must be 1 or 2, got {value}")
    return value


def resolve_spi_sources(
    chan_1: int | None,
    chan_2: int | None,
    clock_source: str | None,
    data_source: str | None,
) -> tuple[int, int, dict[str, Any]]:
    capture_map = {
        "chan_1": resolve_scope_channel(1 if chan_1 is None else chan_1, "chan_1"),
        "chan_2": resolve_scope_channel(2 if chan_2 is None else chan_2, "chan_2"),
    }
    resolved_clock_source = "chan_1" if clock_source is None else str(clock_source)
    resolved_data_source = "chan_2" if data_source is None else str(data_source)
    validate_choice("clock_source", resolved_clock_source, VALID_SPI_SOURCE_NAMES)
    validate_choice("data_source", resolved_data_source, VALID_SPI_SOURCE_NAMES)
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


def validate_protocol_command_request(key: str,) -> None:
    if key not in PROTOCOL:
        raise ValueError(f"unsupported protocol command: {key}")
    if not is_supported_protocol_command(key):
        raise ValueError(f"protocol command is excluded: {key}")


def validate_scope_io_request(delay: float | None, read_size: int | None,) -> None:
    if delay is not None and delay < 0:
        raise ValueError("delay must be non-negative")
    if read_size is not None and read_size < 1:
        raise ValueError("read_size must be at least 1")


def validate_spi_sample_indexes_request(clock_low_ratio: float, clock_high_ratio: float,) -> None:
    validate_float_range("clock_low_ratio", clock_low_ratio, 0.05, 0.45)
    validate_float_range("clock_high_ratio", clock_high_ratio, 0.55, 0.95)
    if clock_low_ratio >= clock_high_ratio:
        raise ValueError("clock_low_ratio must be less than clock_high_ratio")


def validate_spi_decode_request(
    clock_low_ratio: float,
    clock_high_ratio: float,
    low_ratio: float,
    high_ratio: float,
    expected_writes: int | None,
    expected_addresses: list[int] | None,
    max_extra_edges: int,
    time_scale: float | None,
    time_scale_margin: float,
) -> list[int] | None:
    validate_spi_sample_indexes_request(clock_low_ratio, clock_high_ratio)
    validate_float_range("low_ratio", low_ratio, 0.05, 0.4)
    validate_float_range("high_ratio", high_ratio, 0.6, 0.95)
    if expected_writes is not None:
        validate_integer_range("expected_writes", expected_writes, 1, 6)
    normalized_addresses = None
    if expected_addresses is not None:
        normalized_addresses = [int(address) for address in expected_addresses]
        if not 1 <= len(normalized_addresses) <= 6:
            raise ValueError("expected_addresses must contain between 1 and 6 addresses")
        for address in normalized_addresses:
            if not 0 <= address <= 5:
                raise ValueError("expected_addresses values must be between 0 and 5")
        if expected_writes is not None and len(normalized_addresses) != expected_writes:
            raise ValueError("expected_addresses length must match expected_writes")
    validate_integer_range("max_extra_edges", max_extra_edges, 0, 16)
    if time_scale is not None:
        validate_float_range("time_scale", time_scale, 500e-9, 20e-6, " seconds/div")
    validate_float_range("time_scale_margin", time_scale_margin, 1.0, 2.0)
    return normalized_addresses


@mcp.tool()
@require_single_tool_call
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


@mcp.tool(name="list-tools")
async def user_list_tools() -> dict[str, Any]:
    """List the user-facing tools exposed by this server."""
    tools = await mcp.list_tools()
    tool_rows = [
        {
            "name": tool.name,
            "title": tool.title,
            "description": tool.description,
            "input_schema": tool.inputSchema,  # How to call a tool: Used by AI
        }
        for tool in tools
    ]
    lines = ["Available tools:"]
    for tool in tool_rows:
        title = f" ({tool['title']})" if tool["title"] else ""
        description = tool["description"] or "No description."
        lines.append(f"- {tool['name']}{title}: {description}")
    return {
        "server": mcp.name,
        "tool_count": len(tool_rows),
        "tools": tool_rows,
        "display_text": "\n".join(lines),
    }


@mcp.tool()
@require_single_tool_call
def rigol_ds1102e_identify() -> dict[str, Any]:
    """Query *IDN? from the scope."""
    return managed_scope().identify()


@mcp.tool()
@require_single_tool_call
def rigol_ds1102e_query(scpi: str,) -> dict[str, Any]:
    """Send a SCPI query and return the response."""
    return managed_scope().query(scpi)


@mcp.tool()
@require_single_tool_call
def rigol_ds1102e_write(scpi: str,) -> dict[str, Any]:
    """Send a SCPI command that does not expect a response."""
    return managed_scope().write(scpi)


@mcp.tool()
@require_single_tool_call
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
@require_single_tool_call
def rigol_ds1102e_get_scope_config(channels: list[int] | None = None,) -> dict[str, Any]:
    """Read the current scope configuration from the scope."""
    return managed_scope().get_scope_config(resolve_scope_channels(channels))


@mcp.tool()
@require_single_tool_call
def rigol_ds1102e_apply_profile(profile: dict[str, Any],) -> dict[str, Any]:
    """Apply multiple setup changes in one request."""
    return managed_scope().apply_profile(extract_writable_profile(profile))


@mcp.tool()
@require_single_tool_call
def rigol_ds1102e_prepare_to_capture_spi_bus(
    channels: list[int] | None = None,
    trigger_mode: str = "EDGE",
    sweep: str = "SINGLE",
    points_mode: str = "RAW",
    run: bool = False,
) -> dict[str, Any]:
    """Prepare the scope for single-trigger RAW waveform capture."""
    return managed_scope().prepare_to_capture_spi_bus(
        resolve_scope_channels(channels),
        trigger_mode,
        sweep,
        points_mode,
        run,
    )


@mcp.tool()
@require_single_tool_call
def rigol_ds1102e_arm_spi_capture() -> dict[str, Any]:
    """Arm a previously configured single-trigger SPI capture and require WAIT."""
    return managed_scope().arm_spi_capture()


@mcp.tool()
@require_single_tool_call
def rigol_ds1102e_setup_for_spi_bus_analysis(
    clock_channel: int = DEFAULT_SPI_CLOCK_CHANNEL,
    data_channel: int = DEFAULT_SPI_DATA_CHANNEL,
    delay: float = DEFAULT_SPI_SETUP_DELAY,
    clock_vertical_scale: float = DEFAULT_SPI_CLOCK_VERTICAL_SCALE,
    trigger_level: float = DEFAULT_SPI_TRIGGER_LEVEL,
    timebase_scale: str = DEFAULT_SPI_TIMEBASE_SCALE,
    verify: bool = False,
) -> dict[str, Any]:
    """Set up the scope for SPI bus analysis using the saTech full-test sequence."""
    resolved_clock_channel = resolve_scope_channel(clock_channel, "clock_channel")
    resolved_data_channel = resolve_scope_channel(data_channel, "data_channel")
    if resolved_clock_channel == resolved_data_channel:
        raise ValueError("clock_channel and data_channel must be different")
    if delay < 0:
        raise ValueError("delay must be non-negative")
    return managed_scope().scope_setup_for_spi_bus_analysis(
        resolved_clock_channel,
        resolved_data_channel,
        delay,
        clock_vertical_scale,
        trigger_level,
        timebase_scale,
        verify,
    )


@mcp.tool()
@require_single_tool_call
def rigol_ds1102e_data_capture(
    channels: list[int] | None = None,
    freeze: bool = True,
    points_mode: str = "RAW",
    encoding: str = "list",
) -> dict[str, Any]:
    """Read currently displayed waveform bytes from selected channels."""
    validate_waveform_encoding(encoding)
    return managed_scope().data_capture(
        resolve_scope_channels(channels),
        freeze,
        points_mode,
        encoding,
    )


@mcp.tool()
@require_single_tool_call
def rigol_ds1102e_spi_sample_indexes(
    chan_1: int | None = None,
    chan_2: int | None = None,
    clock_source: str | None = None,
    data_source: str | None = None,
    freeze: bool = True,
    points_mode: str = DEFAULT_SPI_POINTS_MODE,
    clock_low_ratio: float = DEFAULT_SPI_CLOCK_LOW_RATIO,
    clock_high_ratio: float = DEFAULT_SPI_CLOCK_HIGH_RATIO,
) -> dict[str, Any]:
    """Return clock sample indexes for SPI analysis after normalizing both channels."""
    clock_scope_channel, data_scope_channel, source_map = resolve_spi_sources(
        chan_1,
        chan_2,
        clock_source,
        data_source,
    )
    validate_spi_sample_indexes_request(clock_low_ratio, clock_high_ratio)
    result = managed_scope().spi_sample_indexes(
        clock_scope_channel,
        data_scope_channel,
        freeze,
        points_mode,
        clock_low_ratio,
        clock_high_ratio,
    )
    result.update(source_map)
    return result


@mcp.tool()
@require_single_tool_call
def rigol_ds1102e_spi_decode(
    chan_1: int | None = None,
    chan_2: int | None = None,
    clock_source: str | None = None,
    data_source: str | None = None,
    freeze: bool = True,
    points_mode: str = DEFAULT_SPI_POINTS_MODE,
    clock_low_ratio: float = DEFAULT_SPI_CLOCK_LOW_RATIO,
    clock_high_ratio: float = DEFAULT_SPI_CLOCK_HIGH_RATIO,
    low_ratio: float = DEFAULT_SPI_LOW_RATIO,
    high_ratio: float = DEFAULT_SPI_HIGH_RATIO,
    expected_writes: int | None = None,
    expected_addresses: list[int] | None = None,
    window_scan: bool = True,
    max_extra_edges: int = DEFAULT_SPI_MAX_EXTRA_EDGES,
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
    normalized_addresses = validate_spi_decode_request(
        clock_low_ratio,
        clock_high_ratio,
        low_ratio,
        high_ratio,
        expected_writes,
        expected_addresses,
        max_extra_edges,
        time_scale,
        time_scale_margin,
    )
    result = managed_scope().spi_decode(
        clock_scope_channel,
        data_scope_channel,
        freeze,
        points_mode,
        clock_low_ratio,
        clock_high_ratio,
        low_ratio,
        high_ratio,
        expected_writes,
        normalized_addresses,
        window_scan,
        max_extra_edges,
        time_scale,
        time_scale_margin,
    )
    result.update(source_map)
    return result


@mcp.tool()
@require_single_tool_call
def rigol_ds1102e_protocol_command(
    key: str,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Execute a supported protocol command by key using the registry metadata."""
    validate_protocol_command_request(key)
    return managed_scope().protocol_command(key, params)


@mcp.tool()
@require_single_tool_call
def rigol_ds1102e_scope_io(delay: float | None = None, read_size: int | None = None,) -> dict[str, Any]:
    """Get or update the managed scope query delay and read size."""
    validate_scope_io_request(delay, read_size)
    return managed_scope().scope_io(delay, read_size)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
