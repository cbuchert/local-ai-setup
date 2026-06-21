"""`llmctl model ...` — model lifecycle over the Manifest and the HF cache (#4).

Pure orchestration over the Effects seam (HF pull/scan/delete, free-disk probe)
and the comment-preserving manifest writers. Every Hugging Face operation goes
through Effects so the whole flow is driven against FakeEffects in tests.

Sync is the auto-reconcile invariant (CONTEXT.md): the on-disk cache is kept
EXACTLY equal to the Manifest — pull listed-and-missing repos, delete unlisted
Orphans. `add`/`rm` are the single-repo edits that trigger it implicitly.

Wiring (the integrator does this in __main__.py; do not edit there from here):
    model ls       -> ls(effects, manifest_path=..., hf_home=...) ; print render_ls(result)
    model add REPO -> add(effects, repo, manifest_path=..., hf_home=...)
    model rm REPO  -> rm(effects, repo, manifest_path=..., hf_home=...)
    model sync     -> sync(effects, manifest_path=..., hf_home=...)
    model default [REPO]
                   -> default(effects, repo=REPO_or_None, manifest_path=..., hf_home=...)
hf_home comes from env (HF_HOME); manifest_path is MANIFEST_PATH. add/sync may
raise InsufficientDisk — the integrator surfaces it as a non-zero exit.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from llmctl.manifest import (
    add_model,
    default_model,
    load_models,
    remove_model,
    set_default,
)

# Free-disk headroom a pull must leave behind, beyond the model's own size.
DISK_MARGIN_BYTES = 10_000_000_000  # 10 GB


class InsufficientDisk(Exception):
    """A pull would not fit free disk with margin — refused before downloading."""


@dataclass
class ReconcilePlan:
    to_pull: list[str] = field(default_factory=list)
    to_delete: list[str] = field(default_factory=list)


@dataclass
class ModelRow:
    repo: str
    listed: bool          # in the Manifest (desired state)
    installed: bool        # present in the on-disk cache
    size_bytes: int = 0    # on-disk size, 0 if not installed


@dataclass
class Listing:
    rows: list[ModelRow] = field(default_factory=list)
    free_disk_gb: float = 0.0


def _diff(manifest_path, cache: dict[str, int]) -> ReconcilePlan:
    listed = [m.repo for m in load_models(manifest_path)]
    return ReconcilePlan(
        to_pull=[r for r in listed if r not in cache],
        to_delete=sorted(r for r in cache if r not in listed),
    )


def _guard_disk(effects, size_bytes: int) -> None:
    free = effects.free_disk_bytes("/")
    if free < size_bytes + DISK_MARGIN_BYTES:
        raise InsufficientDisk(
            f"need {(size_bytes + DISK_MARGIN_BYTES) / 1e9:.0f} GB "
            f"(model + {DISK_MARGIN_BYTES / 1e9:.0f} GB margin), "
            f"only {free / 1e9:.0f} GB free"
        )


def ls(effects, *, manifest_path, hf_home: str) -> Listing:
    """List the Manifest ∪ cache with install state, size, and free disk."""
    cache = dict(effects.hf_cache(hf_home))
    listed = {m.repo for m in load_models(manifest_path)}
    rows = [
        ModelRow(
            repo=repo,
            listed=repo in listed,
            installed=repo in cache,
            size_bytes=cache.get(repo, 0),
        )
        for repo in sorted(listed | set(cache))
    ]
    return Listing(rows=rows, free_disk_gb=effects.free_disk_bytes("/") / 1e9)


def render_ls(listing: Listing) -> str:
    """Render a Listing as a plain-text table for `model ls`."""
    def state(row: ModelRow) -> str:
        if row.installed and row.listed:
            return "installed"
        if row.listed:
            return "missing"     # listed, not yet pulled
        return "orphan"          # in cache, unlisted
    lines = []
    for row in listing.rows:
        size = f"{row.size_bytes / 1e9:.1f} GB" if row.installed else "-"
        lines.append(f"{state(row):<10} {size:>9}  {row.repo}")
    lines.append(f"free disk: {listing.free_disk_gb:.0f} GB")
    return "\n".join(lines)


def add(effects, repo: str, *, manifest_path, hf_home: str) -> None:
    """Append `repo` to the Manifest and pull it (disk-guarded)."""
    _guard_disk(effects, effects.hf_repo_size(repo))
    add_model(manifest_path, repo)
    effects.hf_download(repo, hf_home)


def rm(effects, repo: str, *, manifest_path, hf_home: str) -> None:
    """Drop `repo` from the Manifest and delete its blobs from the cache."""
    remove_model(manifest_path, repo)
    effects.hf_delete(repo, hf_home)


def sync(effects, *, manifest_path, hf_home: str) -> ReconcilePlan:
    """Reconcile the cache to EXACTLY the Manifest: pull missing, delete Orphans."""
    cache = dict(effects.hf_cache(hf_home))
    plan = _diff(manifest_path, cache)
    for repo in plan.to_pull:
        _guard_disk(effects, effects.hf_repo_size(repo))
        effects.hf_download(repo, hf_home)
    for repo in plan.to_delete:
        effects.hf_delete(repo, hf_home)
    return plan


def default(effects, *, repo: str | None = None, manifest_path, hf_home: str):
    """Show the default repo (repo=None), or set it — validating it is listed.

    Setting raises KeyError when `repo` is not a Manifest entry.
    """
    models = load_models(manifest_path)
    if repo is None:
        d = default_model(models)
        return d.repo if d else None
    match = next((m for m in models if m.repo == repo), None)
    if match is None:
        raise KeyError(repo)
    set_default(manifest_path, match.name)
    return repo
