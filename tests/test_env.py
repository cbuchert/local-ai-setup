import tempfile
import unittest
from pathlib import Path

from llmctl import env as env_mod
from llmctl.effects import RunResult
from tests.fakes import FakeEffects


class ParseTest(unittest.TestCase):
    def test_parse_skips_comments_and_blanks(self):
        parsed = env_mod.parse_env("# c\n\nA=1\nB = two \n")
        self.assertEqual(parsed, {"A": "1", "B": "two"})


class EnsureEnvTest(unittest.TestCase):
    def test_creates_env_from_example_when_missing(self):
        d = Path(tempfile.mkdtemp())
        example = d / ".env.example"
        example.write_text("SERVER_HOSTNAME=studio.local\nAPI_KEY=\n")
        env_path = d / ".env"
        result = env_mod.ensure_env(env_path, example)
        self.assertTrue(env_path.exists())
        self.assertEqual(result["SERVER_HOSTNAME"], "studio.local")

    def test_preserves_existing_env(self):
        d = Path(tempfile.mkdtemp())
        (d / ".env").write_text("API_KEY=existing\n")
        result = env_mod.ensure_env(d / ".env", d / ".env.example")
        self.assertEqual(result["API_KEY"], "existing")


class ApiKeyTest(unittest.TestCase):
    def test_generates_32_hex_chars(self):
        key = env_mod.generate_api_key()
        self.assertEqual(len(key), 32)
        int(key, 16)  # valid hex


class GpuDetectTest(unittest.TestCase):
    def test_caps_to_total_ram_minus_8gb_headroom(self):
        # 64 GiB → 65536 MB − 8192 = 57344 MB
        fx = FakeEffects(
            run_results={("sysctl", "-n", "hw.memsize"): RunResult(0, "68719476736\n", "")}
        )
        self.assertEqual(env_mod.detect_gpu_cap_mb(fx), 57344)


if __name__ == "__main__":
    unittest.main()
