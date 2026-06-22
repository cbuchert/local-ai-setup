# Python CLI with a thin shell bootstrap

Orchestration moved from numbered Bash scripts to a Python package (`llmctl`)
driven by a single `llmctl` CLI, with only `pre-bootstrap.sh` left in shell.

The work split is half "drive privileged system daemons" (brew, `launchctl`,
`sudo`, `pmset`) — bash's heartland — and half "CLI dispatch, model management,
interactive config, status" — where bash gets ugly fast. The feature set grew
decisively toward the second half (`model add/rm/ls`, guided setup, teardown,
JSON status), so the language follows the work. Crucially the MLX runtime is
already Python and already pulls in `huggingface_hub`, so Python adds **zero new
dependencies** and `huggingface_hub` as a library replaces curl+jq+`hf`-subprocess
for every model operation.

Privileged actions still shell out (`sudo launchctl ...`) via one `run()` helper
— that part is genuinely just "run this command." `pre-bootstrap.sh` stays shell
because it must run on a bare machine before Python or brew exist: it installs
CLT + Homebrew, creates the venv, then hands off to the CLI.

One deliberate exception to "zero new dependencies": **`tomlkit`**. `models.toml`
is both human-edited and programmatically rewritten (`model add`/`rm`/`default`),
and stdlib `tomllib` is read-only — a hand-rolled writer would strip the file's
explanatory comments. `tomlkit` round-trips TOML with comments and layout intact,
which is worth one small, pure-Python dep.

Trade-off accepted: porting the proven Bash idempotency logic (sudo keepalive,
daemon install, the bootout-race poll) carries re-test cost. Justified because
the Ollama→mlx change rewrites most of the scripts anyway — little survives
unchanged, so this is the moment to switch, not a gratuitous rewrite.
