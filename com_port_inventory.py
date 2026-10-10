#!/usr/bin/env python3
"""Inventory serial ports and match stable hardware IDs against a CSV registry.

The script never opens ports and never sends commands to connected devices.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import os
import re
import sys
import tempfile
import time
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from serial.tools import list_ports


CSV_FIELDS = (
    "device_id", "name", "expected_port", "notes", "vid", "pid",
    "serial_number", "manufacturer", "product", "last_seen",
    'physical_device_id', 'interface_id',
)


@dataclass(frozen=True, slots=True)
class PortInfo:
    port: str
    device_id: str
    description: str
    hwid: str
    vid: str
    pid: str
    serial_number: str
    manufacturer: str
    product: str
    location: str
    physical_device_id: str = ''
    interface_id: str = ''


def clean(value: object | None) -> str:
    return "" if value is None else str(value).strip()


def physical_device_id(port: object) -> str:
    """Prefer a USB serial number; fall back to the OS hardware identifier."""
    vid = getattr(port, "vid", None)
    pid = getattr(port, "pid", None)
    serial_number = clean(getattr(port, "serial_number", None))
    if vid is not None and pid is not None and serial_number:
        return f"USB:{vid:04X}:{pid:04X}:{serial_number}"

    hwid = clean(getattr(port, "hwid", None))
    if hwid and hwid.lower() != "n/a":
        digest = hashlib.sha256(hwid.upper().encode("utf-8")).hexdigest()[:16].upper()
        return f"HWID:{digest}"

    # This ID is explicitly marked unstable: the OS exposed no hardware identity.
    device = clean(getattr(port, "device", None))
    return f"PORT:{device.upper()}"


def usb_interface_id(port: object) -> str:
    match = re.search(r'\bMI_([0-9A-F]{2})\b', clean(getattr(port, 'hwid', None)), re.I)
    if match:
        return 'MI_' + match[1].upper()
    number = getattr(port, 'interface_number', None)
    if type(number) is int and 0 <= number <= 255:
        return f'IF_{number:02X}'
    # A descriptive interface name is not assumed unique. Collisions remain visible.
    interface = clean(getattr(port, 'interface', None))
    if interface:
        return 'LABEL_' + hashlib.sha256(interface.encode('utf-8')).hexdigest()[:12].upper()
    return ''


def stable_device_id(port: object) -> str:
    device = physical_device_id(port)
    interface = usb_interface_id(port)
    return f'{device}:IF:{interface}' if device.startswith('USB:') and interface else device


def scan_ports() -> list[PortInfo]:
    result = []
    for port in sorted(list_ports.comports(), key=lambda item: item.device):
        result.append(PortInfo(
            port=clean(port.device),
            device_id=stable_device_id(port),
            description=clean(port.description),
            hwid=clean(port.hwid),
            vid=f"{port.vid:04X}" if port.vid is not None else "",
            pid=f"{port.pid:04X}" if port.pid is not None else "",
            serial_number=clean(port.serial_number),
            manufacturer=clean(port.manufacturer),
            product=clean(port.product),
            location=clean(port.location),
            physical_device_id=physical_device_id(port),
            interface_id=usb_interface_id(port),
        ))
    return result


def read_registry(path: Path) -> dict[str, dict[str, str]]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        if not reader.fieldnames or "device_id" not in reader.fieldnames:
            raise ValueError(f"CSV {path} must contain a device_id column")
        if len(set(reader.fieldnames)) != len(reader.fieldnames):
            raise ValueError('CSV has duplicate column names')
        registry = {}
        for line, row in enumerate(reader, 2):
            identity = clean(row.get('device_id'))
            if not identity:
                continue
            if identity in registry:
                raise ValueError(f'CSV duplicate device_id {identity!r} at line {line}')
            if None in row:
                raise ValueError(f'CSV malformed row at line {line}')
            registry[identity] = {field: clean(row.get(field)) for field in (*CSV_FIELDS, *reader.fieldnames)}
        return registry


def write_registry(path: Path, registry: dict[str, dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    extra = sorted({field for row in registry.values() for field in row} - set(CSV_FIELDS))
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', dir=path.parent, prefix='.' + path.name + '.',
                                         suffix='.tmp', delete=False, encoding='utf-8-sig', newline='') as stream:
            temporary = Path(stream.name)
            writer = csv.DictWriter(stream, fieldnames=(*CSV_FIELDS, *extra))
            writer.writeheader()
            writer.writerows(registry[key] for key in sorted(registry))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def update_registry(
    registry: dict[str, dict[str, str]], ports: Iterable[PortInfo],
    update_existing: bool = False,
) -> int:
    changed = 0
    ports = list(ports)
    counts = Counter(port.device_id for port in ports)
    physical_counts = Counter(port.physical_device_id or port.device_id for port in ports)
    for port in ports:
        physical = port.physical_device_id or port.device_id
        if counts[port.device_id] > 1:
            raise ValueError(f'ambiguous port identity {port.device_id}; CSV was not changed')
        if physical != port.device_id and physical in registry:
            if physical_counts[physical] > 1:
                raise ValueError(f'legacy device ID {physical} matches multiple interfaces; migrate CSV explicitly')
            if not update_existing:
                raise ValueError('legacy interface ID needs --accept-current-ports migration')
    now = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    for port in ports:
        physical = port.physical_device_id or port.device_id
        if port.device_id not in registry and physical != port.device_id and physical in registry:
            row = registry.pop(physical)
            row['device_id'] = port.device_id
            registry[port.device_id] = row
            changed += 1
        row = registry.get(port.device_id)
        if row is None:
            registry[port.device_id] = {
                "device_id": port.device_id,
                "name": "",
                "expected_port": port.port,
                "notes": "",
                "vid": port.vid,
                "pid": port.pid,
                "serial_number": port.serial_number,
                "manufacturer": port.manufacturer,
                "product": port.product or port.description,
                "last_seen": now,
                'physical_device_id': physical,
                'interface_id': port.interface_id,
            }
            changed += 1
        elif update_existing:
            for field, value in {
                "expected_port": port.port, "vid": port.vid, "pid": port.pid,
                "serial_number": port.serial_number, "manufacturer": port.manufacturer,
                "product": port.product or port.description, "last_seen": now,
                'physical_device_id': physical, 'interface_id': port.interface_id,
            }.items():
                if row.get(field) != value:
                    row[field] = value
                    changed += 1
    return changed


def compare(ports: list[PortInfo], registry: dict[str, dict[str, str]]) -> list[tuple[str, PortInfo | None, dict[str, str] | None]]:
    rows = []
    present = set()
    counts = Counter(port.device_id for port in ports)
    physical_counts = Counter(port.physical_device_id or port.device_id for port in ports)
    for port in ports:
        known = registry.get(port.device_id)
        present.add(port.device_id)
        physical = port.physical_device_id or port.device_id
        if counts[port.device_id] > 1:
            status = 'AMBIGUOUS'
        elif known is None and physical in registry:
            known = registry[physical]
            present.add(physical)
            status = 'AMBIGUOUS' if physical_counts[physical] > 1 else 'LEGACY'
        elif known is None:
            status = "NEW"
        elif known.get("expected_port") and known["expected_port"].upper() != port.port.upper():
            status = "MOVED"
        else:
            status = "OK"
        rows.append((status, port, known))
    for device_id, known in registry.items():
        if device_id not in present:
            rows.append(("ABSENT", None, known))
    return rows


def print_report(ports: list[PortInfo], registry: dict[str, dict[str, str]]) -> None:
    rows = compare(ports, registry)
    print(f"Serial ports: {len(ports)}; registry entries: {len(registry)}")
    print(f"{'STATUS':<7} {'PORT':<8} {'NAME':<24} DEVICE ID")
    print("-" * 100)
    for status, port, known in rows:
        actual_port = port.port if port else "-"
        name = (known or {}).get("name") or (port.description if port else "")
        device_id = port.device_id if port else (known or {}).get("device_id", "")
        print(f"{status:<7} {actual_port:<8} {name[:24]:<24} {device_id}")
        if port:
            print(f"        VID:PID={port.vid or '-'}:{port.pid or '-'} serial={port.serial_number or '-'}")
            print(f"        manufacturer={port.manufacturer or '-'} product={port.product or port.description or '-'}")
            print(f"        location={port.location or '-'} hwid={port.hwid or '-'}")
        if status == "MOVED" and known:
            print(f"        expected_port={known.get('expected_port') or '-'} actual_port={actual_port}")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", type=Path, default=Path("com_devices.csv"), help="device registry CSV")
    parser.add_argument("--add-new", action="store_true", help="append unknown devices to CSV")
    parser.add_argument(
        "--accept-current-ports", action="store_true",
        help="also replace expected_port for known devices with the current port",
    )
    parser.add_argument(
        "--watch", type=float, metavar="SECONDS", default=0,
        help="repeat scanning at this interval; zero performs one scan",
    )
    return parser.parse_args(argv)


def run_once(args: argparse.Namespace) -> None:
    ports = scan_ports()
    registry = read_registry(args.csv)
    print_report(ports, registry)
    if args.add_new or args.accept_current_ports:
        changes = update_registry(registry, ports, args.accept_current_ports)
        if changes or not args.csv.exists():
            write_registry(args.csv, registry)
        print(f"CSV updated: {args.csv} ({changes} field changes)")


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        while True:
            run_once(args)
            if args.watch <= 0:
                return 0
            time.sleep(max(0.2, args.watch))
            print()
    except (OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
