"""Tool-call shim — parse Qwen tool-call text into OpenAI `tool_calls`.

mlx_lm.server forwards a request's `tools` into the chat template (so the model
emits a call) but never parses the model's output back into structured
`tool_calls` — it comes back as raw text in `content` with `finish_reason:
stop`. Native-tool agents (OpenCode) need the structured form. This module
parses Qwen's `<function=…><parameter=…>` output into OpenAI `tool_calls`.

The proxy server (this module's __main__) sits between Caddy and mlx_lm.server:
non-tool requests pass straight through (streaming preserved); requests carrying
`tools` are forced non-stream upstream, parsed here, and returned.
"""

from __future__ import annotations

import argparse
import http.server
import json
import re
import urllib.error
import urllib.request
from pathlib import Path

from llmctl import sys as sys_mod

LABEL = "com.mlx.toolshim"
TEMPLATE_REL = "config/com.mlx.toolshim.plist.tmpl"

_FUNC = re.compile(r"<function=([^>\s]+)>(.*?)</function>", re.DOTALL)
_PARAM = re.compile(r"<parameter=([^>\s]+)>(.*?)</parameter>", re.DOTALL)
_STRAY = re.compile(r"</?tool_call>")


def parse_tool_calls(content):
    """(cleaned_content, tool_calls). Empty list + original content if none."""
    if not content or "<function=" not in content:
        return content, []
    calls = []
    for i, m in enumerate(_FUNC.finditer(content)):
        args = {p.group(1): p.group(2).strip() for p in _PARAM.finditer(m.group(2))}
        calls.append({
            "id": f"call_{i}",
            "type": "function",
            "function": {"name": m.group(1), "arguments": json.dumps(args)},
        })
    cleaned = _STRAY.sub("", _FUNC.sub("", content)).strip()
    return (cleaned or None), calls


def transform_response(resp: dict) -> dict:
    """Promote any Qwen tool-call text in choice 0 to structured tool_calls."""
    try:
        choice = resp["choices"][0]
        msg = choice["message"]
    except (KeyError, IndexError, TypeError):
        return resp
    cleaned, calls = parse_tool_calls(msg.get("content"))
    if calls:
        msg["tool_calls"] = calls
        msg["content"] = cleaned
        choice["finish_reason"] = "tool_calls"
    return resp


def request_uses_tools(body) -> bool:
    return bool(isinstance(body, dict) and body.get("tools"))


def to_stream_chunks(resp: dict) -> str:
    """Render a (transformed) non-stream completion as a one-chunk SSE stream.

    Tool-bearing turns don't stream token-by-token — the client gets the whole
    result in a single chat.completion.chunk, then [DONE].
    """
    choice = resp["choices"][0]
    msg = choice.get("message", {})
    delta = {"role": "assistant"}
    if msg.get("content"):
        delta["content"] = msg["content"]
    if msg.get("tool_calls"):
        delta["tool_calls"] = [{**tc, "index": i} for i, tc in enumerate(msg["tool_calls"])]
    chunk = {
        "id": resp.get("id", "chatcmpl-shim"),
        "object": "chat.completion.chunk",
        "model": resp.get("model", ""),
        "choices": [{"index": 0, "delta": delta, "finish_reason": choice.get("finish_reason")}],
    }
    return f"data: {json.dumps(chunk)}\n\ndata: [DONE]\n\n"


# --- Phase: render + install the shim daemon ------------------------------

def _split_host(hostport: str, default_port: str) -> tuple[str, str]:
    host, _, port = hostport.rpartition(":")
    return (host, port) if host else (hostport, default_port)


def render_plist(*, env: dict, repo_root, read_text=None) -> str:
    if read_text is None:
        read_text = lambda p: Path(p).read_text()
    host, port = _split_host(env.get("SHIM_HOST", "127.0.0.1:8081"), "8081")
    home = env.get("HOME") or str(Path.home())
    logs = f"{home}/Library/Logs"
    values = {
        "VENV_PYTHON": f"{repo_root}/.venv/bin/python",
        "REPO_ROOT": str(repo_root),
        "SHIM_BIND_HOST": host,
        "SHIM_BIND_PORT": port,
        "MLX_HOST": env.get("MLX_HOST", "127.0.0.1:8080"),
        "SHIM_LOG": f"{logs}/toolshim.log",
        "SHIM_ERR": f"{logs}/toolshim.err",
    }
    template = read_text(Path(repo_root) / TEMPLATE_REL)
    return sys_mod.render_template(template, values, escape_xml=True)


def install(effects, *, env: dict, repo_root) -> None:
    plist = render_plist(env=env, repo_root=repo_root, read_text=effects.read_text)
    sys_mod.install_daemon(effects, label=LABEL, plist_text=plist)


def restart(effects) -> None:
    effects.run(["sudo", "launchctl", "kickstart", "-k", f"system/{LABEL}"])


# --- Reverse proxy: Caddy -> shim -> mlx_lm.server ------------------------

_HOP = {"host", "content-length", "connection", "accept-encoding", "transfer-encoding"}


def _upstream_request(upstream, path, method, headers, body):
    req = urllib.request.Request(f"http://{upstream}{path}", data=body, method=method)
    for k, v in headers.items():
        if k.lower() not in _HOP:
            req.add_header(k, v)
    return req


class Handler(http.server.BaseHTTPRequestHandler):
    upstream = "127.0.0.1:8080"
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):  # don't spam the daemon log
        pass

    def do_GET(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(n) if n else b""

    def _send(self, status, ctype, payload: bytes):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _dispatch(self, method):
        body = self._body()
        if method == "POST" and self.path.endswith("/v1/chat/completions"):
            try:
                data = json.loads(body)
            except Exception:
                data = None
            if request_uses_tools(data):
                return self._tools(data)
        self._passthrough(method, body)

    def _tools(self, data):
        wants_stream = bool(data.get("stream"))
        payload = json.dumps({**data, "stream": False}).encode()
        req = _upstream_request(self.upstream, self.path, "POST",
                                {"Content-Type": "application/json"}, payload)
        try:
            with urllib.request.urlopen(req) as r:
                raw = r.read()
        except urllib.error.HTTPError as e:
            return self._send(e.code, "application/json", e.read())
        except Exception as e:
            return self._send(502, "application/json",
                              json.dumps({"error": {"message": str(e)}}).encode())
        try:
            resp = transform_response(json.loads(raw))
        except Exception:
            return self._send(200, "application/json", raw)
        if wants_stream:
            self._send(200, "text/event-stream", to_stream_chunks(resp).encode())
        else:
            self._send(200, "application/json", json.dumps(resp).encode())

    def _passthrough(self, method, body):
        req = _upstream_request(self.upstream, self.path, method, dict(self.headers),
                                body or None)
        try:
            r = urllib.request.urlopen(req)
        except urllib.error.HTTPError as e:
            r = e
        except Exception as e:
            return self._send(502, "application/json",
                              json.dumps({"error": {"message": str(e)}}).encode())
        # Stream the upstream response back; close-delimited so SSE flows through.
        self.send_response(getattr(r, "status", 200) or 200)
        self.send_header("Content-Type", r.headers.get("Content-Type", "application/octet-stream"))
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True
        while True:
            chunk = r.read(8192)
            if not chunk:
                break
            self.wfile.write(chunk)
            self.wfile.flush()


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="llmctl.toolshim")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8081)
    p.add_argument("--upstream", default="127.0.0.1:8080")
    args = p.parse_args(argv)
    Handler.upstream = args.upstream
    http.server.ThreadingHTTPServer((args.host, args.port), Handler).serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
