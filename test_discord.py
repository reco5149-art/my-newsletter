"""
디스코드 웹훅 연결 테스트

    python test_discord.py

.env 파일의 DISCORD_WEBHOOK_URL로 테스트 메시지를 하나 보냅니다.
실제 브리핑에서 기사가 어떤 카드 모양으로 보일지 미리보기도 함께 보냅니다.
"""
import os
import sys
from datetime import datetime, timezone

import requests
from dotenv import load_dotenv

load_dotenv()
url = os.getenv("DISCORD_WEBHOOK_URL", "").strip()

if not url:
    sys.exit("[실패] .env 파일에 DISCORD_WEBHOOK_URL이 없어요. .env.example을 참고해 넣어 주세요.")
if "/api/webhooks/" not in url:
    sys.exit("[실패] 웹훅 주소 모양이 아니에요. https://discord.com/api/webhooks/... 형태여야 해요.")

message = {
    "username": "AI 뉴스레터",
    "content": "**연결 테스트 성공!** 매일 아침 이 채널로 AI 뉴스 브리핑이 올 거예요.",
    "embeds": [{
        "title": "[미리보기] 기사 카드는 이런 모양이에요",
        "url": "https://example.com",
        "description": "**한 줄 요약** 자리입니다. 실제 브리핑에서는 기사 1건이 카드 1장으로 표시돼요.",
        "color": 0x5865F2,
        "fields": [
            {"name": "핵심", "value": "• 핵심 내용 1\n• 핵심 내용 2\n• 핵심 내용 3", "inline": False},
            {"name": "왜 중요한가", "value": "내 일에 어떤 영향이 있는지 설명하는 자리", "inline": False},
        ],
        "footer": {"text": "출처: 매체 이름 · AI가 요약했습니다. 자세한 내용은 원문 링크로 확인하세요"},
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }],
}

try:
    resp = requests.post(url, json=message, timeout=15)
except requests.RequestException as e:
    sys.exit(f"[실패] 디스코드에 접속하지 못했어요. 인터넷 연결을 확인해 주세요. ({type(e).__name__})")

if resp.status_code in (200, 204):
    print("[성공] 디스코드 채널을 확인해 보세요.")
    sys.exit(0)
if resp.status_code in (401, 404):
    print("[실패] 웹훅이 삭제됐거나 주소가 틀렸어요. 디스코드에서 웹훅 URL을 다시 복사해 주세요.")
elif resp.status_code == 429:
    print("[실패] 너무 자주 보냈어요. 1분쯤 뒤에 다시 해 보세요.")
else:
    print(f"[실패] 디스코드 응답 코드 {resp.status_code}: {resp.text[:200]}")
sys.exit(1)
