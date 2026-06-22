"""FakeEffects — the recording fake every slice's tests drive the CLI through.

Scripts subprocess results by argv and HTTP statuses by URL, and records every
call so a test can assert which probes/effects a command issued.
"""

from __future__ import annotations

from llmctl.effects import RunResult


class FakeEffects:
    def __init__(
        self,
        *,
        run_results=None,
        http_statuses=None,
        installed_models=None,
        free_disk_bytes=0,
        read_texts=None,
        hf_cache=None,
        hf_repo_size=None,
        dir_sizes=None,
        paths=None,
    ):
        # run_results: {tuple(argv): RunResult or [RunResult, ...]}. A list is
        # consumed one per call (last value repeats) so a probe can change
        # across calls — e.g. a daemon "loaded" then "unloaded" after bootout.
        # Unscripted argv -> rc 1 (the "command failed / not loaded" default).
        self._run_results = dict(run_results or {})
        self._http_statuses = dict(http_statuses or {})
        self._installed_models = list(installed_models or [])
        self._free_disk_bytes = free_disk_bytes
        self._read_texts = dict(read_texts or {})
        # hf_cache: [(repo, size_bytes), ...] — the HF hub cache contents.
        # hf_repo_size: {repo: size_bytes} — pre-pull size lookup for the guard.
        self._hf_cache = list(hf_cache or [])
        self._hf_repo_size = dict(hf_repo_size or {})
        self._dir_sizes = dict(dir_sizes or {})
        self._paths = set(str(p) for p in (paths or set()))
        self.calls = []

    def run(self, argv, *, input=None):
        self.calls.append(("run", tuple(argv)))
        val = self._run_results.get(tuple(argv), RunResult(1, "", ""))
        if isinstance(val, list):
            return val.pop(0) if len(val) > 1 else (val[0] if val else RunResult(1, "", ""))
        return val

    def read_text(self, path):
        self.calls.append(("read_text", str(path)))
        return self._read_texts.get(str(path))

    def http_status(self, url, *, token=None, timeout=2.0):
        self.calls.append(("http", url))
        return self._http_statuses.get(url)

    def installed_models(self, hf_home):
        self.calls.append(("installed_models", hf_home))
        return list(self._installed_models)

    def free_disk_bytes(self, path="/"):
        self.calls.append(("free_disk", path))
        return self._free_disk_bytes

    def hf_download(self, repo, hf_home):
        self.calls.append(("hf_download", repo, hf_home))
        # Record the pull as a now-present cache entry.
        if repo not in [r for r, _ in self._hf_cache]:
            self._hf_cache.append((repo, self._hf_repo_size.get(repo, 0)))

    def hf_cache(self, hf_home):
        self.calls.append(("hf_cache", hf_home))
        return list(self._hf_cache)

    def hf_delete(self, repo, hf_home):
        self.calls.append(("hf_delete", repo, hf_home))
        self._hf_cache = [(r, s) for r, s in self._hf_cache if r != repo]

    def hf_repo_size(self, repo):
        self.calls.append(("hf_repo_size", repo))
        return self._hf_repo_size.get(repo, 0)

    def path_exists(self, path):
        self.calls.append(("path_exists", str(path)))
        return str(path) in self._paths

    def dir_size_bytes(self, path):
        self.calls.append(("dir_size", str(path)))
        return self._dir_sizes.get(str(path), 0)
