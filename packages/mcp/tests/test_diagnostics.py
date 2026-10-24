import json
import plistlib
from pathlib import Path
import tempfile
import unittest

from opengcua_mcp.diagnostics import host_diagnostics, driver_metadata


class DiagnosticTests(unittest.TestCase):
    def test_only_fresh_private_same_host_observations_count(self):
        with tempfile.TemporaryDirectory(dir='/private/tmp') as root:
            path = Path(root) / 'dock-status.json'
            def write(pid=123, when=100, mode=0o600):
                path.write_text(json.dumps({'host_pid': pid, 'captured_at': when,
                                            'dock': {'status': 'ok', 'count': 1}}))
                path.chmod(mode)
            write()
            self.assertEqual(host_diagnostics(path, host_pid=123, now=101)['dock']['count'], 1)
            for args in ({'pid': 124}, {'when': 90}, {'when': 105}, {'mode': 0o644}):
                write(**args)
                report = host_diagnostics(path, host_pid=123, now=101)
                self.assertIsNone(report['dock']['count'])
                self.assertEqual(report['dock']['status'], 'unavailable')
            path.unlink()
            path.symlink_to(Path(root) / 'missing')
            self.assertIsNone(host_diagnostics(path)['dock']['count'])


    def test_driver_metadata_is_explicit_about_unknown_and_unverified_values(self):
        with tempfile.TemporaryDirectory() as root:
            app = Path(root)
            missing = driver_metadata(app)
            self.assertIsNone(missing['version'])
            self.assertEqual(missing['metadata_status'], 'unavailable')
            (app / 'Contents').mkdir()
            path = app / 'Contents/Info.plist'
            for value in ({}, [], {'CFBundleShortVersionString': 36}):
                path.write_bytes(plistlib.dumps(value))
                self.assertIsNone(driver_metadata(app)['version'])
            path.write_bytes(b'bad plist')
            self.assertEqual(driver_metadata(app)['metadata_status'], 'unavailable')
            path.write_bytes(b'x' * (64 * 1024 + 1))
            self.assertEqual(driver_metadata(app)['metadata_status'], 'unavailable')
            path.write_bytes(plistlib.dumps({'CFBundleShortVersionString': '0.36.0'}))
            report = driver_metadata(app)
            self.assertEqual(report['version'], '0.36.0')
            self.assertFalse(report['signature_checked'])
