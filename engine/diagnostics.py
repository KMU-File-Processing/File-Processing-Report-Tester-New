#!/usr/bin/env python3
"""FP-Judge diagnostics.

Run this first when grading does not work. It checks the environment, the
compilers, and whether a submission actually compiles.

    python diagnose.py                    environment only
    python diagnose.py <submission dir>   also filename parsing and a real compile
"""
from __future__ import annotations

import locale
import os
import platform
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

# When bundled as an executable, resources are extracted under _MEIPASS
HERE = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))


def head(title):
    print()
    print(f"== {title} " + "=" * max(0, 62 - len(title)))


def run(argv=None):
    args = list(argv if argv is not None else sys.argv)
    head("환경")
    print(f"  OS            {platform.platform()}")
    print(f"  파이썬         {sys.version.split()[0]}")
    print(f"  기본 인코딩     {sys.getdefaultencoding()}")
    print(f"  파일시스템 인코딩 {sys.getfilesystemencoding()}")
    print(f"  선호 인코딩     {locale.getpreferredencoding(False)}")
    if os.name == "nt":
        try:
            cp = subprocess.run(["chcp"], capture_output=True, text=True,
                                shell=True).stdout.strip()
            print(f"  콘솔 코드페이지  {cp}")
        except Exception:
            pass
    print(f"  임시 폴더       {tempfile.gettempdir()}")

    from . import toolchain

    safe = toolchain.ascii_safe_tempdir()
    print(f"  작업 폴더       {safe}")
    try:
        str(safe).encode("ascii")
        print("                  ASCII 안전 (정상)")
    except UnicodeEncodeError:
        print("                  [경고] 경로에 비ASCII 문자가 있습니다")
    shutil.rmtree(safe, ignore_errors=True)

    head("컴파일러")
    comps = toolchain.detect_compilers()
    if not comps:
        print("  [실패] C++ 컴파일러를 찾지 못했습니다.")
        print()
        print("  Windows: MSYS2(https://www.msys2.org/) 설치 후")
        print("           pacman -S mingw-w64-ucrt-x86_64-gcc")
        print("           보통 C:\\msys64\\ucrt64\\bin 에 g++.exe 가 생깁니다.")
        print("  macOS:   xcode-select --install")
        print("  Linux:   sudo apt install g++")
        return 1
    for c in comps:
        print(f"  {c.cxx}")
        print(f"    버전  {c.version or '(확인 불가)'}")
        print(f"    출처  {c.source}")
        print(f"    C++17 {'지원' if c.cxx17 else '[경고] 미지원'}")
    if not comps[0].cxx17:
        print()
        print("  [경고] " + comps[0].warning)

    head("헬로월드 컴파일")
    c = comps[0]
    work = toolchain.ascii_safe_tempdir()
    try:
        src = work / "src.cpp"
        src.write_text('#include <iostream>\nint main(){std::cout<<"ok\\n";}\n',
                       encoding="utf-8")
        exe = work / ("t.exe" if os.name == "nt" else "t")
        r = subprocess.run([c.cxx, "-std=c++17", src.name, "-o", exe.name],
                           cwd=str(work), capture_output=True, text=True,
                           errors="replace")
        if r.returncode != 0:
            print("  [실패] 컴파일되지 않습니다.")
            print("  " + (r.stderr or r.stdout).strip().replace("\n", "\n  "))
            return 1
        out = subprocess.run([str(exe)], capture_output=True, text=True).stdout
        print(f"  컴파일 성공, 실행 결과 {out.strip()!r}")
    finally:
        shutil.rmtree(work, ignore_errors=True)

    head("테스트케이스")
    td = HERE / "testdata"
    if not td.is_dir():
        print(f"  [실패] testdata 폴더가 없습니다: {td}")
        return 1
    for proj in ("bst", "avl", "btree"):
        h = len(list((td / proj / "hidden").glob("*.in")))
        p = len(list((td / proj / "public").glob("*.in")))
        print(f"  {proj:<6} hidden {h:>3}   public {p:>3}")

    if len(args) < 2:
        head("결과")
        print("  환경에 문제가 없습니다.")
        print("  제출 폴더까지 확인하려면:  python diagnose.py <제출폴더>")
        return 0

    folder = Path(args[1]).expanduser()
    head(f"제출 폴더  {folder}")
    if not folder.is_dir():
        print("  [실패] 폴더를 찾을 수 없습니다.")
        return 1

    from . import submission
    found, problems = submission.collect(folder)
    print(f"  채점 대상 {len(found)}건, 제외 {len(problems)}건")
    for f in found[:10]:
        risky = ""
        try:
            f["filename"].encode("cp949")
        except UnicodeEncodeError:
            risky = "   <- CP949 로 표현 불가한 문자 포함 (내부에서 ASCII 이름으로 복사해 처리)"
        print(f"    {f['student_id']:<10} {f['name']:<10} {f['filename']}{risky}")
    if len(found) > 10:
        print(f"    ... 외 {len(found) - 10}건")
    for p in problems[:5]:
        print(f"    [제외] {p['filename']} — {p['reason']}")

    if not found:
        return 1

    head("첫 번째 제출물 실제 컴파일")
    target = found[0]
    work = toolchain.ascii_safe_tempdir()
    try:
        cr = toolchain.compile_submission(c, target["path"], work,
                                          want_leak_build=True)
        print(f"  대상          {target['filename']}")
        print(f"  컴파일        {'성공' if cr.ok else '실패'}   표준 {cr.std or '-'}")
        print(f"  -Werror       {'통과' if cr.werror_ok else '경고 있음'}")
        if cr.leak_exe:
            print(f"  누수 검사 빌드  가능 (방식: {cr.leak_method})")
        else:
            print("  누수 검사 빌드  불가")
            print()
            print("  --- 누수 검사 빌드 로그 ---")
            print("  " + (cr.leak_stderr or "(로그 없음)").strip().replace("\n", "\n  "))
        if not cr.ok:
            print()
            print("  --- 컴파일 로그 ---")
            for a in cr.attempts:
                print(f"  [{a['std']}] rc={a['rc']}")
                print("  " + (a["stderr"] or "").strip().replace("\n", "\n  "))
            return 1
    finally:
        shutil.rmtree(work, ignore_errors=True)

    head("결과")
    print("  모두 정상입니다. 채점을 진행할 수 있습니다.")
    return 0


