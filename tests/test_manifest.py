import tempfile
import unittest
from pathlib import Path

from llmctl.manifest import default_model, load_models, set_default

SAMPLE = """
[[models]]
name = "qwen3-coder-30b"
repo = "mlx-community/Qwen3-Coder-30B-A3B-Instruct-4bit"
default = true
role = "agent"
max_tokens = 8192
temp = 0.7
prompt_cache_bytes = 32000000000

[[models]]
name = "gpt-oss-20b"
repo = "mlx-community/gpt-oss-20b-MXFP4-Q8"
role = "chat"
max_tokens = 4096
temp = 1.0
prompt_cache_bytes = 8000000000
"""


class LoadModelsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()) / "models.toml"
        self.tmp.write_text(SAMPLE)

    def test_parses_repos_and_profiles(self):
        models = load_models(self.tmp)
        self.assertEqual([m.name for m in models], ["qwen3-coder-30b", "gpt-oss-20b"])
        self.assertEqual(models[0].repo, "mlx-community/Qwen3-Coder-30B-A3B-Instruct-4bit")
        self.assertEqual(models[0].profile["max_tokens"], 8192)
        self.assertEqual(models[0].profile["prompt_cache_bytes"], 32000000000)

    def test_default_model_is_the_marked_one(self):
        d = default_model(load_models(self.tmp))
        self.assertEqual(d.name, "qwen3-coder-30b")


class SetDefaultTest(unittest.TestCase):
    def test_moves_marker_to_named_model_and_keeps_exactly_one(self):
        tmp = Path(tempfile.mkdtemp()) / "models.toml"
        tmp.write_text("# keep me\n" + SAMPLE)
        set_default(tmp, "gpt-oss-20b")
        models = load_models(tmp)
        self.assertEqual(default_model(models).name, "gpt-oss-20b")
        self.assertEqual(sum(m.default for m in models), 1)
        self.assertIn("# keep me", tmp.read_text())  # comments preserved

    def test_unknown_model_raises(self):
        tmp = Path(tempfile.mkdtemp()) / "models.toml"
        tmp.write_text(SAMPLE)
        with self.assertRaises(KeyError):
            set_default(tmp, "nope")


class CommittedManifestTest(unittest.TestCase):
    def test_repo_models_toml_parses_with_exactly_one_default(self):
        repo_root = Path(__file__).resolve().parent.parent
        models = load_models(repo_root / "models.toml")
        self.assertGreaterEqual(len(models), 1)
        defaults = [m for m in models if m.default]
        self.assertEqual(len(defaults), 1, "exactly one model must be default")


if __name__ == "__main__":
    unittest.main()
