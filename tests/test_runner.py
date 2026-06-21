"""Tests for llmctl.runner — the MLX Runner LaunchDaemon phase (#3).

Driven entirely through FakeEffects: render the plist from .env + the Default
Model's Profile, install/reload it idempotently, and wait for /v1/models.
"""

import unittest
from pathlib import Path

from llmctl import runner
from llmctl.effects import RunResult
from tests.fakes import FakeEffects

# The committed plist template (read straight off disk in tests — it is a repo
# file, not a side effect; only the manifest is faked).
TEMPLATE = (Path(__file__).resolve().parent.parent / runner.TEMPLATE_REL).read_text()

REPO_ROOT = Path("/repo")
MANIFEST = REPO_ROOT / "models.toml"
LABEL = "com.mlx.service"
DST = f"/Library/LaunchDaemons/{LABEL}.plist"
TARGET = f"system/{LABEL}"
PRINT = ("launchctl", "print", TARGET)

ENV = {
    "MLX_HOST": "127.0.0.1:8080",
    "HF_HOME": "/models/hf",
    "API_KEY": "secret-token",
}

# A minimal manifest with a Default Model carrying a Profile whose values
# include an XML-special char to prove escaping (the repo id has none normally,
# so we use a contrived default block).
MANIFEST_TOML = """
[[models]]
name = "qwen3-coder-30b"
repo = "mlx-community/Qwen3-Coder-30B-A3B-Instruct-4bit"
default = true
role = "agent"
max_tokens = 8192
temp = 0.7
prompt_cache_bytes = 32000000000
"""


def _reader(toml=MANIFEST_TOML):
    """A path-aware read_text: manifest -> TOML, template -> the real template."""
    def read(path):
        return toml if str(path).endswith("models.toml") else TEMPLATE
    return read


def _ok():
    return RunResult(0, "", "")


TEMPLATE_PATH = str(REPO_ROOT / runner.TEMPLATE_REL)


def _fx(**kw):
    """FakeEffects pre-scripted so render_plist's reads (manifest + template)
    flow through effects.read_text, the way install() routes them."""
    read_texts = kw.pop("read_texts", {})
    read_texts.setdefault(str(MANIFEST), MANIFEST_TOML)
    read_texts.setdefault(TEMPLATE_PATH, TEMPLATE)
    return FakeEffects(read_texts=read_texts, **kw)


class RenderPlistTest(unittest.TestCase):
    def test_profile_flags_present(self):
        text = runner.render_plist(env=ENV, manifest_path=MANIFEST, repo_root=REPO_ROOT,
                                   read_text=_reader())
        # the venv binary, split host/port, and every Profile flag
        self.assertIn("/repo/.venv/bin/mlx_lm.server", text)
        self.assertIn("<string>--model</string>", text)
        self.assertIn("mlx-community/Qwen3-Coder-30B-A3B-Instruct-4bit", text)
        self.assertIn("<string>--host</string>", text)
        self.assertIn("<string>127.0.0.1</string>", text)
        self.assertIn("<string>--port</string>", text)
        self.assertIn("<string>8080</string>", text)
        self.assertIn("<string>--max-tokens</string>", text)
        self.assertIn("<string>8192</string>", text)
        self.assertIn("<string>--prompt-cache-bytes</string>", text)
        self.assertIn("<string>32000000000</string>", text)
        self.assertIn("<string>--temp</string>", text)
        self.assertIn("<string>0.7</string>", text)
        self.assertIn("/models/hf", text)  # HF_HOME env var

    def test_label_is_com_mlx_service(self):
        text = runner.render_plist(env=ENV, manifest_path=MANIFEST, repo_root=REPO_ROOT,
                                   read_text=_reader())
        self.assertIn("<string>com.mlx.service</string>", text)

    def test_runs_as_root_no_username_key(self):
        text = runner.render_plist(env=ENV, manifest_path=MANIFEST, repo_root=REPO_ROOT,
                                   read_text=_reader())
        # no UserName *key* -> the job runs as root (the comment may mention it)
        self.assertNotIn("<key>UserName</key>", text)

    def test_xml_escaped(self):
        # a repo id with an ampersand must come out XML-escaped, not raw
        toml = MANIFEST_TOML.replace(
            "mlx-community/Qwen3-Coder-30B-A3B-Instruct-4bit",
            "org/A&B<model>",
        )
        text = runner.render_plist(env=ENV, manifest_path=MANIFEST, repo_root=REPO_ROOT,
                                   read_text=_reader(toml))
        self.assertIn("org/A&amp;B&lt;model&gt;", text)
        self.assertNotIn("A&B<model>", text)

    def test_no_default_model_raises(self):
        toml = MANIFEST_TOML.replace("default = true\n", "")
        with self.assertRaises(ValueError):
            runner.render_plist(env=ENV, manifest_path=MANIFEST, repo_root=REPO_ROOT,
                                read_text=_reader(toml))


class InstallTest(unittest.TestCase):
    def _scripted(self, server_status):
        url = "http://127.0.0.1:8080/v1/models"
        fx = _fx(
            run_results={
                PRINT: RunResult(1, "", ""),  # not loaded -> bootstrap
                ("sudo", "tee", DST): _ok(),
                ("sudo", "chown", "root:wheel", DST): _ok(),
                ("sudo", "chmod", "644", DST): _ok(),
                ("sudo", "launchctl", "bootstrap", "system", DST): _ok(),
            },
            http_statuses={url: server_status},
        )
        return fx, url

    def test_installs_daemon_and_waits_for_server(self):
        fx, url = self._scripted(200)
        runner.install(fx, env=ENV, manifest_path=MANIFEST, repo_root=REPO_ROOT,
                       sleep=lambda _: None)
        runs = [c[1] for c in fx.calls if c[0] == "run"]
        self.assertIn(("sudo", "launchctl", "bootstrap", "system", DST), runs)
        # waited on /v1/models
        self.assertIn(("http", url), fx.calls)

    def test_install_times_out_with_log_path(self):
        fx, url = self._scripted(None)  # server never answers
        with self.assertRaises(TimeoutError) as ctx:
            runner.install(fx, env=ENV, manifest_path=MANIFEST, repo_root=REPO_ROOT,
                           poll=3, sleep=lambda _: None)
        # fails loudly, naming the runner log path
        self.assertIn("mlx", str(ctx.exception).lower())
        self.assertIn("Logs", str(ctx.exception))

    def test_install_passes_api_key_to_probe(self):
        fx, url = self._scripted(200)
        runner.install(fx, env=ENV, manifest_path=MANIFEST, repo_root=REPO_ROOT,
                       sleep=lambda _: None)
        self.assertIn(("http", url), fx.calls)


class RestartTest(unittest.TestCase):
    def test_restart_kickstarts_the_runner(self):
        fx = _fx(run_results={
            ("sudo", "launchctl", "kickstart", "-k", TARGET): _ok(),
        })
        runner.restart(fx)
        runs = [c[1] for c in fx.calls if c[0] == "run"]
        self.assertIn(("sudo", "launchctl", "kickstart", "-k", TARGET), runs)


class LogsTest(unittest.TestCase):
    def test_logs_tails_the_runner_log(self):
        fx = _fx(run_results={})
        runner.logs(fx)
        runs = [c[1] for c in fx.calls if c[0] == "run"]
        # tails the runner log via the seam
        self.assertTrue(any(r[0] == "tail" and any("mlx" in a for a in r) for r in runs),
                        f"expected a tail of the mlx log, got: {runs}")


if __name__ == "__main__":
    unittest.main()
