# Connecting clients

The server speaks the **OpenAI-compatible API** over HTTPS with a bearer token.
Any tool that lets you point at a custom OpenAI base URL works — Cline, Aider,
Continue.dev, OpenCode, Qwen Code, the OpenAI SDKs, plain `curl`.

## The contract

Three values are all any client needs:

- **Base URL** — `https://<SERVER_HOSTNAME>/v1`
- **API key** — the `API_KEY` from the server's `.env`, sent as a bearer token
- **Model** — a repo id from `llmctl model ls` on the server

Per-client setup is just "where do those three fields go." That's documented
(and kept current) by each client, not here — reproducing their config formats
only invites drift:
[Cline](https://docs.cline.bot/provider-config/openai-compatible) ·
[Aider](https://aider.chat/docs/llms/openai-compat.html) ·
[Continue.dev](https://docs.continue.dev/customize/model-providers/overview) ·
[OpenCode](https://opencode.ai/docs/providers) (a custom `@ai-sdk/openai-compatible` provider).

Point an agent's coder model at the default (a 30B-class model — the floor for
reliable agentic use); point an autocomplete role at the small FIM model.

## Things that aren't obvious

**Trust the CA, once per client machine.** Caddy serves with its own internal
root CA. The server exports it to a stable system path; until a client trusts
that CA (e.g. macOS: add it to the System keychain as a trusted root), HTTPS
calls fail with a cert error. This is the only manual per-client step — it can't
be done from the server because it runs on a different machine.

*Node-based clients (OpenCode, and anything on raw Node) don't read the OS
keychain* — Node trusts its own bundled CA list. Copy the CA over and point Node
at it with `NODE_EXTRA_CA_CERTS=/path/to/ca.crt` (in the shell that launches the
client). The keychain step covers `curl`, browsers, and Electron apps that honor
system certs; `NODE_EXTRA_CA_CERTS` covers the rest. Symptom when it's missing:
`unable to verify the first certificate`.

**Tool-calling is not parsed server-side.** `mlx_lm.server` forwards a request's
`tools` into the chat template (so the model *emits* a call), but it does not
parse the model's output back into OpenAI structured `tool_calls` — the call
comes back as raw text in `content` with `finish_reason: stop` (e.g. Qwen emits
`<function=…><parameter=…>…`). So agents that require **native** structured tool
calls (OpenCode) won't get an executable call; agents that parse tool calls from
text *themselves* (Continue.dev converts tools to XML for exactly this) can still
drive agent mode. Plain chat/completions are unaffected. Verified against
mlx_lm 0.31.3 — revisit if a release adds a tool-call parser.

**Cap context on the client, not the server.** `mlx_lm.server` has no
context-length knob, so a request's context is bounded only by the model's
native window and available KV memory. Every shipped model clears ≥100k, but a
runaway agentic session that fills 100k+ of fp16 KV pressures memory. For
interactive work, cap the context window in the client (16–32k is plenty for
coding) and treat the full window as the exception. The server won't do it for
you.
