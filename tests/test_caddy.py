import unittest

from llmctl import caddy
from llmctl.caddy import CADDY_CA, CADDYFILE_DST, STABLE_CA
from llmctl.effects import RunResult
from tests.fakes import FakeEffects

ENV = {
    "SERVER_HOSTNAME": "studio.local",
    "MLX_HOST": "127.0.0.1:8080",
    "SHIM_HOST": "127.0.0.1:8081",
    "API_KEY": "deadbeef",
}

LABEL = "com.caddy.service"
PLIST_DST = f"/Library/LaunchDaemons/{LABEL}.plist"
PRINT = ("launchctl", "print", f"system/{LABEL}")
REPO = "/repo"
REPO_CA = f"{REPO}/exported-ca/root.crt"


def _ok(out=""):
    return RunResult(0, out, "")


class RenderCaddyfileTest(unittest.TestCase):
    def test_includes_hostname_upstream_and_bearer(self):
        out = caddy.render_caddyfile(ENV)
        self.assertIn("studio.local {", out)
        self.assertIn("reverse_proxy 127.0.0.1:8081", out)  # upstream is the shim
        self.assertIn('Authorization "Bearer deadbeef"', out)
        self.assertIn("tls internal", out)
        # no leftover placeholders
        self.assertNotIn("${", out)


class InstallTest(unittest.TestCase):
    def _fx(self, *, extra=None, read_texts=None):
        results = {
            PRINT: RunResult(1, "", ""),  # daemon not loaded
            ("sudo", "launchctl", "bootstrap", "system", PLIST_DST): _ok(),
            ("sudo", "cat", CADDY_CA): _ok("CA-PEM-DATA"),
        }
        # any sudo tee/chown/chmod succeeds
        for r in (extra or {}):
            results[r] = (extra or {})[r]
        fx = FakeEffects(run_results=results, read_texts=read_texts or {})
        return fx

    def test_renders_and_places_caddyfile(self):
        fx = self._fx()
        caddy.install(fx, env=ENV, repo_root=REPO)
        teed = [c[1] for c in fx.calls if c[0] == "run" and c[1][:2] == ("sudo", "tee")]
        self.assertIn(("sudo", "tee", CADDYFILE_DST), [t[:3] for t in teed])

    def test_kicks_caddy_when_only_caddyfile_changed(self):
        # daemon loaded + plist unchanged -> install_daemon is a no-op, so a
        # Caddyfile-only change (new upstream) must kickstart Caddy to reload.
        fx = self._fx(
            extra={PRINT: _ok()},
            read_texts={PLIST_DST: caddy.PLIST_TMPL.read_text()},
        )
        caddy.install(fx, env=ENV, repo_root=REPO)
        runs = [c[1] for c in fx.calls if c[0] == "run"]
        self.assertIn(("sudo", "launchctl", "kickstart", "-k", f"system/{LABEL}"), runs)

    def test_installs_caddy_daemon(self):
        fx = self._fx()
        caddy.install(fx, env=ENV, repo_root=REPO)
        runs = [c[1] for c in fx.calls if c[0] == "run"]
        self.assertIn(("sudo", "launchctl", "bootstrap", "system", PLIST_DST), runs)

    def test_exports_ca_to_stable_and_repo_paths(self):
        fx = self._fx()
        caddy.install(fx, env=ENV, repo_root=REPO)
        teed = [c[1][2] for c in fx.calls if c[0] == "run" and c[1][:2] == ("sudo", "tee")]
        self.assertIn(STABLE_CA, teed)
        self.assertIn(REPO_CA, teed)

    def test_reads_generated_ca_when_stable_absent(self):
        fx = self._fx()
        caddy.install(fx, env=ENV, repo_root=REPO)
        self.assertIn(("run", ("sudo", "cat", CADDY_CA)), fx.calls)

    def test_preserves_existing_ca_on_rerun(self):
        # stable CA already present -> do NOT regenerate from Caddy, do NOT
        # rewrite the stable path; clients stay trusted.
        fx = self._fx(read_texts={STABLE_CA: "OLD-CA-PEM"})
        caddy.install(fx, env=ENV, repo_root=REPO)
        # never reads Caddy's freshly generated CA
        self.assertNotIn(("run", ("sudo", "cat", CADDY_CA)), fx.calls)
        # never overwrites the stable path
        stable_writes = [
            c for c in fx.calls
            if c[0] == "run" and c[1][:2] == ("sudo", "tee") and c[1][2] == STABLE_CA
        ]
        self.assertEqual(stable_writes, [])
        # but still publishes the preserved CA to the repo export
        repo_writes = [
            c for c in fx.calls
            if c[0] == "run" and c[1][:2] == ("sudo", "tee") and c[1][2] == REPO_CA
        ]
        self.assertTrue(repo_writes)


if __name__ == "__main__":
    unittest.main()
