"""
③ 요약 단계만 따로 실행해 보는 스크립트

    python stages/run_summarize.py

select_result.json(② 선별 결과)을 읽어 본문을 가져오고 기사마다 요약한 뒤,
summary_result.json으로 저장합니다. (다음 단계인 '검수'의 재료)
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent      # 프로젝트 폴더 (stages/의 한 단계 위)
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)                                     # 결과 파일(*_result.json)을 프로젝트 폴더에 읽고 씀

import json

from dotenv import load_dotenv
from langgraph.graph import END, START, StateGraph

from state import NewsState
from nodes.summarize_news import fetch_fulltext, summarize

load_dotenv()


def build_summarize_graph():
    graph = StateGraph(NewsState)
    graph.add_node("fetch_fulltext", fetch_fulltext)
    graph.add_node("summarize", summarize)
    graph.add_edge(START, "fetch_fulltext")
    graph.add_edge("fetch_fulltext", "summarize")
    graph.add_edge("summarize", END)
    return graph.compile()


def print_preview(result: dict) -> None:
    """디스코드에 올라갈 모습과 비슷하게 보여 줍니다."""
    print("\n" + "=" * 60)
    print("오늘의 AI 뉴스 브리핑 (미리보기)")
    for i, art in enumerate(result["selected"], 1):
        s = result["summaries"].get(art["id"])
        print("-" * 60)
        if not s:
            print(f"{i}. (요약 실패) {art['title']}")
            continue
        print(f"{i}. {s['headline']}")
        print(f"   {s['one_liner']}")
        for p in s["key_points"]:
            print(f"   • {p}")
        print(f"   왜 중요한가: {s['why_it_matters']}")
        basis = "본문을 읽고 요약" if s["based_on"] == "본문" else "짧은 설명만 보고 요약"
        print(f"   출처: {s['source']} · {basis}")
        print(f"   {s['url']}")
    if result.get("errors"):
        print("-" * 60)
        print("문제:")
        for e in result["errors"]:
            print(f"  · {e}")
    print("=" * 60)


if __name__ == "__main__":
    source = Path("select_result.json")
    if not source.exists():
        sys.exit("select_result.json이 없어요. 먼저 python stages/run_select.py로 선별해 주세요.")
    picked = json.loads(source.read_text(encoding="utf-8"))

    print(f"요약 시작... (발행 {len(picked['selected'])}건 + 예비 {len(picked['reserve'])}건)")
    result = build_summarize_graph().invoke({
        "selected": picked["selected"], "reserve": picked["reserve"],
        "scores": picked.get("scores", {}), "errors": [],
    })
    print_preview(result)

    out = {key: result.get(key) for key in ("selected", "reserve", "summaries", "retry_counts")}
    Path("summary_result.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print("저장 완료: summary_result.json")
