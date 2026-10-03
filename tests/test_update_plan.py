"""Update guidance must identify changes without touching user data or WSA."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class UpdatePlanTests(unittest.TestCase):
    def run_plan(self, *arguments, record=None):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            data = root / 'HongguoDesktopHelper'
            data.mkdir()
            settings = data / 'settings.json'
            settings.write_text('{"auto_fullscreen":true,"other":"preserve"}', encoding='utf-8')
            if record is not None:
                (data / 'installation.json').write_text(json.dumps(record), encoding='utf-8')
            before = {p.name: p.read_bytes() for p in data.iterdir()}
            env = os.environ.copy()
            env['LOCALAPPDATA'] = directory
            result = subprocess.run(
                ['powershell.exe', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
                 '-File', str(ROOT / 'scripts/update-plan.ps1'), '-Json', *arguments],
                capture_output=True, text=True, encoding='utf-8-sig', errors='replace', env=env, timeout=30)
            self.assertEqual({p.name: p.read_bytes() for p in data.iterdir()}, before)
            return result

    def test_v010_upgrade_requires_helper_and_settings_review_without_environment_reinstall(self):
        result = self.run_plan('-CurrentVersion', 'v0.1.0')
        self.assertEqual(result.returncode, 0, result.stderr)
        plan = json.loads(result.stdout)
        self.assertEqual(plan['status'], 'update_required')
        self.assertEqual([c['version'] for c in plan['changes']], ['v0.1.1', 'v0.1.2', 'v0.1.8'])
        self.assertEqual({c['id'] for c in plan['components_to_update_or_review']}, {'helper', 'settings', 'native_window', 'android_bridge', 'guardian'})
        self.assertIn('WSA', plan['changes'][0]['unchanged_components'])
        self.assertTrue(plan['must_verify_actual_executable_or_source'])
        self.assertFalse(plan['modifies_system'])

    def test_same_version_reports_documentation_changes_without_helper_reinstall(self):
        result = self.run_plan('-CurrentVersion', '0.1.8')
        self.assertEqual(result.returncode, 0, result.stderr)
        plan = json.loads(result.stdout)
        self.assertEqual(plan['status'], 'current')
        self.assertFalse(plan['requires_helper_reinstall'])
        self.assertEqual(plan['components_to_update_or_review'], [])
        self.assertTrue(plan['documentation_changes'])

    def test_missing_record_is_unknown_instead_of_guessing_current(self):
        result = self.run_plan()
        self.assertEqual(result.returncode, 0, result.stderr)
        plan = json.loads(result.stdout)
        self.assertEqual(plan['status'], 'unknown')
        self.assertIsNone(plan['current_version'])
        self.assertTrue(plan['unknown_version_action'])

    def test_install_record_is_a_version_hint_not_proof_of_running_version(self):
        result = self.run_plan(record={'version': 'v0.1.0', 'executable': 'missing.exe'})
        self.assertEqual(result.returncode, 0, result.stderr)
        plan = json.loads(result.stdout)
        self.assertEqual(plan['version_source'], 'installation_record')
        self.assertEqual(plan['status'], 'update_required')
        self.assertTrue(plan['must_verify_actual_executable_or_source'])

    def test_newer_version_is_not_automatically_downgraded(self):
        result = self.run_plan('-CurrentVersion', 'v0.2.0')
        self.assertEqual(result.returncode, 0, result.stderr)
        plan = json.loads(result.stdout)
        self.assertEqual(plan['status'], 'newer_than_manifest')
        self.assertFalse(plan['requires_helper_reinstall'])
        self.assertEqual(plan['changes'], [])

    def test_plan_accumulates_intervening_releases(self):
        result = self.run_plan('-CurrentVersion', 'v0.0.1')
        self.assertEqual(result.returncode, 0, result.stderr)
        plan = json.loads(result.stdout)
        self.assertEqual([c['version'] for c in plan['changes']], ['v0.1.0', 'v0.1.1', 'v0.1.2', 'v0.1.8'])

    def test_v011_upgrade_updates_native_window_without_reinstalling_wsa_or_apk(self):
        result = self.run_plan('-CurrentVersion', 'v0.1.1')
        self.assertEqual(result.returncode, 0, result.stderr)
        plan = json.loads(result.stdout)
        self.assertEqual([c['version'] for c in plan['changes']], ['v0.1.2', 'v0.1.8'])
        self.assertEqual({c['id'] for c in plan['components_to_update_or_review']}, {'helper', 'native_window', 'android_bridge', 'guardian'})
        self.assertIn('WSA', plan['changes'][0]['unchanged_components'])

    def test_public_upgrade_requires_bundled_guardian_without_intermediate_builds(self):
        result = self.run_plan('-CurrentVersion', 'v0.1.2')
        self.assertEqual(result.returncode, 0, result.stderr)
        plan = json.loads(result.stdout)
        self.assertEqual([c['version'] for c in plan['changes']], ['v0.1.8'])
        self.assertEqual({c['id'] for c in plan['components_to_update_or_review']},
                         {'helper', 'native_window', 'android_bridge', 'guardian'})
        self.assertIn('WSA', plan['changes'][0]['unchanged_components'])
        self.assertIn('红果 APP / APK', plan['changes'][0]['unchanged_components'])

    def test_invalid_version_fails_without_mutation(self):
        result = self.run_plan('-CurrentVersion', 'latest')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Unsupported version', result.stderr)


if __name__ == '__main__':
    unittest.main()
