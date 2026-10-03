"""Checks for installer boundaries; never touches WSA or application data."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("prepare_wsa_images", ROOT / "scripts/prepare_wsa_images.py")
repair = importlib.util.module_from_spec(spec)
spec.loader.exec_module(repair)


class CompatibilityTests(unittest.TestCase):
    def test_video_repair_removes_only_target_decoder_and_preserves_audio(self):
        source = b'<MediaCodecs><Decoders><MediaCodec name="audio.test"/><MediaCodec name="OMX.android.latte.hevc.decoder"/><MediaCodec name="video.other"/></Decoders><Encoders><MediaCodec name="encode.test"/></Encoders></MediaCodecs>'
        result = repair.remove_hevc_declaration(source)
        self.assertNotIn(b'OMX.android.latte.hevc.decoder', result)
        for name in (b'audio.test', b'video.other', b'encode.test'):
            self.assertIn(name, result)

    def test_unknown_codec_layout_is_rejected(self):
        for source in (b'<MediaCodecs/>', b'<MediaCodecs><Decoders/></MediaCodecs>'):
            with self.assertRaises(RuntimeError):
                repair.remove_hevc_declaration(source)

    def test_unknown_image_rejected_before_backup_or_work_creation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'system.vhdx').write_bytes(b'unrecognized')
            (root / 'vendor.vhdx').write_bytes(b'original-vendor')
            manifest = {'images': {name: {'original_sha256': '0' * 64} for name in ('system', 'vendor')}}
            with self.assertRaisesRegex(RuntimeError, 'Unsupported'):
                repair.prepare(root, root / 'backup', root / 'work', root / 'archive.tar.gz', manifest)
            self.assertFalse((root / 'backup').exists())
            self.assertFalse((root / 'work').exists())
            self.assertEqual((root / 'system.vhdx').read_bytes(), b'unrecognized')


class PowerShellTests(unittest.TestCase):
    def powershell(self, command):
        return subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-Command', command],
                              capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=40)

    def test_dry_run_does_not_install_or_launch(self):
        result = self.powershell("& './scripts/install.ps1' -DryRun -InstallWsa -RepairWsa -Launch")
        self.assertEqual(result.returncode, 0, result.stderr)
        plan = json.loads(result.stdout)
        self.assertEqual(plan['mode'], 'Release')
        self.assertTrue(plan['install_wsa'])
        self.assertTrue(plan['repair_wsa'])

    def test_corrupt_cached_download_is_rejected_offline(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'fixture.zip').write_bytes(b'corrupt')
            safe_directory = str(root).replace("'", "''")
            expected = hashlib.sha256(b'correct').hexdigest()
            command = ". './scripts/common.ps1'; $item = [pscustomobject]@{filename='fixture.zip';url='https://example.invalid/fixture.zip';sha256='" + expected + "'}; Get-VerifiedDownload $item '" + safe_directory + "' -Offline"
            result = self.powershell(command)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('checksum mismatch', result.stderr)
            self.assertEqual((root / 'fixture.zip').read_bytes(), b'corrupt')

    def test_zip_path_traversal_is_rejected_before_extraction(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = root / 'hostile.zip'
            with zipfile.ZipFile(archive, 'w') as stream:
                stream.writestr('../outside.txt', 'should not be written')
            safe_archive = str(archive).replace("'", "''")
            safe_output = str(root / 'output').replace("'", "''")
            command = ". './scripts/common.ps1'; Expand-SafeZip '" + safe_archive + "' '" + safe_output + "'"
            result = self.powershell(command)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('escapes installation directory', result.stderr)
            self.assertFalse((root / 'outside.txt').exists())

    def run_release_install(self, root, guardian=True):
        scripts = root / 'scripts'
        scripts.mkdir()
        for name in ['common.ps1', 'install.ps1', 'doctor.ps1']:
            shutil.copy2(ROOT / 'scripts' / name, scripts / name)
        with (scripts / 'common.ps1').open('a', encoding='utf-8') as stream:
            stream.write('''
function Get-WsaInstallation { [pscustomobject]@{Version='fixture'} }
function New-HelperShortcut {
    param($Executable, $Arguments, $WorkingDirectory, $IconExecutable)
    [ordered]@{target=$Executable;working=$WorkingDirectory;icon=$IconExecutable} |
        ConvertTo-Json | Set-Content -LiteralPath (Join-Path $script:ProjectRoot 'shortcut.json') -Encoding UTF8
}
''')
        bundle = root / 'bundle.zip'
        with zipfile.ZipFile(bundle, 'w') as archive:
            archive.writestr('hongguo_desktop.exe', b'fixture-main')
            archive.writestr('app/assets/platform-tools/adb.exe', b'fixture-adb')
            if guardian:
                archive.writestr('helper-guardian.exe', b'fixture-guardian')
        manifests = root / 'manifests'
        manifests.mkdir()
        (manifests / 'release.json').write_text(json.dumps({
            'version': 'v0.1.8', 'filename': 'bundle.zip',
            'sha256': hashlib.sha256(bundle.read_bytes()).hexdigest()
        }), encoding='utf-8')
        env = os.environ.copy()
        env['LOCALAPPDATA'] = str(root / 'user-data')
        result = subprocess.run([
            'powershell.exe', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
            '-File', str(scripts / 'install.ps1'), '-Mode', 'Release',
            '-BundlePath', str(bundle), '-InstallDirectory', str(root / 'program')
        ], capture_output=True, text=True, encoding='utf-8', errors='replace', env=env, timeout=40)
        return result

    def test_release_shortcut_uses_guardian_and_record_preserves_main_for_version_checks(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = self.run_release_install(root)
            self.assertEqual(result.returncode, 0, result.stderr)
            record = json.loads((root / 'user-data/HongguoDesktopHelper/installation.json').read_text(encoding='utf-8-sig'))
            shortcut = json.loads((root / 'shortcut.json').read_text(encoding='utf-8-sig'))
            self.assertEqual(Path(record['executable']).name, 'hongguo_desktop.exe')
            self.assertEqual(Path(shortcut['target']).name, 'helper-guardian.exe')
            self.assertEqual(shortcut['target'], record['launcher'])
            self.assertEqual(shortcut['icon'], record['executable'])
            self.assertEqual(shortcut['working'], record['working_directory'])
            self.assertTrue(Path(record['launcher']).is_file())

    def test_release_without_guardian_never_creates_install_record_or_shortcut(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = self.run_release_install(root, guardian=False)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('background guardian', result.stderr)
            self.assertFalse((root / 'shortcut.json').exists())
            self.assertFalse((root / 'user-data/HongguoDesktopHelper/installation.json').exists())


if __name__ == '__main__':
    unittest.main()
