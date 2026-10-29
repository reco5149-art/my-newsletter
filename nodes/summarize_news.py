"""
③ 요약 단계의 노드 2개
  fetch_fulltext : 고른 7건(발행 5 + 예비 2)의 본문 가져오기
                   직접 가져오기 → 안 되면 Tavily로 한 번 더 → 그래도 안 되면 짧은 설명만 사용
  summarize      : 기사마다 GPT에게 정해진 칸(제목 · 한 줄 요약 · 핵심 · 왜 중요한가)으로 요약 받기
                   ④ 검수에서 문제가 나온 기사만 다시 요약할 수 있게 만들어 둠
"""
import json
import os

from bs4 import BeautifulSoup
from pydantic import BaseModel, Field

import config
from llm import LLMError, ask_structured
from state import Article, NewsState
from utils import extract_main_text, fetch, friendly_error, normalize_url


# ─────────────────────────────────────────────────────
# 노드 7. 본문 가져오기
# ─────────────────────────────────────────────────────
def _tavily_extract(urls: list[str]) -> dict[str, str]:
    """Tavily에게 본문을 대신 가져와 달라고 합니다. → {정리된 주소: 본문}"""
    api_key = os.getenv("TAVILY_API_KEY")
    if not (urls and api_key and config.USE_TAVILY_EXTRACT):
        return {}
    from tavily import TavilyClient
    try:
        response = TavilyClient(api_key=api_key).extract(urls=urls, format="text")
    except Exception as e:
        print(f"  [본문] Tavily 실패: {friendly_error(e)}")
        return {}
    return {normalize_url(r["url"]): (r.get("raw_content") or "") for r in response.get("results", [])}


def fetch_fulltext(state: NewsState) -> dict:
    selected, reserve = state.get("selected", []), state.get("reserve", [])
    found: dict[str, tuple[str, str]] = {}     # {기사id: (본문, 가져온 방법)}
    retry_with_tavily = []

    for art in selected + reserve:
        if len(art["content"]) >= config.MIN_CONTENT_CHARS:
            continue                           # 수집할 때 이미 본문을 받아 둔 기사
        try:
            text = extract_main_text(BeautifulSoup(fetch(art["url"]).text, "html.parser"))
        except Exception:
            text = ""
        if len(text) >= config.MIN_CONTENT_CHARS:
            found[art["id"]] = (text, "직접")
        else:
            retry_with_tavily.append(art)

    by_url = _tavily_extract([a["url"] for a in retry_with_tavily])
    for art in retry_with_tavily:
        text = by_url.get(normalize_url(art["url"]), "")
        if len(text) >= config.MIN_CONTENT_CHARS:
            found[art["id"]] = (text[:20000], "Tavily")

    def update(arts: list[Article]) -> list[Article]:
        result = []
        for a in arts:
            if a["id"] in found:
                a = {**a, "content": found[a["id"]][0]}
            how = (found[a["id"]][1] if a["id"] in found
                   else "수집 때 받음" if len(a["content"]) >= config.MIN_CONTENT_CHARS
                   else "못 가져옴 → 짧은 설명으로 요약")
            print(f"  [본문] {a['title'][:40]}: {how} ({len(a['content']):,}자)")
            result.append(a)
        return result

    return {"selected": update(selected), "reserve": update(reserve)}


# ─────────────────────────────────────────────────────
# 노드 8. 요약
# ─────────────────────────────────────────────────────
class SummaryOut(BaseModel):
    headline: str = Field(description="한국어 제목, 40자 이내. 과장이나 낚시 없이 사실만")
    one_liner: str = Field(description="무슨 일이 있었는지 한국어 한 문장")
    key_points: list[str] = Field(
        description="원문에 있는 핵심 사실 1~3개, 각각 한국어 한 문장. 원문 정보가 적으면 적게")
    why_it_matters: str = Field(description="독자에게 왜 중요한지 한국어 한두 문장")


SYSTEM_PROMPT = """너는 매일 아침 AI 뉴스 브리핑을 쓰는 기자야.
독자: {reader}

주어진 기사 원문만 보고 한국어로 요약해.

규칙
- 원문에 없는 내용은 절대 쓰지 마. 확실하지 않으면 빼.
- 숫자 · 금액 · 날짜는 원문의 값을 정확히 옮겨. 영어 단위를 한국어로 바꿀 때는 값이 똑같아야 해
  (예: $2.9 billion → 29억 달러, $620 million → 6억2000만 달러). 환율 계산(달러 → 원)은 하지 마.
- 제품 · 모델 이름은 원문 표기 그대로 써 (예: Gemini 4 Carbon). 회사 이름은 한국어로 써도 돼 (예: 구글, 엔비디아).
- headline은 40자 이내.
- key_points는 원문에 있는 사실만 1~3개. 원문이 짧으면 1~2개만 써도 돼. 억지로 채우지 마.
- why_it_matters는 원문 내용에 근거해서 써. 앞으로의 전망은 '~할 수 있다'처럼 조심스럽게."""


def _user_message(art: Article, based_on: str, previous: dict | None, issues: list[str]) -> str:
    body = art["content"][:config.MAX_CONTENT_CHARS] if based_on == "본문" else art["snippet"]
    note = "" if based_on == "본문" else " (본문을 가져오지 못해 짧은 설명만 있음. 이 안에 있는 내용만 써)"
    message = (f"제목: {art['title']}\n출처: {art['source']}\n발행: {art['published_at'][:16]}\n\n"
               f"원문{note}:\n{body}")
    if previous and issues:
        message += ("\n\n---\n지난번 요약:\n" + json.dumps(previous, ensure_ascii=False, indent=2)
                    + "\n\n지난번 요약에서 발견된 문제 (꼭 고쳐서 다시 써):\n"
                    + "\n".join(f"- {i}" for i in issues))
    return message


def summarize(state: NewsState) -> dict:
    summaries = dict(state.get("summaries", {}))
    retry_counts = dict(state.get("retry_counts", {}))
    issues_by_id = state.get("review_issues", {})
    errors = []

    for art in state.get("selected", []):
        issues = issues_by_id.get(art["id"], [])
        if art["id"] in summaries and not issues:
            continue                           # 이미 요약했고 검수에서 문제도 없던 기사

        based_on = "본문" if len(art["content"]) >= config.MIN_CONTENT_CHARS else "설명만"
        previous = summaries.get(art["id"])
        try:
            out, model = ask_structured(
                SYSTEM_PROMPT.format(reader=config.READER),
                _user_message(art, based_on, previous, issues),
                SummaryOut,
            )
        except LLMError as e:
            errors.append(f"[요약] {art['title'][:30]}: {e}")
            print(f"  [요약] 실패: {art['title'][:40]} ({e})")
            continue

        summaries[art["id"]] = {
            "headline": out.headline.strip(),
            "one_liner": out.one_liner.strip(),
            "key_points": [p.strip() for p in out.key_points if p.strip()][:3],
            "why_it_matters": out.why_it_matters.strip(),
            # 링크 · 출처는 GPT가 아니라 코드가 넣음 → 링크가 틀릴 일이 없음
            "url": art["url"],
            "source": art["source"],
            "title": art["title"],
            "based_on": based_on,
            "model": model,
        }
        retry_counts[art["id"]] = retry_counts.get(art["id"], 0) + 1
        again = " (다시 요약)" if issues else ""
        print(f"  [요약] {out.headline[:40]}{again}")

    return {"summaries": summaries, "retry_counts": retry_counts, "errors": errors}
