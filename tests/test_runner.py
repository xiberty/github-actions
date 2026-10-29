"""Test the CLI without contacting GitHub or starting real containers."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


PACKAGE = Path(__file__).resolve().parents[1]
TEST_TOKEN = "FAKE_REGISTRATION_TOKEN_FOR_LOCAL_TESTS"
IMAGE = "myoung34/github-runner:2.338.2-ubuntu-noble"

FAKE_DOCKER = r'''
import json
import os
from pathlib import Path
import sys

state_file = Path(os.environ["FAKE_STATE_FILE"])
trace_file = Path(os.environ["FAKE_TRACE_FILE"])
state = json.loads(state_file.read_text())
args = sys.argv[1:]
has_token = bool(os.environ.get("RUNNER_TOKEN"))
with trace_file.open("a") as stream:
    stream.write(json.dumps({"args": args, "has_token": has_token}) + "\n")

def save():
    state_file.write_text(json.dumps(state))

if args[0] == "info":
    if state.get("daemon_down"):
        sys.exit(1)
    print(state.get("os", "linux"))
elif args[:2] == ["image", "inspect"]:
    sys.exit(0 if state.get("image_cached", True) else 1)
elif args[:2] == ["volume", "ls"]:
    if state.get("volume_exists"):
        print("xiberty-gh-runner-state")
elif args[0] == "run":
    if state.get("probe_error"):
        sys.exit(125)
    sys.exit(0 if state.get("registered") else 1)
elif args[0] == "compose":
    commands = {"version", "config", "pull", "run", "up", "ps", "restart", "stop", "logs"}
    command = next(value for value in args[1:] if value in commands)
    if command == "version":
        print("Docker Compose version v2.40.0")
    elif command == "config" and "--images" in args:
        print("myoung34/github-runner:2.338.2-ubuntu-noble")
    elif command == "pull":
        state["image_cached"] = True
        save()
    elif command == "run":
        if not has_token:
            sys.exit(20)
        if state.get("registration_error"):
            sys.exit(21)
        state.update(volume_exists=True, registered=True)
        save()
    elif command == "up":
        if has_token or not state.get("registered"):
            sys.exit(22)
        state["running"] = True
        save()
    elif command == "stop":
        state["running"] = False
        save()
    elif command == "ps":
        print("runner running (healthy)")
else:
    sys.exit(90)
'''


class RunnerScriptTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="runner cli tests ")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.package = self.root / "package with spaces"
        self.package.mkdir()
        shutil.copy2(PACKAGE / "runner.sh", self.package)
        shutil.copy2(PACKAGE / "compose.yaml", self.package)
        self.stub_bin = self.root / "bin"
        self.stub_bin.mkdir()
        docker = self.stub_bin / "docker"
        docker.write_text(f"#!{sys.executable}\n{FAKE_DOCKER}")
        docker.chmod(0o700)
        self.state_file = self.root / "state.json"
        self.trace_file = self.root / "trace.jsonl"
        self.set_state()

    def set_state(self, **values):
        self.state_file.write_text(json.dumps(values))

    def run_script(self, command, token=None):
        environment = {
            "PATH": f"{self.stub_bin}{os.pathsep}{os.defpath}",
            "LANG": "C",
            "FAKE_STATE_FILE": str(self.state_file),
            "FAKE_TRACE_FILE": str(self.trace_file),
        }
        if token is not None:
            environment["RUNNER_TOKEN"] = token
        return subprocess.run(
            ["/bin/bash", str(self.package / "runner.sh"), command],
            cwd=self.root,
            env=environment,
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )

    def trace(self):
        if not self.trace_file.exists():
            return []
        return [json.loads(line) for line in self.trace_file.read_text().splitlines()]

    def compose_calls(self, command):
        return [
            item for item in self.trace()
            if item["args"][0] == "compose" and command in item["args"]
        ]

    def assert_token_is_not_printed(self, result):
        self.assertNotIn(TEST_TOKEN, result.stdout + result.stderr)

    def test_check_does_not_need_the_daemon_or_pass_on_the_token(self):
        self.set_state(daemon_down=True)

        result = self.run_script("check", TEST_TOKEN)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.compose_calls("config"))
        self.assertFalse(any(item["args"][0] == "info" for item in self.trace()))
        self.assertFalse(any(item["has_token"] for item in self.trace()))
        self.assert_token_is_not_printed(result)

    def test_help_does_not_contact_docker(self):
        result = self.run_script("help")

        self.assertEqual(result.returncode, 0)
        self.assertEqual(self.trace(), [])

    def test_unknown_command_fails_without_side_effects(self):
        result = self.run_script("unknown", TEST_TOKEN)

        self.assertEqual(result.returncode, 2)
        self.assertEqual(self.trace(), [])
        self.assert_token_is_not_printed(result)

    def test_up_requires_registration(self):
        result = self.run_script("up")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("./runner.sh init", result.stderr)
        self.assertEqual(self.compose_calls("up"), [])

    def test_init_bootstraps_once_and_starts_without_a_token(self):
        self.set_state(image_cached=False)

        result = self.run_script("init", TEST_TOKEN)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(self.compose_calls("pull")), 1)
        registration = self.compose_calls("run")
        self.assertEqual(len(registration), 1)
        self.assertTrue(registration[0]["has_token"])
        self.assertIn("START_DOCKER_SERVICE=false", registration[0]["args"])
        self.assertEqual(registration[0]["args"][-1], "/bin/true")
        self.assertTrue(all(not item["has_token"] for item in self.compose_calls("up")))
        self.assertTrue(json.loads(self.state_file.read_text())["running"])
        self.assert_token_is_not_printed(result)

    def test_init_reuses_the_saved_registration_without_a_token(self):
        self.set_state(volume_exists=True, registered=True)

        result = self.run_script("init")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.compose_calls("run"), [])
        self.assertEqual(len(self.compose_calls("up")), 1)

    def test_registration_probe_is_read_only_and_unprivileged(self):
        self.set_state(volume_exists=True, registered=True)

        result = self.run_script("up")

        self.assertEqual(result.returncode, 0, result.stderr)
        probe = next(item["args"] for item in self.trace() if item["args"][0] == "run")
        self.assertIn("--read-only", probe)
        self.assertIn("no-new-privileges", probe)
        self.assertNotIn("--privileged", probe)
        self.assertIn("type=volume,src=xiberty-gh-runner-state,dst=/state,readonly", probe)

    def test_repeated_up_does_not_register_again(self):
        self.set_state(volume_exists=True, registered=True)

        first = self.run_script("up")
        second = self.run_script("up")

        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(self.compose_calls("run"), [])
        self.assertFalse(any(item["has_token"] for item in self.trace()))

    def test_registration_failure_does_not_start_the_permanent_runner(self):
        self.set_state(registration_error=True)

        result = self.run_script("init", TEST_TOKEN)

        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.compose_calls("up"), [])
        self.assert_token_is_not_printed(result)

    def test_probe_failure_is_not_treated_as_a_missing_registration(self):
        self.set_state(volume_exists=True, registered=True, probe_error=True)

        result = self.run_script("init", TEST_TOKEN)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("No se pudo comprobar", result.stderr)
        self.assertEqual(self.compose_calls("run"), [])
        self.assert_token_is_not_printed(result)

    def test_daemon_failure_does_not_attempt_registration(self):
        self.set_state(daemon_down=True)

        result = self.run_script("init", TEST_TOKEN)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Docker no está disponible", result.stderr)
        self.assertEqual(self.compose_calls("run"), [])

    def test_restart_reuses_credentials_and_waits_for_health(self):
        self.set_state(volume_exists=True, registered=True)

        result = self.run_script("restart")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(self.compose_calls("restart")), 1)
        self.assertIn("--wait", self.compose_calls("up")[0]["args"])
        self.assertFalse(any(item["has_token"] for item in self.trace()))

    def test_stop_preserves_registration(self):
        self.set_state(volume_exists=True, registered=True, running=True)

        result = self.run_script("stop")

        self.assertEqual(result.returncode, 0, result.stderr)
        saved = json.loads(self.state_file.read_text())
        self.assertTrue(saved["registered"])
        self.assertFalse(saved["running"])

    def test_whitespace_in_the_token_is_rejected(self):
        result = self.run_script("init", f"{TEST_TOKEN}\nextra")

        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.compose_calls("run"), [])
        self.assert_token_is_not_printed(result)


if __name__ == "__main__":
    unittest.main()
