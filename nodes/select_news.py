"""
② 선별 단계의 노드 2개
  rule_filter : 규칙 점수로 100건 → 20건 (무료 · 빠름)
  llm_score   : GPT가 20건을 한 번에 채점 → 5건 + 예비 2건

GPT에게 100건을 다 보내면 비용이 들고 느리므로, 규칙으로 먼저 줄인 뒤 20건만 보냅니다.
"""
import re
from collections import Counter
from datetime import datetime, timedelta, timezone

from pydantic import BaseModel, Field

import config
from llm import LLMError, ask_structured
from nodes.publish_news import load_history
from state import Article, NewsState
from utils import domain_of, normalize_url


# ─────────────────────────────────────────────────────
# 노드 5. 규칙 점수
# ─────────────────────────────────────────────────────
def _has(text: str, word: str) -> bool:
    """영어는 낱말 단위로(ai가 said에 걸리지 않게), 한국어는 글자 그대로 찾습니다."""
    if word.isascii():
        return re.search(rf"(?<![a-z0-9]){re.escape(word)}(?:s|es|ed|d|ing)?(?![a-z0-9])", text) is not None
    return word in text


def _source_kind(art: Article) -> str:
    if art["source"] in config.OFFICIAL_SOURCES:
        return "official"
    if art["via"] in ("rss", "scrape"):
        return "rss"
    domain = domain_of(art["url"])
    if any(domain == d or domain.endswith("." + d) for d in config.TRUSTED_DOMAINS):
        return "trusted"
    return "unknown"


def rule_score(art: Article, now: datetime) -> tuple[int, str]:
    """기사 하나의 규칙 점수와 점수 내역을 돌려줍니다."""
    text = f"{art['title']} {art['snippet']}".lower()
    parts = {}

    kind = _source_kind(art)
    parts["출처"] = config.SOURCE_POINTS[kind]

    hits = [name for name, words in config.KEYWORD_GROUPS.items() if any(_has(text, w) for w in words)]
    parts["키워드"] = len(hits)

    parts["여러곳"] = min(config.COVERAGE_POINTS_MAX,                # 다른 곳도 다뤘으면 1곳당 +2
                       config.COVERAGE_POINTS_PER_SOURCE * (len(art["mentioned_by"]) - 1))

    if art["published_at"]:
        hours = (now - datetime.fromisoformat(art["published_at"])).total_seconds() / 3600
        parts["최신"] = (config.FRESHNESS["within_6h"] if hours <= 6
                        else config.FRESHNESS["within_12h"] if hours <= 12 else 0)

    if not any(_has(text, w) for w in config.AI_WORDS):
        parts["AI아님"] = config.NOT_AI_PENALTY
    if any(_has(text, w) for w in config.SPAM_WORDS):
        parts["광고성"] = config.SPAM_PENALTY

    total = sum(parts.values())
    note = " ".join(f"{k}{v:+d}" for k, v in parts.items() if v)
    return total, note


# 같은 소식 묶기에 쓰지 않을 흔한 낱말 (이 낱말만 겹쳐서는 같은 소식으로 보지 않음)
COMMON_WORDS = set("""a an the of to in on for and or with by at from as is are be its it this that
new ai model models launch launches launched release releases released announce announces says
said report reports company companies tool tools agent agents open source first latest how why
what now just more than after over into about up out""".split())


def _story_keys(title: str) -> set[str]:
    """제목에서 흔한 낱말을 빼고, 이어진 세 낱말 묶음을 만듭니다. 예) 'gemini 4 carbon'"""
    words = [w for w in re.findall(r"[a-z0-9가-힣]+", title.lower()) if w not in COMMON_WORDS]
    return {" ".join(words[i:i + 3]) for i in range(len(words) - 2)}


def group_same_story(articles: list[Article], now: datetime) -> list[Article]:
    """제목이 달라도 'gemini 4 carbon'처럼 세 낱말이 겹치면 같은 소식으로 묶고,
    묶음마다 점수가 가장 높은 기사 하나만 남깁니다. 남은 기사의 mentioned_by에 다른 곳을 합칩니다.
    """
    parent = list(range(len(articles)))

    def root(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    owner = {}
    for i, art in enumerate(articles):
        for key in _story_keys(art["title"]):
            if key in owner:
                parent[root(i)] = root(owner[key])
            else:
                owner[key] = i

    groups: dict[int, list[Article]] = {}
    for i, art in enumerate(articles):
        groups.setdefault(root(i), []).append(art)

    result = []
    for members in groups.values():
        best = max(members, key=lambda a: (rule_score(a, now)[0], a["source"] in config.OFFICIAL_SOURCES))
        merged = {**best, "mentioned_by": list(best["mentioned_by"])}
        for other in members:
            for name in other["mentioned_by"]:
                if name not in merged["mentioned_by"]:
                    merged["mentioned_by"].append(name)
        result.append(merged)
    return result


def drop_already_sent(articles: list[Article], now: datetime) -> list[Article]:
    """최근에 보낸 기사(같은 주소), 이틀 안에 보낸 소식(같은 세 낱말 묶음)은 뺍니다."""
    history = load_history()
    if not history:
        return articles
    sent_urls = {h["url"] for h in history}
    recent = [h for h in history if now - datetime.fromisoformat(h["sent_at"]) <= timedelta(days=config.SAME_STORY_DAYS)]
    sent_keys = set().union(*(_story_keys(h["title"]) for h in recent)) if recent else set()
    fresh = [a for a in articles
             if normalize_url(a["url"]) not in sent_urls and not (_story_keys(a["title"]) & sent_keys)]
    print(f"  [이미 보낸 기사 빼기] {len(articles)}건 → {len(fresh)}건")
    return fresh


def rule_filter(state: NewsState) -> dict:
    now = datetime.now(timezone.utc)
    before = drop_already_sent(state.get("articles", []), now)
    articles = group_same_story(before, now)
    scores = {}
    for art in articles:
        total, note = rule_score(art, now)
        scores[art["id"]] = {"rule": total, "rule_note": note}
    print(f"  [같은 소식 묶기] {len(before)}건 → {len(articles)}건")

    ranked = sorted(articles, key=lambda a: scores[a["id"]]["rule"], reverse=True)

    # 한 매체가 후보를 독차지하지 않도록 매체당 최대 MAX_PER_SOURCE건
    per_source = Counter()
    candidates = []
    for art in ranked:
        if per_source[art["source"]] >= config.MAX_PER_SOURCE:
            continue
        per_source[art["source"]] += 1
        candidates.append(art)
        if len(candidates) >= config.CANDIDATE_COUNT:
            break

    print(f"  [규칙 점수] {len(articles)}건 → {len(candidates)}건")
    return {"candidates": candidates, "scores": scores}


# ─────────────────────────────────────────────────────
# 노드 6. GPT 채점
# ─────────────────────────────────────────────────────
class ArticleScore(BaseModel):
    id: str = Field(description="후보 목록에 적힌 기사 id를 그대로")
    score: int = Field(description="독자에게 중요한 정도, 1~10 정수")
    reason: str = Field(description="점수를 준 이유, 한국어 한 문장")
    duplicate_of: str | None = Field(
        description="같은 소식을 다룬 더 나은 기사가 목록에 있으면 그 기사의 id, 없으면 null")


class ScoreList(BaseModel):
    scores: list[ArticleScore] = Field(description="후보 기사 전부에 대한 채점 결과")


SYSTEM_PROMPT = """너는 매일 아침 AI 뉴스 브리핑을 만드는 편집자야.
독자: {reader}

후보 기사마다 독자에게 얼마나 중요한지 1~10점으로 매겨.
{criteria}

규칙
- 후보 전부를 빠짐없이 채점해.
- 같은 소식을 다룬 기사가 여럿이면(영어·한국어처럼 언어가 달라도) 가장 정보가 많고 믿을 만한
  기사 하나에만 duplicate_of를 null로 두고, 나머지는 duplicate_of에 그 기사의 id를 적어.
- reason은 한국어 한 문장으로."""


def _candidate_lines(candidates: list[Article]) -> str:
    lines = []
    for a in candidates:
        lines.append(
            f"[{a['id']}] {a['title']}\n"
            f"  출처: {a['source']} | 다룬 곳: {len(a['mentioned_by'])}곳 | 발행: {a['published_at'][:16] or '모름'}\n"
            f"  설명: {a['snippet'][:300]}"
        )
    return "\n\n".join(lines)


def llm_score(state: NewsState) -> dict:
    candidates = state.get("candidates", [])
    scores = {k: dict(v) for k, v in state.get("scores", {}).items()}
    need = config.PICK_COUNT + config.RESERVE_COUNT
    by_id = {a["id"]: a for a in candidates}
    errors = []
    if not candidates:                       # 후보가 없으면 GPT를 부르지 않음
        print("  [GPT 채점] 후보가 없어서 건너뜀")
        return {"selected": [], "reserve": [], "scores": scores, "errors": errors}

    try:
        user = f"후보 기사 {len(candidates)}건:\n\n{_candidate_lines(candidates)}"
        recent = load_history()[-15:]
        if recent:   # 어제 · 그제 이미 보낸 소식을 알려 주면, 같은 소식을 또 고르지 않음
            user += ("\n\n---\n최근에 이미 보낸 소식 (같은 소식이면 1점을 줘):\n"
                     + "\n".join(f"- {h['headline']} ({h['title'][:60]})" for h in recent))
        criteria = "\n".join(f"- {c}" for c in config.IMPORTANCE_CRITERIA)   # audience.yaml의 중요도 판단 문장
        result, model = ask_structured(SYSTEM_PROMPT.format(reader=config.READER, criteria=criteria),
                                       user, ScoreList)
        matched = 0
        for s in result.scores:
            if s.id in by_id:
                matched += 1
                dup = s.duplicate_of if s.duplicate_of in by_id and s.duplicate_of != s.id else None
                scores[s.id].update(llm=max(1, min(10, s.score)), reason=s.reason, duplicate_of=dup)
        print(f"  [GPT 채점] {model}로 {matched}건 채점")
    except LLMError as e:
        # GPT가 안 되면 멈추지 않고 규칙 점수 순서대로 고름 (매일 아침 발송이 끊기지 않게)
        errors.append(f"[GPT 채점] {e} → 규칙 점수 순서로 대신 고름")
        print(f"  [GPT 채점] 실패: {e} → 규칙 점수 순서로 대신 고름")

    # 최종 점수 = GPT 점수 + 여러 곳에서 보도한 소식 가산점 (GPT가 놓치는 '업계 관심도'를 보탬)
    dup_count = Counter(v.get("duplicate_of") for v in scores.values() if v.get("duplicate_of"))
    for a in candidates:
        s = scores[a["id"]]
        coverage = len(a["mentioned_by"]) + dup_count[a["id"]]
        s["bonus"] = min(config.COVERAGE_BONUS_MAX, coverage - 1)
        s["final"] = s.get("llm", 0) + s["bonus"]

    def rank_key(a: Article):
        s = scores[a["id"]]
        return (s["final"], s["rule"])

    def is_duplicate(a: Article) -> bool:
        # 'A는 B와 같은 소식' 이고 B가 대표 기사일 때만 A를 뺌 (서로를 가리키면 둘 다 남김)
        target = scores[a["id"]].get("duplicate_of")
        return bool(target) and not scores[target].get("duplicate_of")

    picked = [a for a in sorted(candidates, key=rank_key, reverse=True) if not is_duplicate(a)][:need]

    selected, reserve = picked[:config.PICK_COUNT], picked[config.PICK_COUNT:]
    print(f"  [선별 완료] 발행 {len(selected)}건 + 예비 {len(reserve)}건")
    return {"selected": selected, "reserve": reserve, "scores": scores, "errors": errors}
