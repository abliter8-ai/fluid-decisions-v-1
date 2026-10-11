"""Offline tests for the sandbox executor's confinement and safe quoting.

No container is started: ``_ssh`` is replaced with a capture so the exact script
sent to ``bash -s`` is asserted. The point is that untrusted paths are quoted
once and confined to the repository, and that the docker/ssh argv never carries
untrusted data.
"""
from __future__ import annotations

import subprocess

import pytest

from harness.sandbox import Sandbox, _confine


def _sandbox(capture):
    box = Sandbox(ssh_host="node", image="img", container="c")
    box._ssh = capture  # type: ignore[assignment]
    return box


def _ok(**_kw):
    return subprocess.CompletedProcess(args=[], returncode=0, stdout="output", stderr="")


def test_confine_rejects_absolute_and_parent_paths():
    assert _confine("crates/time/src/lib.rs") == "crates/time/src/lib.rs"
    for bad in ("/etc/passwd", "~/secret", "../outside", "a/../../b", ""):
        with pytest.raises(Exception):
            _confine(bad)


def test_read_file_quotes_the_path_and_never_puts_it_in_argv():
    seen = {}

    def capture(remote_args, *, stdin=None, timeout=0):
        seen["args"] = remote_args
        seen["stdin"] = stdin
        return _ok()
    box = _sandbox(capture)
    # A path with a space forces shell quoting, proving the quoting is applied.
    box.execute("read_file", {"path": "crates/time/a file.rs"})
    assert "a file.rs" not in " ".join(seen["args"])  # never in the ssh/docker argv
    assert seen["stdin"] == "cat -- 'crates/time/a file.rs'"
    assert seen["args"][:4] == ["docker", "exec", "-i", "--workdir"]


def test_run_shell_passes_the_command_through_stdin_verbatim():
    seen = {}

    def capture(remote_args, *, stdin=None, timeout=0):
        seen["stdin"] = stdin
        return _ok()
    box = _sandbox(capture)
    box.execute("run_shell", {"command": "cargo build -p filmcraft-time"})
    assert seen["stdin"] == "cargo build -p filmcraft-time"


def test_write_file_is_confined_and_uses_a_fixed_heredoc():
    seen = {}

    def capture(remote_args, *, stdin=None, timeout=0):
        seen["stdin"] = stdin
        return _ok()
    box = _sandbox(capture)
    out = box.execute("write_file", {"path": "docs/choice-a.md", "content": "new line\n"})
    assert out["ok"] is True
    assert "docs/choice-a.md" in seen["stdin"] and "FD_EOF_7b6c1344" in seen["stdin"]
    # An escaping path is refused before anything is sent.
    blocked = box.execute("write_file", {"path": "../../etc/x", "content": "y"})
    assert blocked["ok"] is False and "refused" in blocked["result"]


def test_run_tests_builds_a_crate_scoped_cargo_invocation():
    seen = {}

    def capture(remote_args, *, stdin=None, timeout=0):
        seen["stdin"] = stdin
        return _ok()
    box = _sandbox(capture)
    box.execute("run_tests", {"crate": "time"})
    assert seen["stdin"] == "cargo test -p filmcraft-time 2>&1"
    bad = box.execute("run_tests", {"crate": "time; rm -rf /"})
    assert bad["ok"] is False


def test_start_uses_no_network_and_limits():
    seen = {}

    def capture(remote_args, *, stdin=None, timeout=0):
        seen["args"] = remote_args
        return subprocess.CompletedProcess(args=[], returncode=0, stdout="deadbeef", stderr="")
    box = _sandbox(capture)
    info = box.start()
    assert info["container_id"] == "deadbeef"
    joined = " ".join(seen["args"])
    assert "--network none" in joined
    assert "--memory" in joined and "--cpus" in joined and "--pids-limit" in joined
    assert "--gpus" not in joined


def test_a_tool_error_becomes_a_result_not_a_crash():
    # A non-UTF-8 file read once raised UnicodeDecodeError and killed the whole
    # run; execute() must now catch any failure and return a tool result.
    def capture(remote_args, *, stdin=None, timeout=0):
        raise UnicodeDecodeError("utf-8", b"\xa3", 0, 1, "invalid start byte")
    box = _sandbox(capture)
    out = box.execute("read_file", {"path": "crates/foo/bin.dat"})
    assert out["ok"] is False and out["error"] == "UnicodeDecodeError"
    assert "failed in the sandbox" in out["result"]


def test_result_is_capped():
    def capture(remote_args, *, stdin=None, timeout=0):
        return subprocess.CompletedProcess(args=[], returncode=0, stdout="x" * 500_000, stderr="")
    box = _sandbox(capture)
    out = box.execute("read_file", {"path": "big.txt"})
    assert len(out["result"]) <= 200_000 and out["truncated"] is True
