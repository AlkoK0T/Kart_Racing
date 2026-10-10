import copy
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import com_port_inventory as inventory


class ComRegistryTests(unittest.TestCase):
    def port(self, number=0, com='COM12'):
        raw = SimpleNamespace(vid=0x483, pid=0xA26A, serial_number='serial',
                              hwid=f'USB VID_0483&PID_A26A&MI_{number:02X}', device=com)
        return inventory.PortInfo(com, inventory.stable_device_id(raw), '', raw.hwid,
                                  '0483', 'A26A', 'serial', '', '', '',
                                  inventory.physical_device_id(raw), inventory.usb_interface_id(raw))

    def test_interfaces_are_distinct_and_move_is_visible(self):
        first, second = self.port(0), self.port(1, 'COM13')
        self.assertNotEqual(first.device_id, second.device_id)
        self.assertEqual(first.physical_device_id, second.physical_device_id)
        registry = {}
        inventory.update_registry(registry, [first, second])
        moved = self.port(0, 'COM20')
        self.assertEqual(inventory.compare([moved, second], registry)[0][0], 'MOVED')

    def test_collision_rejects_entire_update(self):
        registry = {'manual': {'name': 'keep'}}
        before = copy.deepcopy(registry)
        with self.assertRaisesRegex(ValueError, 'ambiguous'):
            inventory.update_registry(registry, [self.port(), self.port(com='COM13')], True)
        self.assertEqual(registry, before)

    def test_legacy_migration_preserves_manual_fields(self):
        port = self.port()
        registry = {port.physical_device_id: dict(device_id=port.physical_device_id,
                    name='Термометр', notes='ручное', operator='Иван', expected_port='COM1')}
        self.assertEqual(inventory.compare([port], registry)[0][0], 'LEGACY')
        with self.assertRaises(ValueError):
            inventory.update_registry(registry, [port])
        inventory.update_registry(registry, [port], True)
        self.assertEqual(registry[port.device_id]['operator'], 'Иван')
        self.assertEqual(registry[port.device_id]['name'], 'Термометр')

    def test_legacy_multi_interface_requires_manual_migration(self):
        first, second = self.port(), self.port(1)
        registry = {first.physical_device_id: {'name': 'unknown interface'}}
        self.assertEqual(inventory.compare([first, second], registry)[0][0], 'AMBIGUOUS')
        with self.assertRaisesRegex(ValueError, 'multiple interfaces'):
            inventory.update_registry(registry, [first, second], True)
        self.assertEqual(len(registry), 1)

    def test_duplicate_csv_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'registry.csv'
            path.write_text('device_id,name\na,one\na,two\n', encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'duplicate device_id'):
                inventory.read_registry(path)

    def test_atomic_replace_failure_preserves_original_and_removes_temp(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'registry.csv'
            original = b'device_id,name,operator\na,manual,owner\n'
            path.write_bytes(original)
            registry = inventory.read_registry(path)
            with patch.object(inventory.os, 'replace', side_effect=OSError('disk')):
                with self.assertRaises(OSError):
                    inventory.write_registry(path, registry)
            self.assertEqual(path.read_bytes(), original)
            self.assertEqual(list(Path(folder).iterdir()), [path])
            inventory.write_registry(path, registry)
            self.assertEqual(inventory.read_registry(path)['a']['operator'], 'owner')

    def test_unidentified_hardware_is_not_claimed_unique(self):
        raw = SimpleNamespace(vid=1, pid=2, serial_number=None, hwid='n/a', device='COM1')
        self.assertEqual(inventory.stable_device_id(raw), 'PORT:COM1')
