#!/usr/bin/env python3
"""FP-Judge 진단 도구.

채점이 안 될 때 이걸 먼저 돌린다.

    python diagnose.py                 환경만 확인
    python diagnose.py <제출폴더>       파일명 파싱 + 실제 컴파일까지 확인

실행 파일을 쓰고 있다면 같은 일을 이렇게 할 수 있다.

    FP-Judge.exe --diagnose
    FP-Judge.exe --diagnose <제출폴더>
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from engine import diagnostics

sys.exit(diagnostics.run(sys.argv))
