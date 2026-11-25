"""Policy and seatbelt checks for the Mac cursor-worker handoff."""

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from codeflow.mac_sandbox import incident_brief, sandbox_handoff_allowed, ssh_command_for_log
from codeflow.sandbox_policy import (
    PolicyError,
    agent_environment,
    assert_outside_origin,
    configure_denied_home,
    assert_push_branch,
    branch_for,
    parse_handoff_config,
    redact,
    scan_text,
    validate_original_command,
)

_STACK = Path(__file__).resolve().parents[1]
_ALLOW = frozenset({"CIS-NET-AUTOMATION", "DATABASE-INSERT", "CWR-INTERFACE"})


class CommandPolicyTests(unittest.TestCase):
    def test_exact_command_is_required(self):
        self.assertEqual(
            validate_original_command("orion-mac-handoff CIS-NET-AUTOMATION", _ALLOW),
            "CIS-NET-AUTOMATION",
        )

    def test_shell_syntax_is_rejected(self):
        for command in (
            "orion-mac-handoff CIS-NET-AUTOMATION; id",
            "orion-mac-handoff CIS-NET-AUTOMATION && cat /Users/YOU/.ssh/id_ed25519",
            "bash -lc id",
            "orion-mac-handoff ../DATABASE-INSERT",
            "orion-mac-handoff CIS-NET-AUTOMATION extra",
        ):
            with self.assertRaises(PolicyError):
                validate_original_command(command, _ALLOW)

    def test_unknown_repo_is_rejected(self):
        with self.assertRaises(PolicyError):
            validate_original_command("orion-mac-handoff OTHER", _ALLOW)

    def test_main_is_not_a_push_branch(self):
        with self.assertRaises(PolicyError):
            assert_push_branch("main")
        branch = branch_for("a" * 64)
        assert_push_branch(branch)
        self.assertTrue(branch.startswith("cursor/incident-"))

    def test_origin_home_is_refused(self):
        configure_denied_home(Path("/Users/YOU"))
        with self.assertRaises(PolicyError):
            assert_outside_origin(Path("/Users/YOU/Developer/REPOS/CIS-NET-AUTOMATION"))
        allowed = assert_outside_origin(Path("/Users/YOUR_USER/clones/CIS-NET-AUTOMATION"))
        self.assertEqual(allowed, Path("/Users/YOUR_USER/clones/CIS-NET-AUTOMATION").resolve())

    def test_secret_text_is_rejected_and_redacted(self):
        blob = "token = 'crsr_" + ("a" * 24) + "'"
        self.assertTrue(scan_text(blob))
        self.assertNotIn("crsr_", redact(blob))

    def test_agent_env_has_no_other_secrets(self):
        env = agent_environment("test-key", Path("/tmp/home"), Path("/tmp"), Path("/tmp/agent/cursor-agent"))
        self.assertEqual(
            set(env),
            {"PATH", "HOME", "TMPDIR", "LANG", "CURSOR_API_KEY"},
        )
        self.assertNotIn("GH_TOKEN", env)
        self.assertNotIn("/Users/YOU", env["PATH"])

    def test_example_config_stays_off(self):
        text = (_STACK / "config" / "mac_sandbox.example.yaml").read_text(encoding="utf-8")
        cfg = parse_handoff_config(text)
        self.assertFalse(cfg["enabled"])
        self.assertEqual(cfg["ssh_user"], "cursor-worker")
        self.assertEqual(
            cfg["allow_repos"],
            ["CIS-NET-AUTOMATION", "DATABASE-INSERT", "CWR-INTERFACE"],
        )
        self.assertIn("CIS-NET-AUTOMATION.git", cfg["git_urls"]["CIS-NET-AUTOMATION"])
        self.assertEqual(cfg["denied_home"], "/Users/YOU")

    def test_launch_script_does_not_pass_trust_or_force(self):
        script = (_STACK / "scripts" / "orion-mac-handoff").read_text(encoding="utf-8")
        self.assertNotIn('"--trust",', script)
        self.assertNotIn('"--force",', script)
        self.assertIn('"--trust" in cmd or "--force" in cmd', script)


class HandoffGateTests(unittest.TestCase):
    def _record(self, **overrides):
        base = {
            "fingerprint": "ab" * 32,
            "repos_name": "CIS-NET-AUTOMATION",
            "fix_target": "code",
            "message": "ModuleNotFoundError: territory_codes",
            "stack_trace": "",
        }
        base.update(overrides)
        return base

    def test_flag_off_does_not_handoff(self):
        self.assertFalse(sandbox_handoff_allowed(self._record(), "new"))

    def test_host_incident_stays_notify_only(self):
        with patch("codeflow.mac_sandbox.feature_enabled", return_value=True):
            with patch(
                "codeflow.mac_sandbox.load_mac_sandbox_config",
                return_value={"enabled": True, "allow_repos": ["CIS-NET-AUTOMATION"]},
            ):
                self.assertFalse(
                    sandbox_handoff_allowed(self._record(fix_target="host"), "new")
                )

    def test_allowlisted_code_incident_can_handoff(self):
        with patch("codeflow.mac_sandbox.feature_enabled", return_value=True):
            with patch(
                "codeflow.mac_sandbox.load_mac_sandbox_config",
                return_value={"enabled": True, "allow_repos": ["CIS-NET-AUTOMATION"]},
            ):
                self.assertTrue(sandbox_handoff_allowed(self._record(), "new"))
                self.assertFalse(sandbox_handoff_allowed(self._record(), "repeat"))

    def test_brief_refuses_a_literal_secret(self):
        record = self._record(message="password = 'supersecretvalue'")
        with self.assertRaises(PolicyError):
            incident_brief(record)

    def test_ssh_command_is_force_command_only(self):
        cmd = ssh_command_for_log(
            {
                "ssh_host": "mac.example",
                "ssh_user": "cursor-worker",
                "identity_file": "/Users/cursor-worker/.ssh/id_ed25519_handoff",
            },
            "CIS-NET-AUTOMATION",
        )
        self.assertIn("orion-mac-handoff CIS-NET-AUTOMATION", cmd)
        self.assertIn("cursor-worker@mac.example", cmd)
        self.assertNotIn("--trust", cmd)
        self.assertNotIn(".env", cmd)


class SeatbeltTests(unittest.TestCase):
    def test_profile_blocks_origin_and_secret_files(self):
        profile = _STACK / "config" / "cursor-worker.sb"
        base = Path(tempfile.mkdtemp()).resolve()
        operator = base / "operator"
        worker = base / "home"
        clone = worker / "clones" / "REPO"
        tmp = base / "tmp"
        agent = base / "agent"
        ssh = worker / ".ssh"
        cfg = worker / ".config"
        for path in (operator, clone, tmp, agent, ssh, cfg):
            path.mkdir(parents=True)
        (operator / "secret").write_text("home", encoding="utf-8")
        (ssh / "id").write_text("PRIVATEKEY", encoding="utf-8")
        (cfg / "cursor-api-key").write_text("hidden", encoding="utf-8")
        (agent / "note").write_text("bin", encoding="utf-8")
        code = f"""
import pathlib, socket
def probe(path):
    try:
        pathlib.Path(path).read_text()
        return "READ"
    except OSError:
        return "BLOCKED"
print("ssh", probe({str(ssh / "id")!r}))
print("apikey", probe({str(cfg / "cursor-api-key")!r}))
print("origin", probe({str(operator / "secret")!r}))
print("agent", probe({str(agent / "note")!r}))
pathlib.Path({str(clone / "out")!r}).write_text("ok")
s = socket.socket()
s.settimeout(2)
try:
    s.connect(("127.0.0.1", 8889))
    print("mysql", "OPEN")
except OSError as exc:
    print("mysql", exc.errno)
"""
        proc = subprocess.run(
            [
                "sandbox-exec",
                "-D",
                f"OPERATOR_HOME={operator}",
                "-D",
                f"WORKER_HOME={worker}",
                "-D",
                f"CLONE={clone}",
                "-D",
                f"TMP={tmp}",
                "-D",
                f"AGENT_ROOT={agent}",
                "-D",
                f"SECRET_SSH={ssh}",
                "-D",
                f"SECRET_CONFIG={cfg}",
                "-f",
                str(profile),
                "--",
                "/usr/bin/python3",
                "-B",
                "-c",
                code,
            ],
            cwd="/tmp",
            env={"HOME": "/tmp", "PATH": "/usr/bin:/bin"},
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        out = proc.stdout
        self.assertIn("ssh BLOCKED", out)
        self.assertIn("apikey BLOCKED", out)
        self.assertIn("origin BLOCKED", out)
        self.assertIn("agent READ", out)
        self.assertIn("mysql 1", out)
        self.assertEqual((clone / "out").read_text(encoding="utf-8"), "ok")


if __name__ == "__main__":
    unittest.main()
