"""Submission discovery and filename parsing.

Filenames downloaded from e-class are inconsistent and contain invisible
characters such as U+200D (zero-width joiner). The original grader used
`cut -d'_' -f1`, which could not tell "ID_name" from "name_ID", so names ended
up in the student ID column. Filenames are parsed structurally with regexes here.

Supported formats (the ID is always 8 digits; XXXX stands for digits):
    2026XXXX_홍길동_AVL - 홍길동(학부생-소프트웨어전공).cpp
    홍길동_2026XXXX_AVL.cpp
    2026XXXX_홍길동_BT_-__홍길동_학부생-소프트웨어전공_.cpp
    2026XXXX_HONG_BST - 홍길동(학부생-소프트웨어학부).cpp
"""
from __future__ import annotations

import re
import unicodedata
from pathlib import Path

SOURCE_EXTS = {".c", ".cpp", ".cc", ".cxx"}

# Invisible characters (zero-width joiner/non-joiner, BOM, direction marks)
INVISIBLE = dict.fromkeys(
    [0x200B, 0x200C, 0x200D, 0x200E, 0x200F, 0xFEFF, 0x2060], None)

STUDENT_ID_RE = re.compile(r"(?<!\d)(\d{8})(?!\d)")
PROJECT_TOKENS = {"BST", "AVL", "BT", "BTREE", "B", "TREE", "CPP", "C"}


def clean(text: str) -> str:
    """Unicode NFC normalisation and removal of invisible characters."""
    return unicodedata.normalize("NFC", text).translate(INVISIBLE).strip()


def _name_before_affiliation(stem: str) -> str | None:
    """Take the token right before '(학부생' or '_학부생' as the name."""
    m = re.search(r"([^\s\-_()]+)\s*[(_]\s*(?:학부생|대학원생|일반)", stem)
    return m.group(1) if m else None


def _name_adjacent_to_id(stem: str, sid: str) -> str | None:
    """Pick a name-like token immediately after or before the student ID."""
    parts = [p for p in re.split(r"[_\s\-]+", stem) if p]
    for i, p in enumerate(parts):
        if p == sid:
            for j in (i + 1, i - 1):
                if 0 <= j < len(parts):
                    cand = parts[j].strip()
                    if not cand or cand.isdigit():
                        continue
                    if cand.upper() in PROJECT_TOKENS:
                        continue
                    return cand
    return None


def parse_filename(path: Path) -> dict:
    stem = clean(path.stem)
    m = STUDENT_ID_RE.search(stem)
    sid = m.group(1) if m else ""

    name = _name_before_affiliation(stem)
    if not name and sid:
        name = _name_adjacent_to_id(stem, sid)
    if not name:
        # Last resort: the first token that is neither a number nor a project keyword
        for p in re.split(r"[_\s\-()]+", stem):
            p = p.strip()
            if p and not p.isdigit() and p.upper() not in PROJECT_TOKENS:
                name = p
                break

    return {
        "path": path,
        "student_id": sid,
        "name": clean(name or ""),
        "filename": path.name,
        "mtime": path.stat().st_mtime,
        "parsed_ok": bool(sid),
    }


def collect(folder: Path, recursive: bool = False) -> tuple[list[dict], list[dict]]:
    """Scan a submission folder and return (gradable files, problem files).

    When the same student ID submitted more than once, only the file with the
    latest modification time is kept.
    """
    folder = Path(folder)
    it = folder.rglob("*") if recursive else folder.glob("*")
    files = [p for p in it if p.is_file() and p.suffix.lower() in SOURCE_EXTS]

    parsed = [parse_filename(p) for p in files]
    good = [x for x in parsed if x["parsed_ok"]]
    problems = [{"filename": x["filename"], "reason": "학번(8자리)을 찾을 수 없습니다"}
                for x in parsed if not x["parsed_ok"]]

    # Duplicate submissions: keep only the latest
    latest: dict[str, dict] = {}
    superseded = []
    for x in sorted(good, key=lambda d: d["mtime"]):
        prev = latest.get(x["student_id"])
        if prev is not None:
            superseded.append({"filename": prev["filename"],
                               "reason": f"중복 제출 — 더 최근 파일이 있어 제외 ({x['filename']})"})
        latest[x["student_id"]] = x

    return sorted(latest.values(), key=lambda d: d["student_id"]), problems + superseded
