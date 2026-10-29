# AI 뉴스레터 에이전트

매일 아침 AI 업계 소식을 모아 **중요한 5건을 골라 한국어로 요약하고, 원문과 맞는지 검수한 뒤 디스코드로 보내는** LangGraph 에이전트입니다.
설계 판단과 실행 결과는 **[REPORT.md](REPORT.md)**에 정리했습니다.

```
① 수집 (RSS · 뉴스 API · 스크래핑) → ② 선별 (규칙 예선 → GPT 본선) → ③ 요약 → ④ 검수 (숫자 값 비교 · GPT 심판, 실패 시 다시 요약 · 예비 교체) → ⑤ 디스코드 발행
```

## 실행 방법

1. Python 3.10 이상, 아래 세 가지 키가 필요합니다.
   - `OPENAI_API_KEY` (platform.openai.com)
   - `TAVILY_API_KEY` (app.tavily.com, 무료)
   - `DISCORD_WEBHOOK_URL` (디스코드 채널 설정 → 연동 → 웹후크)
2. `.env.example`을 복사해 `.env`를 만들고 키를 넣습니다. (`.env`는 저장소에 올리지 않습니다)
3. 실행
   - **윈도우**: `실행하기.bat` 더블클릭 → 설치 · 키 확인 · 실행을 한 번에
   - **직접**: `pip install -r requirements.txt` → `python run.py`
4. 결과: 디스코드 채널에 브리핑이 오고, `store/metrics.jsonl`에 실행 기록이 한 줄 추가됩니다.

단계 하나씩 시험하려면 `python stages/run_collect.py` → `run_select.py` → `run_summarize.py` → `run_review.py` → `run_publish.py` 순서로 실행합니다.

## 바꾸고 싶을 때

| 바꿀 것 | 파일 |
|---|---|
| 독자, 중요도 기준, 믿을 만한 매체, 키워드, 제외 조건 | `audience.yaml` |
| 수집할 RSS · 검색 키워드 · 스크래핑 사이트, GPT 모델 | `config.py` |
| 노드 연결 순서 | `graph.py` |
