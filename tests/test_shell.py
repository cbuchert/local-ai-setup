import tempfile
import unittest
from pathlib import Path

from llmctl.shell import MARKER_START, unwire_path, wire_path


class WirePathTest(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        self.rc = self.d / ".zshrc"
        self.bin = "/repo/bin"

    def test_appends_guarded_block(self):
        self.rc.write_text("export FOO=1\n")
        wire_path([self.rc], self.bin)
        text = self.rc.read_text()
        self.assertIn(MARKER_START, text)
        self.assertIn(f'export PATH="{self.bin}:$PATH"', text)
        self.assertIn("export FOO=1", text)  # existing content kept

    def test_idempotent(self):
        wire_path([self.rc], self.bin)
        wire_path([self.rc], self.bin)
        self.assertEqual(self.rc.read_text().count(MARKER_START), 1)

    def test_creates_file_when_absent(self):
        wire_path([self.rc], self.bin)
        self.assertTrue(self.rc.exists())

    def test_unwire_removes_block_and_keeps_rest(self):
        self.rc.write_text("export FOO=1\n")
        wire_path([self.rc], self.bin)
        unwire_path([self.rc])
        text = self.rc.read_text()
        self.assertNotIn(MARKER_START, text)
        self.assertIn("export FOO=1", text)

    def test_unwire_is_noop_when_absent(self):
        self.rc.write_text("export FOO=1\n")
        unwire_path([self.rc])
        self.assertEqual(self.rc.read_text(), "export FOO=1\n")


if __name__ == "__main__":
    unittest.main()
