"""
기술 설정: 어디서(소스 목록) · 얼마나 · 어떤 GPT로 가져올지.
독자 · 중요도 기준 · 제외 조건은 audience.yaml에 있고, 여기서 불러와 같은 이름의 변수로 씁니다.
"""
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent
AUDIENCE = yaml.safe_load((ROOT / "audience.yaml").read_text(encoding="utf-8"))

# ── 공통 ─────────────────────────────────────────────
HOURS_BACK = AUDIENCE["exclude"]["hours_back"]   # 최근 몇 시간 안의 기사만 가져올지 (audience.yaml)
REQUEST_TIMEOUT = 15     # 사이트 하나를 최대 몇 초까지 기다릴지
USER_AGENT = "Mozilla/5.0 (compatible; AINewsletterBot/0.1)"


# ── ① RSS: 주력 소스 ─────────────────────────────────
# name: 화면에 보일 이름 / url: RSS 주소
# timezone: 날짜에 시간대 표시가 없는 피드만 적어 줍니다 (예: AI타임스는 한국 시간)
RSS_FEEDS = [
    {"name": "TechCrunch AI", "url": "https://techcrunch.com/category/artificial-intelligence/feed/"},
    {"name": "MIT Technology Review AI", "url": "https://www.technologyreview.com/topic/artificial-intelligence/feed"},
    {"name": "The Verge AI", "url": "https://www.theverge.com/rss/ai-artificial-intelligence/index.xml"},
    {"name": "Ars Technica AI", "url": "https://arstechnica.com/ai/feed/"},
    {"name": "OpenAI", "url": "https://openai.com/news/rss.xml"},
    {"name": "Google DeepMind", "url": "https://deepmind.google/blog/rss.xml"},
    {"name": "Mistral AI", "url": "https://mistral.ai/news/rss"},
    {"name": "Hugging Face", "url": "https://huggingface.co/blog/feed.xml"},
    {"name": "Hacker News", "url": "https://hnrss.org/newest?q=AI&points=100"},
    {"name": "AI타임스", "url": "https://www.aitimes.com/rss/allArticle.xml", "timezone": "Asia/Seoul"},
]


# ── ② 뉴스 API (Tavily): RSS가 놓친 기사 보충 ─────────
# 키워드 1개 = 검색 1번 = 1크레딧. 5개 × 30일 = 한 달 약 150크레딧
API_KEYWORDS = [
    "new AI model release",
    "LLM",
    "AI agent",
    "AI regulation policy",
    "AI startup funding",
]
API_MAX_RESULTS = 10     # 키워드 하나당 최대 기사 수 (최대 20)


# ── ③ 스크래핑: RSS가 없는 사이트 ─────────────────────
# list_url: 글 목록 페이지 / link_pattern: 기사 주소의 모양 (정규식)
SCRAPE_SITES = [
    {"name": "Anthropic", "list_url": "https://www.anthropic.com/news",
     "link_pattern": r"^/(news|research)/[^/]+/?$"},
    {"name": "Meta AI", "list_url": "https://ai.meta.com/blog/",
     "link_pattern": r"^/blog/[^/]+/?$"},
]
SCRAPE_MAX_LINKS = 15    # 사이트마다 확인할 기사 링크 수
SCRAPE_DELAY = 0.5       # 기사 페이지를 하나 열 때마다 쉬는 시간(초)


# ══ ② 선별 (값은 audience.yaml) ════════════════════════
_sel, _rule, _exc = AUDIENCE["selection"], AUDIENCE["rule_score"], AUDIENCE["exclude"]
READER = AUDIENCE["reader"]
IMPORTANCE_CRITERIA = AUDIENCE["importance_criteria"]
CANDIDATE_COUNT = _sel["batch_size"]          # 예선 통과 → 본선(GPT 한 번 호출)에 보내는 후보 수
PICK_COUNT = _sel["pick_count"]
RESERVE_COUNT = _sel["reserve_count"]
MAX_PER_SOURCE = _sel["max_per_source"]
COVERAGE_BONUS_MAX = _sel["coverage_bonus_max"]

OFFICIAL_SOURCES = _rule["official_sources"]
SOURCE_POINTS = _rule["source_points"]
TRUSTED_DOMAINS = _rule["trusted_domains"]
KEYWORD_GROUPS = _rule["keyword_groups"]
COVERAGE_POINTS_PER_SOURCE = _rule["coverage_points_per_source"]
COVERAGE_POINTS_MAX = _rule["coverage_points_max"]
FRESHNESS = _rule["freshness"]

AI_WORDS, NOT_AI_PENALTY = _exc["ai_words"], _exc["not_ai_penalty"]
SPAM_WORDS, SPAM_PENALTY = _exc["spam_words"], _exc["spam_penalty"]
HISTORY_DAYS = _exc["history_days"]           # 보낸 기사와 같은 주소는 이 기간 동안 다시 안 보냄
SAME_STORY_DAYS = _exc["same_story_days"]     # 보낸 소식과 같은 소식은 이 기간 동안 다시 안 보냄

# GPT 모델: 앞에서부터 써 보고, 내 키로 못 쓰는 모델이면 다음 모델로 넘어감
LLM_MODELS = ["gpt-6-luna", "gpt-6.1-terra", "gpt-5-mini", "gpt-4.1-mini"]


# ══ ③ 요약 ═══════════════════════════════════════════
MIN_CONTENT_CHARS = 600   # 본문이 이보다 짧으면 '본문을 못 가져왔다'고 보고 다른 방법을 시도
MAX_CONTENT_CHARS = 8000  # GPT에게 보낼 본문 최대 길이 (너무 길면 앞부분만)
USE_TAVILY_EXTRACT = True # 직접 못 가져온 본문은 Tavily로 한 번 더 시도 (5건당 1크레딧)


# ══ ④ 검수 ═══════════════════════════════════════════
MAX_SUMMARY_TRIES = 2     # 같은 기사를 몇 번까지 요약해 볼지 (첫 요약 포함). 이만큼 실패하면 예비 기사로 교체
HEADLINE_MAX = 40         # 제목 최대 글자 수
USE_LLM_JUDGE = True      # 규칙 검사를 통과한 요약을 GPT 심판에게 한 번 더 확인받을지
# 원문에 없어도 문제 삼지 않을 흔한 영어 줄임말 (요약에 써도 됨)
COMMON_LATIN = ["ai", "ceo", "cfo", "cto", "cbo", "coo", "gpu", "gpus", "cpu", "api", "apis",
                "ipo", "llm", "llms", "us", "uk", "eu", "it", "app", "apps", "inc", "corp",
                "vs", "q1", "q2", "q3", "q4", "sns", "sw", "hw"]


# ══ ⑤ 발행 · 기록 ═════════════════════════════════════
BOT_NAME = "AI 뉴스레터"                          # 디스코드에 보일 보낸 사람 이름
STORE = ROOT / "store"
HISTORY_FILE = STORE / "sent_history.json"       # 보낸 기사 기록 (다음 날 같은 기사를 또 보내지 않으려고)
METRICS_FILE = STORE / "metrics.jsonl"           # 실행마다 한 줄씩: 수집 · 선별 · 검수 · 발행 기록
LAST_RUN_FILE = STORE / "last_run.json"          # 마지막 실행의 자세한 결과
