"""PATH wiring — put `llmctl` on PATH via a guarded, idempotent rc block.

Written to `.zshrc`, `.bashrc`, and `.bash_profile` (the bash-login-shell
gotcha: an SSH bash session reads `.bash_profile`, not `.bashrc`). Marker-
delimited so re-running is a no-op and `teardown --all` can remove it cleanly.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

MARKER_START = "# >>> llmctl >>>"
MARKER_END = "# <<< llmctl <<<"


def default_rc_files() -> list[Path]:
    home = Path(os.path.expanduser("~"))
    return [home / ".zshrc", home / ".bashrc", home / ".bash_profile"]


def _block(bin_dir) -> str:
    return f'{MARKER_START}\nexport PATH="{bin_dir}:$PATH"\n{MARKER_END}\n'


def wire_path(rc_files, bin_dir) -> None:
    for rc in rc_files:
        p = Path(rc)
        text = p.read_text() if p.exists() else ""
        if MARKER_START in text:
            continue
        sep = "" if text == "" or text.endswith("\n") else "\n"
        p.write_text(text + sep + _block(bin_dir))


def unwire_path(rc_files) -> None:
    pattern = re.compile(
        r"\n?" + re.escape(MARKER_START) + r".*?" + re.escape(MARKER_END) + r"\n?",
        re.DOTALL,
    )
    for rc in rc_files:
        p = Path(rc)
        if not p.exists():
            continue
        text = p.read_text()
        if MARKER_START not in text:
            continue
        p.write_text(pattern.sub("\n", text, count=1).lstrip("\n") or "")
