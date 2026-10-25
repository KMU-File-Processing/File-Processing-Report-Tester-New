"""Test case loading, output comparison, scoring and the grading pipeline."""
from __future__ import annotations

import json
import shutil
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path

from . import runner, toolchain

PROJECTS = {
    "bst": "Binary Search Tree",
    "avl": "AVL Tree",
    "btree": "B-Tree (M=4)",
}

# Points (unchanged from the original grading policy)
POINTS_SIMPLE = 8       # per simple case
POINTS_RANDOM = 2       # per random case
LEAK_PENALTY = 20       # deduction when a leak is detected
FINAL_BASE = 30.0       # final = FINAL_BASE + FINAL_SCALE * raw
FINAL_SCALE = 0.7


# ------------------------------------------------------------------ test data

@dataclass
class TestCase:
    name: str
    kind: str          # "simple" | "random"
    stdin: str
    expected: str


def load_testcases(testdata_root: Path, project: str, mode: str) -> list[TestCase]:
    """mode: 'hidden' (TA grading) or 'public' (student mock grading)."""
    d = testdata_root / project / mode
    if not d.is_dir():
        raise FileNotFoundError(f"테스트케이스를 찾을 수 없습니다: {d}")
    cases = []
    for inp in sorted(d.glob("*.in")):
        exp = inp.with_suffix(".out")
        if not exp.exists():
            continue
        name = inp.stem
        cases.append(TestCase(
            name=name,
            kind="simple" if name.startswith("simple") else "random",
            stdin=inp.read_text(encoding="utf-8"),
            expected=exp.read_text(encoding="utf-8"),
        ))
    return cases


# ------------------------------------------------------------------ output comparison

def normalize(text: str) -> str:
    """Normalise line endings only.

    On Windows the C++ runtime writes '\\n' as '\\r\\n' in text mode.
    Expected files use '\\n', so without this every case fails on Windows.

    No other whitespace is touched: spacing is significant in tree output.
    """
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _line_count(parts: list[str]) -> int:
    """Number of real lines in a split() result.

    "a\\nb\\n".split("\\n") is ["a", "b", ""]: the piece after the final newline
    is not a line. Counting it made every output one line longer than it is.
    """
    return len(parts) - 1 if parts and parts[-1] == "" else len(parts)


def first_diff(actual: str, expected: str, context: int = 3) -> dict | None:
    """Return the first mismatching line with surrounding context."""
    a = normalize(actual).split("\n")
    b = normalize(expected).split("\n")
    n = max(len(a), len(b))
    for i in range(n):
        x = a[i] if i < len(a) else None
        y = b[i] if i < len(b) else None
        if x != y:
            lo = max(0, i - context)
            hi = min(n, i + context + 1)
            rows = []
            for j in range(lo, hi):
                xa = a[j] if j < len(a) else None
                yb = b[j] if j < len(b) else None
                rows.append({
                    "line": j + 1,
                    "actual": xa,
                    "expected": yb,
                    "same": xa == yb,
                })
            return {"line": i + 1, "rows": rows,
                    "actual_lines": _line_count(a),
                    "expected_lines": _line_count(b)}
    return None


# ------------------------------------------------------------------ grading

@dataclass
class CaseResult:
    name: str
    kind: str
    verdict: str            # AC / WA / TLE / RE-*
    ms: int = 0
    diff: dict | None = None

    def as_dict(self):
        return {"name": self.name, "kind": self.kind, "verdict": self.verdict,
                "ms": self.ms, "diff": self.diff}


@dataclass
class Score:
    simple_passed: int = 0
    simple_total: int = 0
    random_passed: int = 0
    random_total: int = 0
    leak_penalty: int = 0
    raw: int = 0
    final: float = 0.0

    def as_dict(self):
        return {
            "simple": f"{self.simple_passed}/{self.simple_total}",
            "random": f"{self.random_passed}/{self.random_total}",
            "simple_passed": self.simple_passed, "simple_total": self.simple_total,
            "random_passed": self.random_passed, "random_total": self.random_total,
            "leak_penalty": self.leak_penalty,
            "raw": self.raw, "final": round(self.final, 2),
        }


def compute_score(cases: list[CaseResult], leaked: bool) -> Score:
    s = Score()
    for c in cases:
        if c.kind == "simple":
            s.simple_total += 1
            if c.verdict == "AC":
                s.simple_passed += 1
        else:
            s.random_total += 1
            if c.verdict == "AC":
                s.random_passed += 1
    # Plain integer arithmetic. Avoids the original bash grader's octal parsing bug.
    s.raw = s.simple_passed * POINTS_SIMPLE + s.random_passed * POINTS_RANDOM
    if leaked:
        s.leak_penalty = LEAK_PENALTY
        s.raw = max(0, s.raw - LEAK_PENALTY)
    s.final = FINAL_BASE + FINAL_SCALE * s.raw
    return s


@dataclass
class SubmissionResult:
    student_id: str
    name: str
    source: str
    compile: dict = field(default_factory=dict)
    cases: list[CaseResult] = field(default_factory=list)
    leak: str = "no"          # "no" | "yes" | "unknown"
    leak_reason: str = ""     # why the check was not possible when "unknown"
    score: Score = field(default_factory=Score)
    error: str = ""

    def as_dict(self):
        return {
            "student_id": self.student_id, "name": self.name,
            "source": self.source, "compile": self.compile,
            "cases": [c.as_dict() for c in self.cases],
            "leak": self.leak, "leak_reason": self.leak_reason,
            "score": self.score.as_dict(),
            "error": self.error,
        }


def grade_one(source: Path, cases: list[TestCase], compiler: toolchain.Compiler,
              student_id: str = "", name: str = "",
              timeout: float = 5.0, memory_mb: int = 512,
              check_leak: bool = True, max_leak_cases: int = 10,
              tle_give_up: int = 4,
              workdir: Path | None = None) -> SubmissionResult:
    res = SubmissionResult(student_id=student_id or source.stem,
                           name=name, source=source.name)

    tmp = Path(workdir) if workdir else toolchain.ascii_safe_tempdir()
    owns_tmp = workdir is None
    try:
        cr = toolchain.compile_submission(compiler, source, tmp,
                                          want_leak_build=check_leak)
        res.compile = cr.as_dict()
        if not cr.ok:
            for c in cases:
                res.cases.append(CaseResult(c.name, c.kind, "CE"))
            res.leak = "unknown"
            res.leak_reason = "컴파일에 실패해 검사하지 못했습니다."
            res.score = compute_score([], False)
            res.score.simple_total = sum(1 for c in cases if c.kind == "simple")
            res.score.random_total = sum(1 for c in cases if c.kind == "random")
            res.score.final = FINAL_BASE
            return res

        # --- output comparison ---
        # A submission with an infinite loop would time out on every case and stall
        # the batch. After several consecutive timeouts the rest are marked TLE.
        tle_streak = 0
        give_up = False
        for c in cases:
            if give_up:
                res.cases.append(CaseResult(c.name, c.kind, "TLE", 0))
                continue
            rr = runner.run_case(cr.exe, c.stdin, timeout=timeout,
                                 memory_mb=memory_mb, cwd=tmp)
            if rr.verdict:
                res.cases.append(CaseResult(c.name, c.kind, rr.verdict, rr.ms))
                if rr.verdict == "TLE":
                    tle_streak += 1
                    if tle_streak >= tle_give_up:
                        give_up = True
                else:
                    tle_streak = 0
                continue
            tle_streak = 0
            if normalize(rr.output) == normalize(c.expected):
                res.cases.append(CaseResult(c.name, c.kind, "AC", rr.ms))
            else:
                res.cases.append(CaseResult(
                    c.name, c.kind, "WA", rr.ms,
                    diff=first_diff(rr.output, c.expected)))

        # --- leak check (separate runs so the output diff is not affected) ---
        # Cases that timed out or crashed are skipped; their leak count is meaningless.
        if check_leak and cr.leak_exe is not None:
            leaked = False
            leak_file = tmp / "leak.txt"
            passed = {c.name for c in res.cases if c.verdict == "AC"}
            targets = [c for c in cases if c.name in passed][:max_leak_cases]
            for c in targets:
                leak_file.unlink(missing_ok=True)
                rr = runner.run_case(cr.leak_exe, c.stdin,
                                     timeout=timeout * 2, memory_mb=memory_mb,
                                     cwd=tmp,
                                     env_extra={"FPJUDGE_LEAK_FILE": str(leak_file)})
                if rr.timed_out or rr.verdict:
                    continue
                try:
                    remaining = int(leak_file.read_text().strip())
                except Exception:
                    continue
                if remaining > 0:
                    leaked = True
                    break
            if leaked:
                res.leak = "yes"
            elif targets:
                res.leak = "no"
            else:
                res.leak = "unknown"
                res.leak_reason = ("통과한 케이스가 없어 검사할 대상이 없습니다. "
                                   "정답을 먼저 맞춘 뒤에 확인됩니다.")
        elif not check_leak:
            res.leak = "unknown"
            res.leak_reason = "누수 검사가 꺼져 있습니다."
        else:
            res.leak = "unknown"
            res.leak_reason = (
                "누수 검사용 빌드에 실패했습니다. "
                "학생 코드가 operator new 를 직접 정의했을 수 있습니다.\n\n"
                + (res.compile.get("leak_stderr") or "(오류 로그 없음)"))

        res.score = compute_score(res.cases, res.leak == "yes")
        return res
    except Exception as e:                      # never let one submission stop the batch
        res.error = f"{type(e).__name__}: {e}"
        return res
    finally:
        if owns_tmp:
            shutil.rmtree(tmp, ignore_errors=True)


def grade_many(sources: list[dict], cases: list[TestCase],
               compiler: toolchain.Compiler, workers: int = 4,
               progress=None, **kw) -> list[SubmissionResult]:
    """sources: [{'path': Path, 'student_id': str, 'name': str}, ...]"""
    results: list[SubmissionResult] = []
    done = 0
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = {
            pool.submit(grade_one, s["path"], cases, compiler,
                        s.get("student_id", ""), s.get("name", ""), **kw): s
            for s in sources
        }
        for fut in as_completed(futures):
            src = futures[fut]
            try:
                results.append(fut.result())
            except Exception as e:
                r = SubmissionResult(src.get("student_id", ""), src.get("name", ""),
                                     Path(src["path"]).name)
                r.error = f"{type(e).__name__}: {e}"
                results.append(r)
            done += 1
            if progress:
                progress(done, len(sources))
    results.sort(key=lambda r: (r.student_id, r.name))
    return results
