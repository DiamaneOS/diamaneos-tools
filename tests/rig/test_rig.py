"""Fail-closed tests for identity-bound USB-rig control."""

import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

TOOLS = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(TOOLS / "src"))
from diamaneos_tools import rig


BATTERY = """Battery Service state:
  AC powered: false
  USB powered: true
  Wireless powered: false
  status: 2
  level: 73
  temperature: 241
"""


class FakeExecutor:
    def __init__(self):
        self.calls = []
        self.roles = {
            "synthetic-a": {"port": 1, "present": True,
                            "path": "usb:1-2.3.1", "level": 73},
            "synthetic-b": {"port": 2, "present": True,
                            "path": "usb:1-2.3.2", "level": 61},
        }
        self.power = {1: True, 2: True}
        self.disrupt_other = False
        self.disagree = False

    @staticmethod
    def _completed(argv, code=0, stdout="", stderr=""):
        return subprocess.CompletedProcess(argv, code, stdout, stderr)

    def __call__(self, argv, **kwargs):
        self.calls.append(list(argv))
        if argv[0] == "/synthetic/adb":
            serial = argv[argv.index("-s") + 1]
            device = self.roles[serial]
            command = argv[argv.index(serial) + 1:]
            if command == ["get-state"]:
                return self._completed(
                    argv, 0 if device["present"] else 1,
                    "device\n" if device["present"] else "")
            if command == ["get-devpath"]:
                return self._completed(argv, stdout=device["path"] + "\n")
            if command == ["shell", "dumpsys", "battery"]:
                battery = BATTERY.replace("level: 73", f"level: {device['level']}")
                return self._completed(argv, stdout=battery)
            raise AssertionError(command)
        if argv[0] == "/synthetic/uhubctl":
            port = int(argv[argv.index("-p") + 1])
            if "-a" in argv:
                action = argv[argv.index("-a") + 1]
                self.power[port] = action == "on"
                for device in self.roles.values():
                    if device["port"] == port:
                        device["present"] = action == "on"
                    elif self.disrupt_other:
                        device["present"] = False
                return self._completed(argv, stdout="Sent power request\n")
            first = self.power[port]
            second = not first if self.disagree else first
            state = lambda value: "0100 power" if value else "0000 off"
            return self._completed(
                argv,
                stdout=(
                    "Current status for hub 2-2.3 [0000:0000]\n"
                    f"  Port {port}: {state(first)}\n"
                    "Current status for hub 1-2.3 [0000:0000]\n"
                    f"  Port {port}: {state(second)}\n"
                ),
            )
        raise AssertionError(argv)


class RigTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.map = self.root / "device-map.json"
        self.map.write_text(json.dumps({
            "schema_version": 1,
            "devices": [
                {"role": "harness", "adb_serial": "synthetic-a",
                 "disposable": True},
                {"role": "telephony-peer", "adb_serial": "synthetic-b",
                 "disposable": False},
            ],
        }), encoding="utf-8")
        self.map.chmod(0o640)
        self.state = self.root / "state"
        self.state.mkdir(mode=0o750)
        self.runs = self.root / "runs"
        self.runs.mkdir(mode=0o750)
        self.sysfs = self.root / "sysfs"
        for path, vendor, product in (
                ("1-2.3", "0001", "0002"),
                ("2-2.3", "0001", "0003")):
            base = self.sysfs / path
            base.mkdir(parents=True)
            (base / "idVendor").write_text(vendor + "\n", encoding="ascii")
            (base / "idProduct").write_text(product + "\n", encoding="ascii")
        battery = {
            "mode": "host-hysteresis",
            "low_percent": 50,
            "high_percent": 80,
            "hard_ceiling_percent": 85,
            "maximum_temperature_c": 40,
            "resume_temperature_c": 37,
            "off_probe_interval_seconds": 3600,
        }
        self.config = {
            "schema_version": 1,
            "adb": "/synthetic/adb",
            "uhubctl": "/synthetic/uhubctl",
            "device_map": str(self.map),
            "state_root": str(self.state),
            "hub": {
                "model": "Synthetic independently switched hub",
                "control_location": "1-2.3",
                "control_port_count": 4,
                "usb2": {"sysfs_path": "1-2.3", "vendor_id": "0001",
                         "product_id": "0002"},
                "usb3": {"sysfs_path": "2-2.3", "vendor_id": "0001",
                         "product_id": "0003"},
            },
            "roles": [
                {"role": "harness", "logical_port": 1,
                 "usb_path": "usb:1-2.3.1", "battery": copy.deepcopy(battery)},
                {"role": "telephony-peer", "logical_port": 2,
                 "usb_path": "usb:1-2.3.2", "battery": copy.deepcopy(battery)},
            ],
            "inhibit_roots": [str(self.runs)],
        }
        self.executor = FakeExecutor()
        self.controller = rig.RigController(
            self.config, executor=self.executor, sysfs_root=self.sysfs)

    def tearDown(self):
        self.temp.cleanup()

    def test_config_binds_role_to_control_port(self):
        invalid = copy.deepcopy(self.config)
        invalid["roles"][0]["usb_path"] = "usb:1-2.3.2"
        with self.assertRaisesRegex(rig.RigError, "does not match"):
            rig.validate_config(invalid)

    def test_config_rejects_duplicate_port(self):
        invalid = copy.deepcopy(self.config)
        invalid["roles"][1]["logical_port"] = 1
        invalid["roles"][1]["usb_path"] = "usb:1-2.3.1"
        with self.assertRaisesRegex(rig.RigError, "duplicate"):
            rig.validate_config(invalid)

    def test_parse_battery(self):
        self.assertEqual(rig.parse_battery(BATTERY), {
            "level_percent": 73,
            "status_code": 2,
            "externally_powered": True,
            "temperature_c": 24.1,
        })

    def test_battery_decision_hysteresis_and_temperature(self):
        policy = self.config["roles"][0]["battery"]
        self.assertEqual(
            rig.battery_decision(
                policy, {"level_percent": 80, "temperature_c": 24}, True),
            {"action": "off", "reason": "hysteresis-high"})
        self.assertEqual(
            rig.battery_decision(
                policy, {"level_percent": 50, "temperature_c": 24}, False),
            {"action": "on", "reason": "hysteresis-low"})
        self.assertEqual(
            rig.battery_decision(
                policy, {"level_percent": 50, "temperature_c": 40}, False),
            {"action": "off", "reason": "battery-temperature-stop"})

    def test_status_is_identity_redacted(self):
        result = self.controller.status()
        rendered = json.dumps(result)
        self.assertNotIn("synthetic-a", rendered)
        self.assertNotIn("synthetic-b", rendered)
        self.assertEqual([item["role"] for item in result["roles"]],
                         ["harness", "telephony-peer"])
        self.assertTrue(all(item["port_powered"] for item in result["roles"]))

    def test_explicit_off_and_on_preserve_other_role(self):
        off = self.controller.set_power("harness", "off", "manual-hold")
        self.assertEqual(off["status"], "PASS")
        self.assertEqual(off["after"]["adb_state"], "absent")
        self.assertTrue(self.executor.roles["synthetic-b"]["present"])
        on = self.controller.set_power("harness", "on", "manual-restore")
        self.assertEqual(on["after"]["usb_path"], "usb:1-2.3.1")
        rendered = json.dumps([off, on])
        self.assertNotIn("synthetic-a", rendered)
        hub_calls = [call for call in self.executor.calls
                     if call[0] == "/synthetic/uhubctl"]
        self.assertFalse(any("cycle" in call or "-S" in call for call in hub_calls))

    def test_active_run_inhibits_power_change(self):
        partial = self.runs / "active.partial"
        partial.mkdir()
        (partial / "result.json").write_text(json.dumps({
            "run_id": "active",
            "target": {"role": "harness"},
        }), encoding="utf-8")
        with self.assertRaisesRegex(rig.RigError, "active test"):
            self.controller.set_power("harness", "off", "manual-hold")
        self.assertFalse(any(call[0] == "/synthetic/uhubctl"
                             for call in self.executor.calls))

    def test_unreadable_partial_inhibits_every_role(self):
        partial = self.runs / "forming.partial"
        partial.mkdir()
        with self.assertRaisesRegex(rig.RigError, "active test"):
            self.controller.set_power(
                "telephony-peer", "off", "manual-hold")

    def test_companion_hub_state_disagreement_fails_closed(self):
        self.executor.disagree = True
        with self.assertRaisesRegex(rig.RigError, "states disagree"):
            self.controller.status()

    def test_other_role_disturbance_is_detected(self):
        self.executor.disrupt_other = True
        with self.assertRaisesRegex(rig.RigError, "disturbed another"):
            self.controller.set_power("harness", "off", "manual-hold")

    def test_reason_cannot_carry_arbitrary_private_text(self):
        with self.assertRaisesRegex(rig.RigError, "reason token"):
            self.controller.set_power(
                "harness", "off", "ticket for synthetic-a")

    def test_maintenance_reasserts_off_intent_after_hub_reboot(self):
        self.controller._write_intent("harness", False, "hysteresis-high")
        self.executor.roles["synthetic-a"]["level"] = 73
        result = self.controller.maintain("harness")
        self.assertEqual(result["decision"]["action"], "hold")
        self.assertFalse(self.executor.power[1])
        self.assertFalse(self.executor.roles["synthetic-a"]["present"])

    def test_failed_off_state_probe_restores_power_off(self):
        self.controller._write_intent("harness", False, "hysteresis-high")
        intent_path = self.state / "ports" / "harness.json"
        intent = json.loads(intent_path.read_text(encoding="utf-8"))
        intent["next_probe_not_before_epoch"] = 0
        intent_path.write_text(json.dumps(intent), encoding="utf-8")
        intent_path.chmod(0o640)
        self.executor.power[1] = False
        self.executor.roles["synthetic-a"]["present"] = False

        original = self.executor.__call__

        def never_reconnect(argv, **kwargs):
            result = original(argv, **kwargs)
            if argv[0] == "/synthetic/uhubctl" and "-a" in argv \
                    and argv[argv.index("-a") + 1] == "on":
                self.executor.roles["synthetic-a"]["present"] = False
            return result

        self.controller.executor = never_reconnect
        with mock.patch.object(rig.time, "sleep", return_value=None), \
                mock.patch.object(
                    rig.time, "monotonic", side_effect=[0, 31]):
            result = self.controller.maintain("harness")
        self.assertEqual(result, {
            "status": "HELD", "role": "harness",
            "reason": "probe-target-unavailable",
        })
        self.assertFalse(self.executor.power[1])

    def test_dry_run_is_static(self):
        result = rig.dry_run(self.config)
        self.assertEqual(result["writes"], "none")
        self.assertFalse(result["cycle_action_supported"])
        self.assertEqual(self.executor.calls, [])

    def test_start_guard_holds_same_role_lock_until_partial_exists(self):
        with mock.patch.object(
                rig, "controller_for_target", return_value=self.controller):
            guard = rig.acquire_test_start_guard(
                "/synthetic/rig.json", "harness", str(self.map), "synthetic-a")
        with self.assertRaisesRegex(rig.RigError, "already locked"):
            self.controller._lock("harness")
        guard.release()
        fd = self.controller._lock("harness")
        self.controller._unlock(fd)

    def test_start_guard_restores_maintenance_held_role(self):
        self.executor.power[1] = False
        self.executor.roles["synthetic-a"]["present"] = False
        self.controller._write_intent("harness", False, "hysteresis-high")
        with mock.patch.object(
                rig, "controller_for_target", return_value=self.controller):
            guard = rig.acquire_test_start_guard(
                "/synthetic/rig.json", "harness", str(self.map), "synthetic-a")
        try:
            self.assertTrue(self.executor.power[1])
            self.assertTrue(self.executor.roles["synthetic-a"]["present"])
            intent = json.loads((
                self.state / "ports" / "harness.json").read_text())
            self.assertTrue(intent["intended_on"])
            self.assertEqual(intent["reason"], "test-start")
            self.assertTrue(self.executor.roles["synthetic-b"]["present"])
        finally:
            guard.release()

    def test_persistent_inhibitor_blocks_test_start_without_power_action(self):
        self.controller.acquire_inhibitor(
            "harness", "flash-session", "firmware-write")
        self.executor.calls.clear()
        with mock.patch.object(
                rig, "controller_for_target", return_value=self.controller):
            with self.assertRaisesRegex(rig.RigError, "inhibits test start"):
                rig.acquire_test_start_guard(
                    "/synthetic/rig.json", "harness", str(self.map),
                    "synthetic-a")
        self.assertFalse(any(call[0] == "/synthetic/uhubctl"
                             for call in self.executor.calls))
        self.controller.release_inhibitor("harness", "flash-session")

    def test_start_guard_rejects_cross_role_target(self):
        with mock.patch.object(rig, "load_config", return_value=self.config):
            with self.assertRaisesRegex(rig.RigError, "does not match"):
                rig.acquire_test_start_guard(
                    "/synthetic/rig.json", "harness", str(self.map),
                    "synthetic-b")

    def test_committed_example_validates_without_runtime_access(self):
        example = rig.load_config(
            TOOLS / "deploy" / "test-host" / "rig.example.json")
        self.assertEqual(example["schema_version"], 1)
        self.assertEqual(len(example["roles"]), 2)

    def test_power_cli_keeps_subcommand_separate_from_requested_action(self):
        args = rig.build_parser().parse_args([
            "power", "--config", "/private/rig.json", "--role", "harness",
            "--action", "off", "--reason", "manual-hold",
        ])
        self.assertEqual(args.action, "power")
        self.assertEqual(args.power_action, "off")

    def test_persistent_inhibitor_blocks_then_releases_power(self):
        acquired = self.controller.acquire_inhibitor(
            "harness", "flash-session", "firmware-write")
        self.assertEqual(acquired["status"], "ACQUIRED")
        with self.assertRaisesRegex(rig.RigError, "active test"):
            self.controller.set_power("harness", "off", "manual-hold")
        listed = self.controller.list_inhibitors("harness")
        self.assertEqual([item["lease_id"] for item in listed],
                         ["flash-session"])
        released = self.controller.release_inhibitor("harness", "flash-session")
        self.assertEqual(released["status"], "RELEASED")
        self.assertEqual(self.controller.list_inhibitors("harness"), [])


if __name__ == "__main__":
    unittest.main()
