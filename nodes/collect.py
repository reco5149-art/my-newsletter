"""
① 수집 단계의 노드 4개
  collect_rss    : RSS 피드에서 최근 기사 모으기 (주력)
  collect_api    : Tavily 뉴스 검색으로 RSS가 놓친 기사 보충
  collect_scrape : RSS가 없는 사이트의 목록 페이지에서 직접 모으기
  dedupe         : 세 곳에서 모은 기사 중 같은 기사 · 같은 소식을 하나로 합치기

노드 규칙: State를 받아서, 바꾸고 싶은 칸만 담은 dict를 돌려줍니다.
"""
import os
import re
import time
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from difflib import SequenceMatcher
from urllib.parse import urljoin, urlparse

import feedparser
from bs4 import BeautifulSoup, NavigableString

import config
from state import Article, NewsState
from utils import (domain_of, end_of_day_utc, extract_main_text, fetch, find_date_in_page,
                   find_date_in_text, friendly_error, html_to_text, iter_dates, make_id, normalize_url, page_description,
                   page_title, parse_date)


def _cutoff() -> datetime:
    """이 시각보다 오래된 기사는 버립니다."""
    return datetime.now(timezone.utc) - timedelta(hours=config.HOURS_BACK)


def _article(title, url, source, via, published: datetime | None, snippet="", content="") -> Article:
    return {
        "id": make_id(url),
        "title": (title or "").strip(),
        "url": url.strip(),
        "source": source,
        "via": via,
        "published_at": published.isoformat() if published else "",
        "snippet": snippet,
        "content": content,
        "mentioned_by": [source],
    }


# ─────────────────────────────────────────────────────
# 노드 1. RSS
# ─────────────────────────────────────────────────────
def collect_rss(state: NewsState) -> dict:
    cutoff = _cutoff()
    articles, errors = [], []

    for feed in config.RSS_FEEDS:
        try:
            parsed = feedparser.parse(fetch(feed["url"]).content)
            if not parsed.entries:
                raise ValueError("기사가 하나도 없음 (주소가 바뀌었을 수 있음)")

            count = 0
            for entry in parsed.entries:
                published = parse_date(entry.get("published") or entry.get("updated"),
                                       feed.get("timezone"))
                if not published or published < cutoff or not entry.get("link"):
                    continue  # 날짜를 모르거나 오래된 기사는 건너뜀
                # 일부 피드는 본문 전체(content)를 함께 줍니다 → 나중에 본문을 따로 안 가져와도 됨
                full = entry.get("content", [{}])[0].get("value", "")
                articles.append(_article(
                    entry.get("title"), entry["link"], feed["name"], "rss", published,
                    snippet=html_to_text(entry.get("summary", "")),
                    content=html_to_text(full, limit=20000) if len(full) > 1000 else "",
                ))
                count += 1
            print(f"  [RSS] {feed['name']}: {count}건")
        except Exception as e:
            e = friendly_error(e)
            errors.append(f"[RSS] {feed['name']}: {e}")
            print(f"  [RSS] {feed['name']}: 실패 ({e})")

    return {"raw_articles": articles, "errors": errors}


# ─────────────────────────────────────────────────────
# 노드 2. 뉴스 API (Tavily)
# ─────────────────────────────────────────────────────
def collect_api(state: NewsState) -> dict:
    api_key = os.getenv("TAVILY_API_KEY")
    if not api_key:
        print("  [API] TAVILY_API_KEY가 없어서 건너뜀")
        return {"raw_articles": [], "errors": ["[API] TAVILY_API_KEY가 없어서 건너뜀"]}

    from tavily import TavilyClient  # 키가 있을 때만 불러옴
    client = TavilyClient(api_key=api_key)
    cutoff = _cutoff()
    articles, errors = [], []

    for keyword in config.API_KEYWORDS:
        try:
            response = client.search(query=keyword, topic="news", time_range="day",
                                     max_results=config.API_MAX_RESULTS)
            count = 0
            for r in response.get("results", []):
                published = parse_date(r.get("published_date"))
                if published and published < cutoff:
                    continue
                articles.append(_article(
                    r.get("title"), r["url"], domain_of(r["url"]), "api", published,
                    snippet=(r.get("content") or "")[:500],
                ))
                count += 1
            print(f"  [API] '{keyword}': {count}건")
        except Exception as e:
            e = friendly_error(e)
            errors.append(f"[API] '{keyword}': {e}")
            print(f"  [API] '{keyword}': 실패 ({e})")

    return {"raw_articles": articles, "errors": errors}


# ─────────────────────────────────────────────────────
# 노드 3. 스크래핑
# ─────────────────────────────────────────────────────
DATE_WINDOW = 150   # 링크 앞뒤 몇 글자 안에서 날짜를 찾을지


def _nearest_date(text: str, start: int, end: int):
    """text[start:end](링크 글자)에서 가장 가까운 날짜를 찾습니다 (앞뒤 DATE_WINDOW 글자 안)."""
    lo, hi = max(0, start - DATE_WINDOW), min(len(text), end + DATE_WINDOW)
    best, best_gap = None, None
    for m_start, m_end, day in iter_dates(text[lo:hi]):
        m_start, m_end = lo + m_start, lo + m_end
        gap = 0 if (m_start < end and m_end > start) else min(abs(start - m_end), abs(m_start - end))
        if best_gap is None or gap < best_gap:
            best, best_gap = day, gap
    return best


def _without_dates(text: str) -> str:
    """링크 글자 속 날짜를 지웁니다. 예) 'Oct 28, 2026 AI for Science' → 'AI for Science'"""
    for start, end, _ in sorted(iter_dates(text), reverse=True):
        text = text[:start] + text[end:]
    return re.sub(r"\s+", " ", text).strip()


def _find_links(list_url: str, link_pattern: str) -> list[dict]:
    """목록 페이지에서 기사 링크를 찾습니다. → [{"url", "title", "snippet", "day"}, ...]

    날짜 찾는 순서
      1) 링크를 감싼 '카드'(다른 기사 링크가 없는 가장 가까운 묶음) 안에 적힌 날짜
      2) 카드를 구분할 수 없는 페이지면, 페이지 글자에서 링크 바로 앞뒤에 적힌 날짜
    """
    soup = BeautifulSoup(fetch(list_url).text, "html.parser")
    for tag in soup(["script", "style", "noscript", "nav", "header", "footer"]):
        tag.decompose()                                # 메뉴 · 머리말 · 꼬리말은 기사 목록이 아님
    host = urlparse(list_url).netloc
    pattern = re.compile(link_pattern)

    def article_key(a) -> str | None:
        """기사 링크면 정리된 주소, 아니면 None. 두 낱말 이하의 짧은 링크(카테고리 이름 등)도 None."""
        url = urljoin(list_url, a["href"])
        parsed = urlparse(url)
        if parsed.netloc != host or not pattern.match(parsed.path):
            return None
        words = a.get_text(" ", strip=True).split()
        return None if 0 < len(words) <= 2 else normalize_url(url)

    def card_date(a, own_key):
        """→ (날짜, 카드를 찾았는지)"""
        node, isolated = a.parent, False
        for _ in range(8):
            if node is None:
                break
            others = {article_key(x) for x in node.find_all("a", href=True)} - {None, own_key}
            if others:                                 # 다른 기사까지 들어 있으면 카드 바깥
                break
            isolated = True
            if (day := find_date_in_text(node.get_text(" ", strip=True))):
                return day, True
            node = node.parent
        return None, isolated

    # 페이지 글자를 한 줄로 이어 붙이면서, 링크가 몇 번째 글자에서 시작하는지 적어 둠 (2번 방법용)
    pieces, marks, pos = [], [], 0
    for node in soup.descendants:
        if type(node) is NavigableString:
            piece = " " + node.strip()
            pieces.append(piece)
            pos += len(piece)
        elif getattr(node, "name", None) == "a" and node.get("href"):
            marks.append((pos, node))
    text = "".join(pieces)

    found: dict[str, dict] = {}
    for start, a in marks:
        url = urljoin(list_url, a["href"])
        parsed = urlparse(url)
        if parsed.netloc != host or not pattern.match(parsed.path):
            continue
        key = normalize_url(url)
        link_text = _without_dates(a.get_text(" ", strip=True))
        item = found.setdefault(key, {"url": url, "title": "", "snippet": "", "day": None, "seen": 0})
        item["seen"] += 1
        if link_text and not item["title"]:            # 처음 나온 글자 = 제목
            item["title"] = link_text
        elif link_text and link_text != item["title"] and len(link_text) > len(item["snippet"]):
            item["snippet"] = link_text[:500]          # 가장 긴 다른 글자 = 설명
        if not item["day"]:
            day, isolated = card_date(a, key)
            if not day and not isolated:
                day = _nearest_date(text, start, start + len(link_text) + 1)
            item["day"] = day

    # '카테고리 메뉴'처럼 보이는 링크 빼기: 이름이 두 낱말 이하 · 설명 글 없음 · 여러 번 반복
    items = [i for i in found.values()
             if not (len(i["title"].split()) <= 2 and not i["snippet"] and i["seen"] >= 2)]
    # 목록에 날짜가 적힌 글이 있다면, 날짜 없는 링크도 뺌
    if any(i["day"] for i in items):
        items = [i for i in items if i["day"]]
    items.sort(key=lambda i: i["day"] or date.min, reverse=True)
    return items[:config.SCRAPE_MAX_LINKS]


def collect_scrape(state: NewsState) -> dict:
    cutoff = _cutoff()
    articles, errors = [], []

    for site in config.SCRAPE_SITES:
        try:
            links = _find_links(site["list_url"], site["link_pattern"])
            if not links:
                raise ValueError("기사 링크를 못 찾음 (사이트 구조가 바뀌었을 수 있음)")

            count = 0
            skipped = Counter()      # 왜 빠졌는지 세어 두기 (결과 화면에 보여 줌)
            open_error = ""
            for link in links:
                url, listed_day = link["url"], link["day"]
                # 목록에 적힌 날짜로 먼저 거르기: 이틀 넘게 지난 글은 페이지를 열지도 않음
                if listed_day and listed_day < (cutoff - timedelta(days=1)).date():
                    skipped["오래됨"] += 1
                    continue
                # 기사 페이지를 열어 정확한 시각 · 제목 · 설명 · 본문을 가져옴
                time.sleep(config.SCRAPE_DELAY)          # 사이트에 부담을 주지 않도록 잠깐 쉬기
                try:
                    resp = fetch(url)
                    if normalize_url(resp.url) != normalize_url(url):
                        # 다른 페이지로 넘어갔으면(로그인 · 목록 페이지 등) 기사 페이지가 아님
                        raise ValueError("기사 대신 다른 페이지로 넘어감")
                    page = BeautifulSoup(resp.text, "html.parser")
                except Exception as e:
                    page, open_error = None, friendly_error(e)

                if page is None:
                    # 페이지는 못 열었지만 목록에 날짜가 있으면, 목록 정보만으로 담음 (본문은 나중 단계에서)
                    published = end_of_day_utc(listed_day) if listed_day else None
                    if not published:
                        skipped["열기 실패"] += 1
                        continue
                    title, snippet, content = link["title"], link["snippet"], ""
                    skipped["목록 정보만 사용"] += 1
                else:
                    published = find_date_in_page(page)
                    # 목록에 적힌 날짜를 더 믿음: 페이지 날짜가 목록 날짜와 다른 날이면 목록 날짜를 씀
                    # (페이지 안의 '관련 글' 날짜를 잘못 읽는 경우가 있어서)
                    if listed_day and (not published or published.date() != listed_day):
                        if published:
                            skipped["페이지 날짜 대신 목록 날짜 사용"] += 1
                        published = end_of_day_utc(listed_day)
                    title = page_title(page) or link["title"]
                    snippet = page_description(page) or link["snippet"]
                    content = extract_main_text(page)

                if not published:
                    skipped["날짜 모름"] += 1
                    continue
                if published < cutoff:
                    skipped["오래됨"] += 1
                    continue
                articles.append(_article(title, url, site["name"], "scrape", published,
                                         snippet=snippet, content=content))
                count += 1

            detail = ", ".join(f"{k} {v}" for k, v in skipped.items())
            if count == 0 and links[0]["day"]:   # 하나도 못 담았으면, 목록에서 찾은 가장 최근 글을 보여 줌
                detail += f" · 목록의 가장 최근 글: {links[0]['day']} '{links[0]['title'][:30]}'"
            if open_error:
                detail += f" · 페이지 열기 오류: {open_error}"
            print(f"  [스크래핑] {site['name']}: {count}건 (링크 {len(links)}개 확인{' · ' + detail if detail else ''})")
        except Exception as e:
            e = friendly_error(e)
            errors.append(f"[스크래핑] {site['name']}: {e}")
            print(f"  [스크래핑] {site['name']}: 실패 ({e})")

    return {"raw_articles": articles, "errors": errors}


# ─────────────────────────────────────────────────────
# 노드 4. 중복 제거
# ─────────────────────────────────────────────────────
SIMILAR_TITLE = 0.85   # 제목이 85% 이상 같으면 같은 소식으로 봄
VIA_PRIORITY = {"rss": 0, "scrape": 1, "api": 2}   # 겹치면 정보가 많은 쪽을 남김


def _plain(title: str) -> str:
    return re.sub(r"[^\w가-힣]+", " ", title.lower()).strip()


def _same_story(a: str, b: str) -> bool:
    a, b = _plain(a), _plain(b)
    if not a or not b:
        return False
    return SequenceMatcher(None, a, b).ratio() >= SIMILAR_TITLE


def _merge(keep: Article, dup: Article) -> None:
    """겹친 기사의 정보를 남길 기사에 보탭니다."""
    for name in dup["mentioned_by"]:
        if name not in keep["mentioned_by"]:
            keep["mentioned_by"].append(name)
    for field in ("published_at", "snippet", "content"):
        if not keep[field] and dup[field]:
            keep[field] = dup[field]


def dedupe(state: NewsState) -> dict:
    raw = state.get("raw_articles", [])
    # 원본을 건드리지 않도록 복사한 뒤, 본문 있는 것 → RSS → 스크래핑 → API 순으로 줄 세움
    ordered = sorted(
        ({**a, "mentioned_by": list(a["mentioned_by"])} for a in raw),
        key=lambda a: (0 if a["content"] else 1, VIA_PRIORITY.get(a["via"], 9)),
    )

    kept: list[Article] = []
    by_url: dict[str, Article] = {}
    for art in ordered:
        if not art["title"]:
            continue
        key = normalize_url(art["url"])
        twin = by_url.get(key) or next((k for k in kept if _same_story(k["title"], art["title"])), None)
        if twin:
            _merge(twin, art)
            by_url.setdefault(key, twin)
        else:
            kept.append(art)
            by_url[key] = art

    kept.sort(key=lambda a: a["published_at"], reverse=True)   # 최신순
    print(f"  [중복 제거] {len(raw)}건 → {len(kept)}건")
    return {"articles": kept}
