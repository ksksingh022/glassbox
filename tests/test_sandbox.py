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


# --- network guard (Phase 4) ------------------------------------------------

def test_sandbox_blocks_outbound_network():
    """Regression: clearing `env` only strips inherited proxy config, it does
    not stop outbound connections. Before the guard was added, a urllib call
    from inside the sandbox genuinely reached leetcode.com and got a 403.

    This is a guardrail, not a security boundary (see the note in
    `sandbox/executor.py`) — but casual/accidental network use must fail.
    """
    result = SubprocessSandbox().execute(
        'import urllib.request\n'
        'urllib.request.urlopen("https://leetcode.com", timeout=3)\n'
        'print("REACHED NETWORK")\n',
        timeout_s=8.0, mem_limit_mb=256,
    )
    assert "REACHED NETWORK" not in result.stdout
    assert result.exit_code != 0
    assert "network access is disabled" in result.stderr


def test_sandbox_blocks_raw_sockets():
    result = SubprocessSandbox().execute(
        'import socket\n'
        'socket.create_connection(("leetcode.com", 443), timeout=3)\n'
        'print("REACHED")\n',
        timeout_s=8.0, mem_limit_mb=256,
    )
    assert "REACHED" not in result.stdout
    assert "network access is disabled" in result.stderr


def test_network_guard_does_not_break_ordinary_code():
    result = SubprocessSandbox().execute(
        "import json\nprint(json.dumps({'ok': sum(range(10))}))\n",
        timeout_s=5.0, mem_limit_mb=256,
    )
    assert result.exit_code == 0
    assert '"ok": 45' in result.stdout
