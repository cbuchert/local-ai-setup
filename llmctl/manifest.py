"""Parse and edit models.toml — the Manifest (desired state of the model set).

Shared by the config walkthrough (#2, the default-model menu) and model
management (#4, reconcile/sync/add/rm/default). Each block is one Model: repo
id, role, an optional `default` marker, and its Profile (the mlx_lm.server
launch settings).

Uses tomlkit so programmatic edits preserve the file's comments and layout —
the Manifest stays human-readable across `model add`/`rm`/`default`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import tomlkit


@dataclass
class Model:
    name: str
    repo: str
    role: str = ""
    default: bool = False
    profile: dict = field(default_factory=dict)


def _doc(path):
    return tomlkit.parse(Path(path).read_text())


def _profile(block) -> dict:
    p = {}
    if "max_tokens" in block:
        p["max_tokens"] = int(block["max_tokens"])
    if "temp" in block:
        p["temp"] = float(block["temp"])
    if "prompt_cache_bytes" in block:
        p["prompt_cache_bytes"] = int(block["prompt_cache_bytes"])
    return p


def load_models(path) -> list[Model]:
    doc = _doc(path)
    return [
        Model(
            name=str(b["name"]),
            repo=str(b["repo"]),
            role=str(b.get("role", "")),
            default=bool(b.get("default", False)),
            profile=_profile(b),
        )
        for b in doc.get("models", [])
    ]


def default_model(models: list[Model]) -> Model | None:
    return next((m for m in models if m.default), None)


def set_default(path, name: str) -> None:
    """Mark `name` as the sole default, preserving comments/layout."""
    doc = _doc(path)
    blocks = doc.get("models", [])
    names = [str(b["name"]) for b in blocks]
    if name not in names:
        raise KeyError(name)
    for b in blocks:
        if "default" in b:
            del b["default"]
        if str(b["name"]) == name:
            b["default"] = True
    Path(path).write_text(tomlkit.dumps(doc))


def add_model(path, repo: str, *, name: str | None = None, role: str = "") -> None:
    """Append a `[[models]]` block for `repo`, preserving comments/layout.

    No-op if the repo is already listed. `name` defaults to the repo's last
    path segment. Profile fields are omitted — the model is untuned until an
    operator edits its block (see ADR 0003).
    """
    doc = _doc(path)
    blocks = doc.get("models", [])
    if any(str(b["repo"]) == repo for b in blocks):
        return
    table = tomlkit.table()
    table["name"] = name or repo.rsplit("/", 1)[-1]
    table["repo"] = repo
    if role:
        table["role"] = role
    doc.setdefault("models", tomlkit.aot()).append(table)
    Path(path).write_text(tomlkit.dumps(doc))


def remove_model(path, repo: str) -> None:
    """Drop the `[[models]]` block whose repo matches, preserving the rest.

    No-op if the repo is absent.
    """
    doc = _doc(path)
    blocks = doc.get("models")
    if blocks is None:
        return
    for i, b in enumerate(blocks):
        if str(b["repo"]) == repo:
            del blocks[i]
            Path(path).write_text(tomlkit.dumps(doc))
            return
