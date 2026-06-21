import tempfile
import types
import unittest
from pathlib import Path

from llmctl import lifecycle
from llmctl.effects import RunResult
from tests.fakes import FakeEffects

RUNNER = "com.mlx.service"
CADDY = "com.caddy.service"
IOGPU = "com.local.iogpu-wired-limit"


def absent():
    return types.SimpleNamespace(present=False)


def present():
    return types.SimpleNamespace(present=True)


def runs(fx):
    return [c[1] for c in fx.calls if c[0] == "run"]


class TeardownTiersTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.rc = [self.root / ".zshrc"]

    def _teardown(self, **kw):
        fx = FakeEffects(hf_cache=[("a/b", 100), ("c/d", 200)])
        lifecycle.teardown(
            fx, repo_root=self.root, hf_home="/hf", rc_files=self.rc, out=lambda *_: None, **kw
        )
        return fx

    def test_default_removes_only_the_runner(self):
        fx = self._teardown()
        r = runs(fx)
        self.assertIn(("sudo", "launchctl", "bootout", f"system/{RUNNER}"), r)
        # preserves caddy, iogpu, CA, models by default
        self.assertNotIn(("sudo", "launchctl", "bootout", f"system/{CADDY}"), r)
        self.assertNotIn(("sudo", "launchctl", "bootout", f"system/{IOGPU}"), r)
        self.assertFalse(any(c[0] == "hf_delete" for c in fx.calls))

    def test_models_flag_wipes_cache(self):
        fx = self._teardown(models_too=True)
        deleted = {c[1] for c in fx.calls if c[0] == "hf_delete"}
        self.assertEqual(deleted, {"a/b", "c/d"})

    def test_ca_flag_removes_ca(self):
        fx = self._teardown(ca=True)
        self.assertIn(("sudo", "rm", "-f", "/usr/local/share/mac-studio-ca.crt"), runs(fx))

    def test_all_removes_everything(self):
        fx = self._teardown(all=True)
        r = runs(fx)
        self.assertIn(("sudo", "launchctl", "bootout", f"system/{CADDY}"), r)
        self.assertIn(("sudo", "launchctl", "bootout", f"system/{IOGPU}"), r)
        self.assertTrue(any(c[0] == "hf_delete" for c in fx.calls))


class InstallGatingTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.calls = []
        self.phases = lambda *a, **k: self.calls.append("phases")
        self.cleaned = []
        self.cleanup_fn = lambda *a, **k: self.cleaned.append("cleanup")

    def _install(self, *, detect, **kw):
        fx = FakeEffects()
        lifecycle.install(
            fx,
            env={},
            manifest_path=self.root / "models.toml",
            repo_root=self.root,
            hf_home="/hf",
            rc_files=[self.root / ".zshrc"],
            env_path=self.root / ".env",
            out=lambda *_: None,
            detect_fn=detect,
            cleanup_fn=self.cleanup_fn,
            phases=self.phases,
            **kw,
        )

    def test_no_remnants_runs_phases_and_wires_path(self):
        self._install(detect=lambda _e: absent())
        self.assertEqual(self.calls, ["phases"])
        self.assertEqual(self.cleaned, [])
        self.assertTrue((self.root / ".zshrc").exists())  # PATH wired

    def test_remnants_with_migrate_flag_runs_cleanup(self):
        self._install(detect=lambda _e: present(), migrate=True)
        self.assertEqual(self.cleaned, ["cleanup"])
        self.assertEqual(self.calls, ["phases"])

    def test_remnants_unattended_without_migrate_aborts(self):
        with self.assertRaises(lifecycle.MigrationNeeded):
            self._install(detect=lambda _e: present(), unattended=True)
        self.assertEqual(self.calls, [])  # never reached the phases

    def test_remnants_interactive_yes_runs_cleanup(self):
        self._install(detect=lambda _e: present(), prompt=lambda _m: "y")
        self.assertEqual(self.cleaned, ["cleanup"])


if __name__ == "__main__":
    unittest.main()
