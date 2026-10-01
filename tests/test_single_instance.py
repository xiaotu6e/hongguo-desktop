"""Check real kernel exclusivity without using the production instance name."""
from pathlib import Path
import sys
import unittest
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/"src"))
from desktop_manager.single_instance import SingleInstance


@unittest.skipUnless(sys.platform == "win32", "Windows named mutex")
class SingleInstanceTests(unittest.TestCase):
    def test_duplicate_launch_is_rejected_and_exit_releases_the_session(self):
        name = "Local\\HongguoInstanceTest_"+uuid.uuid4().hex
        first, second = SingleInstance(name), SingleInstance(name)
        try:
            self.assertTrue(first.acquire())
            self.assertTrue(first.acquire())
            self.assertFalse(second.acquire())
            self.assertIsNone(second.handle)
            first.close()
            self.assertTrue(second.acquire())
        finally:
            first.close()
            second.close()
