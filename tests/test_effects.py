import tempfile
import unittest
from pathlib import Path

from llmctl.effects import Effects


class HfGuardsTest(unittest.TestCase):
    """Real Effects must not raise CacheNotFound on a fresh box (no cache dir)."""

    def test_hf_cache_and_delete_noop_on_missing_cache_dir(self):
        hf_home = str(Path(tempfile.mkdtemp()) / "no-hf-home")  # no /hub under it
        fx = Effects()
        self.assertEqual(fx.hf_cache(hf_home), [])
        fx.hf_delete("some/repo", hf_home)  # must be a no-op, not CacheNotFound


if __name__ == "__main__":
    unittest.main()
