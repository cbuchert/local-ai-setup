import contextlib
import io
import unittest

from llmctl.__main__ import main


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

    def test_unbuilt_verb_is_a_stub_pointing_at_its_issue(self):
        code, out = run(["update"])
        self.assertNotEqual(code, 0)
        self.assertIn("not yet implemented", out)
        self.assertIn("#", out)  # references the tracking issue

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
