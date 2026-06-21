"""llmctl — manage a local MLX LLM inference server.

argparse dispatch. `status` is wired; the remaining verbs are stubs that name
their tracking issue until their slice lands. Every command receives the
Effects seam — no command touches subprocess/hf directly.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from llmctl import env as env_mod
from llmctl import setup as setup_mod
from llmctl import status as status_mod
from llmctl.effects import Effects

ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = ROOT / ".env"
EXAMPLE_PATH = ROOT / ".env.example"
MANIFEST_PATH = ROOT / "models.toml"

# Unbuilt verbs -> the issue that will implement them. Keeps the CLI honest
# while the slices land in parallel.
STUBS = {
    "install": 8,
    "cleanup": 7,
    "update": 8,
    "reset": 8,
    "teardown": 8,
    "logs": 3,
    "restart": 3,
    "runner": 3,
    "caddy": 5,
    "power": 6,
    "model": 4,
}


def cmd_status(args, effects) -> int:
    env = env_mod.load_env(ENV_PATH)
    report = status_mod.gather_status(
        effects,
        mlx_host=env.get("MLX_HOST", "127.0.0.1:8080"),
        api_key=env.get("API_KEY"),
        hf_home=env.get("HF_HOME", ""),
    )
    print(status_mod.render(report))
    return 0


def cmd_setup(args, effects) -> int:
    setup_mod.run_config_walkthrough(
        env_path=ENV_PATH,
        example_path=EXAMPLE_PATH,
        manifest_path=MANIFEST_PATH,
        effects=effects,
        prompt=input,
        out=print,
    )
    print("\nConfig written. Full provisioning (cleanup → install) lands in #8.")
    return 0


def _stub(name: str):
    def handler(args, effects) -> int:
        print(f"llmctl {name}: not yet implemented (#{STUBS[name]})")
        return 1

    return handler


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="llmctl", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("status", help="report server health").set_defaults(func=cmd_status)
    sub.add_parser("setup", help="guided config walkthrough").set_defaults(func=cmd_setup)

    # Flat stub verbs.
    for name in ("install", "cleanup", "update", "reset", "teardown",
                 "logs", "restart"):
        sub.add_parser(name, help=f"(#{STUBS[name]})").set_defaults(func=_stub(name))

    # Group verbs with subcommands (stubs share the group's handler for now).
    for group, subs in (
        ("model", ("ls", "add", "rm", "sync", "default")),
        ("runner", ("install", "restart", "logs")),
        ("caddy", ("install",)),
        ("power", ("apply",)),
    ):
        gp = sub.add_parser(group, help=f"(#{STUBS[group]})")
        gsub = gp.add_subparsers(dest=f"{group}_command", required=False)
        for s in subs:
            gsub.add_parser(s).set_defaults(func=_stub(group))
        gp.set_defaults(func=_stub(group))

    return parser


def main(argv=None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as e:
        # argparse exits 2 on bad input; surface it as our return code.
        return int(e.code or 0)
    return args.func(args, Effects())


if __name__ == "__main__":
    sys.exit(main())
