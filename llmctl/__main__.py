"""llmctl — manage a local MLX LLM inference server.

argparse dispatch. Every command receives the Effects seam — no command
touches subprocess/hf directly.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from llmctl import caddy as caddy_mod
from llmctl import cleanup as cleanup_mod
from llmctl import env as env_mod
from llmctl import lifecycle
from llmctl import models as models_mod
from llmctl import power as power_mod
from llmctl import runner as runner_mod
from llmctl import setup as setup_mod
from llmctl import shell
from llmctl import status as status_mod
from llmctl.effects import Effects

ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = ROOT / ".env"
EXAMPLE_PATH = ROOT / ".env.example"
MANIFEST_PATH = ROOT / "models.toml"
IOGPU_TMPL = ROOT / "config" / "com.local.iogpu-wired-limit.plist.tmpl"


def _env() -> dict:
    return env_mod.load_env(ENV_PATH)


def _install_kwargs() -> dict:
    env = _env()
    return dict(
        env=env,
        manifest_path=MANIFEST_PATH,
        repo_root=ROOT,
        hf_home=env.get("HF_HOME", ""),
        rc_files=shell.default_rc_files(),
        env_path=ENV_PATH,
    )


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
    if args.config_only:
        return 0
    print("\n==> provisioning")
    try:
        lifecycle.install(effects, **_install_kwargs())
    except lifecycle.MigrationNeeded as e:
        print(e)
        return 1
    return 0


def cmd_install(args, effects) -> int:
    try:
        lifecycle.install(
            effects, migrate=args.migrate, unattended=args.unattended, **_install_kwargs()
        )
    except lifecycle.MigrationNeeded as e:
        print(e)
        return 1
    return 0


def cmd_update(args, effects) -> int:
    env = _env()
    lifecycle.update(
        effects, env=env, manifest_path=MANIFEST_PATH, repo_root=ROOT,
        hf_home=env.get("HF_HOME", ""),
    )
    return 0


def cmd_reset(args, effects) -> int:
    try:
        lifecycle.reset(
            effects, migrate=args.migrate, unattended=args.unattended, **_install_kwargs()
        )
    except lifecycle.MigrationNeeded as e:
        print(e)
        return 1
    return 0


def cmd_teardown(args, effects) -> int:
    lifecycle.teardown(
        effects,
        repo_root=ROOT,
        hf_home=_env().get("HF_HOME", ""),
        rc_files=shell.default_rc_files(),
        models_too=args.models,
        ca=args.ca,
        all=args.all,
    )
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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="llmctl", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    sp = sub.add_parser("setup", help="guided config, then provision")
    sp.add_argument("--config-only", action="store_true", help="write config and stop")
    sp.set_defaults(func=cmd_setup)

    ip = sub.add_parser("install", help="provision (non-interactive)")
    ip.add_argument("--migrate", action="store_true", help="opt into Ollama cleanup")
    ip.add_argument("--unattended", action="store_true")
    ip.set_defaults(func=cmd_install)

    sub.add_parser("update", help="upgrade packages + reload").set_defaults(func=cmd_update)

    rp = sub.add_parser("reset", help="identity-preserving teardown + install")
    rp.add_argument("--migrate", action="store_true")
    rp.add_argument("--unattended", action="store_true")
    rp.set_defaults(func=cmd_reset)

    tp = sub.add_parser("teardown", help="remove the runner stack")
    tp.add_argument("--models", action="store_true", help="also wipe the model cache")
    tp.add_argument("--ca", action="store_true", help="also drop Caddy's CA")
    tp.add_argument("--all", action="store_true", help="back to bare (caddy, iogpu, PATH)")
    tp.set_defaults(func=cmd_teardown)

    sub.add_parser("status", help="report server health").set_defaults(func=cmd_status)
    sub.add_parser("cleanup", help="migrate off an old Ollama setup").set_defaults(func=cmd_cleanup)
    sub.add_parser("restart", help="restart the runner").set_defaults(func=cmd_restart)
    sub.add_parser("logs", help="tail the runner log").set_defaults(func=cmd_logs)

    mp = sub.add_parser("model", help="manage the model set")
    msub = mp.add_subparsers(dest="model_command", required=True)
    msub.add_parser("ls")
    msub.add_parser("add").add_argument("repo")
    msub.add_parser("rm").add_argument("repo")
    msub.add_parser("sync")
    msub.add_parser("default").add_argument("repo", nargs="?")
    mp.set_defaults(func=cmd_model)

    rnp = sub.add_parser("runner", help="mlx_lm.server lifecycle")
    rnsub = rnp.add_subparsers(dest="runner_command", required=True)
    for s in ("install", "restart", "logs"):
        rnsub.add_parser(s)
    rnp.set_defaults(func=cmd_runner)

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
