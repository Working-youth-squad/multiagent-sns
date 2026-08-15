"""실패 원인 1줄 분석 (C6, FR-W4) — LLM은 서술만, 장애는 삼켜 None 반환.

FR-W4: "LLM 호출 실패 시 분류명만이라도 발송". 이 함수가 None을 돌려주면 상위
(dispatch)는 `error_class`만으로 알림을 보낸다 — 알림 경로는 LLM 장애에 막히지 않는다.
모델은 주입받는다([sns.agents.analyst]와 동일 seam): 테스트는 ScriptedChatModel.
"""

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage

from sns.notify.alerts import Alert

_SYSTEM = (
    "당신은 SNS 발행 파이프라인의 운영 보조다. 오류 분류와 원문을 보고 가장 그럴듯한 "
    "원인을 한국어 한 문장으로만 말한다. 근거가 약하면 단정하지 말고 '~로 보인다'로 쓴다. "
    "한 문장만 출력한다."
)


def _message_text(content: object) -> str:
    """메시지 content → 텍스트. 일부 모델(Gemini 3.x)은 블록 리스트를 반환하므로
    text 블록만 추출한다([sns.agents.analyst._message_text]와 동일 처리)."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and block.get("type") == "text":
                parts.append(str(block.get("text", "")))
        return "".join(parts)
    return str(content)


def _first_line(text: str, *, limit: int = 200) -> str:
    for raw in text.splitlines():
        line = raw.strip()
        if line:
            return line if len(line) <= limit else line[: limit - 1] + "…"
    return ""


def analyze_cause(model: BaseChatModel, alert: Alert) -> str | None:
    """실패 알림의 원인 1줄. 분석거리(원문·분류)가 없거나 LLM 장애면 None."""
    if not alert.error_raw and not alert.error_class:
        return None
    prompt = f"분류: {alert.error_class}\n원문: {alert.error_raw}\n한 문장 원인:"
    try:
        resp = model.invoke([SystemMessage(content=_SYSTEM), HumanMessage(content=prompt)])
        line = _first_line(_message_text(resp.content))
    except Exception:
        return None  # LLM 장애 — 상위가 분류명으로 폴백
    return line or None
