"""llmctl — manage a local MLX LLM inference server.

argparse dispatch. Every command receives the Effects seam — no command
touches subprocess/hf directly. The remaining stubs (install/update/reset/
teardown) name their tracking issue until the lifecycle slice (#8) lands.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from llmctl import caddy as caddy_mod
from llmctl import cleanup as cleanup_mod
from llmctl import env as env_mod
from llmctl import models as models_mod
from llmctl import power as power_mod
from llmctl import runner as runner_mod
from llmctl import setup as setup_mod
from llmctl import status as status_mod
from llmctl.effects import Effects

ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = ROOT / ".env"
EXAMPLE_PATH = ROOT / ".env.example"
MANIFEST_PATH = ROOT / "models.toml"
IOGPU_TMPL = ROOT / "config" / "com.local.iogpu-wired-limit.plist.tmpl"

# Lifecycle verbs that compose the phases — land in #8.
STUBS = {"install": 8, "update": 8, "reset": 8, "teardown": 8}


def _env() -> dict:
    return env_mod.load_env(ENV_PATH)


def cmd_status(args, effects) -> int:
    env = _env()
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


def cmd_runner(args, effects) -> int:
    try:
        if args.runner_command == "install":
            runner_mod.install(effects, env=_env(), manifest_path=MANIFEST_PATH, repo_root=ROOT)
        elif args.runner_command == "restart":
            runner_mod.restart(effects)
        elif args.runner_command == "logs":
            runner_mod.logs(effects, env=_env())
    except TimeoutError as e:
        print(e)
        return 1
    return 0


def cmd_model(args, effects) -> int:
    kw = dict(manifest_path=MANIFEST_PATH, hf_home=_env().get("HF_HOME", ""))
    try:
        c = args.model_command
        if c == "ls":
            print(models_mod.render_ls(models_mod.ls(effects, **kw)))
        elif c == "add":
            models_mod.add(effects, args.repo, **kw)
        elif c == "rm":
            models_mod.rm(effects, args.repo, **kw)
        elif c == "sync":
            models_mod.sync(effects, **kw)
        elif c == "default":
            models_mod.default(effects, repo=args.repo, **kw)
    except models_mod.InsufficientDisk as e:
        print(f"refusing pull: {e}")
        return 1
    except KeyError as e:
        print(f"not in manifest: {e}")
        return 1
    return 0


def cmd_caddy(args, effects) -> int:
    caddy_mod.install(effects, env=_env(), repo_root=ROOT)
    return 0


def cmd_power(args, effects) -> int:
    power_mod.apply(effects, env=_env(), template_path=IOGPU_TMPL)
    return 0


def cmd_cleanup(args, effects) -> int:
    cleanup_mod.run(effects, env_path=ENV_PATH)
    return 0


def cmd_restart(args, effects) -> int:
    runner_mod.restart(effects)
    return 0


def cmd_logs(args, effects) -> int:
    runner_mod.logs(effects, env=_env())
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
    sub.add_parser("cleanup", help="migrate off an old Ollama setup").set_defaults(func=cmd_cleanup)
    sub.add_parser("restart", help="restart the runner").set_defaults(func=cmd_restart)
    sub.add_parser("logs", help="tail the runner log").set_defaults(func=cmd_logs)

    for name in ("install", "update", "reset", "teardown"):
        sub.add_parser(name, help=f"(#{STUBS[name]})").set_defaults(func=_stub(name))

    mp = sub.add_parser("model", help="manage the model set")
    msub = mp.add_subparsers(dest="model_command", required=True)
    msub.add_parser("ls")
    msub.add_parser("add").add_argument("repo")
    msub.add_parser("rm").add_argument("repo")
    msub.add_parser("sync")
    msub.add_parser("default").add_argument("repo", nargs="?")
    mp.set_defaults(func=cmd_model)

    rp = sub.add_parser("runner", help="mlx_lm.server lifecycle")
    rsub = rp.add_subparsers(dest="runner_command", required=True)
    for s in ("install", "restart", "logs"):
        rsub.add_parser(s)
    rp.set_defaults(func=cmd_runner)

    cp = sub.add_parser("caddy", help="HTTPS proxy + CA")
    cp.add_subparsers(dest="caddy_command", required=True).add_parser("install")
    cp.set_defaults(func=cmd_caddy)

    pp = sub.add_parser("power", help="server power + GPU memory cap")
    pp.add_subparsers(dest="power_command", required=True).add_parser("apply")
    pp.set_defaults(func=cmd_power)

    return parser


def main(argv=None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as e:
        return int(e.code or 0)
    return args.func(args, Effects())


if __name__ == "__main__":
    sys.exit(main())
