"""Run submission executables safely.

Core rule:
    stdout and stderr MUST be captured as a single merged stream.
    The original grader ran `./exe < case > out 2>&1`. Capturing them
    separately makes every submission that prints messages to the other
    stream fail.
"""
from __future__ import annotations

import os
import platform
import signal
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

IS_WINDOWS = platform.system() == "Windows"

# Termination signal -> verdict
SIGNAL_VERDICT = {
    signal.SIGABRT: "RE-abort",
    signal.SIGSEGV: "RE-segv",
}
if hasattr(signal, "SIGBUS"):
    SIGNAL_VERDICT[signal.SIGBUS] = "RE-bus"
if hasattr(signal, "SIGFPE"):
    SIGNAL_VERDICT[signal.SIGFPE] = "RE-fpe"


@dataclass
class RunResult:
    output: str
    returncode: int
    ms: int
    timed_out: bool = False
    verdict: str | None = None   # set only on abnormal termination


def _limits(memory_mb: int):
    """POSIX only. Apply a memory limit to the child process."""
    if IS_WINDOWS:
        return None

    def apply():
        import resource
        limit = memory_mb * 1024 * 1024
        try:
            resource.setrlimit(resource.RLIMIT_AS, (limit, limit))
        except (ValueError, OSError):
            pass
        try:
            resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        except (ValueError, OSError):
            pass
        os.setsid()

    return apply


def run_case(exe: Path, stdin_data: str, timeout: float = 5.0,
             memory_mb: int = 512, cwd: Path | None = None,
             env_extra: dict | None = None) -> RunResult:
    env = dict(os.environ)
    if env_extra:
        env.update(env_extra)

    kwargs = {}
    if IS_WINDOWS:
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    else:
        kwargs["preexec_fn"] = _limits(memory_mb)

    started = time.perf_counter()
    try:
        p = subprocess.run(
            [str(exe)],
            input=stdin_data,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,   # required: reproduces the original 2>&1
            text=True,
            errors="replace",
            timeout=timeout,
            cwd=str(cwd) if cwd else None,
            env=env,
            **kwargs,
        )
    except subprocess.TimeoutExpired as e:
        ms = int((time.perf_counter() - started) * 1000)
        partial = e.output or ""
        if isinstance(partial, bytes):
            partial = partial.decode("utf-8", "replace")
        return RunResult(output=partial, returncode=124, ms=ms,
                         timed_out=True, verdict="TLE")
    except OSError as e:
        ms = int((time.perf_counter() - started) * 1000)
        return RunResult(output=f"실행할 수 없습니다: {e}", returncode=-1,
                         ms=ms, verdict="RE-spawn")

    ms = int((time.perf_counter() - started) * 1000)
    verdict = None
    rc = p.returncode
    if rc < 0:                      # POSIX: killed by a signal
        verdict = SIGNAL_VERDICT.get(-rc, f"RE-signal{-rc}")
    elif rc > 128 and not IS_WINDOWS:
        verdict = SIGNAL_VERDICT.get(rc - 128, f"RE-signal{rc - 128}")
    elif IS_WINDOWS and rc != 0:
        # Windows reports structured exceptions as codes such as 0xC0000005
        if rc in (0xC0000005, -1073741819):
            verdict = "RE-segv"
        elif rc in (0xC00000FD, -1073741571):       # stack overflow
            verdict = "RE-segv"
        elif rc in (0xC000001D, -1073741795):       # illegal instruction: GCC's trap for a missing return
            verdict = "RE-ill"
        elif rc in (0xC0000409, -1073740791, 3):
            verdict = "RE-abort"
        # Any other non-zero exit code is a normal exit (e.g. main returning 1)

    return RunResult(output=p.stdout or "", returncode=rc, ms=ms, verdict=verdict)
