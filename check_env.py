"""
.env 파일에 키가 제대로 들어갔는지 확인합니다. (키 값은 화면에 보여 주지 않음)

    python check_env.py

문제가 있으면 종료 코드 1을 돌려줍니다. (실행하기.bat이 이걸 보고 메모장을 열어 줌)
"""
import sys
from pathlib import Path

from dotenv import dotenv_values

CHECKS = [
    # (이름, 올바른 모양인지 확인하는 함수, 모양 설명, 꼭 필요한지)
    ("DISCORD_WEBHOOK_URL", lambda v: "/api/webhooks/" in v,
     "https://discord.com/api/webhooks/... 형태여야 해요", True),
    ("TAVILY_API_KEY", lambda v: v.startswith("tvly-"),
     "tvly-로 시작해야 해요 (tavily.com 대시보드의 API Keys에서 복사)", False),
    ("OPENAI_API_KEY", lambda v: v.startswith("sk-"),
     "sk-로 시작해야 해요", False),
]

if not Path(".env").exists():
    print("[문제] .env 파일이 없어요.")
    sys.exit(1)

values = dotenv_values(".env", encoding="utf-8-sig")
problem = False

print(".env 확인 결과:")
for name, looks_ok, shape, required in CHECKS:
    value = (values.get(name) or "").strip().strip('"').strip("'")
    if not value:
        mark = "[비어 있음]" if required else "[비어 있음 · 나중에 넣어도 됨]"
        print(f"  {mark} {name}")
        problem = problem or required
    elif not looks_ok(value):
        print(f"  [모양이 이상함] {name}: {shape}")
        problem = True
    else:
        print(f"  [OK] {name}")

sys.exit(1 if problem else 0)
