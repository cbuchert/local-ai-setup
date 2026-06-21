import unittest

from llmctl import power
from llmctl.effects import RunResult
from tests.fakes import FakeEffects

LABEL = "com.local.iogpu-wired-limit"
DST = f"/Library/LaunchDaemons/{LABEL}.plist"
TARGET = f"system/{LABEL}"
PRINT = ("launchctl", "print", TARGET)
TMPL = "/repo/config/com.local.iogpu-wired-limit.plist.tmpl"

PLIST_TMPL = (
    "<plist>\n"
    "  <string>iogpu.wired_limit_mb=${IOGPU_WIRED_LIMIT_MB}</string>\n"
    "</plist>\n"
)


def _ok():
    return RunResult(0, "", "")


def _fx(read_texts=None, run_results=None):
    """FakeEffects with the daemon-install happy path scripted (absent -> bootstrap)."""
    runs = {
        PRINT: RunResult(1, "", ""),  # not loaded
        ("sudo", "tee", DST): _ok(),
        ("sudo", "chown", "root:wheel", DST): _ok(),
        ("sudo", "chmod", "644", DST): _ok(),
        ("sudo", "launchctl", "bootstrap", "system", DST): _ok(),
    }
    runs.update(run_results or {})
    return FakeEffects(run_results=runs, read_texts={TMPL: PLIST_TMPL, **(read_texts or {})})


def _apply(fx, cap="32768"):
    return power.apply(fx, env={"IOGPU_WIRED_LIMIT_MB": cap}, template_path=TMPL)


def _runs(fx):
    return [c[1] for c in fx.calls if c[0] == "run"]


class PowerSettingsTest(unittest.TestCase):
    def test_disables_sleep(self):
        fx = _fx()
        _apply(fx)
        self.assertIn(("sudo", "pmset", "-a", "sleep", "0", "disablesleep", "1"), _runs(fx))

    def test_autorestart_on_power_loss(self):
        fx = _fx()
        _apply(fx)
        self.assertIn(("sudo", "pmset", "-a", "autorestart", "1"), _runs(fx))

    def test_wake_on_network(self):
        fx = _fx()
        _apply(fx)
        self.assertIn(("sudo", "pmset", "-a", "womp", "1"), _runs(fx))

    def test_restart_on_freeze(self):
        fx = _fx()
        _apply(fx)
        self.assertIn(("sudo", "systemsetup", "-setrestartfreeze", "on"), _runs(fx))


class IogpuDaemonTest(unittest.TestCase):
    def test_renders_cap_into_plist(self):
        rendered = power.render_iogpu_plist(_fx(), "49152", TMPL)
        self.assertIn("iogpu.wired_limit_mb=49152", rendered)

    def test_installs_daemon(self):
        fx = _fx()
        _apply(fx)
        self.assertIn(("sudo", "launchctl", "bootstrap", "system", DST), _runs(fx))

    def test_writes_rendered_cap_to_disk(self):
        fx = _fx()
        _apply(fx, cap="49152")
        tee = [c for c in fx.calls if c[0] == "run" and c[1] == ("sudo", "tee", DST)]
        self.assertEqual(len(tee), 1)

    def test_reads_template_through_seam(self):
        fx = _fx()
        _apply(fx)
        self.assertIn(("read_text", TMPL), fx.calls)


class ImmediateCapTest(unittest.TestCase):
    def test_applies_cap_now_via_sysctl(self):
        fx = _fx()
        _apply(fx, cap="32768")
        self.assertIn(("sudo", "sysctl", "iogpu.wired_limit_mb=32768"), _runs(fx))


if __name__ == "__main__":
    unittest.main()
