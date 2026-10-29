"""
run.py — 파이프라인 실행 스크립트

    python run.py

graph.py의 LangGraph를 한 번 실행합니다 (수집 → 선별 → 요약 → 검수 → 디스코드 발행).
실행이 끝나면
  · store/metrics.jsonl  에 이번 실행 기록을 한 줄 추가 (소스별 수집량, 선별 순위, 검수 결과, 발행 수, GPT 호출 수)
  · store/last_run.json  에 이번 결과를 자세히 저장 (고른 기사 · 점수 · 요약 · 검수 기록)
"""
import json
import time
from collections import Counter
from datetime import datetime, timezone

from dotenv import load_dotenv

import config
import llm
from graph import build_graph

load_dotenv(config.ROOT / ".env")


def make_metrics(result: dict, started: float) -> dict:
    """이번 실행을 숫자로 요약합니다. → store/metrics.jsonl 한 줄"""
    raw = result.get("raw_articles", [])
    scores = result.get("scores", {})
    candidates = [a["id"] for a in result.get("candidates", [])]
    rule_rank = {i: n for n, i in enumerate(sorted(candidates, key=lambda i: scores[i]["rule"], reverse=True), 1)}
    summaries = result.get("summaries", {})
    retry = result.get("retry_counts", {})
    log = result.get("review_log", [])

    def picked(a):
        s, summ = scores.get(a["id"], {}), summaries.get(a["id"], {})
        return {"title": a["title"], "source": a["source"], "rule_rank": rule_rank.get(a["id"]),
                "rule": s.get("rule"), "gpt": s.get("llm"), "bonus": s.get("bonus"), "final": s.get("final"),
                "based_on": summ.get("based_on"), "summary_tries": retry.get(a["id"], 0)}

    by_source = Counter(a["source"] if a["via"] != "api" else "Tavily(뉴스 API)" for a in raw)
    return {
        "run_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "seconds": round(time.time() - started),
        "collected": {"total": len(raw), "by_via": dict(Counter(a["via"] for a in raw)),
                      "by_source": dict(by_source.most_common())},
        "funnel": {"raw": len(raw), "after_dedupe": len(result.get("articles", [])),
                   "candidates": len(candidates), "published": result.get("published", 0)},
        "selected": [picked(a) for a in result.get("selected", [])],
        "reserve": [picked(a) for a in result.get("reserve", [])],
        "review": {
            "rule_issues": sum(1 for line in log if line.startswith("[규칙 검사]")),
            "judge_issues": sum(1 for line in log if line.startswith("[GPT 심판]")),
            "resummarized": sum(1 for n in retry.values() if n > 1),
            "swapped_out": len(result.get("dropped", [])),
        },
        "body_source": dict(Counter(s.get("based_on") for s in summaries.values())),
        "gpt_calls": dict(llm.CALLS),
        "errors": result.get("errors", []),
    }


def print_report(result: dict, metrics: dict) -> None:
    f = metrics["funnel"]
    print("\n" + "=" * 60)
    print(f"수집 {f['raw']}건 → 중복 제거 {f['after_dedupe']}건 → 후보 {f['candidates']}건 → 발행 {f['published']}건")
    if result.get("review_log"):
        print("\n검수 과정:")
        for line in result["review_log"]:
            print(f"  {line}")
    print("\n오늘 보낸 기사:")
    for i, art in enumerate(result.get("selected", []), 1):
        s = result.get("summaries", {}).get(art["id"])
        if s:
            print(f"  {i}. {s['headline']}  ({s['source']})")
    print(f"\nGPT 호출: {metrics['gpt_calls']} · 걸린 시간: {metrics['seconds']}초")
    if result.get("errors"):
        print("\n문제 · 건너뛴 것:")
        for e in result["errors"]:
            print(f"  · {e}")
    print("=" * 60)


def main() -> None:
    print("AI 뉴스레터 만들기 시작...")
    started = time.time()
    result = build_graph().invoke(
        {"raw_articles": [], "errors": [], "review_log": [], "dropped": []},
        config={"recursion_limit": 80},   # 검수에서 다시 요약 · 교체가 여러 번 돌아도 멈추지 않게
    )
    metrics = make_metrics(result, started)
    print_report(result, metrics)

    config.STORE.mkdir(exist_ok=True)
    with open(config.METRICS_FILE, "a", encoding="utf-8") as fp:
        fp.write(json.dumps(metrics, ensure_ascii=False) + "\n")
    keep = ("selected", "reserve", "scores", "summaries", "retry_counts", "review_log", "dropped",
            "published", "errors")
    config.LAST_RUN_FILE.write_text(json.dumps({k: result.get(k) for k in keep}, ensure_ascii=False, indent=2),
                                    encoding="utf-8")
    print(f"기록 저장: {config.METRICS_FILE.relative_to(config.ROOT)}, {config.LAST_RUN_FILE.relative_to(config.ROOT)}")


if __name__ == "__main__":
    main()
