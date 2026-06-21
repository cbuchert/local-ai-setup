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
