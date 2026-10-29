"""
④ 검수 단계의 노드 3개와 분기 하나
  rule_check  : 코드로 확인 (무료 · 확실한 것만)
                - 요약 속 숫자가 원문에 있는지 '값'으로 비교 ($2.9 billion = 29억 달러, two = 2)
                - 제목 길이, 핵심 개수 같은 형식
                - 원문에 없는 영어 이름은 확신할 수 없으니 GPT 심판에게 '특히 확인할 것'으로 넘김
  llm_judge   : GPT 심판이 문장마다 "원문에 근거가 있는가"를 판정
  route       : 문제 없으면 끝(→ 발행), 문제 있으면 swap_failed로
  swap_failed : 문제가 있는 기사 중, 이미 MAX_SUMMARY_TRIES번 요약한 기사는 예비 기사로 바꿈
                (나머지는 summarize가 문제 목록을 보고 다시 요약)
"""
import hashlib
import json
import re

from pydantic import BaseModel, Field

import config
from llm import LLMError, ask_structured
from state import Article, NewsState


# ─────────────────────────────────────────────────────
# 숫자 읽기: 글 속 숫자를 '값'으로 바꿉니다
# ─────────────────────────────────────────────────────
NUM = r"\d+(?:,\d{3})*(?:\.\d+)?"
KO_UNITS = {"조": 1e12, "억": 1e8, "만": 1e4, "천": 1e3}
EN_UNITS = {"trillion": 1e12, "billion": 1e9, "bn": 1e9, "million": 1e6, "thousand": 1e3}
EN_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
    "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "fifteen": 15, "twenty": 20, "thirty": 30,
    "fifty": 50, "hundred": 100, "dozen": 12, "half": 0.5,
    "first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5,
    "twice": 2, "double": 2, "doubled": 2, "doubles": 2, "triple": 3, "tripled": 3, "triples": 3,
    "quadruple": 4, "quadrupled": 4, "quadruples": 4,
}
KO_WORDS = {"한": 1, "두": 2, "세": 3, "네": 4, "다섯": 5, "여섯": 6, "일곱": 7, "여덟": 8, "아홉": 9, "열": 10}
KO_COUNTERS = "배|곳|명|개|번|가지|달|주|해|차례|분기"


def _num(text: str) -> float:
    return float(text.replace(",", ""))


def numbers_in(text: str) -> list[tuple[str, float]]:
    """글 속의 숫자를 (원래 글자, 값)으로 모두 찾습니다.
    예) '6억2000만 달러' → 620000000 / '$2.9 billion' → 2900000000 / 'two weeks' → 2
    """
    found, taken = [], []

    def free(start, end):
        return all(end <= s or start >= e for s, e in taken)

    # 1) 한국어 큰 단위 묶음: 6억2000만, 3.8조, 922억8000만
    for m in re.finditer(rf"(?:{NUM}\s*[조억만천]\s*)+", text):
        value = sum(_num(n) * KO_UNITS[u] for n, u in re.findall(rf"({NUM})\s*([조억만천])", m.group()))
        found.append((m.group().strip(), value))
        taken.append(m.span())
    # 2) 영어 단위 · 그냥 숫자: $2.9 billion, 48%, 2026
    for m in re.finditer(rf"({NUM})(?:\s*(trillion|billion|bn|million|thousand)\b|\s*%)?", text, re.I):
        if free(*m.span()):
            unit = EN_UNITS.get((m.group(2) or "").lower(), 1)
            found.append((m.group().strip(), _num(m.group(1)) * unit))
    # 3) 숫자를 글자로 쓴 것: two, quadrupled, 두 배
    for m in re.finditer(r"[A-Za-z]+", text):
        if m.group().lower() in EN_WORDS:
            found.append((m.group(), EN_WORDS[m.group().lower()]))
    for m in re.finditer(rf"({'|'.join(KO_WORDS)})\s*({KO_COUNTERS})", text):
        found.append((m.group(), KO_WORDS[m.group(1)]))
    return found


def _same_value(a: float, b: float) -> bool:
    return abs(a - b) <= 0.005 * max(abs(a), abs(b), 1e-9)


def source_text(art: Article, summary: dict) -> str:
    """요약할 때 GPT가 본 원문 (본문, 없으면 짧은 설명 + 제목)"""
    if summary.get("based_on") == "본문":
        return art["content"][:config.MAX_CONTENT_CHARS] + "\n" + art["title"]
    return art["snippet"] + "\n" + art["title"]


def summary_sentences(s: dict) -> list[str]:
    return [s["headline"], s["one_liner"], *s["key_points"], s["why_it_matters"]]


def fingerprint(s: dict) -> str:
    return hashlib.sha1(json.dumps(summary_sentences(s), ensure_ascii=False).encode()).hexdigest()[:12]


# ─────────────────────────────────────────────────────
# 노드 9. 규칙 검사
# ─────────────────────────────────────────────────────
def check_one(art: Article, s: dict) -> tuple[list[str], list[str]]:
    """기사 하나의 요약을 검사합니다. → (확실한 문제, GPT 심판이 특히 확인할 것)"""
    issues, hints = [], []
    src = source_text(art, s)
    src_values = [v for _, v in numbers_in(src)]
    src_digits = re.sub(r"[,\s]", "", src)

    # 숫자: 요약에 나온 숫자마다 원문에 같은 값이 있어야 함
    for raw, value in numbers_in(" ".join(summary_sentences(s))):
        if not re.search(r"\d", raw):
            continue                       # '두 배'처럼 글자로 쓴 숫자는 GPT 심판에게 맡김
        if any(_same_value(value, v) for v in src_values) or re.sub(r"[,\s]", "", raw) in src_digits:
            continue
        issues.append(f"원문에서 '{raw}'에 해당하는 숫자를 찾을 수 없어요")

    # 형식
    if len(s["headline"]) > config.HEADLINE_MAX:
        issues.append(f"제목이 {len(s['headline'])}자예요. {config.HEADLINE_MAX}자 이내로 줄여 주세요")
    if not s["key_points"]:
        issues.append("핵심 내용이 비어 있어요")
    if not s["one_liner"] or not s["why_it_matters"]:
        issues.append("한 줄 요약이나 '왜 중요한가'가 비어 있어요")

    # 영어 이름: 원문에 없으면 '의심'만 (한국어 원문을 영어로 옮겨 적었을 수도 있으니)
    src_lower = src.lower()
    for word in sorted(set(re.findall(r"[A-Za-z][A-Za-z0-9.+-]*[A-Za-z0-9]", " ".join(summary_sentences(s))))):
        if word.lower() not in config.COMMON_LATIN and word.lower() not in src_lower:
            hints.append(f"'{word}'라는 이름이 원문에 그대로는 없어요. 원문의 어떤 이름을 옮긴 것인지, 맞게 옮겼는지")
    return issues, hints


def rule_check(state: NewsState) -> dict:
    summaries = state.get("summaries", {})
    issues_by_id, hints_by_id, log = {}, {}, []
    for art in state.get("selected", []):
        s = summaries.get(art["id"])
        if not s:
            issues_by_id[art["id"]] = ["요약이 없어요 (요약 단계에서 실패)"]
            continue
        issues, hints = check_one(art, s)
        if issues:
            issues_by_id[art["id"]] = issues
            log.append(f"[규칙 검사] {s['headline'][:30]}: " + " / ".join(issues))
        if hints:
            hints_by_id[art["id"]] = hints
    passed = len(state.get("selected", [])) - len(issues_by_id)
    print(f"  [규칙 검사] 통과 {passed}건 · 문제 {len(issues_by_id)}건")
    return {"review_issues": issues_by_id, "review_hints": hints_by_id, "review_log": log}


# ─────────────────────────────────────────────────────
# 노드 10. GPT 심판
# ─────────────────────────────────────────────────────
class SentenceVerdict(BaseModel):
    sentence: str = Field(description="검사한 요약 문장 그대로")
    supported: bool = Field(description="원문 내용으로 뒷받침되면 true")
    problem: str = Field(description="뒷받침되지 않으면 원문과 무엇이 다른지 한국어 한 문장, 괜찮으면 빈 문자열")


class JudgeOut(BaseModel):
    verdicts: list[SentenceVerdict] = Field(description="요약 문장 전부에 대한 판정")


JUDGE_PROMPT = """너는 뉴스 요약을 검사하는 꼼꼼한 팩트체커야.
원문과 요약 문장들이 주어지면, 문장마다 원문으로 뒷받침되는지 판정해.

판정 기준
- 원문에 있는 사실을 옮기거나 줄인 문장 → 통과
- 영어 금액·숫자를 한국어 단위로 정확히 바꾼 것(예: $2.9 billion → 29억 달러) → 통과
- '왜 중요한가' 문장은 원문 내용을 바탕으로 한 합리적인 해석이면 통과. 조심스러운 전망('~할 수 있다')도 통과
- 원문에 없는 숫자·이름·사건을 새로 주장하거나, 원문과 뜻이 다르거나, 원문보다 단정적으로 쓴 문장 → 실패
- 애매하면 실패로 하지 말고 통과로 해. 확실히 틀린 것만 실패로 해."""


def llm_judge(state: NewsState) -> dict:
    summaries = state.get("summaries", {})
    issues_by_id = {k: list(v) for k, v in state.get("review_issues", {}).items()}
    hints_by_id = state.get("review_hints", {})
    passed = dict(state.get("review_passed", {}))
    log, errors = [], []

    for art in state.get("selected", []):
        s = summaries.get(art["id"])
        if not s or art["id"] in issues_by_id:
            continue                                   # 규칙 검사에서 이미 문제가 나온 기사
        mark = fingerprint(s)
        if passed.get(art["id"]) == mark:
            continue                                   # 같은 요약을 이미 통과시킨 적 있음
        if not config.USE_LLM_JUDGE:
            passed[art["id"]] = mark
            continue

        sentences = summary_sentences(s)
        user = (f"원문:\n{source_text(art, s)}\n\n---\n요약 문장:\n"
                + "\n".join(f"{i + 1}. {t}" for i, t in enumerate(sentences)))
        if hints_by_id.get(art["id"]):
            user += "\n\n특히 확인할 것:\n" + "\n".join(f"- {h}" for h in hints_by_id[art["id"]])
        try:
            out, _ = ask_structured(JUDGE_PROMPT, user, JudgeOut)
        except LLMError as e:
            # 심판이 안 되면 멈추지 않음: 규칙 검사만 통과한 채로 넘어가고 기록을 남김
            errors.append(f"[GPT 심판] {e} → 규칙 검사만 통과한 채로 진행")
            passed[art["id"]] = mark
            continue

        problems = [f"'{v.sentence[:40]}' → {v.problem or '원문에 근거가 없어요'}"
                    for v in out.verdicts if not v.supported]
        if problems:
            issues_by_id[art["id"]] = problems
            log.append(f"[GPT 심판] {s['headline'][:30]}: " + " / ".join(problems))
        else:
            passed[art["id"]] = mark

    bad = sum(1 for a in state.get("selected", []) if a["id"] in issues_by_id)
    print(f"  [GPT 심판] 문제 있는 요약 {bad}건")
    return {"review_issues": issues_by_id, "review_passed": passed, "review_log": log, "errors": errors}


# ─────────────────────────────────────────────────────
# 분기 11. 문제가 남아 있나?
# ─────────────────────────────────────────────────────
def route(state: NewsState) -> str:
    issues = state.get("review_issues", {})
    return "fix" if any(a["id"] in issues for a in state.get("selected", [])) else "done"


# ─────────────────────────────────────────────────────
# 노드 12. 예비 기사로 교체
# ─────────────────────────────────────────────────────
def swap_failed(state: NewsState) -> dict:
    selected, reserve = list(state.get("selected", [])), list(state.get("reserve", []))
    summaries = dict(state.get("summaries", {}))
    issues_by_id = dict(state.get("review_issues", {}))
    retry_counts = state.get("retry_counts", {})
    log, dropped = [], []

    for art in list(selected):
        if art["id"] not in issues_by_id:
            continue
        if retry_counts.get(art["id"], 0) < config.MAX_SUMMARY_TRIES and art["id"] in summaries:
            log.append(f"[다시 요약] {art['title'][:40]}")
            continue                                   # summarize가 문제 목록을 보고 다시 요약함
        # 이미 충분히 다시 해 봤는데도 문제 → 빼고 예비 기사로
        selected.remove(art)
        summaries.pop(art["id"], None)
        issues_by_id.pop(art["id"], None)
        dropped.append(art["title"])
        if reserve:
            new = reserve.pop(0)
            selected.append(new)
            log.append(f"[교체] '{art['title'][:30]}' 빼고 예비 기사 '{new['title'][:30]}' 넣음")
        else:
            log.append(f"[제외] '{art['title'][:30]}' 뺌 (남은 예비 기사 없음)")

    for line in log:
        print(f"  {line}")
    return {"selected": selected, "reserve": reserve, "summaries": summaries,
            "review_issues": issues_by_id, "review_log": log, "dropped": dropped}
