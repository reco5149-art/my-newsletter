"""
① 수집 단계만 따로 실행해 보는 스크립트

    python stages/run_collect.py

결과는 화면에 요약해서 보여 주고, collect_result.json 파일로도 저장합니다.
(다음 단계인 '선별'을 만들 때 이 파일을 재료로 씁니다)
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent      # 프로젝트 폴더 (stages/의 한 단계 위)
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)                                     # 결과 파일(*_result.json)을 프로젝트 폴더에 읽고 씀

import json
from collections import Counter

from dotenv import load_dotenv
from langgraph.graph import END, START, StateGraph

from nodes.collect import collect_api, collect_rss, collect_scrape, dedupe
from state import NewsState

load_dotenv()  # .env 파일의 키를 불러옴


def build_collect_graph():
    graph = StateGraph(NewsState)

    graph.add_node("collect_rss", collect_rss)
    graph.add_node("collect_api", collect_api)
    graph.add_node("collect_scrape", collect_scrape)
    graph.add_node("dedupe", dedupe)

    # START에서 세 갈래로 동시에 출발
    graph.add_edge(START, "collect_rss")
    graph.add_edge(START, "collect_api")
    graph.add_edge(START, "collect_scrape")
    # 세 노드가 모두 끝나야 dedupe 실행
    graph.add_edge(["collect_rss", "collect_api", "collect_scrape"], "dedupe")
    graph.add_edge("dedupe", END)

    return graph.compile()


def print_report(result: dict) -> None:
    raw, articles = result.get("raw_articles", []), result.get("articles", [])
    by_via = Counter(a["via"] for a in raw)

    print("\n" + "=" * 50)
    print(f"모은 기사: {len(raw)}건  (RSS {by_via['rss']} / API {by_via['api']} / 스크래핑 {by_via['scrape']})")
    print(f"중복 제거 후: {len(articles)}건")
    if not raw:
        print("\n⚠ 기사를 하나도 못 모았어요. 인터넷 연결을 확인하고 아래 실패 목록을 보세요.")

    multi = sorted((a for a in articles if len(a["mentioned_by"]) > 1),
                   key=lambda a: len(a["mentioned_by"]), reverse=True)[:5]
    if multi:
        print("\n여러 곳에서 다룬 소식:")
        for a in multi:
            print(f"  · ({len(a['mentioned_by'])}곳) {a['title'][:60]}")

    print("\n최신 기사 5건:")
    for a in articles[:5]:
        print(f"  · [{a['source']}] {a['title'][:60]}")

    if result.get("errors"):
        print("\n실패하거나 건너뛴 소스:")
        for e in result["errors"]:
            print(f"  · {e}")
    print("=" * 50)


if __name__ == "__main__":
    print("수집 시작...")
    result = build_collect_graph().invoke({"raw_articles": [], "errors": []})
    print_report(result)

    with open("collect_result.json", "w", encoding="utf-8") as f:
        json.dump(result.get("articles", []), f, ensure_ascii=False, indent=2)
    print("저장 완료: collect_result.json")
