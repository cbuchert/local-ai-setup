"""`llmctl power apply` (#6) — server power behavior + the GPU memory cap.

Ports scripts/40-power-settings.sh: pmset/systemsetup settings that make a
headless Mac behave like an always-on server, plus the `iogpu` wired-memory
cap. The cap is runtime-agnostic and survives reboot ONLY via its own
RunAtLoad LaunchDaemon (`com.local.iogpu-wired-limit`) running
`sysctl iogpu.wired_limit_mb=<IOGPU_WIRED_LIMIT_MB>` at boot — so we both
install that daemon and apply the cap immediately. All side effects flow
through the injected Effects seam (see llmctl/effects.py), so the whole thing
is driven by FakeEffects in tests.

Wire-up (do not edit __main__.py here): in `llmctl/__main__.py`, point the
`power apply` subcommand's handler at:

    power.apply(
        effects,
        env=env_mod.load_env(ENV_PATH),
        template_path=ROOT / "config" / "com.local.iogpu-wired-limit.plist.tmpl",
    )
"""

from __future__ import annotations

from llmctl import sys as sys_mod

LABEL = "com.local.iogpu-wired-limit"

# Durable server power config. pmset is idempotent — re-applying current values
# is a no-op, so `apply` is safe to re-run.
POWER_COMMANDS = [
    ["sudo", "pmset", "-a", "sleep", "0", "disablesleep", "1"],
    ["sudo", "pmset", "-a", "autorestart", "1"],  # restart after power loss
    ["sudo", "pmset", "-a", "womp", "1"],  # wake on network (WoL)
    ["sudo", "systemsetup", "-setrestartfreeze", "on"],  # restart on kernel hang
]


def render_iogpu_plist(effects, cap_mb, template_path) -> str:
    """Render the iogpu LaunchDaemon plist with the cap substituted in."""
    text = effects.read_text(str(template_path))
    return sys_mod.render_template(text, {"IOGPU_WIRED_LIMIT_MB": cap_mb})


def apply(effects, *, env, template_path) -> None:
    cap = env["IOGPU_WIRED_LIMIT_MB"]

    for cmd in POWER_COMMANDS:
        effects.run(cmd)

    plist = render_iogpu_plist(effects, cap, template_path)
    sys_mod.install_daemon(effects, label=LABEL, plist_text=plist)

    # Apply the cap now too — the daemon only re-applies it at boot.
    effects.run(["sudo", "sysctl", f"iogpu.wired_limit_mb={cap}"])
