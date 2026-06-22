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

**Tool-calling works, via the shim.** `mlx_lm.server` itself returns tool calls
as raw `<function=…><parameter=…>` text with `finish_reason: stop`, not OpenAI
structured `tool_calls` — so native-tool agents (OpenCode) couldn't use them. A
small reverse-proxy shim (Caddy → shim → mlx_lm.server, `com.mlx.toolshim`)
parses that text into proper `tool_calls`, so agents get executable calls. Tools
*execute* on the client; the model only requests them. One caveat: tool-bearing
turns don't stream token-by-token (the shim buffers a tool turn to parse it);
plain chat streams normally and passes straight through.

**Cap context on the client, not the server.** `mlx_lm.server` has no
context-length knob, so a request's context is bounded only by the model's
native window and available KV memory. Every shipped model clears ≥100k, but a
runaway agentic session that fills 100k+ of fp16 KV pressures memory. For
interactive work, cap the context window in the client (16–32k is plenty for
coding) and treat the full window as the exception. The server won't do it for
you.
