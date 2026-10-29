"""
② 선별 단계만 따로 실행해 보는 스크립트

    python stages/run_select.py

collect_result.json(① 수집 결과)을 읽어서 5건 + 예비 2건을 고르고,
select_result.json으로 저장합니다. (다음 단계인 '요약'의 재료)
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

from nodes.select_news import llm_score, rule_filter
from state import NewsState

load_dotenv()


def build_select_graph():
    graph = StateGraph(NewsState)
    graph.add_node("rule_filter", rule_filter)
    graph.add_node("llm_score", llm_score)
    graph.add_edge(START, "rule_filter")
    graph.add_edge("rule_filter", "llm_score")
    graph.add_edge("llm_score", END)
    return graph.compile()


def print_report(result: dict) -> None:
    scores = result["scores"]

    def line(a):
        s = scores[a["id"]]
        gpt = f"GPT {s['llm']}" if "llm" in s else "GPT 없음"
        bonus = f" + 여러 곳 보도 {s['bonus']}" if s.get("bonus") else ""
        return f"[최종 {s['final']}점 = {gpt}{bonus} · 규칙 {s['rule']}점] {a['title'][:50]}  ({a['source']})"

    print("\n" + "=" * 60)
    print("발행할 5건:")
    for i, a in enumerate(result["selected"], 1):
        print(f"  {i}. {line(a)}")
        if scores[a["id"]].get("reason"):
            print(f"     → {scores[a['id']]['reason']}")
    print("\n예비 2건:")
    for a in result["reserve"]:
        print(f"  · {line(a)}")

    dups = [a for a in result["candidates"] if scores[a["id"]].get("duplicate_of")]
    if dups:
        print("\n같은 소식이라 뺀 기사:")
        for a in dups:
            print(f"  · {a['title'][:60]}")

    print("\n규칙 점수 상위 20건 (GPT에게 보낸 후보):")
    for a in result["candidates"]:
        s = scores[a["id"]]
        print(f"  {s['rule']:>3}점 [{s['rule_note']}] {a['title'][:50]}")

    if result.get("errors"):
        print("\n문제:")
        for e in result["errors"]:
            print(f"  · {e}")
    print("=" * 60)


if __name__ == "__main__":
    source = Path("collect_result.json")
    if not source.exists():
        sys.exit("collect_result.json이 없어요. 먼저 python stages/run_collect.py로 수집해 주세요.")
    articles = json.loads(source.read_text(encoding="utf-8"))

    print(f"선별 시작... (수집된 기사 {len(articles)}건)")
    result = build_select_graph().invoke({"articles": articles, "errors": []})
    print_report(result)

    out = {
        "selected": result["selected"],
        "reserve": result["reserve"],
        "scores": {a["id"]: result["scores"][a["id"]] for a in result["candidates"]},
    }
    Path("select_result.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print("저장 완료: select_result.json")
