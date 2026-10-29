"""
GPT에게 '정해진 모양(구조화 출력)'으로 답을 받는 도구. 선별 · 요약 · 검수 단계가 같이 씁니다.
"""
import os

import openai
from langchain_openai import ChatOpenAI
from pydantic import BaseModel

import config


CALLS: dict[str, int] = {}   # 이번 실행에서 모델별로 GPT를 몇 번 불렀는지 (store/metrics.jsonl에 기록)


class LLMError(Exception):
    """GPT 호출이 실패했을 때, 이유를 쉬운 말로 담아 둡니다."""


def ask_structured(system: str, user: str, schema: type[BaseModel]) -> tuple[BaseModel, str]:
    """GPT에게 묻고 schema 모양의 답을 받습니다. → (답, 사용한 모델 이름)
    config.LLM_MODELS 순서대로 시도하고, 내 키로 못 쓰는 모델이면 다음 모델로 넘어갑니다.
    """
    if not os.getenv("OPENAI_API_KEY"):
        raise LLMError("OPENAI_API_KEY가 없어요 (.env 확인)")
    messages = [("system", system), ("user", user)]
    skipped = []
    for model in config.LLM_MODELS:
        llm = ChatOpenAI(model=model, timeout=90, max_retries=2)
        structured = llm.with_structured_output(schema, method="json_schema", strict=True)
        try:
            answer = structured.invoke(messages)
            CALLS[model] = CALLS.get(model, 0) + 1
            return answer, model
        except (openai.NotFoundError, openai.PermissionDeniedError):
            skipped.append(model)            # 없는 모델이거나 이 키로 못 쓰는 모델
            print(f"  [GPT] {model}: 이 키로 쓸 수 없어서 다음 모델로 넘어감")
        except openai.BadRequestError as e:
            text = str(e).lower()
            if "model" in text or "response_format" in text or "json_schema" in text:
                skipped.append(model)        # 이 모델이 구조화 출력을 지원하지 않는 경우 등
                print(f"  [GPT] {model}: 이 방식으로 쓸 수 없어서 다음 모델로 넘어감")
                continue
            raise LLMError(f"OpenAI가 요청을 거절했어요: {str(e)[:200]}")
        except openai.AuthenticationError:
            raise LLMError("OpenAI 키가 잘못됐어요 (.env의 OPENAI_API_KEY 확인)")
        except openai.RateLimitError as e:
            if "quota" in str(e).lower():
                raise LLMError("OpenAI 크레딧이 부족해요 (platform.openai.com에서 결제 · 한도 확인)")
            raise LLMError("OpenAI에 너무 자주 요청했어요. 잠시 후 다시 해 보세요")
        except openai.APIConnectionError:
            raise LLMError("OpenAI에 접속하지 못했어요 (인터넷 연결 확인)")
    raise LLMError(f"쓸 수 있는 모델이 없어요 (시도한 모델: {', '.join(skipped)})")
