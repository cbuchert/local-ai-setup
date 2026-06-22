import json
import unittest

from llmctl.toolshim import parse_tool_calls, transform_response

# The exact content casper's Qwen3-Coder emitted for a get_weather tool call.
CASPER = "Paris\n<function=get_weather>\n<parameter=city>\nParis\n</parameter>\n</function>\n</tool_call>"


class ParseToolCallsTest(unittest.TestCase):
    def test_parses_the_real_casper_format(self):
        content, calls = parse_tool_calls(CASPER)
        self.assertEqual(len(calls), 1)
        fn = calls[0]["function"]
        self.assertEqual(fn["name"], "get_weather")
        self.assertEqual(json.loads(fn["arguments"]), {"city": "Paris"})
        self.assertEqual(calls[0]["type"], "function")
        self.assertTrue(calls[0]["id"])
        # the <function>/<tool_call> markup is stripped from leftover content
        self.assertNotIn("<function", content or "")
        self.assertNotIn("</tool_call>", content or "")

    def test_no_tool_call_leaves_content_untouched(self):
        content, calls = parse_tool_calls("Just a normal answer.")
        self.assertEqual(calls, [])
        self.assertEqual(content, "Just a normal answer.")

    def test_multiple_parameters(self):
        c = "<function=search><parameter=q>cats</parameter><parameter=limit>5</parameter></function>"
        _, calls = parse_tool_calls(c)
        self.assertEqual(json.loads(calls[0]["function"]["arguments"]), {"q": "cats", "limit": "5"})

    def test_multiple_calls(self):
        c = ("<function=a><parameter=x>1</parameter></function>"
             "<function=b><parameter=y>2</parameter></function>")
        _, calls = parse_tool_calls(c)
        self.assertEqual([c["function"]["name"] for c in calls], ["a", "b"])
        self.assertNotEqual(calls[0]["id"], calls[1]["id"])  # distinct ids


class TransformResponseTest(unittest.TestCase):
    def _resp(self, content, finish="stop"):
        return {"choices": [{"message": {"role": "assistant", "content": content},
                             "finish_reason": finish}]}

    def test_promotes_tool_call_to_structured_field(self):
        out = transform_response(self._resp(CASPER))
        choice = out["choices"][0]
        self.assertEqual(choice["finish_reason"], "tool_calls")
        self.assertEqual(choice["message"]["tool_calls"][0]["function"]["name"], "get_weather")

    def test_passes_through_plain_response_unchanged(self):
        resp = self._resp("hello there")
        out = transform_response(resp)
        self.assertNotIn("tool_calls", out["choices"][0]["message"])
        self.assertEqual(out["choices"][0]["finish_reason"], "stop")
        self.assertEqual(out["choices"][0]["message"]["content"], "hello there")


class HelpersTest(unittest.TestCase):
    def test_request_uses_tools(self):
        from llmctl.toolshim import request_uses_tools
        self.assertTrue(request_uses_tools({"tools": [{"type": "function"}]}))
        self.assertFalse(request_uses_tools({"messages": []}))
        self.assertFalse(request_uses_tools({"tools": []}))
        self.assertFalse(request_uses_tools(None))

    def test_to_stream_chunks_emits_tool_call_then_done(self):
        from llmctl.toolshim import to_stream_chunks
        resp = transform_response({
            "id": "x", "model": "m",
            "choices": [{"message": {"role": "assistant", "content": CASPER},
                         "finish_reason": "stop"}],
        })
        sse = to_stream_chunks(resp)
        self.assertIn("data: ", sse)
        self.assertIn("chat.completion.chunk", sse)
        self.assertIn("get_weather", sse)
        self.assertIn("tool_calls", sse)
        self.assertTrue(sse.rstrip().endswith("[DONE]"))


class RenderPlistTest(unittest.TestCase):
    def test_renders_bind_upstream_pythonpath_no_leftovers(self):
        from pathlib import Path

        from llmctl.toolshim import render_plist
        repo_root = Path(__file__).resolve().parent.parent
        env = {"SHIM_HOST": "127.0.0.1:8081", "MLX_HOST": "127.0.0.1:8080", "HOME": "/Users/op"}
        text = render_plist(env=env, repo_root=repo_root)
        self.assertIn("com.mlx.toolshim", text)
        self.assertIn("8081", text)              # shim bind port
        self.assertIn("127.0.0.1:8080", text)    # upstream
        self.assertIn(str(repo_root), text)      # PYTHONPATH + venv python
        self.assertNotIn("${", text)             # fully substituted


if __name__ == "__main__":
    unittest.main()
