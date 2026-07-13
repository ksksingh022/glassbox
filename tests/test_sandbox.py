"""Sandbox kill tests — the two malicious programs from plan §3.5."""
from harness.sandbox.executor import SubprocessSandbox


def test_sandbox_kills_infinite_loop():
    sandbox = SubprocessSandbox()
    result = sandbox.execute("while True:\n    pass\n", timeout_s=1.0, mem_limit_mb=128)
    assert result.timed_out is True


def test_sandbox_kills_memory_bomb():
    sandbox = SubprocessSandbox()
    code = "x = []\nwhile True:\n    x.append(bytearray(10 * 1024 * 1024))\n"
    result = sandbox.execute(code, timeout_s=5.0, mem_limit_mb=64)
    # Either the OS kills it for exceeding RLIMIT_AS (non-zero exit / MemoryError)
    # or the wall-clock timeout catches it as a backstop.
    assert result.timed_out or result.exit_code != 0


def test_sandbox_runs_normal_code():
    sandbox = SubprocessSandbox()
    result = sandbox.execute("print('hello')\n", timeout_s=2.0, mem_limit_mb=128)
    assert result.timed_out is False
    assert result.exit_code == 0
    assert "hello" in result.stdout
