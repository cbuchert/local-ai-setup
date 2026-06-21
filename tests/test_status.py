import unittest

from llmctl.effects import RunResult
from llmctl.status import RUNNER_LABEL, gather_status, render
from tests.fakes import FakeEffects

HOST = "127.0.0.1:8080"
HF = "/tmp/hf"
URL = f"http://{HOST}/v1/models"


def _gather(fx):
    return gather_status(fx, mlx_host=HOST, api_key="tok", hf_home=HF)


class GatherStatusTest(unittest.TestCase):
    def test_runner_running_when_daemon_loaded_and_server_answers(self):
        fx = FakeEffects(
            run_results={("launchctl", "print", f"system/{RUNNER_LABEL}"): _ok()},
            http_statuses={URL: 200},
        )
        status = _gather(fx)
        self.assertTrue(status.runner_loaded)
        self.assertTrue(status.server_ok)

    def test_runner_down_when_daemon_not_loaded(self):
        fx = FakeEffects(http_statuses={URL: None})
        status = _gather(fx)
        self.assertFalse(status.runner_loaded)
        self.assertFalse(status.server_ok)
        self.assertIn(
            ("run", ("launchctl", "print", f"system/{RUNNER_LABEL}")), fx.calls
        )

    def test_reports_installed_models_and_free_disk(self):
        fx = FakeEffects(
            installed_models=["mlx-community/Qwen3-Coder-30B-A3B-Instruct-4bit"],
            free_disk_bytes=250_000_000_000,
        )
        status = _gather(fx)
        self.assertEqual(
            status.installed_models,
            ["mlx-community/Qwen3-Coder-30B-A3B-Instruct-4bit"],
        )
        self.assertAlmostEqual(status.free_disk_gb, 250.0, places=0)
        self.assertIn(("installed_models", HF), fx.calls)


class RenderTest(unittest.TestCase):
    def test_render_surfaces_runner_state_models_and_disk(self):
        fx = FakeEffects(
            run_results={("launchctl", "print", f"system/{RUNNER_LABEL}"): _ok()},
            http_statuses={URL: 200},
            installed_models=["a/b", "c/d"],
            free_disk_bytes=120_000_000_000,
        )
        text = render(_gather(fx))
        self.assertIn("running", text)   # server up
        self.assertIn("2", text)         # model count
        self.assertIn("120", text)       # free GB


def _ok():
    return RunResult(0, "", "")


if __name__ == "__main__":
    unittest.main()
