"""
State: 모든 노드가 함께 읽고 쓰는 '공용 메모장'의 모양을 정합니다.
"""
import operator
from typing import Annotated, TypedDict


class Article(TypedDict):
    id: str                  # 기사 고유 번호 (주소로 만듦)
    title: str
    url: str
    source: str              # 매체 이름 (예: "TechCrunch AI")
    via: str                 # 어떻게 가져왔나: "rss" | "api" | "scrape"
    published_at: str        # 발행 시각 (UTC, ISO 형식). 모르면 ""
    snippet: str             # 짧은 설명 (최대 500자)
    content: str             # 본문. 비어 있으면 나중에 fetch_fulltext가 채움
    mentioned_by: list[str]  # 이 소식을 다룬 소스 목록 (여러 곳일수록 중요한 뉴스)


class NewsState(TypedDict, total=False):
    # ① 수집 — 노드 3개가 동시에 넣기 때문에 '덮어쓰기' 대신 '합치기'(operator.add) 규칙
    raw_articles: Annotated[list[Article], operator.add]
    errors: Annotated[list[str], operator.add]   # 실패하거나 건너뛴 소스 기록
    articles: list[Article]                      # 중복 제거 후

    # ② 선별
    candidates: list[Article]      # 규칙 점수 상위 20건
    selected: list[Article]        # 최종 5건
    reserve: list[Article]         # 예비 2건
    scores: dict[str, dict]        # {기사id: {"rule": 점수, "rule_note": 내역, "llm": 점수, "reason": 이유}}

    # ③ 요약
    summaries: dict[str, dict]     # {기사id: {"headline", "one_liner", "key_points", "why_it_matters",
                                   #           "url", "source", "based_on"}}
    retry_counts: dict[str, int]   # {기사id: 요약한 횟수}

    # ④ 검수
    review_issues: dict[str, list[str]]   # {기사id: ["원문에서 '30%'에 해당하는 숫자를 찾을 수 없어요", ...]}
    review_hints: dict[str, list[str]]    # {기사id: [GPT 심판이 특히 확인할 것]} (규칙 검사가 확신하지 못한 것)
    review_passed: dict[str, str]         # {기사id: 통과한 요약의 지문} → 요약이 그대로면 다시 심판하지 않음
    review_log: Annotated[list[str], operator.add]   # 검수 과정 기록 (결과 화면에 보여 줌)
    dropped: Annotated[list[str], operator.add]      # 검수를 끝내 통과 못 해 뺀 기사 제목

    # ⑤ 발행
    published: int                 # 디스코드로 보낸 기사 수
