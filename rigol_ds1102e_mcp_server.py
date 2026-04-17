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


DEFAULT_GLOB_PATTERNS = (
    "/dev/usbtmc*",
    "/dev/usbmisc/usbtmc*",
    "/dev/usb/usbtmc*",
)

mcp = FastMCP("rigol_ds1102e", json_response=True)


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def list_candidate_devices() -> list[str]:
    devices: list[str] = []
    for pattern in DEFAULT_GLOB_PATTERNS:
        devices.extend(glob.glob(pattern))
    return sorted(set(devices))


def build_scope(device: str, delay: float, read_size: int) -> RigolDS1102E:
    return RigolDS1102E(device=device, query_delay=delay, read_size=read_size)


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
    return {
        "timestamp": utc_timestamp(),
        "device": device,
        "scpi": scpi,
        "status": "ok",
    }


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
