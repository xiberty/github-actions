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
FIRST = "xiberty-gh-runner-state"
SECOND = "xiberty-gh-runner-2-state"
BOTH = (FIRST, SECOND)
PROJECTS = ("xiberty-github-runner", "xiberty-github-runner-2")

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
volume = os.environ.get("RUNNER_STATE_VOLUME")
project = args[args.index("--project-name") + 1] if "--project-name" in args else None
with trace_file.open("a") as stream:
    stream.write(json.dumps({
        "args": args,
        "has_token": has_token,
        "project": project,
        "runner_name": os.environ.get("RUNNER_NAME"),
    }) + "\n")

def save():
    state_file.write_text(json.dumps(state))

def add(key, value):
    state[key] = sorted(set(state.get(key, [])) | {value})
    save()

def remove(key, value):
    state[key] = sorted(set(state.get(key, [])) - {value})
    save()

if args[0] == "info":
    if state.get("daemon_down"):
        sys.exit(1)
    print(state.get("os", "linux"))
elif args[:2] == ["image", "inspect"]:
    sys.exit(0 if state.get("image_cached", True) else 1)
elif args[:2] == ["volume", "ls"]:
    for name in state.get("volumes", []):
        print(name)
elif args[0] == "run":
    if state.get("probe_error"):
        sys.exit(125)
    mount = next(value for value in args if value.startswith("type=volume,"))
    probed = mount.split("src=")[1].split(",")[0]
    sys.exit(0 if probed in state.get("registered", []) else 1)
elif args[0] == "compose":
    commands = {"version", "config", "pull", "run", "up", "ps", "restart", "stop", "logs", "exec"}
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
        add("volumes", volume)
        add("registered", volume)
    elif command == "up":
        if has_token or volume not in state.get("registered", []):
            sys.exit(22)
        add("running", project)
    elif command == "stop":
        remove("running", project)
    elif command == "ps":
        if "--quiet" in args:
            if project in state.get("running", []):
                print("container-id")
        else:
            print("runner running (healthy)")
    elif command == "exec":
        if "pgrep" in args:
            sys.exit(0 if project in state.get("busy", []) else 1)
        print("pruned")
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
        shutil.copytree(PACKAGE / "docker", self.package / "docker")
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

    def set_registered(self, *volumes, **values):
        self.set_state(volumes=sorted(volumes), registered=sorted(volumes), **values)

    def saved_state(self):
        return json.loads(self.state_file.read_text())

    def run_script(self, command, token=None, count=None):
        environment = {
            "PATH": f"{self.stub_bin}{os.pathsep}{os.defpath}",
            "LANG": "C",
            "FAKE_STATE_FILE": str(self.state_file),
            "FAKE_TRACE_FILE": str(self.trace_file),
        }
        if token is not None:
            environment["RUNNER_TOKEN"] = token
        if count is not None:
            environment["RUNNER_COUNT"] = count
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
        self.assertEqual(
            [item["runner_name"] for item in registration],
            ["mac-mini-docker", "mac-mini-docker-2"],
        )
        self.assertTrue(all(item["has_token"] for item in registration))
        self.assertIn("START_DOCKER_SERVICE=false", registration[0]["args"])
        self.assertEqual(registration[0]["args"][-1], "/bin/true")
        self.assertTrue(all(not item["has_token"] for item in self.compose_calls("up")))
        self.assertEqual(self.saved_state()["running"], sorted(PROJECTS))
        self.assert_token_is_not_printed(result)

    def test_init_reuses_the_saved_registration_without_a_token(self):
        self.set_registered(*BOTH)

        result = self.run_script("init")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.compose_calls("run"), [])
        self.assertEqual(len(self.compose_calls("up")), 2)

    def test_registration_probe_is_read_only_and_unprivileged(self):
        self.set_registered(*BOTH)

        result = self.run_script("up")

        self.assertEqual(result.returncode, 0, result.stderr)
        probe = next(item["args"] for item in self.trace() if item["args"][0] == "run")
        self.assertIn("--read-only", probe)
        self.assertIn("no-new-privileges", probe)
        self.assertNotIn("--privileged", probe)
        self.assertIn("type=volume,src=xiberty-gh-runner-state,dst=/state,readonly", probe)

    def test_repeated_up_does_not_register_again(self):
        self.set_registered(*BOTH)

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
        self.set_registered(*BOTH, probe_error=True)

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
        self.set_registered(*BOTH)

        result = self.run_script("restart")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(self.compose_calls("restart")), 2)
        self.assertIn("--wait", self.compose_calls("up")[0]["args"])
        self.assertFalse(any(item["has_token"] for item in self.trace()))

    def test_stop_preserves_registration(self):
        self.set_registered(*BOTH, running=list(PROJECTS))

        result = self.run_script("stop")

        self.assertEqual(result.returncode, 0, result.stderr)
        saved = self.saved_state()
        self.assertEqual(saved["registered"], sorted(BOTH))
        self.assertEqual(saved["running"], [])

    def test_first_runner_keeps_the_original_names(self):
        self.set_registered(*BOTH)

        result = self.run_script("up")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            [(item["project"], item["runner_name"]) for item in self.compose_calls("up")],
            [
                ("xiberty-github-runner", "mac-mini-docker"),
                ("xiberty-github-runner-2", "mac-mini-docker-2"),
            ],
        )

    def test_init_only_registers_the_missing_runners(self):
        self.set_registered(FIRST)

        result = self.run_script("init", TEST_TOKEN, count="3")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            [item["runner_name"] for item in self.compose_calls("run")],
            ["mac-mini-docker-2", "mac-mini-docker-3"],
        )
        self.assertEqual(len(self.compose_calls("up")), 3)
        self.assert_token_is_not_printed(result)

    def test_up_does_not_start_any_runner_if_one_is_unregistered(self):
        self.set_registered(FIRST)

        result = self.run_script("up")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("mac-mini-docker-2", result.stderr)
        self.assertEqual(self.compose_calls("up"), [])

    def test_count_of_one_manages_only_the_original_runner(self):
        self.set_registered(FIRST)

        result = self.run_script("up", count="1")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.saved_state()["running"], ["xiberty-github-runner"])

    def test_invalid_count_is_rejected_without_contacting_docker(self):
        result = self.run_script("status", count="0")

        self.assertEqual(result.returncode, 2)
        self.assertIn("RUNNER_COUNT", result.stderr)
        self.assertEqual(self.trace(), [])

    def test_prune_skips_busy_and_stopped_runners(self):
        self.set_registered(*BOTH, running=[PROJECTS[1]], busy=[PROJECTS[1]])

        result = self.run_script("prune", count="2")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("mac-mini-docker: detenido", result.stdout)
        self.assertIn("mac-mini-docker-2: ejecutando un job", result.stdout)
        cleanups = [item for item in self.compose_calls("exec") if "pgrep" not in item["args"]]
        self.assertEqual(cleanups, [])

    def test_prune_cleans_idle_runners_without_touching_named_volumes(self):
        self.set_registered(*BOTH, running=list(PROJECTS))

        result = self.run_script("prune")

        self.assertEqual(result.returncode, 0, result.stderr)
        cleanups = [item for item in self.compose_calls("exec") if "pgrep" not in item["args"]]
        self.assertEqual([item["project"] for item in cleanups], list(PROJECTS))
        script = cleanups[0]["args"][-1]
        self.assertIn("docker image prune --all --force", script)
        self.assertNotIn("volume prune --all", script)
        self.assertEqual(self.saved_state()["registered"], sorted(BOTH))

    def test_whitespace_in_the_token_is_rejected(self):
        result = self.run_script("init", f"{TEST_TOKEN}\nextra")

        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.compose_calls("run"), [])
        self.assert_token_is_not_printed(result)


if __name__ == "__main__":
    unittest.main()
