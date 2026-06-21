"""The guided `llmctl setup` config walkthrough (#2).

Prompts (each pre-filled, Enter-through) for hostname, Default Model (a menu
from the Manifest), model store, and the auto-detected GPU cap, then writes a
valid `.env` and surfaces the generated API key. Pure orchestration: prompt and
output are injected, GPU detection goes through the Effects seam. System
provisioning (cleanup → install) is #8.
"""

from __future__ import annotations

from llmctl import env as env_mod
from llmctl import manifest as manifest_mod


def run_config_walkthrough(*, env_path, example_path, manifest_path, effects, prompt, out):
    current = env_mod.ensure_env(env_path, example_path)
    models = manifest_mod.load_models(manifest_path)

    hostname = prompt(
        f"Server hostname [{current.get('SERVER_HOSTNAME', 'studio.local')}]: "
    ).strip() or current.get("SERVER_HOSTNAME", "studio.local")

    cur_default = manifest_mod.default_model(models)
    default_idx = (models.index(cur_default) + 1) if cur_default else 1
    out("Default model:")
    for i, m in enumerate(models, 1):
        marker = "  (current)" if m is cur_default else ""
        out(f"  {i}) {m.name} — {m.role}{marker}")
    sel = prompt(f"Pick default [{default_idx}]: ").strip() or str(default_idx)
    chosen = models[int(sel) - 1]
    manifest_mod.set_default(manifest_path, chosen.name)

    hf = prompt(
        f"Model store HF_HOME (blank = ~/.cache/huggingface) [{current.get('HF_HOME', '')}]: "
    ).strip() or current.get("HF_HOME", "")

    cap_default = current.get("IOGPU_WIRED_LIMIT_MB") or str(
        env_mod.detect_gpu_cap_mb(effects)
    )
    cap = prompt(f"GPU memory cap MB [{cap_default}]: ").strip() or cap_default

    values = {
        "SERVER_HOSTNAME": hostname,
        "API_KEY": current.get("API_KEY") or env_mod.generate_api_key(),
        "MLX_HOST": current.get("MLX_HOST", "127.0.0.1:8080"),
        "HF_HOME": hf,
        "IOGPU_WIRED_LIMIT_MB": cap,
    }
    env_mod.write_env(env_path, values)

    out(f"\nWrote {env_path}")
    out(f"API key: {values['API_KEY']}")
    out(f"Default model: {chosen.name}")
    return values
