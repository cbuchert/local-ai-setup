import tempfile
import unittest
from pathlib import Path

from llmctl import cleanup as cleanup_mod
from llmctl.effects import RunResult
from tests.fakes import FakeEffects

DAEMON_PRINT = ("launchctl", "print", "system/com.ollama.service")
DAEMON_PLIST = "/Library/LaunchDaemons/com.ollama.service.plist"
DAEMON_BOOTOUT = ("sudo", "launchctl", "bootout", "system/com.ollama.service")
DAEMON_RM = ("sudo", "rm", "-f", DAEMON_PLIST)
BREW_FORMULA = ("brew", "list", "ollama")
BREW_CASK = ("brew", "list", "--cask", "ollama-app")
UNINSTALL_FORMULA = ("brew", "uninstall", "ollama")
UNINSTALL_CASK = ("brew", "uninstall", "--cask", "ollama-app")

HOME = Path.home()
OLLAMA_DIR = str(HOME / ".ollama")
LOG = str(HOME / "Library/Logs/ollama.log")
ERR = str(HOME / "Library/Logs/ollama.err")

GB = 1024 ** 3


def _ok(out=""):
    return RunResult(0, out, "")


def _fail():
    return RunResult(1, "", "")


def _all_present():
    """A box with every Ollama remnant present."""
    return FakeEffects(
        run_results={
            DAEMON_PRINT: _ok(),  # daemon loaded
            BREW_FORMULA: _ok(),  # formula installed
            BREW_CASK: _ok(),  # cask installed
            DAEMON_BOOTOUT: _ok(),
            DAEMON_RM: _ok(),
            UNINSTALL_FORMULA: _ok(),
            UNINSTALL_CASK: _ok(),
            ("sudo", "rm", "-rf", OLLAMA_DIR): _ok(),
            ("rm", "-f", LOG): _ok(),
            ("rm", "-f", ERR): _ok(),
        },
        dir_sizes={OLLAMA_DIR: 12 * GB},
        paths={OLLAMA_DIR, LOG, ERR},
    )


def _clean():
    """A migrated box: daemon gone, brew gone, no blobs, no logs."""
    return FakeEffects(
        run_results={
            DAEMON_PRINT: _fail(),
            BREW_FORMULA: _fail(),
            BREW_CASK: _fail(),
        },
    )


class DetectTest(unittest.TestCase):
    def test_finds_every_remnant_when_present(self):
        r = cleanup_mod.detect(_all_present())
        self.assertTrue(r.daemon)
        self.assertTrue(r.formula)
        self.assertTrue(r.cask)
        self.assertTrue(r.blobs)
        self.assertEqual(r.blob_bytes, 12 * GB)
        self.assertEqual(sorted(r.logs), sorted([LOG, ERR]))
        self.assertTrue(r.present)

    def test_clean_box_finds_nothing(self):
        r = cleanup_mod.detect(_clean())
        self.assertFalse(r.daemon)
        self.assertFalse(r.formula)
        self.assertFalse(r.cask)
        self.assertFalse(r.blobs)
        self.assertEqual(r.logs, [])
        self.assertFalse(r.present)


class RunRemovalTest(unittest.TestCase):
    def _run(self, fx, env_path):
        return cleanup_mod.run(fx, env_path=env_path, out=lambda *_: None)

    def _env(self, text=""):
        d = Path(tempfile.mkdtemp())
        p = d / ".env"
        p.write_text(text)
        return p

    def test_boots_out_and_removes_daemon(self):
        fx = _all_present()
        self._run(fx, self._env())
        runs = [c[1] for c in fx.calls if c[0] == "run"]
        self.assertIn(DAEMON_BOOTOUT, runs)
        self.assertIn(DAEMON_RM, runs)

    def test_uninstalls_brew_formula_and_cask(self):
        fx = _all_present()
        self._run(fx, self._env())
        runs = [c[1] for c in fx.calls if c[0] == "run"]
        self.assertIn(UNINSTALL_FORMULA, runs)
        self.assertIn(UNINSTALL_CASK, runs)

    def test_deletes_blob_dir_and_logs(self):
        fx = _all_present()
        self._run(fx, self._env())
        runs = [c[1] for c in fx.calls if c[0] == "run"]
        self.assertIn(("sudo", "rm", "-rf", OLLAMA_DIR), runs)
        self.assertIn(("rm", "-f", LOG), runs)
        self.assertIn(("rm", "-f", ERR), runs)

    def test_logs_reclaimed_gb(self):
        fx = _all_present()
        out = []
        cleanup_mod.run(fx, env_path=self._env(), out=out.append)
        report = "\n".join(out)
        self.assertIn("12", report)  # 12 GB reclaimed
        self.assertIn("GB", report)

    def test_carries_forward_server_identity(self):
        fx = _all_present()
        env_path = self._env(
            "OLLAMA_API_KEY=secret-token-abc\n"
            "SERVER_HOSTNAME=studio.local\n"
            "IOGPU_WIRED_LIMIT_MB=57344\n"
            "OLLAMA_HOST=127.0.0.1:11434\n"
        )
        cleanup_mod.run(fx, env_path=env_path, out=lambda *_: None)
        from llmctl import env as env_mod
        new = env_mod.load_env(env_path)
        self.assertEqual(new["API_KEY"], "secret-token-abc")
        self.assertEqual(new["SERVER_HOSTNAME"], "studio.local")
        self.assertEqual(new["IOGPU_WIRED_LIMIT_MB"], "57344")
        self.assertNotIn("OLLAMA_API_KEY", new)
        self.assertNotIn("OLLAMA_HOST", new)

    def test_reaps_surviving_ollama_process(self):
        # bootout leaves a live `ollama serve` holding the port (hardware bug)
        fx = _all_present()
        self._run(fx, self._env())
        runs = [c[1] for c in fx.calls if c[0] == "run"]
        self.assertIn(("pkill", "-f", "ollama serve"), runs)
        self.assertIn(("sudo", "pkill", "-f", "ollama serve"), runs)

    def test_backfills_missing_schema_keys_from_example(self):
        d = Path(tempfile.mkdtemp())
        (d / ".env.example").write_text("MLX_HOST=127.0.0.1:8080\nHF_HOME=\n")
        env_path = d / ".env"
        env_path.write_text("OLLAMA_API_KEY=tok\nSERVER_HOSTNAME=casper.local\n")
        cleanup_mod.run(_all_present(), env_path=env_path, out=lambda *_: None)
        from llmctl import env as env_mod
        new = env_mod.load_env(env_path)
        self.assertEqual(new["MLX_HOST"], "127.0.0.1:8080")  # backfilled, not empty
        self.assertEqual(new["API_KEY"], "tok")
        self.assertEqual(new["SERVER_HOSTNAME"], "casper.local")

    def test_keeps_existing_api_key_when_no_ollama_key(self):
        # already-migrated .env on a box that still has, say, leftover logs
        fx = _all_present()
        env_path = self._env("API_KEY=already-here\nSERVER_HOSTNAME=h\n")
        cleanup_mod.run(fx, env_path=env_path, out=lambda *_: None)
        from llmctl import env as env_mod
        self.assertEqual(env_mod.load_env(env_path)["API_KEY"], "already-here")


class NoOpTest(unittest.TestCase):
    def test_clean_box_issues_no_removal_commands(self):
        fx = _clean()
        d = Path(tempfile.mkdtemp())
        env_path = d / ".env"
        env_path.write_text("API_KEY=keep\n")
        cleanup_mod.run(fx, env_path=env_path, out=lambda *_: None)
        runs = [c[1] for c in fx.calls if c[0] == "run"]
        for argv in (DAEMON_BOOTOUT, UNINSTALL_FORMULA, UNINSTALL_CASK,
                     ("pkill", "-f", "ollama serve")):
            self.assertNotIn(argv, runs)
        # .env untouched on a no-op
        self.assertEqual(env_path.read_text(), "API_KEY=keep\n")

    def test_clean_box_returns_clean_remnants(self):
        self.assertFalse(cleanup_mod.run(_clean(), env_path=Path("/nonexistent/.env")).present)


if __name__ == "__main__":
    unittest.main()
