"""
④ 검수 단계만 따로 실행해 보는 스크립트

    python stages/run_review.py

summary_result.json(③ 요약 결과)을 읽어 검수하고, 문제가 있으면 다시 요약하거나 예비 기사로 바꿉니다.
모두 통과하면 review_result.json으로 저장합니다. (다음 단계인 '발행'의 재료)

    rule_check → llm_judge → 문제 없음 → 끝
                     ↑          └ 문제 있음 → swap_failed → summarize ┐
                     └────────────────────────────────────────────────┘
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

from nodes.review_news import llm_judge, route, rule_check, swap_failed
from state import NewsState
from nodes.summarize_news import summarize

load_dotenv()


def build_review_graph():
    graph = StateGraph(NewsState)
    graph.add_node("rule_check", rule_check)
    graph.add_node("llm_judge", llm_judge)
    graph.add_node("swap_failed", swap_failed)
    graph.add_node("summarize", summarize)

    graph.add_edge(START, "rule_check")
    graph.add_edge("rule_check", "llm_judge")
    graph.add_conditional_edges("llm_judge", route, {"done": END, "fix": "swap_failed"})
    graph.add_edge("swap_failed", "summarize")
    graph.add_edge("summarize", "rule_check")      # 다시 요약한 것을 다시 검수
    return graph.compile()


def print_report(result: dict) -> None:
    print("\n" + "=" * 60)
    print("검수 과정:")
    for line in result.get("review_log") or ["  (모든 요약이 한 번에 통과)"]:
        print(f"  {line}")
    if result.get("dropped"):
        print("\n검수를 끝내 통과하지 못해 뺀 기사:")
        for t in result["dropped"]:
            print(f"  · {t}")

    print(f"\n발행할 기사 {len(result['selected'])}건 (모두 검수 통과):")
    for i, art in enumerate(result["selected"], 1):
        s = result["summaries"][art["id"]]
        tries = result.get("retry_counts", {}).get(art["id"], 1)
        again = f" · {tries}번째 요약에서 통과" if tries > 1 else ""
        print(f"  {i}. {s['headline']}  ({s['source']}{again})")

    if result.get("errors"):
        print("\n문제:")
        for e in result["errors"]:
            print(f"  · {e}")
    print("=" * 60)


if __name__ == "__main__":
    source = Path("summary_result.json")
    if not source.exists():
        sys.exit("summary_result.json이 없어요. 먼저 python stages/run_summarize.py로 요약해 주세요.")
    data = json.loads(source.read_text(encoding="utf-8"))

    print(f"검수 시작... (요약 {len(data['summaries'])}건)")
    result = build_review_graph().invoke(
        {"selected": data["selected"], "reserve": data["reserve"], "summaries": data["summaries"],
         "retry_counts": data.get("retry_counts", {}), "errors": [], "review_log": [], "dropped": []},
        config={"recursion_limit": 60},   # 다시 요약 · 교체가 여러 번 돌아도 멈추지 않게 여유를 줌
    )
    print_report(result)

    out = {key: result.get(key) for key in ("selected", "reserve", "summaries", "retry_counts",
                                            "review_log", "dropped")}
    Path("review_result.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print("저장 완료: review_result.json")
