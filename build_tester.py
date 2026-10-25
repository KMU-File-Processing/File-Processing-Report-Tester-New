#!/usr/bin/env python3
"""
FP-Tester 실행 파일 만들기.

    python build_tester.py

Windows 에서는 build_tester.bat 을 더블클릭해도 된다.
빌드에만 Python 이 필요하고, 완성된 실행 파일은 Python 없이 동작한다.

만들어지는 파일은 실행하는 OS 의 것 하나뿐이다.
  Windows 에서 돌리면  dist/FP-Tester.exe
  macOS 에서 돌리면    dist/FP-Tester      (Apple Silicon / Intel 은 각각 따로)
  Linux 에서 돌리면    dist/FP-Tester

PyInstaller 는 교차 컴파일을 하지 못한다. mac 용 바이너리가 필요하면
반드시 Mac 에서 이 스크립트를 돌려야 한다.

--- 조교에게 ---
이 저장소에는 공개 테스트케이스만 들어 있으므로, 여기서 만든 실행 파일은
항상 학생용이다. 아래 안전 점검이 hidden 테스트케이스가 섞여 들어온 경우
빌드를 중단시킨다. 이 점검을 제거하지 말 것.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
APP_NAME = "FP-Tester"


def say(msg=""):
    enc = getattr(sys.stdout, "encoding", None) or "utf-8"
    try:
        print(msg)
    except UnicodeEncodeError:
        print(msg.encode(enc, "replace").decode(enc, "replace"))


def safety_check():
    """조교용 자료가 섞여 있으면 빌드하지 않는다.

    학생에게 나가는 실행 파일 안에 hidden 테스트케이스가 들어가면
    실채점 정답이 그대로 유출된다. 되돌릴 방법이 없으므로 여기서 막는다.
    """
    problems = []

    hidden = list((HERE / "testdata").rglob("hidden"))
    if hidden:
        problems.append(f"hidden 테스트케이스 폴더 {len(hidden)}개")

    if (HERE / "testdata" / "manifest.json").exists():
        problems.append("testdata/manifest.json (hidden 시드가 들어 있음)")

    if problems:
        say()
        say("  [중단] 조교용 자료가 저장소에 섞여 있습니다.")
        for p in problems:
            say(f"           - {p}")
        say()
        say("  이대로 빌드하면 학생용 실행 파일에 실채점 정답이 들어갑니다.")
        say("  해당 파일을 지운 뒤 다시 실행하세요.")
        say()
        sys.exit(1)

    n = len(list((HERE / "testdata").rglob("*.in")))
    say(f"  안전 점검 통과. 공개 테스트케이스 {n}개만 포함합니다.")


def have(module):
    try:
        __import__(module)
        return True
    except ImportError:
        return False


def ensure_pyinstaller():
    if have("PyInstaller"):
        say("  PyInstaller 가 이미 설치되어 있습니다.")
        return
    base = [sys.executable, "-m", "pip", "install", "--quiet", "--upgrade",
            "pyinstaller"]
    say("  $ pip install pyinstaller")
    for extra in ([], ["--break-system-packages"], ["--user"]):
        if subprocess.run(base + extra).returncode == 0:
            return
    say()
    say("  [실패] PyInstaller 설치에 실패했습니다.")
    say("  가상환경에서 다시 시도해 보세요:")
    say("      python -m venv .venv")
    say("      .venv\\Scripts\\python build_tester.py" if os.name == "nt"
        else "      .venv/bin/python build_tester.py")
    sys.exit(1)


def main():
    os.chdir(HERE)

    say()
    say(f"  {APP_NAME} 빌드")
    say(f"  파이썬 {sys.version.split()[0]}  ({sys.executable})")
    say()

    if sys.version_info < (3, 10):
        say("  [실패] Python 3.10 이상이 필요합니다.")
        sys.exit(1)

    for needed in ("ui", "testdata", "app.py", "engine"):
        if not (HERE / needed).exists():
            say(f"  [실패] {needed} 를 찾을 수 없습니다. "
                "저장소 폴더 안에서 실행하세요.")
            sys.exit(1)

    say("  [1/3] 안전 점검과 빌드 도구를 확인합니다.")
    safety_check()
    ensure_pyinstaller()

    # 실행 파일 안에서도 학생용임을 명시한다
    (HERE / "edition.txt").write_text("student", encoding="utf-8")

    say()
    say("  [2/3] 실행 파일을 만듭니다. 몇 분 걸릴 수 있습니다.")
    sep = os.pathsep
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean", "--onefile", "--console",
        "--name", APP_NAME,
        "--distpath", str(HERE / "dist"),
        "--workpath", str(HERE / "build"),
        "--specpath", str(HERE / "build"),
        "--add-data", f"{HERE / 'ui'}{sep}ui",
        "--add-data", f"{HERE / 'testdata'}{sep}testdata",
        "--add-data", f"{HERE / 'edition.txt'}{sep}.",
        "--add-data", f"{HERE / 'engine' / 'leakcheck.hpp'}{sep}engine",
        "--add-data", f"{HERE / 'engine' / 'leakcheck_c.h'}{sep}engine",
        "--add-data", f"{HERE / 'engine' / 'compat_win.h'}{sep}engine",
        # 파일 선택 대화상자(app.py 의 --pick)가 tkinter 를 쓴다
        "--hidden-import", "tkinter",
        "--hidden-import", "tkinter.filedialog",
        str(HERE / "app.py"),
    ]
    say(f"  $ {' '.join(str(c) for c in cmd[:6])} ...")
    if subprocess.run(cmd).returncode != 0:
        say()
        say("  [실패] 빌드에 실패했습니다.")
        sys.exit(1)

    say()
    say("  [3/3] 정리합니다.")
    shutil.rmtree(HERE / "build", ignore_errors=True)

    exe = HERE / "dist" / (APP_NAME + (".exe" if os.name == "nt" else ""))
    say()
    if exe.exists():
        say(f"  완료했습니다.  {exe}   "
            f"({exe.stat().st_size / (1024 * 1024):.0f} MB)")
        say("  이 파일 하나만 있으면 어디서든 동작합니다.")
        say("  저장소에 커밋하지 말고 Releases 에 첨부하세요.")
    else:
        say("  빌드는 끝났지만 실행 파일을 찾지 못했습니다. dist 폴더를 확인하세요.")
    say()


if __name__ == "__main__":
    main()
