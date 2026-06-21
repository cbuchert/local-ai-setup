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
