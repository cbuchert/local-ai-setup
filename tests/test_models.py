import tempfile
import unittest
from pathlib import Path

from llmctl import models as models_mod
from llmctl.manifest import add_model, load_models, remove_model
from tests.fakes import FakeEffects

SAMPLE = """# keep me
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

HF = "/tmp/hf"
DEFAULT_REPO = "mlx-community/Qwen3-Coder-30B-A3B-Instruct-4bit"
CHAT_REPO = "mlx-community/gpt-oss-20b-MXFP4-Q8"


def _manifest():
    p = Path(tempfile.mkdtemp()) / "models.toml"
    p.write_text(SAMPLE)
    return p


# --- manifest writers (tomlkit, comment-preserving) ---------------------------


class AddModelTest(unittest.TestCase):
    def test_appends_block_and_preserves_comments(self):
        p = _manifest()
        add_model(p, "org/new-model", name="new-model", role="chat")
        repos = [m.repo for m in load_models(p)]
        self.assertIn("org/new-model", repos)
        self.assertEqual(len(repos), 3)
        self.assertIn("# keep me", p.read_text())

    def test_add_existing_repo_is_noop(self):
        p = _manifest()
        add_model(p, CHAT_REPO, name="dup")
        self.assertEqual(len(load_models(p)), 2)

    def test_derives_name_from_repo_when_omitted(self):
        p = _manifest()
        add_model(p, "org/My-Model")
        added = next(m for m in load_models(p) if m.repo == "org/My-Model")
        self.assertEqual(added.name, "My-Model")


class RemoveModelTest(unittest.TestCase):
    def test_drops_block_for_repo_and_preserves_comments(self):
        p = _manifest()
        remove_model(p, CHAT_REPO)
        repos = [m.repo for m in load_models(p)]
        self.assertNotIn(CHAT_REPO, repos)
        self.assertEqual(repos, [DEFAULT_REPO])
        self.assertIn("# keep me", p.read_text())

    def test_remove_absent_repo_is_noop(self):
        p = _manifest()
        remove_model(p, "org/not-here")
        self.assertEqual(len(load_models(p)), 2)


# --- reconcile / sync ---------------------------------------------------------


class SyncTest(unittest.TestCase):
    def test_diff_pulls_missing_and_deletes_orphans(self):
        # cache has the chat repo (listed) + an orphan; default is missing.
        fx = FakeEffects(
            free_disk_bytes=10 ** 15,
            hf_cache=[(CHAT_REPO, 12_000_000_000), ("org/orphan", 5_000_000_000)],
            hf_repo_size={DEFAULT_REPO: 17_000_000_000},
        )
        p = _manifest()
        plan = models_mod.sync(fx, manifest_path=p, hf_home=HF)
        self.assertEqual(plan.to_pull, [DEFAULT_REPO])
        self.assertEqual(plan.to_delete, ["org/orphan"])
        # dispatched the pull and the delete through the seam.
        self.assertIn(("hf_download", DEFAULT_REPO, HF), fx.calls)
        self.assertIn(("hf_delete", "org/orphan", HF), fx.calls)

    def test_noop_when_cache_matches_manifest(self):
        fx = FakeEffects(
            free_disk_bytes=10 ** 15,
            hf_cache=[(DEFAULT_REPO, 17_000_000_000), (CHAT_REPO, 12_000_000_000)],
        )
        p = _manifest()
        plan = models_mod.sync(fx, manifest_path=p, hf_home=HF)
        self.assertEqual(plan.to_pull, [])
        self.assertEqual(plan.to_delete, [])


# --- disk guard ---------------------------------------------------------------


class DiskGuardTest(unittest.TestCase):
    def test_add_refuses_pull_that_will_not_fit(self):
        fx = FakeEffects(
            free_disk_bytes=10_000_000_000,  # 10 GB free
            hf_cache=[],
            hf_repo_size={"org/huge": 9_999_000_000},  # ~10 GB, under margin
        )
        p = _manifest()
        with self.assertRaises(models_mod.InsufficientDisk):
            models_mod.add(fx, "org/huge", manifest_path=p, hf_home=HF)
        # nothing pulled, manifest untouched.
        self.assertNotIn(("hf_download", "org/huge", HF), [c for c in fx.calls])
        self.assertNotIn("org/huge", [m.repo for m in load_models(p)])

    def test_add_pulls_when_it_fits_with_margin(self):
        fx = FakeEffects(
            free_disk_bytes=500_000_000_000,
            hf_cache=[],
            hf_repo_size={"org/small": 1_000_000_000},
        )
        p = _manifest()
        models_mod.add(fx, "org/small", manifest_path=p, hf_home=HF)
        self.assertIn("org/small", [m.repo for m in load_models(p)])
        self.assertIn(("hf_download", "org/small", HF), fx.calls)


# --- add / rm -----------------------------------------------------------------


class AddRmTest(unittest.TestCase):
    def test_add_appends_manifest_then_pulls(self):
        fx = FakeEffects(
            free_disk_bytes=10 ** 15,
            hf_cache=[],
            hf_repo_size={"org/x": 2_000_000_000},
        )
        p = _manifest()
        models_mod.add(fx, "org/x", manifest_path=p, hf_home=HF)
        self.assertIn("org/x", [m.repo for m in load_models(p)])
        self.assertIn(("hf_download", "org/x", HF), fx.calls)

    def test_rm_drops_manifest_then_deletes_blobs(self):
        fx = FakeEffects(
            free_disk_bytes=10 ** 15,
            hf_cache=[(CHAT_REPO, 12_000_000_000)],
        )
        p = _manifest()
        models_mod.rm(fx, CHAT_REPO, manifest_path=p, hf_home=HF)
        self.assertNotIn(CHAT_REPO, [m.repo for m in load_models(p)])
        self.assertIn(("hf_delete", CHAT_REPO, HF), fx.calls)


# --- default ------------------------------------------------------------------


class DefaultTest(unittest.TestCase):
    def test_reads_current_default_when_no_repo(self):
        p = _manifest()
        fx = FakeEffects()
        self.assertEqual(models_mod.default(fx, manifest_path=p, hf_home=HF), DEFAULT_REPO)

    def test_sets_default_to_a_manifest_entry(self):
        p = _manifest()
        fx = FakeEffects()
        models_mod.default(fx, repo=CHAT_REPO, manifest_path=p, hf_home=HF)
        from llmctl.manifest import default_model
        self.assertEqual(default_model(load_models(p)).repo, CHAT_REPO)

    def test_rejects_default_not_in_manifest(self):
        p = _manifest()
        fx = FakeEffects()
        with self.assertRaises(KeyError):
            models_mod.default(fx, repo="org/not-listed", manifest_path=p, hf_home=HF)


# --- ls -----------------------------------------------------------------------


class LsTest(unittest.TestCase):
    def test_lists_manifest_union_cache_with_state_size_and_free_disk(self):
        fx = FakeEffects(
            free_disk_bytes=300_000_000_000,
            # default repo installed; chat repo listed-but-missing; orphan in cache.
            hf_cache=[(DEFAULT_REPO, 17_000_000_000), ("org/orphan", 5_000_000_000)],
        )
        p = _manifest()
        listing = models_mod.ls(fx, manifest_path=p, hf_home=HF)
        by_repo = {row.repo: row for row in listing.rows}
        self.assertEqual(set(by_repo), {DEFAULT_REPO, CHAT_REPO, "org/orphan"})
        self.assertTrue(by_repo[DEFAULT_REPO].installed)
        self.assertTrue(by_repo[DEFAULT_REPO].listed)
        self.assertFalse(by_repo[CHAT_REPO].installed)  # listed, not on disk
        self.assertTrue(by_repo[CHAT_REPO].listed)
        self.assertFalse(by_repo["org/orphan"].listed)  # orphan
        self.assertTrue(by_repo["org/orphan"].installed)
        self.assertEqual(by_repo[DEFAULT_REPO].size_bytes, 17_000_000_000)
        self.assertAlmostEqual(listing.free_disk_gb, 300.0, places=0)


if __name__ == "__main__":
    unittest.main()
