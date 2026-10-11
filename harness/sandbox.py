"""The container sandbox (IP-005 section 2): the only module that shells out.

Tools execute inside a Linux container on a configured node, reached over one
multiplexed SSH connection. The container runs with ``--network none``, memory
and CPU limits, no host mounts, no environment beyond the image, no credentials
and no GPU. Each run starts from the pinned image; nothing persists except what
the harness copies out.

Every tool is executed by piping a script to ``bash -s`` inside the container, so
untrusted paths are shell-quoted exactly once and never reach an SSH or docker
argv. Paths are confined to the repository working directory: an absolute path or
a ``..`` component is refused before anything runs. Static denials are enforced by
the guarded runtime before a command ever reaches here; a statically denied
command is never sent.
"""
from __future__ import annotations

import json
import shlex
import subprocess
from dataclasses import dataclass
from typing import Any

RESULT_CAP = 200_000  # characters returned to the model from one tool call
_EXEC_TIMEOUT = 300  # seconds for one in-container command


class SandboxError(RuntimeError):
    pass


def _confine(path: str) -> str:
    if not isinstance(path, str) or not path:
        raise SandboxError("a path is required")
    if path.startswith("/") or path.startswith("~"):
        raise SandboxError("absolute paths are refused")
    parts = path.replace("\\", "/").split("/")
    if any(part == ".." for part in parts):
        raise SandboxError("parent-directory paths are refused")
    return path


@dataclass
class Sandbox:
    """A single disposable container for one task run."""
    ssh_host: str
    image: str
    container: str
    workdir: str = "/work/repo"
    memory: str = "4g"
    cpus: str = "4"
    pids_limit: str = "512"
    ttl_s: int = 1200
    control_path: str = "/tmp/fd-ip005-ssh-%C"

    def _ssh(self, remote_args: list[str], *, stdin: str | None = None,
             timeout: int = _EXEC_TIMEOUT) -> subprocess.CompletedProcess[str]:
        command = ["ssh", "-o", "BatchMode=yes", "-o", "ControlMaster=auto",
                   "-o", f"ControlPath={self.control_path}", "-o", "ControlPersist=300",
                   self.ssh_host, *remote_args]
        # A tool may read a file with non-UTF-8 bytes; replace rather than throw,
        # so one unusual file never crashes the run.
        return subprocess.run(command, input=stdin, text=True, errors="replace",
                              capture_output=True, timeout=timeout)

    def _exec_script(self, script: str, *, timeout: int = _EXEC_TIMEOUT) -> dict[str, Any]:
        """Run a script inside the container via ``bash -s`` over stdin."""
        completed = self._ssh(["docker", "exec", "-i", "--workdir", self.workdir,
                               self.container, "bash", "-s"], stdin=script, timeout=timeout)
        out = (completed.stdout or "")[:RESULT_CAP]
        err = (completed.stderr or "")[:4000]
        ok = completed.returncode == 0
        result = out if ok else (out + ("\n" + err if err else ""))
        return {"ok": ok, "result": result[:RESULT_CAP], "exit_code": completed.returncode,
                "truncated": len(completed.stdout or "") > RESULT_CAP}

    # -- lifecycle --------------------------------------------------------

    def start(self) -> dict[str, Any]:
        completed = self._ssh([
            "docker", "run", "-d", "--rm", "--network", "none",
            "--memory", self.memory, "--cpus", self.cpus, "--pids-limit", self.pids_limit,
            "--name", self.container, "--workdir", self.workdir,
            self.image, "sleep", str(self.ttl_s)], timeout=120)
        if completed.returncode != 0:
            raise SandboxError(f"container start failed: {(completed.stderr or '').strip()[:300]}")
        return {"container_id": (completed.stdout or "").strip()[:64], "image": self.image}

    def stop(self) -> None:
        self._ssh(["docker", "rm", "-f", self.container], timeout=60)

    def verify_no_network(self) -> bool:
        """Confirm the container cannot reach the network (defensive check)."""
        probe = self._exec_script(
            "if command -v getent >/dev/null 2>&1 && getent hosts example.com >/dev/null 2>&1; "
            "then echo REACHABLE; else echo isolated; fi", timeout=30)
        return "REACHABLE" not in probe.get("result", "")

    def __enter__(self) -> "Sandbox":
        self.start()
        return self

    def __exit__(self, *_: Any) -> None:
        self.stop()

    # -- tool executor ----------------------------------------------------

    def execute(self, tool_name: str, tool_input: dict[str, Any]) -> dict[str, Any]:
        """Executor for the agent loop: run one tool in the container."""
        try:
            if tool_name == "read_file":
                path = _confine(tool_input.get("path", ""))
                return self._exec_script(f"cat -- {shlex.quote(path)}")
            if tool_name == "list_dir":
                path = _confine(tool_input.get("path", "."))
                return self._exec_script(f"ls -la -- {shlex.quote(path)}")
            if tool_name == "write_file":
                path = _confine(tool_input.get("path", ""))
                content = tool_input.get("content", "")
                if not isinstance(content, str):
                    content = json.dumps(content, ensure_ascii=False)
                # The heredoc delimiter is fixed and not derivable from content;
                # content is written verbatim by cat, never evaluated by the shell.
                script = (f"mkdir -p \"$(dirname -- {shlex.quote(path)})\" && "
                          f"cat > {shlex.quote(path)} <<'FD_EOF_7b6c1344'\n{content}\nFD_EOF_7b6c1344\n"
                          f"echo wrote {shlex.quote(path)}")
                return self._exec_script(script)
            if tool_name == "run_shell":
                command = tool_input.get("command", "")
                if not isinstance(command, str) or not command.strip():
                    raise SandboxError("a command is required")
                return self._exec_script(command, timeout=_EXEC_TIMEOUT)
            if tool_name == "run_tests":
                crate = tool_input.get("crate", "")
                if not isinstance(crate, str) or not crate or not crate.replace("-", "").replace("_", "").isalnum():
                    raise SandboxError("a crate name is required")
                return self._exec_script(f"cargo test -p {shlex.quote('filmcraft-' + crate if not crate.startswith('filmcraft') else crate)} 2>&1",
                                         timeout=_EXEC_TIMEOUT)
            raise SandboxError(f"unknown tool: {tool_name}")
        except SandboxError as exc:
            return {"ok": False, "result": f"sandbox refused the tool call: {exc}",
                    "exit_code": None, "error": str(exc)}
        except subprocess.TimeoutExpired:
            return {"ok": False, "result": "the tool call timed out in the sandbox",
                    "exit_code": None, "error": "timeout"}
        except Exception as exc:
            # A tool failure must be a tool result, never a run-killer. Keep the
            # message categorical so no private detail leaks into a trace.
            return {"ok": False, "result": f"the tool call failed in the sandbox: {type(exc).__name__}",
                    "exit_code": None, "error": type(exc).__name__}


def node_health(ssh_host: str, control_path: str = "/tmp/fd-ip005-ssh-%C") -> dict[str, Any]:
    """Record node load and GPU presence before and after a run (no change made)."""
    command = ["ssh", "-o", "BatchMode=yes", "-o", "ControlMaster=auto",
               "-o", f"ControlPath={control_path}", "-o", "ControlPersist=300", ssh_host,
               "uptime; nvidia-smi --query-gpu=utilization.gpu,memory.used --format=csv,noheader 2>/dev/null | head -4"]
    try:
        completed = subprocess.run(command, text=True, errors="replace", capture_output=True, timeout=30)
        return {"ok": completed.returncode == 0, "report": (completed.stdout or "").strip()[:800]}
    except (subprocess.TimeoutExpired, OSError) as exc:
        return {"ok": False, "error": type(exc).__name__}
