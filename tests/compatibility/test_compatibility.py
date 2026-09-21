"""Fail-closed official-suite registry and result tests."""

import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import zipfile


TOOLS = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(TOOLS / "src"))
from diamaneos_tools import compatibility as api
from jsonschema import Draft7Validator


class CompatibilityTest(unittest.TestCase):
    def setUp(self):
        self.config = api._load_json(TOOLS / "config" / "test-suites.json")

    def test_committed_registry_and_schema_are_valid(self):
        self.assertEqual([], api._validate_registry(self.config))
        schema = json.loads((TOOLS / "schemas" /
                             "test-suites.schema.json").read_text())
        Draft7Validator.check_schema(schema)
        self.assertEqual("17_r2", self.config["target"]["suite_release"])
        self.assertEqual(37, self.config["target"]["api_level"])

    def test_candidate_is_bound_to_canonical_build_environment(self):
        changed = copy.deepcopy(self.config)
        changed["target"]["candidate_environment_id"] = "stale-environment"
        self.assertIn("compatibility candidate does not match build environment",
                      api._validate_registry(changed))

    def test_registry_requires_complete_applicability_and_safe_filters(self):
        changed = copy.deepcopy(self.config)
        changed["applicability"].pop()
        self.assertIn("compatibility applicability decision set is incomplete",
                      api._validate_registry(changed))
        changed = copy.deepcopy(self.config)
        changed["trial_profiles"][0]["module"] = "unsafe filter"
        self.assertIn("compatibility trial contains an unsafe filter",
                      api._validate_registry(changed))
        changed = copy.deepcopy(self.config)
        changed["trial_profiles"][0]["required_fixture_ids"].append("unknown")
        self.assertIn("compatibility trial refers to an unknown fixture",
                      api._validate_registry(changed))

    def test_setup_record_is_private_complete_and_candidate_bound(self):
        profile = next(item for item in self.config["trial_profiles"]
                       if item["id"] == "stock16-harness-trial")
        fingerprint = "a" * 64
        record = {
            "schema_version": 1,
            "profile_id": profile["id"],
            "candidate_fingerprint_sha256": fingerprint,
            "prepared_at_utc": "2026-09-20T12:00:00Z",
            "fixture_ids": [],
            "operator_attestations": {
                "device_changes_authorized": True,
                "device_contains_no_daily_data": True,
                "result_storage_is_private": True,
                "teardown_understood": True,
            },
        }
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "setup.json"
            path.write_text(json.dumps(record))
            path.chmod(0o640)
            result = api._setup_record(path, self.config, profile, fingerprint)
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(),
                             result["record_sha256"])
            record["candidate_fingerprint_sha256"] = "b" * 64
            path.write_text(json.dumps(record))
            with self.assertRaisesRegex(api.CompatibilityError,
                                        "identity does not match"):
                api._setup_record(path, self.config, profile, fingerprint)
            path.chmod(0o644)
            with self.assertRaisesRegex(api.CompatibilityError,
                                        "not owner-controlled"):
                api._setup_record(path, self.config, profile, fingerprint)

    def _result(self, root: Path, xml: str):
        root.mkdir(parents=True)
        (root / "test_result.xml").write_text(xml, encoding="utf-8")

    def test_valid_result_passes_and_test_failure_remains_fail(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "pass"
            self._result(root, '<Result suite_version="17_r2"><Module '
                         'name="M" done="true"><TestCase name="C">'
                         '<Test name="t" result="pass"/></TestCase>'
                         '</Module><Summary pass="1" failed="0" modules_done="1" modules_total="1"/></Result>')
            result = api.parse_tradefed_result(root, "17_r2", expected_module="M", expected_test="C#t")
            self.assertEqual("PASS", result["status"])
            self.assertEqual(1, result["test_count"])

            failed = Path(temp) / "fail"
            self._result(failed, '<Result suite_version="17_r2"><Module '
                         'name="M" done="true"><TestCase name="C">'
                         '<Test name="t" result="fail"/></TestCase>'
                         '</Module></Result>')
            self.assertEqual("FAIL", api.parse_tradefed_result(
                failed, "17_r2")["status"])

    def test_empty_truncated_mixed_or_incomplete_results_never_pass(self):
        with tempfile.TemporaryDirectory() as temp:
            temp = Path(temp)
            empty = temp / "empty"
            empty.mkdir()
            self.assertEqual("INCOMPLETE", api.parse_tradefed_result(
                empty, "17_r2")["status"])
            truncated = temp / "truncated"
            self._result(truncated, '<Result suite_version="17_r2"><Module>')
            self.assertEqual("HARNESS_ERROR", api.parse_tradefed_result(
                truncated, "17_r2")["status"])
            mixed = temp / "mixed-version"
            self._result(mixed, '<Result suite_version="16_r6"><Module '
                         'done="true"><Test result="pass"/></Module></Result>')
            self.assertEqual("HARNESS_ERROR", api.parse_tradefed_result(
                mixed, "17_r2")["status"])
            incomplete = temp / "incomplete"
            self._result(incomplete, '<Result suite_version="17_r2"><Module '
                         'done="false"><Test result="pass"/></Module></Result>')
            self.assertEqual("INCOMPLETE", api.parse_tradefed_result(
                incomplete, "17_r2")["status"])

    def test_package_requires_committed_hash_and_exact_layout(self):
        with tempfile.TemporaryDirectory() as temp:
            temp = Path(temp)
            archive = temp / "android-cts-17_r2-linux_x86-arm.zip"
            with zipfile.ZipFile(archive, "w") as out:
                info = zipfile.ZipInfo("android-cts/tools/cts-tradefed")
                info.external_attr = 0o100755 << 16
                out.writestr(info, "#!/bin/sh\nexit 0\n")
            package_root = temp / "packages"
            launcher = package_root / "android-cts" / "tools" / "cts-tradefed"
            launcher.parent.mkdir(parents=True)
            launcher.write_text("#!/bin/sh\nexit 0\n")
            launcher.chmod(0o755)
            with self.assertRaisesRegex(api.CompatibilityError, "not yet approved"):
                api.inspect_package(self.config, "cts-17-r2-arm", archive,
                                    package_root)
            changed = copy.deepcopy(self.config)
            package = next(item for item in changed["packages"]
                           if item["id"] == "cts-17-r2-arm")
            package["archive_sha256"] = hashlib.sha256(
                archive.read_bytes()).hexdigest()
            package["acquisition_status"] = "verified"
            result = api.inspect_package(changed, package["id"], archive,
                                         package_root)
            self.assertEqual("PASS", result["status"])
            self.assertEqual(1, result["file_count"])
            archive.write_bytes(b"changed")
            with self.assertRaisesRegex(api.CompatibilityError, "hash mismatch"):
                api.inspect_package(changed, package["id"], archive,
                                    package_root)

    def test_authenticated_extraction_rejects_changed_missing_extra_or_mode_inputs(self):
        with tempfile.TemporaryDirectory() as temp:
            temp = Path(temp)
            package = self.config["packages"][0]
            archive = temp / package["archive_name"]
            with zipfile.ZipFile(archive, "w") as bundle:
                info = zipfile.ZipInfo(package["extracted_directory"] + "/" + package["launcher"])
                info.external_attr = 0o100755 << 16
                bundle.writestr(info, "#!/bin/sh\nexit 0\n")
            package["acquisition_status"] = "verified"
            package["archive_sha256"] = hashlib.sha256(archive.read_bytes()).hexdigest()
            destination = temp / "extracted"
            proof = api.extract_package(self.config, package["id"], archive, destination)
            root = destination / package["extracted_directory"]
            launcher = root / package["launcher"]
            original = launcher.read_bytes()
            (root / "results").mkdir()
            (root / "results" / "generated").write_text("not an input")
            self.assertEqual(proof, api.inspect_package(self.config, package["id"], archive, destination))
            for mutation in ("content", "missing", "extra", "mode"):
                with self.subTest(mutation=mutation):
                    launcher.write_bytes(original)
                    launcher.chmod(0o750)
                    if mutation == "content": launcher.write_text("changed")
                    if mutation == "missing": launcher.unlink()
                    if mutation == "extra": (root / "extra").write_text("extra")
                    if mutation == "mode": launcher.chmod(0o640)
                    with self.assertRaises(api.CompatibilityError):
                        api.inspect_package(self.config, package["id"], archive, destination)
                    (root / "extra").unlink(missing_ok=True)
            with self.assertRaisesRegex(api.CompatibilityError, "already exists"):
                api.extract_package(self.config, package["id"], archive, destination)

    def test_archive_traversal_and_symlink_members_rejected_before_publication(self):
        with tempfile.TemporaryDirectory() as temp:
            temp = Path(temp)
            package = self.config["packages"][0]
            for name, mode in (("../escape", 0o100644),
                               (package["extracted_directory"] + "/link", 0o120777)):
                archive = temp / package["archive_name"]
                with zipfile.ZipFile(archive, "w") as bundle:
                    info = zipfile.ZipInfo(name)
                    info.external_attr = mode << 16
                    bundle.writestr(info, "bad")
                package["acquisition_status"] = "verified"
                package["archive_sha256"] = hashlib.sha256(archive.read_bytes()).hexdigest()
                with self.assertRaises(api.CompatibilityError):
                    api.extract_package(self.config, package["id"], archive, temp / "new")
                self.assertFalse((temp / "new").exists())

    def test_pass_requires_requested_coverage_done_and_consistent_summary(self):
        valid = ('<Result suite_version="17_r2"><Module name="M" done="true">'
                 '<TestCase name="C"><Test name="t" result="pass"/></TestCase></Module>'
                 '<Summary pass="1" failed="0" modules_done="1" modules_total="1"/></Result>')
        cases = (valid.replace('name="M"', 'name="Other"'),
                 valid.replace('name="t"', 'name="other"'),
                 valid.replace(' done="true"', ''),
                 valid.replace('modules_total="1"', 'modules_total="2"'),
                 valid.replace('<Summary pass="1" failed="0" modules_done="1" modules_total="1"/>', ''))
        with tempfile.TemporaryDirectory() as temp:
            for index, xml in enumerate(cases):
                path = Path(temp) / str(index)
                self._result(path, xml)
                self.assertEqual("INCOMPLETE", api.parse_tradefed_result(
                    path, "17_r2", "M", "C#t")["status"])

    def test_nonzero_process_is_never_success_transport(self):
        result = api._bounded_process([sys.executable, "-c", "raise SystemExit(7)"], 5, TOOLS)
        self.assertEqual("error", result["transport"])
        self.assertEqual(7, result["returncode"])

    def test_package_tree_rejects_symlinks(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "package"
            root.mkdir()
            (root / "real").write_text("x")
            os.symlink(root / "real", root / "link")
            with self.assertRaisesRegex(api.CompatibilityError, "symbolic link"):
                api._safe_tree(root)

    def test_dry_run_is_read_only_and_reports_pending_inputs(self):
        before = (TOOLS / "config" / "test-suites.json").read_bytes()
        result = subprocess.run(
            [sys.executable,
             str(TOOLS / "bin" / "diamaneos"), "test", "compatibility",
             "--dry-run"], cwd=TOOLS, capture_output=True, text=True,
            timeout=20, env={"PATH": "/usr/bin:/bin",
                             "PYTHONDONTWRITEBYTECODE": "1"})
        self.assertEqual(0, result.returncode, result.stderr)
        plan = json.loads(result.stdout)
        self.assertEqual("BLOCKED", plan["status"])
        self.assertEqual(0, plan["device_commands_executed"])
        self.assertEqual(before,
                         (TOOLS / "config" / "test-suites.json").read_bytes())

    def test_host_gate_enforces_official_resources_and_tools(self):
        observation = {
            "architecture": "x86_64",
            "memory_gib": 32.0,
            "free_disk_gib": 256.0,
            "glibc": "glibc 2.41",
            "locale": "en_US.UTF-8",
            "available_locales": ["C", "C.utf8", "en_US.utf8"],
            "ffmpeg_version": "ffmpeg version 5.1.3",
            "adb_version": "Android Debug Bridge version 1.0.41",
            "aapt2_version": "Android Asset Packaging Tool (aapt) 2.20",
            "aapt_version": "Android Asset Packaging Tool, v0.2",
        }
        result = api._evaluate_host(self.config, observation)
        self.assertEqual("PASS", result["status"])
        observation["memory_gib"] = 8.0
        result = api._evaluate_host(self.config, observation)
        self.assertEqual("BLOCKED", result["status"])
        self.assertFalse(result["checks"]["memory"])
        observation["memory_gib"] = 32.0
        observation["available_locales"] = ["C", "C.utf8"]
        result = api._evaluate_host(self.config, observation)
        self.assertEqual("BLOCKED", result["status"])
        self.assertFalse(result["checks"]["english_locale"])

    def test_process_capture_is_bounded(self):
        previous = api.MAX_STREAM_BYTES
        api.MAX_STREAM_BYTES = 128
        try:
            result = api._bounded_process(
                [sys.executable, "-c", "print('x' * 1000)"], 10, TOOLS)
        finally:
            api.MAX_STREAM_BYTES = previous
        self.assertEqual("overflow", result["transport"])
        self.assertEqual(128, len(result["stdout"]))

    def test_retry_rejects_changed_package_or_successful_parent(self):
        package = {"package_id": "cts", "archive_sha256": "1" * 64}
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "result.json"
            path.write_text(json.dumps({
                "operation": "compatibility-trial",
                "profile_id": "trial",
                "package": package,
                "status": "PASS",
                "run_id": "old",
            }))
            with self.assertRaisesRegex(api.CompatibilityError, "incompatible"):
                api._result_parent(str(path), "trial", package)

    def test_jdk_notice_links_materialize_but_unsafe_links_fail(self):
        package = {"extracted_directory": "android-cts"}
        for target, valid in (("../java.base/LICENSE", True),
                              ("../../../../outside", False),
                              ("/etc/passwd", False),
                              ("../java.base/missing", False),
                              ("LICENSE", False)):
            with self.subTest(target=target), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                archive = root / "suite.zip"
                with zipfile.ZipFile(archive, "w") as out:
                    out.writestr("android-cts/jdk/legal/java.base/LICENSE", b"license text")
                    link = zipfile.ZipInfo("android-cts/jdk/legal/java.compiler/LICENSE")
                    link.external_attr = 0o120777 << 16
                    out.writestr(link, target)
                if not valid:
                    with self.assertRaises(api.CompatibilityError):
                        api._archive_inputs(archive, package, root / "out")
                    continue
                records = api._archive_inputs(archive, package, root / "out")
                notice = root / "out/android-cts/jdk/legal/java.compiler/LICENSE"
                self.assertFalse(notice.is_symlink())
                self.assertEqual(b"license text", notice.read_bytes())
                tree, _ = api._safe_tree(root / "out/android-cts")
                self.assertEqual(records, tree)

    def test_tradefed_mode_is_fixed_for_direct_execution(self):
        result = api._bounded_process([sys.executable, "-c",
            "import os; print(os.environ['USE_ATS'], os.environ['ENABLE_XTS_DYNAMIC_DOWNLOADER'])"], 5, TOOLS)
        self.assertEqual(b"false false\n", result["stdout"])

    def test_official_latest_alias_and_zip_do_not_duplicate_a_session(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "results"
            self.assertEqual(set(), api._result_directories(root))
            root.mkdir()
            before = api._result_directories(root)
            (root / "session").mkdir()
            (root / "latest").symlink_to("session", target_is_directory=True)
            (root / "session.zip").write_bytes(b"archive")
            self.assertEqual({"session"}, api._result_directories(root) - before)
            (root / "latest").unlink()
            (root / "latest").symlink_to(Path(temp), target_is_directory=True)
            with self.assertRaises(api.CompatibilityError):
                api._result_directories(root)

    def test_only_the_selected_harness_may_be_attached(self):
        api._require_single_attached_target(["selected"], "selected")
        for observed in ([], ["daily-phone"],
                         ["selected", "mapped-peer"]):
            with self.assertRaisesRegex(
                    api.CompatibilityError, "not uniquely attached"):
                api._require_single_attached_target(observed, "selected")


if __name__ == "__main__":
    unittest.main()
