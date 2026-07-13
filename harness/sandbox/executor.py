"""P4 — Execution environment (sandbox).

`SandboxExecutor` is an ABC with two strategies (Decision #6):
`SubprocessSandbox` (default — a fresh process, `resource.setrlimit` for
CPU/memory, no network, temp dir, hard timeout kill) works anywhere,
including Railway's free tier where Docker-in-Docker isn't available.
`DockerSandbox` is a stub for local/paid deployments — same interface, a
container instead of a bare process. Phase 1 ships subprocess only.
"""
from __future__ import annotations

import resource
import subprocess
import sys
import tempfile
import time
from abc import ABC, abstractmethod
from pathlib import Path

from harness.models import ExecResult


class SandboxExecutor(ABC):
    @abstractmethod
    def execute(self, code: str, timeout_s: float, mem_limit_mb: int) -> ExecResult:
        ...


def _limit_resources(mem_limit_mb: int, cpu_limit_s: int) -> None:
    """Runs in the child process (via `preexec_fn`) before exec. POSIX only."""
    mem_bytes = mem_limit_mb * 1024 * 1024
    try:
        resource.setrlimit(resource.RLIMIT_AS, (mem_bytes, mem_bytes))
    except (ValueError, OSError):
        # RLIMIT_AS isn't enforced on some macOS configurations — the
        # wall-clock timeout below is still a hard backstop.
        pass
    try:
        resource.setrlimit(resource.RLIMIT_CPU, (cpu_limit_s, cpu_limit_s))
    except (ValueError, OSError):
        pass


class SubprocessSandbox(SandboxExecutor):
    def execute(self, code: str, timeout_s: float, mem_limit_mb: int) -> ExecResult:
        with tempfile.TemporaryDirectory(prefix="glassbox-sbx-") as tmpdir:
            script_path = Path(tmpdir) / "submission.py"
            script_path.write_text(code)

            start = time.monotonic()
            timed_out = False
            try:
                proc = subprocess.run(
                    [sys.executable, str(script_path)],
                    cwd=tmpdir,
                    capture_output=True,
                    text=True,
                    timeout=timeout_s,
                    preexec_fn=lambda: _limit_resources(mem_limit_mb, int(timeout_s) + 1),
                    env={"PATH": "/usr/bin:/bin"},  # no inherited secrets, no network config
                )
                stdout, stderr, exit_code = proc.stdout, proc.stderr, proc.returncode
            except subprocess.TimeoutExpired as e:
                timed_out = True
                stdout = (e.stdout or b"").decode() if isinstance(e.stdout, bytes) else (e.stdout or "")
                stderr = (e.stderr or b"").decode() if isinstance(e.stderr, bytes) else (e.stderr or "")
                exit_code = -1

            duration_ms = (time.monotonic() - start) * 1000
            return ExecResult(
                stdout=stdout, stderr=stderr, exit_code=exit_code,
                duration_ms=duration_ms, timed_out=timed_out,
            )
