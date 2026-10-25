"""Compiler discovery and compilation."""
from __future__ import annotations

import os
import sys
import platform
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

HERE = Path(__file__).resolve().parent

# When bundled with PyInstaller, resources are extracted under _MEIPASS,
# and the engine headers live in _MEIPASS/engine/.
_MEIPASS = getattr(sys, "_MEIPASS", None)


def _resource(name: str) -> Path:
    if _MEIPASS:
        bundled = Path(_MEIPASS) / "engine" / name
        if bundled.exists():
            return bundled
    return HERE / name


LEAKCHECK = _resource("leakcheck.hpp")        # C++: replaces operator new/delete
LEAKCHECK_C = _resource("leakcheck_c.h")      # C: wraps malloc/free via macros
COMPAT_WIN = _resource("compat_win.h")        # Windows header workarounds (fallback only)

IS_WINDOWS = platform.system() == "Windows"

# Candidate drivers, highest priority first.
CXX_CANDIDATES = ["g++", "clang++", "c++"]
CC_CANDIDATES = ["gcc", "clang", "cc"]

# Common Windows install locations searched even when not on PATH
WINDOWS_HINTS = [
    r"C:\msys64\ucrt64\bin",
    r"C:\msys64\mingw64\bin",
    r"C:\MinGW\bin",
    r"C:\mingw64\bin",
    r"C:\Program Files\LLVM\bin",
    r"C:\ProgramData\chocolatey\bin",
]


@dataclass
class Compiler:
    cxx: str
    cc: str
    version: str = ""
    source: str = "PATH"
    cxx17: bool = True

    @property
    def warning(self) -> str:
        if self.cxx17:
            return ""
        return ("C++17 문법(구조적 바인딩, 클래스 템플릿 인자 추론 등)을 지원하지 않는 "
                "오래된 컴파일러입니다. 올바른 제출물이 컴파일 실패(CE)로 채점될 수 있습니다. "
                "GCC 7 이상(MSYS2 ucrt64 권장) 또는 Clang 5 이상을 설치하세요.")

    def as_dict(self):
        return {"cxx": self.cxx, "cc": self.cc,
                "version": self.version, "source": self.source,
                "cxx17": self.cxx17, "warning": self.warning}


def _version_of(exe: str) -> str:
    try:
        out = subprocess.run([exe, "--version"], capture_output=True,
                             text=True, timeout=15)
        first = (out.stdout or out.stderr).splitlines()
        return first[0].strip() if first else ""
    except Exception:
        return ""


# C++17 features that real submissions use. MinGW.org GCC 6.3 accepts -std=c++17
# but cannot compile these, turning valid submissions into CE (observed in practice).
_CXX17_PROBE = """
#include <utility>
template <class T> struct Box { T v; Box(T x) : v(x) {} };
inline int g = 0;
int main() { Box b(1); auto [x, y] = std::pair<int, int>(b.v, g); return x + y - 1; }
"""

_CXX17_CACHE: dict[str, bool] = {}


def _supports_cxx17(exe: str) -> bool:
    if exe in _CXX17_CACHE:
        return _CXX17_CACHE[exe]
    ok = False
    work = ascii_safe_tempdir()
    try:
        (work / "probe.cpp").write_text(_CXX17_PROBE, encoding="utf-8")
        p = subprocess.run([exe, "-std=c++17", "-fsyntax-only", "probe.cpp"],
                           cwd=str(work), capture_output=True, timeout=60)
        ok = p.returncode == 0
    except Exception:
        ok = False
    finally:
        shutil.rmtree(work, ignore_errors=True)
    _CXX17_CACHE[exe] = ok
    return ok


def detect_compilers() -> list[Compiler]:
    """Find every usable C++ compiler.

    Compilers that support C++17 are listed first; discovery order is kept
    within each group.
    """
    found: list[Compiler] = []
    seen: set[str] = set()

    search_dirs = [None]
    if IS_WINDOWS:
        search_dirs += [d for d in WINDOWS_HINTS if Path(d).is_dir()]

    for d in search_dirs:
        for cxx_name, cc_name in zip(CXX_CANDIDATES, CC_CANDIDATES):
            exe = (shutil.which(cxx_name) if d is None
                   else shutil.which(cxx_name, path=d))
            if not exe:
                continue
            real = str(Path(exe).resolve())
            if real in seen:
                continue
            seen.add(real)
            cc = (shutil.which(cc_name) if d is None
                  else shutil.which(cc_name, path=d)) or exe
            found.append(Compiler(cxx=exe, cc=cc, version=_version_of(exe),
                                  source="PATH" if d is None else d,
                                  cxx17=_supports_cxx17(exe)))
    found.sort(key=lambda c: not c.cxx17)
    return found


def default_compiler() -> Compiler | None:
    found = detect_compilers()
    return found[0] if found else None


@dataclass
class CompileResult:
    ok: bool
    exe: Path | None
    std: str = ""
    stderr: str = ""
    werror_ok: bool = False
    werror_stderr: str = ""
    leak_exe: Path | None = None
    leak_supported: bool = True
    leak_stderr: str = ""
    leak_method: str = ""
    compat: bool = False          # whether the Windows compat prelude was needed
    attempts: list[dict] = field(default_factory=list)

    def as_dict(self):
        return {
            "ok": self.ok, "std": self.std, "stderr": self.stderr,
            "werror_ok": self.werror_ok, "werror_stderr": self.werror_stderr,
            "leak_supported": self.leak_supported,
            "leak_stderr": self.leak_stderr,
            "leak_method": self.leak_method,
            "compat": self.compat,
            "attempts": self.attempts,
        }


STD_LADDER = ["c++17", "c++20", "c++14", ""]
# C submissions try the compiler default (GNU dialect) first. Strict -std=c11
# hides POSIX declarations such as getline, which truncates pointers on 64-bit.
STD_LADDER_C = ["", "c11"]


# Link statically on Windows.
# A dynamically linked executable loads the first libstdc++-6.dll found on PATH.
# Git for Windows and similar tools often put a different build on PATH; with a
# mismatched DLL, printf and cout use different CRT buffers, output lines come
# out of order and correct submissions get WA (observed with GCC 16 + Git's DLL).
LINK_FLAGS = ["-static"] if IS_WINDOWS else []


def _driver(compiler: Compiler, source: Path) -> str:
    return compiler.cc if source.suffix.lower() == ".c" else compiler.cxx


def _run(cmd, cwd, timeout=120):
    try:
        p = subprocess.run(cmd, cwd=str(cwd), capture_output=True,
                           text=True, timeout=timeout,
                           errors="replace")
        out = (p.stderr or "") + (p.stdout or "")
        if p.returncode != 0:
            # Keep the exact failing command; it is the most useful diagnostic.
            out = "$ " + " ".join(str(c) for c in cmd) + "\n" + out
        return p.returncode, out
    except subprocess.TimeoutExpired:
        return 124, "컴파일이 제한 시간을 초과했습니다."
    except FileNotFoundError as e:
        return 127, f"컴파일러를 실행할 수 없습니다: {e}"


def ascii_safe_tempdir() -> Path:
    """Create a temporary directory whose path is pure ASCII.

    If the Windows user name is non-ASCII (e.g. Korean), %TEMP% contains it.
    MinGW compilers receive arguments in the ANSI code page (CP949), so paths
    with characters outside that code page cannot be opened.
    """
    candidates = [Path(tempfile.gettempdir())]
    if IS_WINDOWS:
        candidates.append(Path(os.environ.get("SystemDrive", "C:") + "\\"))
    for base in candidates:
        try:
            base.mkdir(parents=True, exist_ok=True)
            str(base).encode("ascii")
        except (UnicodeEncodeError, OSError):
            continue
        try:
            return Path(tempfile.mkdtemp(prefix="fpjudge_", dir=str(base)))
        except OSError:
            continue
    return Path(tempfile.mkdtemp(prefix="fpjudge_"))


def compile_submission(compiler: Compiler, source: Path, out_dir: Path,
                       want_leak_build: bool = True) -> CompileResult:
    """Compile a submission.

    Builds:
      1) -Wall -Wextra -Werror : warning hint only, never scored.
      2) grading build         : the executable used for output comparison.
      3) leak build            : a separate build with a tracker header injected.

    The source is always copied to an ASCII file name first. e-class filenames
    contain Korean text and U+200D (zero-width joiner); U+200D is not in CP949,
    so passing the original path to a MinGW compiler fails with "no input file".
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    result = CompileResult(ok=False, exe=None)

    # --- 0) copy to an ASCII file name ---
    work_src = out_dir / ("src" + source.suffix.lower())
    try:
        shutil.copyfile(source, work_src)
    except OSError as e:
        result.stderr = f"소스 파일을 읽을 수 없습니다: {e}"
        result.attempts.append({"std": "-", "rc": -1, "stderr": result.stderr})
        return result

    drv = _driver(compiler, source)
    is_c = source.suffix.lower() == ".c"
    exe_suffix = ".exe" if IS_WINDOWS else ""

    # --- 1) warning probe ---
    probe_exe = out_dir / f"probe{exe_suffix}"
    probe_std = [] if is_c else ["-std=c++17"]
    rc, err = _run([drv, "-Wall", "-Wextra", "-Werror"] + probe_std +
                   [work_src.name, "-o", probe_exe.name] + LINK_FLAGS, out_dir)
    result.werror_ok = (rc == 0)
    result.werror_stderr = err.strip()
    probe_exe.unlink(missing_ok=True)

    # --- 2) grading build: walk the language standard ladder ---
    # If every attempt fails on Windows, retry the ladder with the compat prelude.
    exe = out_dir / f"submission{exe_suffix}"
    preludes: list[list[str]] = [[]]
    if IS_WINDOWS:
        try:
            shutil.copyfile(COMPAT_WIN, out_dir / COMPAT_WIN.name)
            preludes.append(["-include", COMPAT_WIN.name])
        except OSError:
            pass
    first_err = ""
    for prelude in preludes:
        for std in (STD_LADDER_C if is_c else STD_LADDER):
            cmd = [drv]
            if std:
                cmd.append(f"-std={std}")
            # No optimisation, matching the original grader (clang++ -g3).
            # At -O2, undefined behaviour such as a missing return crashes far more
            # often, turning submissions that passed originally into zero scores.
            cmd += ["-O0", "-g"] + prelude + [work_src.name, "-o", exe.name]
            cmd += LINK_FLAGS
            rc, err = _run(cmd, out_dir)
            label = (std or "(default)") + (" +compat" if prelude else "")
            result.attempts.append({"std": label, "rc": rc,
                                    "stderr": err.strip()[:4000]})
            if rc == 0:
                result.ok = True
                result.std = std or "(default)"
                result.compat = bool(prelude)
                result.exe = exe
                result.stderr = err.strip()
                break
            first_err = first_err or err.strip()
        if result.ok:
            break

    if not result.ok:
        # Report the error from the unmodified build; it is clearer than the compat retry's
        result.stderr = first_err
        return result

    # --- 3) leak build ---
    #
    # Two injection methods are tried in order:
    #   a) force-include the header with -include      (standard)
    #   b) a wrapper source that #includes both files  (when a fails)
    # b sidesteps any differences in -include handling, so it works everywhere.
    if want_leak_build:
        leak_exe = out_dir / f"submission_leak{exe_suffix}"
        header = LEAKCHECK_C if is_c else LEAKCHECK
        local_hdr = out_dir / header.name
        try:
            shutil.copyfile(header, local_hdr)
        except OSError as e:
            result.leak_supported = False
            result.leak_stderr = (f"누수 검사 헤더를 복사할 수 없습니다: {e}\n"
                                  f"원본 경로: {header}")
            return result

        std_flag = ([f"-std={result.std}"]
                    if result.std != "(default)" else [])
        compat_inc = ["-include", COMPAT_WIN.name] if result.compat else []

        attempts = []

        # a) -include
        # Optimisation off: since C++14 compilers may elide matching new/delete
        # pairs, so -O1 and above can hide real leaks.
        opt = ["-O0", "-g"]
        cmd = ([drv] + std_flag + opt + compat_inc +
               ["-include", local_hdr.name, work_src.name,
                "-o", leak_exe.name] + LINK_FLAGS)
        rc, err = _run(cmd, out_dir)
        attempts.append(("-include", rc, err))

        # b) wrapper source
        if rc != 0:
            wrapper = out_dir / ("leakwrap.c" if is_c else "leakwrap.cpp")
            compat_line = (f'#include "{COMPAT_WIN.name}"\n'
                           if result.compat else "")
            wrapper.write_text(
                compat_line +
                f'#include "{local_hdr.name}"\n#include "{work_src.name}"\n',
                encoding="utf-8")
            cmd = ([drv] + std_flag + opt +
                   [wrapper.name, "-o", leak_exe.name] + LINK_FLAGS)
            rc, err = _run(cmd, out_dir)
            attempts.append(("wrapper", rc, err))

        if rc == 0:
            result.leak_exe = leak_exe
            result.leak_method = attempts[-1][0]
        else:
            # e.g. the submission defines its own operator new.
            # Only the leak check is skipped; grading continues normally.
            result.leak_supported = False
            result.leak_stderr = "\n\n".join(
                f"[{how}] rc={r}\n{e.strip()}" for how, r, e in attempts)[:4000]
    else:
        result.leak_supported = False

    return result
