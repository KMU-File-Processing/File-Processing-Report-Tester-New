#!/usr/bin/env python3
"""
FP-Judge — 화일처리 레포트 채점기 / 모의 채점기

로컬에서만 동작한다. 127.0.0.1 에 서버를 띄우고 기본 브라우저를 연다.
외부에서 접근할 수 없고, 인증도 필요 없다.

    python app.py

의존성은 표준 라이브러리뿐이다. xlsx 내보내기에만 openpyxl 이 필요하며,
없으면 그 기능만 비활성화된다.
"""
from __future__ import annotations

import json
import mimetypes
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from engine import judge, report, submission, toolchain

# PyInstaller 로 묶였을 때는 _MEIPASS 아래에 리소스가 풀린다
BASE = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
UI_DIR = BASE / "ui"
TESTDATA = BASE / "testdata"

# 실행 파일 옆(= 사용자가 보는 폴더)에 결과를 남긴다
OUTPUT_ROOT = Path(sys.executable).parent if getattr(sys, "frozen", False) \
    else Path(__file__).resolve().parent
OUTPUT_ROOT = OUTPUT_ROOT / "결과"


# ------------------------------------------------------------------ 에디션
#
# 두 가지 배포본이 있다.
#   ta      — 조교용. hidden 테스트케이스와 일괄 채점 기능을 갖는다.
#   student — 학생용 모의 채점기. public 케이스만 들어 있다.
#
# 학생용 빌드에는 hidden 테스트케이스를 아예 넣지 않는다.
# UI에서 조교 화면을 숨기는 것만으로는 부족하다. 실행 파일 안에 정답이
# 남아 있으면 추출하거나 API를 직접 호출해 실채점 결과를 미리 알 수 있다.
# 그래서 "가진 데이터가 곧 권한"이 되도록 만들었다.

def _detect_edition() -> str:
    marker = BASE / "edition.txt"
    if marker.exists():
        v = marker.read_text(encoding="utf-8").strip().lower()
        if v in ("ta", "student"):
            return v
    # 표식이 없으면 데이터로 판단한다. hidden 이 하나도 없으면 학생용이다.
    for proj in ("bst", "avl", "btree"):
        d = TESTDATA / proj / "hidden"
        if d.is_dir() and any(d.glob("*.in")):
            return "ta"
    return "student"


EDITION = _detect_edition()


def is_ta() -> bool:
    return EDITION == "ta"


# ------------------------------------------------------------------ 작업 상태

class Job:
    def __init__(self, jid):
        self.id = jid
        self.state = "running"        # running | done | error
        self.done = 0
        self.total = 0
        self.message = ""
        self.results = []
        self.project = ""
        self.mode = ""
        self.problems = []
        self.error = ""


JOBS: dict[str, Job] = {}
JOBS_LOCK = threading.Lock()


def _run_job(job: Job, sources, cases, compiler, opts):
    try:
        job.total = len(sources)

        def progress(done, total):
            job.done, job.total = done, total

        job.results = judge.grade_many(
            sources, cases, compiler,
            workers=opts.get("workers", 4),
            progress=progress,
            timeout=opts.get("timeout", 5.0),
            check_leak=opts.get("check_leak", True),
        )
        job.state = "done"
    except Exception:
        job.state = "error"
        job.error = traceback.format_exc(limit=4)


# ------------------------------------------------------------------ API

def api_config():
    compilers = [c.as_dict() for c in toolchain.detect_compilers()]
    projects = []
    for key, label in judge.PROJECTS.items():
        entry = {"key": key, "label": label, "hidden": 0, "public": 0}
        modes = ("hidden", "public") if is_ta() else ("public",)
        for mode in modes:
            d = TESTDATA / key / mode
            entry[mode] = len(list(d.glob("*.in"))) if d.is_dir() else 0
        projects.append(entry)
    try:
        import openpyxl  # noqa: F401
        xlsx = True
    except ImportError:
        xlsx = False
    return {"compilers": compilers, "projects": projects,
            "xlsx": xlsx and is_ta(), "edition": EDITION,
            "output_root": str(OUTPUT_ROOT) if is_ta() else ""}


def api_scan(body):
    if not is_ta():
        return {"error": "이 기능은 조교용 배포본에서만 사용할 수 있습니다."}
    folder = Path(body["folder"]).expanduser()
    if not folder.is_dir():
        return {"error": f"폴더를 찾을 수 없습니다: {folder}"}
    found, problems = submission.collect(folder, body.get("recursive", False))
    return {
        "found": [{"student_id": f["student_id"], "name": f["name"],
                   "filename": f["filename"]} for f in found],
        "problems": problems,
    }


def _pick_compiler(body):
    comps = toolchain.detect_compilers()
    if not comps:
        return None
    want = body.get("compiler")
    for c in comps:
        if c.cxx == want:
            return c
    return comps[0]


def api_grade(body):
    project = body["project"]
    mode = body.get("mode", "hidden")

    # 학생용 배포본은 public 케이스, 단일 파일만 허용한다.
    if not is_ta():
        if mode != "public":
            return {"error": "이 배포본에는 공개 테스트케이스만 들어 있습니다."}
        if not body.get("single_file"):
            return {"error": "이 기능은 조교용 배포본에서만 사용할 수 있습니다."}

    compiler = _pick_compiler(body)
    if compiler is None:
        return {"error": "C++ 컴파일러를 찾지 못했습니다. "
                         "MinGW-w64(g++) 또는 LLVM(clang++)을 설치한 뒤 다시 실행해 주세요."}

    cases = judge.load_testcases(TESTDATA, project, mode)
    if not cases:
        return {"error": f"{project}/{mode} 테스트케이스가 비어 있습니다."}

    if body.get("single_file"):
        p = Path(body["single_file"]).expanduser()
        if not p.is_file():
            return {"error": f"파일을 찾을 수 없습니다: {p}"}
        info = submission.parse_filename(p)
        sources = [{"path": p, "student_id": info["student_id"] or p.stem,
                    "name": info["name"]}]
        problems = []
    else:
        folder = Path(body["folder"]).expanduser()
        if not folder.is_dir():
            return {"error": f"폴더를 찾을 수 없습니다: {folder}"}
        found, problems = submission.collect(folder, body.get("recursive", False))
        if not found:
            return {"error": "채점할 소스 파일이 없습니다. (.c / .cpp)"}
        sources = found

    jid = uuid.uuid4().hex[:12]
    job = Job(jid)
    job.project, job.mode, job.problems = project, mode, problems
    job.total = len(sources)
    with JOBS_LOCK:
        JOBS[jid] = job

    opts = {"workers": int(body.get("workers", 4)),
            "timeout": float(body.get("timeout", 5.0)),
            "check_leak": bool(body.get("check_leak", True))}
    threading.Thread(target=_run_job,
                     args=(job, sources, cases, compiler, opts),
                     daemon=True).start()
    return {"job": jid, "total": len(sources),
            "cases": [{"name": c.name, "kind": c.kind} for c in cases],
            "compiler": compiler.as_dict()}


def api_job(jid):
    job = JOBS.get(jid)
    if job is None:
        return {"error": "알 수 없는 작업입니다."}
    out = {"state": job.state, "done": job.done, "total": job.total,
           "problems": job.problems, "error": job.error}
    if job.state == "done":
        results = [r.as_dict() for r in job.results]
        if not is_ta():
            # 모의 채점기는 통과/실패만 알려준다. 점수는 응답에서 아예 뺀다.
            for r in results:
                r.pop("score", None)
                r.pop("student_id", None)
        out["results"] = results
    return out


def api_case_input(body):
    """학생이 실패한 케이스의 입력을 복사할 수 있도록 원문을 준다."""
    mode = body["mode"]
    if not is_ta() and mode != "public":
        return {"error": "이 배포본에는 공개 테스트케이스만 들어 있습니다."}
    case = Path(body["case"]).name        # 경로 조작 차단
    path = TESTDATA / body["project"] / mode / f"{case}.in"
    if not path.is_file():
        return {"error": "케이스를 찾을 수 없습니다."}
    return {"stdin": path.read_text(encoding="utf-8")}


def api_export(body):
    if not is_ta():
        return {"error": "이 기능은 조교용 배포본에서만 사용할 수 있습니다."}
    job = JOBS.get(body.get("job", ""))
    if job is None or job.state != "done":
        return {"error": "완료된 채점 결과가 없습니다."}

    stamp = time.strftime("%Y%m%d-%H%M%S")
    out_dir = OUTPUT_ROOT / f"{job.project}-{stamp}"
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []

    report.write_json(job.results, job.project, out_dir / "result.json")
    written.append(str(out_dir / "result.json"))

    report.write_deepthought(job.results, job.project, out_dir / "deepthought")
    written.append(str(out_dir / "deepthought"))

    try:
        report.write_xlsx(job.results, job.project, out_dir / "성적.xlsx")
        written.append(str(out_dir / "성적.xlsx"))
    except RuntimeError as e:
        written.append(f"(xlsx 생략: {e})")

    return {"dir": str(out_dir), "written": written}


def run_picker(kind: str, start: str = "", out_file: str = "") -> int:
    """OS 기본 파일 대화상자를 띄우고 고른 경로를 out_file 에 UTF-8 로 쓴다.

    서버 프로세스가 아니라 --pick 로 실행된 별도 프로세스에서 돈다 (_pick_native).
    GUI 툴킷은 메인 스레드에서만 안전하고, 대화상자가 멈춰도 채점 서버는
    영향을 받지 않아야 하기 때문이다.

    결과를 stdout 이 아니라 파일로 넘기는 이유: Windows 콘솔 코드페이지(CP949)에
    없는 문자가 경로에 있으면 print 가 UnicodeEncodeError 로 죽는다.
    e-class 제출물 경로에는 U+200D(zero-width joiner) 가 섞여 있어서 실제로 터진다.
    """
    try:
        import tkinter
        from tkinter import filedialog
    except Exception as e:                      # tkinter 없는 환경 (헤드리스 리눅스 등)
        print(f"tkinter 를 쓸 수 없습니다: {e}", file=sys.stderr)
        return 2

    initial = start if start and Path(start).is_dir() else str(Path.home())
    root = tkinter.Tk()
    root.withdraw()
    try:
        root.attributes("-topmost", True)       # 브라우저 뒤에 숨지 않게
    except Exception:
        pass
    try:
        if kind == "file":
            picked = filedialog.askopenfilename(
                parent=root, title="소스 파일 선택", initialdir=initial,
                filetypes=[("C/C++ 소스", "*.c *.cpp *.cc *.cxx"),
                           ("모든 파일", "*.*")])
        else:
            picked = filedialog.askdirectory(
                parent=root, title="제출 폴더 선택", initialdir=initial,
                mustexist=True)
    finally:
        root.destroy()

    # askdirectory 는 Windows 에서도 '/' 구분자를 돌려준다
    text = str(Path(picked)) if picked else ""
    if out_file:
        Path(out_file).write_text(text, encoding="utf-8")
    elif text:
        print(text)
    return 0


def _pick_native(kind: str, start: str = "") -> dict:
    """대화상자 프로세스를 띄우고 결과를 돌려준다.

    {"path": ...} | {"canceled": True} | {"error": ..., "fallback": True}
    """
    out_file = Path(tempfile.gettempdir()) / f"fpjudge_pick_{uuid.uuid4().hex}.txt"
    if getattr(sys, "frozen", False):
        cmd = [sys.executable, "--pick", kind]
    else:
        cmd = [sys.executable, str(Path(__file__).resolve()), "--pick", kind]
    cmd += ["--pick-out", str(out_file)]
    if start:
        cmd += ["--pick-start", start]

    kwargs = {}
    if sys.platform.startswith("win"):
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    # 자식과 인코딩을 맞춰 둔다. 콘솔 코드페이지에 맡기면 오류 메시지가 깨진다.
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    # PyInstaller onefile 실행 파일이 자기 자신을 다시 실행할 때는
    # 부모의 압축 해제 정보를 물려주면 안 된다. 물려받은 자식은 이미 풀린
    # 상태로 착각해 엉뚱하게 동작한다.
    for key in list(env):
        if key.startswith("_PYI_") or key == "_MEIPASS2":
            env.pop(key, None)

    try:
        p = subprocess.run(cmd, capture_output=True, text=True,
                           encoding="utf-8", errors="replace",
                           timeout=600, env=env, **kwargs)
    except Exception as e:
        out_file.unlink(missing_ok=True)
        return {"error": f"대화상자를 열 수 없습니다: {e}", "fallback": True}

    try:
        if p.returncode != 0:
            detail = (p.stderr or p.stdout or "").strip()
            print(f"[pick] rc={p.returncode} {detail}", flush=True)
            return {"error": detail or f"대화상자를 열 수 없습니다. (rc={p.returncode})",
                    "fallback": True}

        try:
            picked = out_file.read_text(encoding="utf-8").strip()
        except OSError as e:
            print(f"[pick] 결과 파일을 읽을 수 없습니다: {e}", flush=True)
            return {"error": f"선택 결과를 읽을 수 없습니다: {e}", "fallback": True}

        if not picked:
            return {"canceled": True}
        return {"path": picked}
    finally:
        out_file.unlink(missing_ok=True)


def api_pick(body):
    """OS 기본 파일 대화상자. 실패하면 UI 가 내장 선택기로 넘어간다."""
    kind = "file" if body.get("kind") == "file" else "folder"
    if kind == "folder" and not is_ta():
        return {"error": "이 기능은 조교용 배포본에서만 사용할 수 있습니다."}
    return _pick_native(kind, str(body.get("start") or ""))


def api_browse(body):
    """내장 폴더 선택기 (OS 대화상자를 못 쓸 때의 대비책).

    브라우저는 절대 경로를 알려주지 않으므로 서버가 대신 훑는다.
    """
    raw = body.get("path")
    want_files = bool(body.get("files"))

    if not raw:
        start = Path.home()
    else:
        start = Path(raw).expanduser()

    if not start.is_dir():
        start = Path.home()
    start = start.resolve()

    dirs, files = [], []
    try:
        for entry in sorted(start.iterdir(), key=lambda p: p.name.lower()):
            if entry.name.startswith("."):
                continue
            try:
                if entry.is_dir():
                    dirs.append(entry.name)
                elif want_files and entry.suffix.lower() in submission.SOURCE_EXTS:
                    files.append(entry.name)
            except OSError:
                continue
    except PermissionError:
        return {"error": f"접근 권한이 없습니다: {start}"}

    roots = []
    if sys.platform.startswith("win"):
        import string
        for letter in string.ascii_uppercase:
            d = Path(f"{letter}:\\")
            if d.exists():
                roots.append(str(d))
    else:
        roots = [str(Path.home()), "/"]

    parent = str(start.parent) if start.parent != start else None
    return {"path": str(start), "parent": parent,
            "dirs": dirs, "files": files, "roots": roots,
            "sep": "\\" if sys.platform.startswith("win") else "/"}


ROUTES = {
    "/api/config": lambda b: api_config(),
    "/api/pick": api_pick,
    "/api/browse": api_browse,
    "/api/scan": api_scan,
    "/api/grade": api_grade,
    "/api/case-input": api_case_input,
    "/api/export": api_export,
}


# ------------------------------------------------------------------ HTTP

class Handler(BaseHTTPRequestHandler):
    server_version = "FPJudge"

    def log_message(self, *a):
        pass

    def _send(self, code, body: bytes, ctype="application/json; charset=utf-8"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"))

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/api/config":
            return self._json(api_config())
        if path.startswith("/api/job/"):
            return self._json(api_job(path.rsplit("/", 1)[-1]))

        rel = "index.html" if path in ("/", "") else path.lstrip("/")
        target = (UI_DIR / rel).resolve()
        if not str(target).startswith(str(UI_DIR.resolve())) or not target.is_file():
            return self._send(404, b"not found", "text/plain; charset=utf-8")
        ctype = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype.endswith("javascript"):
            ctype += "; charset=utf-8"
        return self._send(200, target.read_bytes(), ctype)

    def do_POST(self):
        path = urlparse(self.path).path
        handler = ROUTES.get(path)
        if handler is None:
            return self._json({"error": "not found"}, 404)
        try:
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length) or b"{}")
            return self._json(handler(body))
        except Exception:
            return self._json({"error": traceback.format_exc(limit=3)}, 500)


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def main():
    import argparse

    parser = argparse.ArgumentParser(
        prog="FP-Judge", description="화일처리 레포트 채점기")
    parser.add_argument("--port", type=int, default=0,
                        help="사용할 포트. 생략하면 비어 있는 포트를 자동으로 고릅니다.")
    parser.add_argument("--no-browser", action="store_true",
                        help="브라우저를 자동으로 열지 않습니다.")
    parser.add_argument("--edition", choices=("ta", "student"),
                        help="배포본 종류를 강제로 지정합니다. 개발·확인용입니다.")
    parser.add_argument("--diagnose", nargs="?", const="", metavar="제출폴더",
                        help="환경과 컴파일러를 점검합니다. "
                             "제출 폴더를 함께 주면 실제 컴파일까지 확인합니다.")
    # 내부용: 서버가 파일 대화상자를 띄울 때 자기 자신을 이 인자로 다시 실행한다.
    parser.add_argument("--pick", choices=("file", "folder"),
                        help=argparse.SUPPRESS)
    parser.add_argument("--pick-start", default="", help=argparse.SUPPRESS)
    parser.add_argument("--pick-out", default="", help=argparse.SUPPRESS)
    args = parser.parse_args()

    if args.pick:
        sys.exit(run_picker(args.pick, args.pick_start, args.pick_out))

    if args.edition:
        globals()["EDITION"] = args.edition

    if args.diagnose is not None:
        from engine import diagnostics
        argv = ["diagnose"] + ([args.diagnose] if args.diagnose else [])
        sys.exit(diagnostics.run(argv))

    # 콘솔 창에서 진행 상황이 바로 보이도록 버퍼링을 끈다.
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except Exception:
        pass

    if not TESTDATA.is_dir():
        print(f"[!] testdata 폴더가 없습니다: {TESTDATA}", flush=True)
        sys.exit(1)

    port = args.port or free_port()
    url = f"http://127.0.0.1:{port}/"
    try:
        server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    except OSError as e:
        print(f"[!] 포트 {port} 를 열 수 없습니다: {e}", flush=True)
        sys.exit(1)

    title = "FP-Judge (조교용 채점기)" if is_ta() else "FP-Judge (모의 채점기)"
    print(f"{title} 를 시작합니다.", flush=True)
    print(f"  주소: {url}", flush=True)
    if is_ta():
        print(f"  결과 저장 위치: {OUTPUT_ROOT}", flush=True)
    comps = toolchain.detect_compilers()
    if comps:
        print(f"  컴파일러: {comps[0].version or comps[0].cxx}", flush=True)
    else:
        print("  컴파일러를 찾지 못했습니다. MinGW-w64 또는 LLVM 설치가 필요합니다.",
              flush=True)
    print("\n이 창을 닫으면 종료됩니다. (Ctrl+C)", flush=True)

    if not args.no_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n종료합니다.", flush=True)
        server.shutdown()


if __name__ == "__main__":
    main()
