from __future__ import annotations

import importlib.util
import io
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest.mock import patch

MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "run.py"
SPEC = importlib.util.spec_from_file_location("twitter_news_accounts_runner", MODULE_PATH)
assert SPEC and SPEC.loader
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)


class AccountsRunnerTests(unittest.TestCase):
    def test_runtime_healthy_handles_missing_python(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertFalse(runner.runtime_healthy(Path(tmp) / "missing-python"))

    def test_failed_staged_build_preserves_existing_runtime(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "runtime"
            root.mkdir()
            sentinel = root / "keep-me"
            sentinel.write_text("working runtime", encoding="utf-8")
            with patch.object(runner.venv.EnvBuilder, "create", side_effect=OSError("disk full")):
                with self.assertRaisesRegex(RuntimeError, "could not build"):
                    runner.build_runtime(root, "digest")
            self.assertEqual("working runtime", sentinel.read_text(encoding="utf-8"))

    def test_main_reports_bootstrap_failure_without_traceback(self):
        stderr = io.StringIO()
        with patch.object(runner, "ensure_runtime", side_effect=RuntimeError("bootstrap failed")), patch.object(
            runner.sys, "argv", ["run.py", "doctor"]
        ), redirect_stderr(stderr):
            self.assertEqual(2, runner.main())
        self.assertEqual("error: bootstrap failed", stderr.getvalue().strip())

    def test_healthy_matching_runtime_is_reused(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(runner, "runtime_root", return_value=Path(tmp)), patch.object(
            runner, "dependency_digest", return_value="digest"
        ), patch.object(runner, "runtime_healthy", return_value=True), patch.object(runner, "build_runtime") as build:
            (Path(tmp) / ".requirements.sha256").write_text("digest\n", encoding="utf-8")
            self.assertEqual(runner.venv_python(Path(tmp)), runner.ensure_runtime())
            build.assert_not_called()


if __name__ == "__main__":
    unittest.main()
