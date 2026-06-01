#!/usr/bin/env python3

from typing import Any


from rigol_ds1102e_protocol_commands import (
    ACQUIRE_COMMANDS,
    CHANNEL_COMMANDS,
    IDENTITY_COMMANDS,
    MEASURE_COMMANDS,
    ScpiCommand,
    SESSION_COMMANDS,
    TIMEBASE_COMMANDS,
    TRIGGER_COMMANDS,
    WAVEFORM_COMMANDS,
)


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
    ":STORage:FACTory:LOAD",    # Restores the system settings as the were before leaving factory.
)

EXCLUDED_PREFIXES: tuple[str, ...] = (
    ":BEEP:",   # Annoying!
    ":KEY:",    # Duplicates all other non-KEY SCPI commands
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
