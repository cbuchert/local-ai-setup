import tempfile
import unittest
from pathlib import Path

from llmctl import env as env_mod
from llmctl.effects import RunResult
from llmctl.setup import run_config_walkthrough
from tests.fakes import FakeEffects

MANIFEST = """
[[models]]
name = "qwen3-coder-30b"
repo = "mlx-community/Qwen3-Coder-30B-A3B-Instruct-4bit"
default = true
role = "agent"

[[models]]
name = "gpt-oss-20b"
repo = "mlx-community/gpt-oss-20b-MXFP4-Q8"
role = "chat"
"""


def scripted(answers):
    it = iter(answers)
    return lambda _prompt: next(it)


class WalkthroughTest(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        (self.d / ".env.example").write_text(
            "SERVER_HOSTNAME=studio.local\nAPI_KEY=\nMLX_HOST=127.0.0.1:8080\n"
            "HF_HOME=\nIOGPU_WIRED_LIMIT_MB=\n"
        )
        self.manifest = self.d / "models.toml"
        self.manifest.write_text(MANIFEST)
        self.out = []
        self.fx = FakeEffects(
            run_results={("sysctl", "-n", "hw.memsize"): RunResult(0, "68719476736\n", "")}
        )

    def _run(self, answers):
        return run_config_walkthrough(
            env_path=self.d / ".env",
            example_path=self.d / ".env.example",
            manifest_path=self.manifest,
            effects=self.fx,
            prompt=scripted(answers),
            out=self.out.append,
        )

    def test_writes_env_picks_default_and_generates_key(self):
        # hostname, model choice (2), HF_HOME (blank), GPU cap (blank=detected)
        values = self._run(["myhost", "2", "", ""])

        written = env_mod.load_env(self.d / ".env")
        self.assertEqual(written["SERVER_HOSTNAME"], "myhost")
        self.assertEqual(written["MLX_HOST"], "127.0.0.1:8080")
        self.assertEqual(written["IOGPU_WIRED_LIMIT_MB"], "57344")  # auto-detected
        self.assertEqual(len(written["API_KEY"]), 32)               # generated
        # default moved to the chosen model
        from llmctl.manifest import default_model, load_models
        self.assertEqual(default_model(load_models(self.manifest)).name, "gpt-oss-20b")
        # the key is surfaced to the operator
        self.assertTrue(any(written["API_KEY"] in line for line in self.out))

    def test_blank_answers_keep_defaults(self):
        self._run(["", "", "", ""])
        written = env_mod.load_env(self.d / ".env")
        self.assertEqual(written["SERVER_HOSTNAME"], "studio.local")
        from llmctl.manifest import default_model, load_models
        self.assertEqual(default_model(load_models(self.manifest)).name, "qwen3-coder-30b")


if __name__ == "__main__":
    unittest.main()
