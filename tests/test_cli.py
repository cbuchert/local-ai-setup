import contextlib
import io
import unittest

from llmctl.__main__ import build_parser, cmd_teardown, main


def run(argv):
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
        code = main(argv)
    return code, out.getvalue()


class CliDispatchTest(unittest.TestCase):
    def test_status_runs_end_to_end(self):
        # status is read-only; safe to drive against real Effects.
        code, out = run(["status"])
        self.assertEqual(code, 0)
        self.assertIn("runner daemon", out)

    def test_lifecycle_verbs_and_flags_parse(self):
        # parse-only (don't execute mutating verbs): the flags are wired
        p = build_parser()
        self.assertTrue(p.parse_args(["install", "--migrate", "--unattended"]).migrate)
        t = p.parse_args(["teardown", "--all"])
        self.assertTrue(t.all)
        self.assertIs(t.func, cmd_teardown)
        self.assertTrue(p.parse_args(["reset", "--migrate"]).migrate)

    def test_model_ls_dispatches_to_real_handler(self):
        # wired (not a stub); lists the Manifest ∪ cache against real Effects
        code, out = run(["model", "ls"])
        self.assertEqual(code, 0)

    def test_model_add_requires_a_repo_arg(self):
        # verifies the model subparser is wired with the required positional
        code, _ = run(["model", "add"])
        self.assertNotEqual(code, 0)

    def test_unknown_command_errors(self):
        code, _ = run(["frobnicate"])
        self.assertNotEqual(code, 0)


if __name__ == "__main__":
    unittest.main()
