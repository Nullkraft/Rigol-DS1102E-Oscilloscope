#!/usr/bin/env python3

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal


CommandKind = Literal["query", "write"]
CommandFamily = Literal[
    "identity",
    "session",
    "channel",
    "timebase",
    "trigger",
    "acquire",
    "measure",
    "waveform",
]


@dataclass(frozen=True)
class ScpiCommand:
    key: str
    template: str
    kind: CommandKind
    family: CommandFamily
    description: str
    args: tuple[str, ...] = ()
    response_hint: str | None = None
    requires_visible_channel: bool = False
    changes_scope_state: bool = False


IDENTITY_COMMANDS: dict[str, ScpiCommand] = {
    "identify": ScpiCommand(
        key="identify",
        template="*IDN?",
        kind="query",
        family="identity",
        description="Identify the connected instrument.",
        response_hint="ascii",
    ),
}


SESSION_COMMANDS: dict[str, ScpiCommand] = {
    "auto_setup": ScpiCommand(
        key="auto_setup",
        template=":AUTO",
        kind="write",
        family="session",
        description="Automatically recover a usable display and trigger setup.",
        changes_scope_state=True,
    ),
    "run": ScpiCommand(
        key="run",
        template=":RUN",
        kind="write",
        family="session",
        description="Start acquisition.",
        changes_scope_state=True,
    ),
    "stop": ScpiCommand(
        key="stop",
        template=":STOP",
        kind="write",
        family="session",
        description="Stop acquisition.",
        changes_scope_state=True,
    ),
    "force_trigger": ScpiCommand(
        key="force_trigger",
        template=":FORCetrig",
        kind="write",
        family="session",
        description="Force a trigger event.",
        changes_scope_state=True,
    ),
    "trigger_status": ScpiCommand(
        key="trigger_status",
        template=":TRIGger:STATus?",
        kind="query",
        family="session",
        description="Query trigger and run status.",
        response_hint="RUN|STOP|T'D|WAIT|AUTO",
    ),
}


CHANNEL_COMMANDS: dict[str, ScpiCommand] = {
    "channel_display_get": ScpiCommand(
        key="channel_display_get",
        template=":CHAN{channel}:DISP?",
        kind="query",
        family="channel",
        description="Query whether a channel is displayed.",
        args=("channel",),
        response_hint="ON|OFF",
    ),
    "channel_display_set": ScpiCommand(
        key="channel_display_set",
        template=":CHAN{channel}:DISP {state}",
        kind="write",
        family="channel",
        description="Enable or disable a channel display.",
        args=("channel", "state"),
        changes_scope_state=True,
    ),
    "channel_coupling_get": ScpiCommand(
        key="channel_coupling_get",
        template=":CHAN{channel}:COUP?",
        kind="query",
        family="channel",
        description="Query channel coupling.",
        args=("channel",),
        response_hint="DC|AC|GND",
    ),
    "channel_coupling_set": ScpiCommand(
        key="channel_coupling_set",
        template=":CHAN{channel}:COUP {coupling}",
        kind="write",
        family="channel",
        description="Set channel coupling.",
        args=("channel", "coupling"),
        changes_scope_state=True,
    ),
    "channel_probe_get": ScpiCommand(
        key="channel_probe_get",
        template=":CHAN{channel}:PROB?",
        kind="query",
        family="channel",
        description="Query probe attenuation ratio.",
        args=("channel",),
        response_hint="float",
    ),
    "channel_probe_set": ScpiCommand(
        key="channel_probe_set",
        template=":CHAN{channel}:PROB {probe}",
        kind="write",
        family="channel",
        description="Set probe attenuation ratio.",
        args=("channel", "probe"),
        changes_scope_state=True,
    ),
    "channel_scale_get": ScpiCommand(
        key="channel_scale_get",
        template=":CHAN{channel}:SCAL?",
        kind="query",
        family="channel",
        description="Query volts per division.",
        args=("channel",),
        response_hint="float",
    ),
    "channel_scale_set": ScpiCommand(
        key="channel_scale_set",
        template=":CHAN{channel}:SCAL {scale}",
        kind="write",
        family="channel",
        description="Set volts per division.",
        args=("channel", "scale"),
        changes_scope_state=True,
    ),
    "channel_offset_get": ScpiCommand(
        key="channel_offset_get",
        template=":CHAN{channel}:OFFS?",
        kind="query",
        family="channel",
        description="Query vertical offset.",
        args=("channel",),
        response_hint="float",
    ),
    "channel_offset_set": ScpiCommand(
        key="channel_offset_set",
        template=":CHAN{channel}:OFFS {offset}",
        kind="write",
        family="channel",
        description="Set vertical offset.",
        args=("channel", "offset"),
        changes_scope_state=True,
    ),
}


TIMEBASE_COMMANDS: dict[str, ScpiCommand] = {
    "timebase_scale_get": ScpiCommand(
        key="timebase_scale_get",
        template=":TIMebase:SCALe?",
        kind="query",
        family="timebase",
        description="Query time per division.",
        response_hint="float",
    ),
    "timebase_scale_set": ScpiCommand(
        key="timebase_scale_set",
        template=":TIMebase:SCALe {scale}",
        kind="write",
        family="timebase",
        description="Set time per division.",
        args=("scale",),
        changes_scope_state=True,
    ),
    "timebase_offset_get": ScpiCommand(
        key="timebase_offset_get",
        template=":TIMebase:OFFSet?",
        kind="query",
        family="timebase",
        description="Query horizontal offset.",
        response_hint="float",
    ),
    "timebase_offset_set": ScpiCommand(
        key="timebase_offset_set",
        template=":TIMebase:OFFSet {offset}",
        kind="write",
        family="timebase",
        description="Set horizontal offset.",
        args=("offset",),
        changes_scope_state=True,
    ),
}


TRIGGER_COMMANDS: dict[str, ScpiCommand] = {
    "trigger_mode_get": ScpiCommand(
        key="trigger_mode_get",
        template=":TRIGger:MODE?",
        kind="query",
        family="trigger",
        description="Query trigger mode.",
        response_hint="enum",
    ),
    "trigger_mode_set": ScpiCommand(
        key="trigger_mode_set",
        template=":TRIGger:MODE {mode}",
        kind="write",
        family="trigger",
        description="Set trigger mode.",
        args=("mode",),
        changes_scope_state=True,
    ),
    "trigger_source_get": ScpiCommand(
        key="trigger_source_get",
        template=":TRIGger:{mode}:SOURce?",
        kind="query",
        family="trigger",
        description="Query trigger source for the selected trigger mode.",
        args=("mode",),
        response_hint="CHAN1|CHAN2|EXT|ACLine",
    ),
    "trigger_source_set": ScpiCommand(
        key="trigger_source_set",
        template=":TRIGger:{mode}:SOURce {source}",
        kind="write",
        family="trigger",
        description="Set trigger source for the selected trigger mode.",
        args=("mode", "source"),
        changes_scope_state=True,
    ),
    "trigger_level_get": ScpiCommand(
        key="trigger_level_get",
        template=":TRIGger:{mode}:LEVel?",
        kind="query",
        family="trigger",
        description="Query trigger level for the selected trigger mode.",
        args=("mode",),
        response_hint="float",
    ),
    "trigger_level_set": ScpiCommand(
        key="trigger_level_set",
        template=":TRIGger:{mode}:LEVel {level}",
        kind="write",
        family="trigger",
        description="Set trigger level for the selected trigger mode.",
        args=("mode", "level"),
        changes_scope_state=True,
    ),
    "trigger_sweep_get": ScpiCommand(
        key="trigger_sweep_get",
        template=":TRIGger:{mode}:SWEep?",
        kind="query",
        family="trigger",
        description="Query trigger sweep mode.",
        args=("mode",),
        response_hint="AUTO|NORM|SING",
    ),
    "trigger_sweep_set": ScpiCommand(
        key="trigger_sweep_set",
        template=":TRIGger:{mode}:SWEep {sweep}",
        kind="write",
        family="trigger",
        description="Set trigger sweep mode.",
        args=("mode", "sweep"),
        changes_scope_state=True,
    ),
    "trigger_holdoff_get": ScpiCommand(
        key="trigger_holdoff_get",
        template=":TRIGger:HOLDoff?",
        kind="query",
        family="trigger",
        description="Query trigger holdoff.",
        response_hint="float",
    ),
    "trigger_holdoff_set": ScpiCommand(
        key="trigger_holdoff_set",
        template=":TRIGger:HOLDoff {holdoff}",
        kind="write",
        family="trigger",
        description="Set trigger holdoff.",
        args=("holdoff",),
        changes_scope_state=True,
    ),
}


ACQUIRE_COMMANDS: dict[str, ScpiCommand] = {
    "acquire_type_get": ScpiCommand(
        key="acquire_type_get",
        template=":ACQuire:TYPE?",
        kind="query",
        family="acquire",
        description="Query acquisition type.",
        response_hint="NORM|AVER|PEAK",
    ),
    "acquire_type_set": ScpiCommand(
        key="acquire_type_set",
        template=":ACQuire:TYPE {acquire_type}",
        kind="write",
        family="acquire",
        description="Set acquisition type.",
        args=("acquire_type",),
        changes_scope_state=True,
    ),
    "acquire_mode_get": ScpiCommand(
        key="acquire_mode_get",
        template=":ACQuire:MODE?",
        kind="query",
        family="acquire",
        description="Query acquisition mode.",
        response_hint="REAL|EQU",
    ),
    "acquire_mode_set": ScpiCommand(
        key="acquire_mode_set",
        template=":ACQuire:MODE {mode}",
        kind="write",
        family="acquire",
        description="Set acquisition mode.",
        args=("mode",),
        changes_scope_state=True,
    ),
    "acquire_averages_get": ScpiCommand(
        key="acquire_averages_get",
        template=":ACQuire:AVERages?",
        kind="query",
        family="acquire",
        description="Query averaging count.",
        response_hint="int",
    ),
    "acquire_averages_set": ScpiCommand(
        key="acquire_averages_set",
        template=":ACQuire:AVERages {count}",
        kind="write",
        family="acquire",
        description="Set averaging count.",
        args=("count",),
        changes_scope_state=True,
    ),
    "acquire_memory_depth_get": ScpiCommand(
        key="acquire_memory_depth_get",
        template=":ACQuire:MEMDepth?",
        kind="query",
        family="acquire",
        description="Query acquisition memory depth.",
        response_hint="enum",
    ),
    "acquire_memory_depth_set": ScpiCommand(
        key="acquire_memory_depth_set",
        template=":ACQuire:MEMDepth {depth}",
        kind="write",
        family="acquire",
        description="Set acquisition memory depth.",
        args=("depth",),
        changes_scope_state=True,
    ),
    "acquire_sampling_rate_get": ScpiCommand(
        key="acquire_sampling_rate_get",
        template=":ACQuire:SAMPlingrate? CHANnel{channel}",
        kind="query",
        family="acquire",
        description="Query sampling rate for an analog channel.",
        args=("channel",),
        response_hint="float",
    ),
}


MEASURE_COMMANDS: dict[str, ScpiCommand] = {
    "measure_clear": ScpiCommand(
        key="measure_clear",
        template=":MEASure:CLEar",
        kind="write",
        family="measure",
        description="Clear displayed measurement results.",
        changes_scope_state=True,
    ),
    "measure_frequency": ScpiCommand(
        key="measure_frequency",
        template=":MEASure:FREQuency? CHAN{channel}",
        kind="query",
        family="measure",
        description="Measure signal frequency.",
        args=("channel",),
        response_hint="float_or_stars",
        requires_visible_channel=True,
    ),
    "measure_period": ScpiCommand(
        key="measure_period",
        template=":MEASure:PERiod? CHAN{channel}",
        kind="query",
        family="measure",
        description="Measure signal period.",
        args=("channel",),
        response_hint="float_or_stars",
        requires_visible_channel=True,
    ),
    "measure_vpp": ScpiCommand(
        key="measure_vpp",
        template=":MEASure:VPP? CHAN{channel}",
        kind="query",
        family="measure",
        description="Measure peak-to-peak voltage.",
        args=("channel",),
        response_hint="float_or_stars",
        requires_visible_channel=True,
    ),
    "measure_vrms": ScpiCommand(
        key="measure_vrms",
        template=":MEASure:VRMS? CHAN{channel}",
        kind="query",
        family="measure",
        description="Measure RMS voltage.",
        args=("channel",),
        response_hint="float_or_stars",
        requires_visible_channel=True,
    ),
    "measure_vavg": ScpiCommand(
        key="measure_vavg",
        template=":MEASure:VAVerage? CHAN{channel}",
        kind="query",
        family="measure",
        description="Measure average voltage.",
        args=("channel",),
        response_hint="float_or_stars",
        requires_visible_channel=True,
    ),
    "measure_vmax": ScpiCommand(
        key="measure_vmax",
        template=":MEASure:VMAX? CHAN{channel}",
        kind="query",
        family="measure",
        description="Measure maximum voltage.",
        args=("channel",),
        response_hint="float_or_stars",
        requires_visible_channel=True,
    ),
    "measure_vmin": ScpiCommand(
        key="measure_vmin",
        template=":MEASure:VMIN? CHAN{channel}",
        kind="query",
        family="measure",
        description="Measure minimum voltage.",
        args=("channel",),
        response_hint="float_or_stars",
        requires_visible_channel=True,
    ),
}


WAVEFORM_COMMANDS: dict[str, ScpiCommand] = {
    "waveform_points_mode_get": ScpiCommand(
        key="waveform_points_mode_get",
        template=":WAVeform:POINts:MODE?",
        kind="query",
        family="waveform",
        description="Query waveform point mode.",
        response_hint="NORM|MAX|RAW",
    ),
    "waveform_points_mode_set": ScpiCommand(
        key="waveform_points_mode_set",
        template=":WAVeform:POINts:MODE {points_mode}",
        kind="write",
        family="waveform",
        description="Set waveform point mode.",
        args=("points_mode",),
        changes_scope_state=True,
    ),
    "waveform_data_get": ScpiCommand(
        key="waveform_data_get",
        template=":WAVeform:DATA? CHAN{channel}",
        kind="query",
        family="waveform",
        description="Read waveform data for a visible analog channel.",
        args=("channel",),
        response_hint="waveform_block",
        requires_visible_channel=True,
    ),
}


PROTOCOL: dict[str, ScpiCommand] = {
    **IDENTITY_COMMANDS,
    **SESSION_COMMANDS,
    **CHANNEL_COMMANDS,
    **TIMEBASE_COMMANDS,
    **TRIGGER_COMMANDS,
    **ACQUIRE_COMMANDS,
    **MEASURE_COMMANDS,
    **WAVEFORM_COMMANDS,
}


EXCLUDED_COMMANDS: tuple[str, ...] = (
    ":STORage:FACTory:LOAD",
)

EXCLUDED_PREFIXES: tuple[str, ...] = (
    ":BEEP",
    ":KEY:",
)

_ENUM_ALIASES: dict[str, dict[str, str]] = {
    "state": {
        "ON": "ON",
        "OFF": "OFF",
    },
    "coupling": {
        "AC": "AC",
        "DC": "DC",
        "GND": "GND",
    },
    "source": {
        "CHAN1": "CHAN1",
        "CHAN2": "CHAN2",
        "EXT": "EXT",
        "ACLINE": "ACLine",
    },
    "sweep": {
        "AUTO": "AUTO",
        "NORM": "NORM",
        "NORMAL": "NORM",
        "SING": "SING",
        "SINGLE": "SING",
    },
    "acquire_type": {
        "NORM": "NORM",
        "NORMAL": "NORM",
        "AVER": "AVER",
        "AVERAGE": "AVER",
        "PEAK": "PEAK",
        "PEAKDETECT": "PEAK",
    },
    "depth": {
        "LONG": "LONG",
        "NORM": "NORM",
        "NORMAL": "NORM",
    },
    "points_mode": {
        "NORM": "NORM",
        "NORMAL": "NORM",
        "MAX": "MAX",
        "RAW": "RAW",
    },
}

_MODE_ALIASES: dict[str, dict[str, str]] = {
    "trigger": {
        "EDGE": "EDGE",
        "PULS": "PULS",
        "PULSE": "PULS",
        "VIDEO": "VIDEO",
        "SLOP": "SLOP",
        "SLOPE": "SLOP",
        "ALT": "ALT",
        "ALTERNATION": "ALT",
        "PATT": "PATT",
        "PATTERN": "PATT",
        "DUR": "DUR",
        "DURATION": "DUR",
    },
    "acquire": {
        "RTIM": "RTIM",
        "RTIME": "RTIM",
        "REAL": "RTIM",
        "REAL_TIME": "RTIM",
        "ETIM": "ETIM",
        "ETIME": "ETIM",
        "EQU": "ETIM",
        "EQUAL_TIME": "ETIM",
    },
}


def get_command(key: str) -> ScpiCommand:
    try:
        return PROTOCOL[key]
    except KeyError as exc:
        raise KeyError(f"unknown protocol command: {key}") from exc


def render_command(key: str, **params: Any) -> str:
    command = get_command(key)
    missing = [name for name in command.args if name not in params]
    if missing:
        raise ValueError(f"missing parameters for {key}: {', '.join(missing)}")
    rendered = command.template.format(**_normalized_params(command, params))
    return rendered


def is_excluded_command(scpi: str) -> bool:
    text = scpi.strip()
    if text in EXCLUDED_COMMANDS:
        return True
    return any(text.startswith(prefix) for prefix in EXCLUDED_PREFIXES)


def _normalized_params(command: ScpiCommand, params: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(params)
    if command.key == "timebase_scale_set" and "scale" in normalized:
        normalized["scale"] = _format_timebase_scale(normalized["scale"])
    if "channel" in normalized:
        channel = int(normalized["channel"])
        if channel not in (1, 2):
            raise ValueError(f"channel must be 1 or 2, got {channel}")
        normalized["channel"] = channel
    if "state" in normalized and isinstance(normalized["state"], bool):
        normalized["state"] = "ON" if normalized["state"] else "OFF"
    if "mode" in normalized and command.family in _MODE_ALIASES:
        normalized["mode"] = _normalize_enum(
            "mode",
            normalized["mode"],
            _MODE_ALIASES[command.family],
        )
    for name in ("state", "coupling", "source", "sweep", "acquire_type", "depth", "points_mode"):
        if name in normalized:
            normalized[name] = _normalize_enum(name, normalized[name], _ENUM_ALIASES[name])
    return normalized


def _format_timebase_scale(value: Any) -> str:
    seconds = float(value)
    for suffix, factor in (("s", 1.0), ("ms", 1e-3), ("us", 1e-6), ("ns", 1e-9)):
        scaled = seconds / factor
        if abs(scaled) >= 1.0:
            return f"{scaled:.12g}{suffix}"
    return f"{seconds / 1e-9:.12g}ns"


def _normalize_enum(name: str, value: Any, aliases: dict[str, str]) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string, got {type(value).__name__}")
    normalized = aliases.get(value.strip().upper())
    if normalized is None:
        allowed = ", ".join(sorted(set(aliases.values())))
        raise ValueError(f"{name} must be one of {allowed}, got {value!r}")
    return normalized


__all__ = [
    "ACQUIRE_COMMANDS",
    "CHANNEL_COMMANDS",
    "EXCLUDED_COMMANDS",
    "EXCLUDED_PREFIXES",
    "IDENTITY_COMMANDS",
    "MEASURE_COMMANDS",
    "PROTOCOL",
    "SESSION_COMMANDS",
    "ScpiCommand",
    "TIMEBASE_COMMANDS",
    "TRIGGER_COMMANDS",
    "WAVEFORM_COMMANDS",
    "get_command",
    "is_excluded_command",
    "render_command",
]
