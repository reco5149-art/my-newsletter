"""
여러 노드가 같이 쓰는 도구 모음: 웹페이지 가져오기, 주소 정리, 날짜 읽기, 본문 뽑기
"""
import hashlib
import json
import re
from datetime import date, datetime, time, timezone
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup
from dateutil import parser as dateparser

import config


# ── 웹페이지 가져오기 ─────────────────────────────────
def fetch(url: str) -> requests.Response:
    """주소에서 내용을 받아 옵니다. 오래 걸리거나 실패하면 오류를 냅니다."""
    resp = requests.get(url, headers={"User-Agent": config.USER_AGENT},
                        timeout=config.REQUEST_TIMEOUT)
    resp.raise_for_status()
    return resp


def friendly_error(e: Exception) -> str:
    """긴 오류 메시지를 알아보기 쉬운 한 줄로 바꿉니다."""
    if isinstance(e, requests.Timeout):
        return f"응답 없음 ({config.REQUEST_TIMEOUT}초 초과)"
    if isinstance(e, requests.ConnectionError):
        return "접속 실패 (인터넷 연결을 확인하거나, 사이트가 접속을 막았을 수 있음)"
    if isinstance(e, requests.HTTPError) and e.response is not None:
        code = e.response.status_code
        reason = {403: "사이트가 접근을 막음", 404: "주소가 없음 (바뀌었을 수 있음)",
                  429: "너무 자주 요청함"}.get(code, "사이트 오류")
        return f"{reason} [{code}]"
    return str(e)[:150]


def html_to_text(html: str, limit: int = 500) -> str:
    """HTML 태그를 지우고 글자만 남깁니다."""
    if not html:
        return ""
    text = BeautifulSoup(html, "html.parser").get_text(" ", strip=True)
    return re.sub(r"\s+", " ", text)[:limit]


# ── 주소 정리 (중복 제거용) ───────────────────────────
TRACKING_PARAMS = {"fbclid", "gclid", "mc_cid", "mc_eid", "ref", "ref_src", "cmpid"}


def normalize_url(url: str) -> str:
    """같은 기사인데 주소 꼬리만 다른 경우를 하나로 맞춥니다.
    예) https://www.site.com/a/?utm_source=x  →  https://site.com/a
    """
    p = urlparse(url.strip())
    host = p.netloc.lower().removeprefix("www.")
    query = [(k, v) for k, v in parse_qsl(p.query)
             if not k.lower().startswith("utm_") and k.lower() not in TRACKING_PARAMS]
    path = p.path.rstrip("/") or "/"
    return urlunparse(("https", host, path, "", urlencode(query), ""))


def make_id(url: str) -> str:
    return hashlib.sha1(normalize_url(url).encode()).hexdigest()[:12]


def domain_of(url: str) -> str:
    return urlparse(url).netloc.lower().removeprefix("www.")


# ── 날짜 읽기 ─────────────────────────────────────────
def parse_date(value, tz_name: str | None = None) -> datetime | None:
    """여러 모양의 날짜 글자를 UTC 시각으로 바꿉니다. 못 읽으면 None.
    tz_name: 날짜에 시간대가 안 적혀 있을 때 가정할 시간대 (기본 UTC)
    """
    if not value:
        return None
    try:
        dt = dateparser.parse(str(value))
    except (ValueError, OverflowError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=ZoneInfo(tz_name or "UTC"))
    return dt.astimezone(timezone.utc)


MONTHS = "Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec"
DATE_PATTERNS = [
    (re.compile(rf"\b({MONTHS})[a-z]*\.?\s+(\d{{1,2}}),?\s+(20\d\d)\b"), "mdy"),  # Oct 28, 2026
    (re.compile(rf"\b(\d{{1,2}})\s+({MONTHS})[a-z]*\.?,?\s+(20\d\d)\b"), "dmy"),  # 28 Oct 2026
    (re.compile(r"\b(20\d\d)[-./]\s?(\d{1,2})[-./]\s?(\d{1,2})\b"), "ymd"),       # 2026-10-28
    (re.compile(r"(20\d\d)년\s?(\d{1,2})월\s?(\d{1,2})일"), "ymd"),               # 2026년 10월 28일
]


def _to_date(m: re.Match, order: str) -> date | None:
    try:
        if order == "mdy":
            return datetime.strptime(f"{m[1][:3]} {m[2]} {m[3]}", "%b %d %Y").date()
        if order == "dmy":
            return datetime.strptime(f"{m[2][:3]} {m[1]} {m[3]}", "%b %d %Y").date()
        return date(int(m[1]), int(m[2]), int(m[3]))
    except ValueError:
        return None


def iter_dates(text: str):
    """글 속의 모든 날짜를 (시작 위치, 끝 위치, 날짜)로 돌려줍니다."""
    for pattern, order in DATE_PATTERNS:
        for m in pattern.finditer(text or ""):
            if (d := _to_date(m, order)):
                yield m.start(), m.end(), d


def find_date_in_text(text: str) -> date | None:
    """'Oct 28, 2026' 같은 날짜를 글 속에서 찾아냅니다 (시각은 모름). 가장 앞에 있는 날짜."""
    found = sorted(iter_dates(text))
    return found[0][2] if found else None


def end_of_day_utc(d: date) -> datetime:
    """날짜만 알고 시각을 모를 때는 그날의 마지막 순간으로 칩니다 (놓치지 않으려고)."""
    return datetime.combine(d, time(23, 59, 59), tzinfo=timezone.utc)


META_DATE_KEYS = ["article:published_time", "og:published_time", "datePublished",
                  "publish_date", "pubdate", "parsely-pub-date", "date"]


def _page_date(value) -> datetime | None:
    """'2026-10-28'처럼 시각 없이 날짜만 있으면 그날의 마지막 순간으로 칩니다."""
    dt = parse_date(value)
    if dt and ":" not in str(value):
        return end_of_day_utc(dateparser.parse(str(value)).date())
    return dt


def find_date_in_page(soup: BeautifulSoup) -> datetime | None:
    """기사 페이지 안에 숨어 있는 발행 시각을 찾습니다."""
    # 1) <meta property="article:published_time" content="...">
    for key in META_DATE_KEYS:
        tag = (soup.find("meta", attrs={"property": key}) or soup.find("meta", attrs={"name": key})
               or soup.find("meta", attrs={"itemprop": key}))
        if tag and (dt := _page_date(tag.get("content"))):
            return dt
    # 2) 구조화 데이터 <script type="application/ld+json"> 속 datePublished
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            found = _search_json(json.loads(script.string or ""), "datePublished")
        except (json.JSONDecodeError, TypeError):
            continue
        if found and (dt := _page_date(found)):
            return dt
    # 3) <time datetime="..."> 또는 <time>Oct 28, 2026</time>
    tag = soup.find("time")
    if tag:
        if tag.get("datetime") and (dt := _page_date(tag["datetime"])):
            return dt
        if (day := find_date_in_text(tag.get_text(" ", strip=True))):
            return end_of_day_utc(day)
    return None


def _search_json(data, key):
    if isinstance(data, dict):
        if key in data:
            return data[key]
        data = list(data.values())
    if isinstance(data, list):
        for item in data:
            if (found := _search_json(item, key)):
                return found
    return None


# ── 기사 페이지에서 제목 · 설명 · 본문 뽑기 ───────────
def page_title(soup: BeautifulSoup) -> str:
    og = soup.find("meta", attrs={"property": "og:title"})
    if og and og.get("content"):
        return og["content"].strip()
    h1 = soup.find("h1")
    if h1:
        return h1.get_text(" ", strip=True)
    return soup.title.get_text(strip=True) if soup.title else ""


def page_description(soup: BeautifulSoup) -> str:
    for attrs in ({"property": "og:description"}, {"name": "description"}):
        tag = soup.find("meta", attrs=attrs)
        if tag and tag.get("content"):
            return tag["content"].strip()[:500]
    return ""


def extract_main_text(soup: BeautifulSoup, limit: int = 20000) -> str:
    """메뉴 · 광고 등을 빼고 본문 글자만 뽑습니다 (완벽하진 않음)."""
    for tag in soup(["script", "style", "nav", "header", "footer", "aside", "form", "noscript"]):
        tag.decompose()
    body = soup.find("article") or soup.find("main") or soup.body or soup
    paragraphs = [p.get_text(" ", strip=True) for p in body.find_all(["p", "h2", "h3", "li"])]
    text = "\n".join(p for p in paragraphs if len(p) > 1)
    if len(text) < 200:  # <p>가 거의 없는 페이지면 전체 글자로 대신
        text = body.get_text("\n", strip=True)
    return text[:limit]
