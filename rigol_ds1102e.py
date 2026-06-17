#!/usr/bin/env python3

import argparse
import os
from pathlib import Path
import sys
import threading
import time

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "SA-Technician-MCP"))
from hardware_ports import USBTMC_GLOB_PATTERNS, list_usbtmc_devices

DEFAULT_GLOB_PATTERNS = USBTMC_GLOB_PATTERNS
RIGOL_DS1102E_IDN_PREFIX = "RIGOL TECHNOLOGIES,DS1102E"


class RigolDS1102E:
    def __init__(self, device, query_delay=0.2, read_size=4096):
        self.device = device
        self.query_delay = query_delay
        self.read_size = read_size
        self._fd = None
        self._io_lock = threading.Lock()

    def write(self, command):
        payload = self._normalize_command(command)
        with self._io_lock:
            os.write(self.open(), payload)

    def query(self, command, delay=None, read_size=None):
        response = self.query_bytes(command, delay, read_size)
        return response.decode("ascii", "replace").replace("\x00", "").strip()

    def query_bytes(self, command, delay=None, read_size=None):
        payload = self._normalize_command(command)
        wait_time = self.query_delay if delay is None else delay
        max_bytes = self.read_size if read_size is None else read_size
        with self._io_lock:
            fd = self.open()
            os.write(fd, payload)
            time.sleep(wait_time)
            return os.read(fd, max_bytes)

    def identify(self):
        return self.query("*IDN?")

    def open(self):
        if self._fd is None:
            self._fd = self._open_device()
        return self._fd

    def close(self):
        with self._io_lock:
            self._close_unlocked()

    def reconnect(self, device=None):
        with self._io_lock:
            self._close_unlocked()
            if device is not None:
                self.device = device
            return self.open()

    def _close_unlocked(self):
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None

    def _open_device(self):
        try:
            return os.open(self.device, os.O_RDWR)
        except FileNotFoundError as exc:
            raise RuntimeError(
                f"USBTMC device {self.device!r} was not found. Check that the scope is connected "
                "and that the usbtmc kernel driver created the device node."
            ) from exc
        except PermissionError as exc:
            raise RuntimeError(
                f"Permission denied opening {self.device!r}. Check the device group and your "
                "current session's supplementary groups."
            ) from exc

    @staticmethod
    def _normalize_command(command):
        text = command.rstrip("\r\n")
        return f"{text}\n".encode("ascii")


def list_candidate_devices():
    return list_usbtmc_devices()


def discover_ds1102e_device(query_delay=0.2, read_size=4096):
    for device in list_candidate_devices():
        scope = RigolDS1102E(device=device, query_delay=query_delay, read_size=read_size)
        try:
            identity = scope.identify()
        except Exception:
            continue
        finally:
            scope.close()
        if identity.startswith(RIGOL_DS1102E_IDN_PREFIX):
            return device
    raise RuntimeError(
        "Could not find a DS1102E by probing USBTMC devices with *IDN?. "
        "Try plugging in the usb cable to the scope."
    )


def build_parser():
    parser = argparse.ArgumentParser(description="Minimal Rigol DS1102E USBTMC helper")
    parser.add_argument(
        "--delay",
        type=float,
        default=0.2,
        help="Seconds to wait before reading a query response",
    )
    parser.add_argument(
        "--read-size",
        type=int,
        default=4096,
        help="Maximum bytes to read from the scope",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("idn", help="Query *IDN?")

    query_parser = subparsers.add_parser("query", help="Send a SCPI query and print the response")
    query_parser.add_argument("scpi", help="SCPI query, for example ':CHAN1:SCAL?'")

    write_parser = subparsers.add_parser("write", help="Send a SCPI command with no readback")
    write_parser.add_argument("scpi", help="SCPI command, for example ':RUN'")

    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()
    device = discover_ds1102e_device(query_delay=args.delay, read_size=args.read_size)
    scope = RigolDS1102E(device=device, query_delay=args.delay, read_size=args.read_size)

    if args.command == "idn":
        print(scope.identify())
        return
    if args.command == "query":
        print(scope.query(args.scpi))
        return
    if args.command == "write":
        scope.write(args.scpi)
        return

    parser.error(f"Unsupported command: {args.command}")


if __name__ == "__main__":
    main()
