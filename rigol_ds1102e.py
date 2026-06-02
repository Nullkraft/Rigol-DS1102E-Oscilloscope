#!/usr/bin/env python3

import argparse
import glob
import os
import time


DEFAULT_GLOB_PATTERNS = (
    "/dev/usbtmc*",
    "/dev/usbmisc/usbtmc*",
    "/dev/usb/usbtmc*",
)
RIGOL_DS1102E_IDN_PREFIX = "RIGOL TECHNOLOGIES,DS1102E"


class RigolDS1102E:
    def __init__(self, device, query_delay=0.2, read_size=4096):
        self.device = device
        self.query_delay = query_delay
        self.read_size = read_size

    def write(self, command):
        payload = self._normalize_command(command)
        fd = self._open_device()
        try:
            os.write(fd, payload)
        finally:
            os.close(fd)

    def query(self, command, delay=None, read_size=None):
        payload = self._normalize_command(command)
        wait_time = self.query_delay if delay is None else delay
        max_bytes = self.read_size if read_size is None else read_size
        fd = self._open_device()
        try:
            os.write(fd, payload)
            time.sleep(wait_time)
            response = os.read(fd, max_bytes)
        finally:
            os.close(fd)
        return response.decode("ascii", "replace").replace("\x00", "").strip()

    def identify(self):
        return self.query("*IDN?")

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
    devices = []
    for pattern in DEFAULT_GLOB_PATTERNS:
        devices.extend(glob.glob(pattern))
    return sorted(set(devices))


def discover_ds1102e_device(query_delay=0.2, read_size=4096):
    for device in list_candidate_devices():
        scope = RigolDS1102E(device=device, query_delay=query_delay, read_size=read_size)
        try:
            identity = scope.identify()
        except Exception:
            continue
        if identity.startswith(RIGOL_DS1102E_IDN_PREFIX):
            return device
    raise RuntimeError("Could not find a DS1102E by probing USBTMC devices with *IDN?.")


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
