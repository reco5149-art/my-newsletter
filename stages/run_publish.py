"""
⑤ 발행 단계만 따로 실행해 보는 스크립트

    python stages/run_publish.py          → review_result.json(④ 검수 결과)을 디스코드로 보냄
    (보내지 않고 내용만 보려면)  set DRY_RUN=1  후 python run_publish.py   ※ Mac은 DRY_RUN=1 python run_publish.py
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent      # 프로젝트 폴더 (stages/의 한 단계 위)
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)                                     # 결과 파일(*_result.json)을 프로젝트 폴더에 읽고 씀

import json

from dotenv import load_dotenv

from nodes.publish_news import publish_discord

load_dotenv()

if __name__ == "__main__":
    source = Path("review_result.json")
    if not source.exists():
        sys.exit("review_result.json이 없어요. 먼저 python stages/run_review.py로 검수해 주세요.")
    data = json.loads(source.read_text(encoding="utf-8"))
    result = publish_discord(data)
    for e in result.get("errors", []):
        print(f"  · {e}")
    sys.exit(0 if not result.get("errors") else 1)
