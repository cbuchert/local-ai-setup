import unittest

from llmctl.effects import RunResult
from llmctl.sys import install_daemon, render_template
from tests.fakes import FakeEffects

LABEL = "com.mlx.service"
DST = f"/Library/LaunchDaemons/{LABEL}.plist"
TARGET = f"system/{LABEL}"
PRINT = ("launchctl", "print", TARGET)


def _ok():
    return RunResult(0, "", "")


class RenderTemplateTest(unittest.TestCase):
    def test_substitutes_known_vars(self):
        out = render_template("host=${HOST}\n", {"HOST": "studio.local"})
        self.assertEqual(out, "host=studio.local\n")

    def test_xml_escapes_when_requested(self):
        out = render_template("<v>${X}</v>", {"X": "a & b <c>"}, escape_xml=True)
        self.assertIn("a &amp; b &lt;c&gt;", out)

    def test_missing_var_raises(self):
        with self.assertRaises(KeyError):
            render_template("${NOPE}", {})

    def test_leftover_placeholder_raises(self):
        # a mistyped lowercase placeholder slips past substitution -> caught
        with self.assertRaises(ValueError):
            render_template("${ok}", {})


class InstallDaemonTest(unittest.TestCase):
    def test_installs_and_bootstraps_when_absent(self):
        fx = FakeEffects(
            run_results={
                PRINT: RunResult(1, "", ""),  # not loaded
                ("sudo", "tee", DST): _ok(),
                ("sudo", "chown", "root:wheel", DST): _ok(),
                ("sudo", "chmod", "644", DST): _ok(),
                ("sudo", "launchctl", "bootstrap", "system", DST): _ok(),
            },
        )
        changed = install_daemon(fx, label=LABEL, plist_text="<plist/>", sleep=lambda _: None)
        self.assertTrue(changed)
        self.assertIn(("run", ("sudo", "launchctl", "bootstrap", "system", DST)), fx.calls)

    def test_noop_when_unchanged_and_loaded(self):
        fx = FakeEffects(
            run_results={PRINT: _ok()},  # loaded
            read_texts={DST: "<plist/>"},  # on-disk matches
        )
        changed = install_daemon(fx, label=LABEL, plist_text="<plist/>", sleep=lambda _: None)
        self.assertFalse(changed)
        # no write, no bootout
        self.assertNotIn(("run", ("sudo", "tee", DST)), fx.calls)
        self.assertFalse(any(c[1][:3] == ("sudo", "launchctl", "bootout") for c in fx.calls if c[0] == "run"))

    def test_reloads_when_changed_and_loaded(self):
        fx = FakeEffects(
            run_results={
                # loaded, then unloaded after bootout
                PRINT: [_ok(), RunResult(1, "", "")],
                ("sudo", "tee", DST): _ok(),
                ("sudo", "chown", "root:wheel", DST): _ok(),
                ("sudo", "chmod", "644", DST): _ok(),
                ("sudo", "launchctl", "bootout", TARGET): _ok(),
                ("sudo", "launchctl", "bootstrap", "system", DST): _ok(),
            },
            read_texts={DST: "<old/>"},
        )
        changed = install_daemon(fx, label=LABEL, plist_text="<new/>", sleep=lambda _: None)
        self.assertTrue(changed)
        runs = [c[1] for c in fx.calls if c[0] == "run"]
        self.assertIn(("sudo", "launchctl", "bootout", TARGET), runs)
        self.assertIn(("sudo", "launchctl", "bootstrap", "system", DST), runs)

    def test_raises_if_daemon_never_unloads(self):
        fx = FakeEffects(
            run_results={
                PRINT: _ok(),  # stays loaded forever
                ("sudo", "tee", DST): _ok(),
                ("sudo", "chown", "root:wheel", DST): _ok(),
                ("sudo", "chmod", "644", DST): _ok(),
                ("sudo", "launchctl", "bootout", TARGET): _ok(),
                ("sudo", "rm", "-f", DST): _ok(),
            },
            read_texts={DST: "<old/>"},
        )
        with self.assertRaises(RuntimeError):
            install_daemon(fx, label=LABEL, plist_text="<new/>", poll=3, sleep=lambda _: None)
        # removes the stale dst so the operator's retry re-enters the changed path
        self.assertIn(("run", ("sudo", "rm", "-f", DST)), fx.calls)


if __name__ == "__main__":
    unittest.main()
