"""The single seam.

Every side effect in llmctl flows through an Effects object: subprocess
(launchctl, brew, sudo, scp), and HTTP probes. Command code receives one as a
dependency and never calls subprocess/urllib directly — that is what keeps the
seam at one, so the whole CLI can be driven in-process against a FakeEffects in
tests (see tests/fakes.py).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path


@dataclass
class RunResult:
    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0


class Effects:
    """Real effects — talks to the actual system."""

    def run(self, argv, *, input: str | None = None) -> RunResult:
        p = subprocess.run(argv, capture_output=True, text=True, input=input)
        return RunResult(p.returncode, p.stdout, p.stderr)

    def http_status(self, url, *, token: str | None = None, timeout: float = 2.0):
        """Return the HTTP status code, or None if the host is unreachable."""
        req = urllib.request.Request(url)
        if token:
            req.add_header("Authorization", f"Bearer {token}")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.status
        except urllib.error.HTTPError as e:
            return e.code
        except (urllib.error.URLError, OSError):
            return None

    def installed_models(self, hf_home: str) -> list[str]:
        """Repo ids present in the HF hub cache under hf_home.

        Decodes `models--{org}--{name}` cache dirs back to `org/name`. Good
        enough for a status count; slice 4 uses huggingface_hub.scan_cache_dir
        for authoritative listing/sizing.
        """
        hub = Path(hf_home or os.path.expanduser("~/.cache/huggingface")) / "hub"
        if not hub.is_dir():
            return []
        repos = []
        for entry in hub.iterdir():
            if entry.is_dir() and entry.name.startswith("models--"):
                repos.append(entry.name[len("models--"):].replace("--", "/", 1))
        return sorted(repos)

    def free_disk_bytes(self, path: str = "/") -> int:
        return shutil.disk_usage(path).free

    def read_text(self, path) -> str | None:
        """File contents, or None if absent/unreadable (e.g. permission)."""
        try:
            return Path(path).read_text()
        except (FileNotFoundError, PermissionError, IsADirectoryError, OSError):
            return None

    def path_exists(self, path) -> bool:
        return Path(path).exists()

    def dir_size_bytes(self, path) -> int:
        """Bytes consumed by a directory tree (0 if absent). Used to report
        the GGUF blobs the Ollama cleanup reclaims."""
        root = Path(path)
        if not root.exists():
            return 0
        total = 0
        for p in root.rglob("*"):
            try:
                if p.is_file() and not p.is_symlink():
                    total += p.stat().st_size
            except OSError:
                pass
        return total

    # --- Hugging Face hub cache (model management, #4) --------------------

    def hf_download(self, repo: str, hf_home: str) -> None:
        """Pull `repo` into the hub cache under hf_home (snapshot_download)."""
        from huggingface_hub import snapshot_download

        snapshot_download(repo_id=repo, cache_dir=_hub(hf_home))

    def hf_cache(self, hf_home: str) -> list[tuple[str, int]]:
        """List `(repo, size_bytes)` for every repo present in the cache.

        Empty on a fresh box where the cache dir doesn't exist yet, rather
        than letting scan_cache_dir raise CacheNotFound.
        """
        from huggingface_hub import scan_cache_dir

        hub = _hub(hf_home)
        if not Path(hub).is_dir():
            return []
        info = scan_cache_dir(cache_dir=hub)
        return [(r.repo_id, r.size_on_disk) for r in info.repos]

    def hf_delete(self, repo: str, hf_home: str) -> None:
        """Delete all of `repo`'s revisions from the cache, freeing its blobs.

        No-op when the cache dir doesn't exist yet (a `model rm` before any
        pull), rather than letting scan_cache_dir raise CacheNotFound.
        """
        from huggingface_hub import scan_cache_dir

        hub = _hub(hf_home)
        if not Path(hub).is_dir():
            return
        info = scan_cache_dir(cache_dir=hub)
        hashes = [
            rev.commit_hash
            for r in info.repos
            if r.repo_id == repo
            for rev in r.revisions
        ]
        if hashes:
            info.delete_revisions(*hashes).execute()

    def hf_repo_size(self, repo: str) -> int:
        """Total download size of `repo` in bytes, before pulling it."""
        from huggingface_hub import HfApi

        info = HfApi().repo_info(repo, files_metadata=True)
        return sum(f.size or 0 for f in info.siblings)


def _hub(hf_home: str) -> str:
    return str(Path(hf_home or os.path.expanduser("~/.cache/huggingface")) / "hub")
