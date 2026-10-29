"""
⑤ 발행 단계
  publish_discord : 검수를 통과한 기사를 디스코드 채널에 '기사 카드' 모양으로 보냅니다.
                    보낸 기사는 sent_history.json에 적어 두고, 다음 실행의 ② 선별에서 뺍니다.

디스코드 규칙(웹훅): 메시지 하나에 카드 10장까지, 카드 글자 합계 6000자까지.
넘으면 메시지를 나눠서 보냅니다.
"""
import json
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

import config
from state import NewsState
from utils import friendly_error, normalize_url

KST = ZoneInfo("Asia/Seoul")
WEEKDAYS = "월화수목금토일"
COLOR_FULL, COLOR_SNIPPET = 0x5865F2, 0xF0B232     # 본문을 읽고 요약 / 짧은 설명만 보고 요약
MESSAGE_CHAR_LIMIT = 5800                          # 디스코드 한도(6000)보다 조금 여유 있게


# ─────────────────────────────────────────────────────
# 보낸 기사 기록
# ─────────────────────────────────────────────────────
def load_history() -> list[dict]:
    """최근 HISTORY_DAYS일 동안 보낸 기사 목록. → [{"url", "title", "headline", "sent_at"}, ...]"""
    path = Path(config.HISTORY_FILE)
    if not path.exists():
        return []
    try:
        items = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    since = datetime.now(timezone.utc) - timedelta(days=config.HISTORY_DAYS)
    return [i for i in items if datetime.fromisoformat(i["sent_at"]) >= since]


def save_history(new_items: list[dict]) -> None:
    items = load_history() + new_items
    Path(config.HISTORY_FILE).parent.mkdir(parents=True, exist_ok=True)
    Path(config.HISTORY_FILE).write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")


# ─────────────────────────────────────────────────────
# 디스코드 메시지 만들기
# ─────────────────────────────────────────────────────
def _cut(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit - 1] + "…"


def make_card(s: dict) -> dict:
    """요약 하나 → 디스코드 카드(embed) 하나"""
    bullets = "\n".join(f"• {p}" for p in s["key_points"])
    basis = "본문을 읽고 요약" if s["based_on"] == "본문" else "짧은 설명만 보고 요약 · 원문 확인 권장"
    return {
        "title": _cut(s["headline"], 256),
        "url": s["url"],
        "description": _cut(f"{s['one_liner']}\n\n{bullets}", 4096),
        "fields": [{"name": "왜 중요한가", "value": _cut(s["why_it_matters"], 1024)}],
        "footer": {"text": _cut(f"{s['source']} · {basis}", 2048)},
        "color": COLOR_FULL if s["based_on"] == "본문" else COLOR_SNIPPET,
    }


def _card_chars(card: dict) -> int:
    return (len(card["title"]) + len(card["description"]) + len(card["footer"]["text"])
            + sum(len(f["name"]) + len(f["value"]) for f in card["fields"]))


def build_messages(summaries: list[dict], dropped_count: int = 0) -> list[dict]:
    """보낼 메시지 목록을 만듭니다. 글자 수 한도를 넘으면 여러 메시지로 나눕니다."""
    now = datetime.now(KST)
    header = f"**오늘의 AI 뉴스 브리핑** · {now.month}월 {now.day}일 ({WEEKDAYS[now.weekday()]})"
    if not summaries:
        return [{"content": header + "\n오늘은 새로 보낼 만한 기사가 없어서 브리핑을 쉬어요."}]

    intro = (f"{header}\nAI가 고르고 요약했어요. 카드 제목을 누르면 원문으로 가요.")
    if dropped_count:
        intro += f"\n(원문과 맞는지 확인되지 않은 기사 {dropped_count}건은 뺐어요)"

    messages, cards, size = [], [], 0
    for card in (make_card(s) for s in summaries):
        if cards and (size + _card_chars(card) > MESSAGE_CHAR_LIMIT or len(cards) == 10):
            messages.append({"embeds": cards})
            cards, size = [], 0
        cards.append(card)
        size += _card_chars(card)
    messages.append({"embeds": cards})
    messages[0]["content"] = intro
    return messages


# ─────────────────────────────────────────────────────
# 노드 13. 디스코드로 보내기
# ─────────────────────────────────────────────────────
def _post(url: str, payload: dict) -> None:
    """메시지 하나 보내기. 너무 자주 보냈다고 하면(429) 기다렸다가 한 번 더."""
    body = {**payload, "username": config.BOT_NAME, "allowed_mentions": {"parse": []}}  # @everyone 같은 알림 막기
    for attempt in range(2):
        resp = requests.post(url, json=body, timeout=15)
        if resp.status_code == 429 and attempt == 0:
            wait = float(resp.json().get("retry_after", 2))
            time.sleep(min(wait, 10))
            continue
        resp.raise_for_status()
        return


def publish_discord(state: NewsState) -> dict:
    summaries = state.get("summaries", {})
    items = [summaries[a["id"]] for a in state.get("selected", []) if a["id"] in summaries]
    messages = build_messages(items, len(state.get("dropped", [])))

    if os.getenv("DRY_RUN"):                     # 시험용: 보내지 않고 내용만 보여 줌
        print(json.dumps(messages, ensure_ascii=False, indent=2))
        return {"published": 0}

    url = os.getenv("DISCORD_WEBHOOK_URL", "").strip()
    if not url:
        print("  [발행] DISCORD_WEBHOOK_URL이 없어서 보내지 못했어요")
        return {"published": 0, "errors": ["[발행] DISCORD_WEBHOOK_URL이 없어요"]}

    try:
        for payload in messages:
            _post(url, payload)
            time.sleep(0.5)
    except Exception as e:
        print(f"  [발행] 실패: {friendly_error(e)}")
        return {"published": 0, "errors": [f"[발행] 디스코드로 보내지 못했어요: {friendly_error(e)}"]}

    sent_at = datetime.now(timezone.utc).isoformat()
    save_history([{"url": normalize_url(s["url"]), "title": s["title"], "headline": s["headline"],
                   "sent_at": sent_at} for s in items])
    print(f"  [발행] 디스코드로 {len(items)}건 보냄 (메시지 {len(messages)}개)")
    return {"published": len(items)}
