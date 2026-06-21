"""Shared system effects — the lib.sh port, rebuilt over the Effects seam.

Used by every LaunchDaemon-installing phase (runner #3, caddy #5, power #6):
`render_template` (plist/Caddyfile rendering) and `install_daemon` (idempotent
install + reload with the bootout-race poll). All privileged actions go through
the injected Effects object, so the whole thing is driven by FakeEffects in
tests.
"""

from __future__ import annotations

import re
import time

_VAR = re.compile(r"\$\{([A-Z_][A-Z0-9_]*)\}")
_ANY_PLACEHOLDER = re.compile(r"\$\{[A-Za-z_][A-Za-z0-9_]*\}")

_XML = [("&", "&amp;"), ("<", "&lt;"), (">", "&gt;"), ('"', "&quot;"), ("'", "&apos;")]

DAEMON_DIR = "/Library/LaunchDaemons"


def render_template(text: str, values: dict, *, escape_xml: bool = False) -> str:
    """Substitute ${VAR} placeholders from `values`.

    Raises KeyError for a referenced-but-missing var, ValueError if any
    placeholder survives (e.g. a mistyped lowercase ${typo} the substitution
    regex skips) — the lib.sh leftover guard.
    """

    def sub(m: re.Match) -> str:
        name = m.group(1)
        if name not in values:
            raise KeyError(name)
        v = str(values[name])
        if escape_xml:
            for a, b in _XML:
                v = v.replace(a, b)
        return v

    out = _VAR.sub(sub, text)
    leftover = _ANY_PLACEHOLDER.search(out)
    if leftover:
        raise ValueError(f"unsubstituted placeholder: {leftover.group(0)}")
    return out


def _install_root_file(effects, dst: str, content: str, mode: str = "644") -> bool:
    """Install content to a root-owned path; return True if it changed."""
    if effects.read_text(dst) == content:
        return False
    effects.run(["sudo", "tee", dst], input=content)
    effects.run(["sudo", "chown", "root:wheel", dst])
    effects.run(["sudo", "chmod", mode, dst])
    return True


def _loaded(effects, target: str) -> bool:
    return effects.run(["launchctl", "print", target]).ok


def install_daemon(
    effects,
    *,
    label: str,
    plist_text: str,
    poll: int = 20,
    sleep=time.sleep,
) -> bool:
    """Install a rendered plist and (re)bootstrap it idempotently.

    Returns True if the on-disk plist changed. If unchanged and already loaded,
    a no-op. If changed and loaded, boots out and polls until actually unloaded
    (bootout is async) before bootstrapping — and on a stuck unload removes the
    dst so the operator's retry re-enters the changed path.
    """
    dst = f"{DAEMON_DIR}/{label}.plist"
    target = f"system/{label}"

    changed = _install_root_file(effects, dst, plist_text)

    if not _loaded(effects, target):
        effects.run(["sudo", "launchctl", "bootstrap", "system", dst])
        return changed

    if not changed:
        return False

    effects.run(["sudo", "launchctl", "bootout", target])
    for _ in range(poll):
        if not _loaded(effects, target):
            break
        sleep(0.5)
    else:
        effects.run(["sudo", "rm", "-f", dst])
        raise RuntimeError(
            f"{label} did not unload within bootout poll; removed {dst} — "
            f"re-run to retry, or: sudo launchctl bootout {target}"
        )
    effects.run(["sudo", "launchctl", "bootstrap", "system", dst])
    return changed
