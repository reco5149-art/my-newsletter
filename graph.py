"""
graph.py — LangGraph 에이전트 워크플로우 (노드를 어떤 순서로 이을지)

  ① 수집   collect_rss ─┐
           collect_api ─┼→ dedupe              (세 갈래 동시 실행 → 모두 끝나면 중복 제거)
           collect_scrape┘
  ② 선별   → rule_filter (예선: 규칙 점수) → llm_score (본선: GPT 채점)
  ③ 요약   → fetch_fulltext → summarize
  ④ 검수   → rule_check → llm_judge ─ done → ⑤ publish_discord → END
                  ↑                  └ fix  → swap_failed → summarize ┐
                  └─────────────────────────────────────────────────────┘

각 노드가 하는 일은 nodes/ 폴더에, 노드들이 주고받는 데이터(State)는 state.py에 있습니다.
"""
from langgraph.graph import END, START, StateGraph

from nodes.collect import collect_api, collect_rss, collect_scrape, dedupe
from nodes.publish_news import publish_discord
from nodes.review_news import llm_judge, route, rule_check, swap_failed
from nodes.select_news import llm_score, rule_filter
from nodes.summarize_news import fetch_fulltext, summarize
from state import NewsState

NODES = [
    # ① 수집
    ("collect_rss", collect_rss), ("collect_api", collect_api), ("collect_scrape", collect_scrape),
    ("dedupe", dedupe),
    # ② 선별
    ("rule_filter", rule_filter), ("llm_score", llm_score),
    # ③ 요약
    ("fetch_fulltext", fetch_fulltext), ("summarize", summarize),
    # ④ 검수
    ("rule_check", rule_check), ("llm_judge", llm_judge), ("swap_failed", swap_failed),
    # ⑤ 발행
    ("publish_discord", publish_discord),
]


def build_graph():
    g = StateGraph(NewsState)
    for name, node in NODES:
        g.add_node(name, node)

    # ① 수집: 세 갈래 동시 출발 → 셋 다 끝나야 중복 제거
    for collector in ("collect_rss", "collect_api", "collect_scrape"):
        g.add_edge(START, collector)
    g.add_edge(["collect_rss", "collect_api", "collect_scrape"], "dedupe")

    # ② 선별 → ③ 요약 → ④ 검수
    g.add_edge("dedupe", "rule_filter")
    g.add_edge("rule_filter", "llm_score")
    g.add_edge("llm_score", "fetch_fulltext")
    g.add_edge("fetch_fulltext", "summarize")
    g.add_edge("summarize", "rule_check")
    g.add_edge("rule_check", "llm_judge")

    # ④ 검수 결과에 따라 갈림길(조건부 엣지): 문제 없으면 발행, 있으면 교체 · 다시 요약 → 다시 검수
    g.add_conditional_edges("llm_judge", route, {"done": "publish_discord", "fix": "swap_failed"})
    g.add_edge("swap_failed", "summarize")

    # ⑤ 발행
    g.add_edge("publish_discord", END)
    return g.compile()


if __name__ == "__main__":
    # python graph.py → 그래프 모양을 Mermaid 코드로 출력 (REPORT.md의 구조도와 비교해 볼 수 있음)
    print(build_graph().get_graph().draw_mermaid())
