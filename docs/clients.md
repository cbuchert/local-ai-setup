# Connecting clients

The server speaks the **OpenAI-compatible API** over HTTPS with a bearer token.
Any tool that can point at a custom OpenAI base URL works — Cline, Aider,
Continue.dev, Qwen Code, plain `curl`, the OpenAI SDKs.

Three values are all you ever need:

| Setting | Value |
| --- | --- |
| **Base URL** | `https://<SERVER_HOSTNAME>/v1` (e.g. `https://studio.local/v1`) |
| **API key** | the `API_KEY` from the server's `.env` (sent as `Authorization: Bearer …`) |
| **Model** | a repo id from `llmctl model ls` on the server |

> The server holds **one model resident at a time**. Naming a different model in
> a request triggers a cold swap (a multi-second reload). Point your daily-driver
> client at the **default** model (the one marked `default = true`) to stay warm.

---

## 1. Trust the server's CA (once per client machine)

Caddy serves with its own internal root CA. Until a client trusts it, HTTPS
calls fail with a certificate error. The server writes the CA to a stable path;
copy it over and trust it once:

```bash
# Copy the CA from the server
scp <SERVER_HOSTNAME>:/usr/local/share/mac-studio-ca.crt /tmp/local-ai-ca.crt

# Trust it system-wide (macOS client)
sudo security add-trusted-cert -d -r trustRoot \
  -k /Library/Keychains/System.keychain \
  /tmp/local-ai-ca.crt
```

This is the only manual per-client step — it can't be done from the server
because it runs on a different machine. (Linux: drop the CA in
`/usr/local/share/ca-certificates/` and run `update-ca-certificates`.)

## 2. Smoke-test the connection

```bash
# List installed models
curl https://studio.local/v1/models \
  -H "Authorization: Bearer $API_KEY"

# One-shot chat completion
curl https://studio.local/v1/chat/completions \
  -H "Authorization: Bearer $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "<repo-id-from-llmctl-model-ls>",
    "messages": [{"role": "user", "content": "say hi in five words"}]
  }'
```

A clean CA trust + a valid token returns JSON. A cert error → revisit step 1; a
`401` → wrong/missing token.

---

## 3. Cline (VS Code)

Open the Cline settings (⚙️) and choose **OpenAI Compatible** as the API
provider, then fill the three fields:

- **Base URL:** `https://studio.local/v1`  *(include `/v1`)*
- **API Key:** your `API_KEY`
- **Model ID:** a repo id from `llmctl model ls` (use the agentic-coding default)

Under the model's advanced settings, set **Context Window** to the model's gated
ceiling (≥100k — see the note in §6) and a sane **Max Output Tokens**. Cline's
agent reliability needs a 30B-class coder model; don't point its agent at the
small autocomplete model.

## 4. Aider (terminal)

Aider talks to any OpenAI-compatible endpoint via two env vars and an
`openai/`-prefixed model name:

```bash
export OPENAI_API_BASE=https://studio.local/v1
export OPENAI_API_KEY=<API_KEY>

aider --model openai/<repo-id-from-llmctl-model-ls>
```

To make it permanent, put the same in `~/.aider.conf.yml`:

```yaml
openai-api-base: https://studio.local/v1
openai-api-key: <API_KEY>
model: openai/<repo-id-from-llmctl-model-ls>
```

## 5. Continue.dev (VS Code)

Add the server as an `openai`-provider model in `~/.continue/config.yaml`. One
entry per role — point `chat`/`edit` at the coder model and `autocomplete` at
the small FIM model:

```yaml
models:
  - name: Local Coder
    provider: openai
    apiBase: https://studio.local/v1
    apiKey: <API_KEY>
    model: <coder-repo-id-from-llmctl-model-ls>
    roles:
      - chat
      - edit
      - apply

  - name: Local Autocomplete
    provider: openai
    apiBase: https://studio.local/v1
    apiKey: <API_KEY>
    model: <fim-repo-id-from-llmctl-model-ls>
    roles:
      - autocomplete
```

---

## 6. Cap context on the client, not the server

The server **cannot** cap context — `mlx_lm.server` has no context-length flag,
so a request's context is bounded only by the model's native window and
available KV memory. Every shipped model clears **≥100k tokens** on this box, but
a runaway agentic session that fills 100k+ of fp16 KV can pressure memory. For
interactive work, cap the context window in the client (16–32k is plenty for
most coding) and let the full window be the exception, not the default. This is
a client-side setting (Cline's *Context Window*, Continue's
`autocompleteOptions.maxPromptTokens`, etc.) — the server won't do it for you.

Sources for the per-client config formats:
[Aider](https://aider.chat/docs/llms/openai-compat.html) ·
[Cline](https://docs.cline.bot/provider-config/openai-compatible) ·
[Continue](https://docs.continue.dev/customize/model-providers/overview)
