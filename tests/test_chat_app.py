"""챗봇 웹 앱 — 폼 POST 관통, 랭킹 원본 박제, 시드 착수, 실패가 발화를 삼키지 않음.

네트워크 0: LLM은 `ScriptedChatModel`, 키워드 조회는 `rank_fn` 주입.
"""

from datetime import UTC, datetime
from typing import Any

from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

from sns.chat.agent import SeedRequest
from sns.chat.drafts import ExportItem
from sns.chat.store import InMemoryChatStore
from sns.research.keywords import aggregate
from sns.research.ranking import KeywordRanking
from sns.tools.contracts import SourceResult
from sns.web.chat.app import create_app
from tests.test_chat_agent import ScriptedChatModel, _tool


def _now() -> datetime:
    return datetime.now(tz=UTC)


def _ranking() -> KeywordRanking:
    return aggregate(
        "개발자",
        (
            SourceResult(
                source="naver_autocomplete", ok=True, items=("개발자 연봉", "개발자 취업")
            ),
            SourceResult(source="google_suggest", ok=True, items=("개발자 취업", "개발자 연봉")),
            SourceResult(source="youtube_suggest", ok=False),
        ),
    )


def _rank(query: str, **kwargs: Any) -> KeywordRanking:
    return _ranking()


def _client(store: InMemoryChatStore, script: list[AIMessage], **kwargs: Any) -> TestClient:
    app = create_app(
        store,
        model=ScriptedChatModel(messages=iter(script)),
        rank_fn=kwargs.pop("rank_fn", _rank),
        **kwargs,
    )
    return TestClient(app)


_SEARCH_SCRIPT = [
    _tool("search_keywords", {"query": "개발자"}),
    AIMessage(content="세 소스에서 찾았습니다."),
]


def test_start_conversation_runs_a_turn_and_redirects() -> None:
    store = InMemoryChatStore()
    client = _client(store, _SEARCH_SCRIPT)

    response = client.post("/conversations", data={"text": "개발자"}, follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"].startswith("/c/")

    cid = response.headers["location"].removeprefix("/c/").split("#")[0]
    roles = [m.role for m in store.messages(cid)]
    assert roles == ["user", "ranking", "assistant"]
    # 제목은 첫 발화에서 딴다(LLM 추가 호출 없음).
    assert store.get_conversation(cid).title == "개발자"  # type: ignore[union-attr]


def test_ranking_is_persisted_as_untouched_original() -> None:
    """LLM이 다시 쓴 문장이 아니라 `ranking_to_dict` 산출 그대로여야 한다."""
    store = InMemoryChatStore()
    client = _client(store, _SEARCH_SCRIPT)
    cid = _start(client)

    ranking_msg = next(m for m in store.messages(cid) if m.role == "ranking")
    assert ranking_msg.payload is not None
    payload = ranking_msg.payload
    assert payload["query"] == "개발자"
    assert payload["filter_mode"] == aggregate_mode()
    assert payload["sources_failed"] == ["youtube_suggest"]
    # 후보 항목이 통계 원본을 그대로 들고 있다.
    assert "rank_std" in payload["candidates"][0]  # type: ignore[index]


def aggregate_mode() -> str:
    return _ranking().filter_mode


def test_conversation_page_renders_table_not_llm_prose() -> None:
    store = InMemoryChatStore()
    client = _client(store, _SEARCH_SCRIPT)
    cid = _start(client)

    page = client.get(f"/c/{cid}")
    assert page.status_code == 200
    assert "개발자 연봉" in page.text
    assert "세 소스에서 찾았습니다." in page.text
    assert "youtube_suggest" in page.text  # 실패 소스 노출


def test_second_turn_appends_without_hidden_state() -> None:
    """폼에 이력을 실어 나르지 않는다 — 서버가 DB에서 다시 읽는다."""
    store = InMemoryChatStore()
    client = _client(
        store,
        [
            *_SEARCH_SCRIPT,
            AIMessage(content="연봉 쪽으로 좁혀볼까요?"),
        ],
    )
    cid = _start(client)

    page = client.get(f"/c/{cid}")
    assert "hidden" not in page.text

    client.post(f"/c/{cid}/messages", data={"text": "연봉 쪽으로"}, follow_redirects=False)
    bodies = [m.body for m in store.messages(cid) if m.role in ("user", "assistant")]
    assert bodies == ["개발자", "세 소스에서 찾았습니다.", "연봉 쪽으로", "연봉 쪽으로 좁혀볼까요?"]


def test_llm_failure_does_not_swallow_user_message() -> None:
    store = InMemoryChatStore()

    class Exploding(ScriptedChatModel):
        def invoke(self, *args: Any, **kwargs: Any) -> Any:
            raise RuntimeError("모델 호출 실패")

    app = create_app(store, model=Exploding(messages=iter([])), rank_fn=_rank)
    client = TestClient(app)
    response = client.post("/conversations", data={"text": "개발자"}, follow_redirects=False)
    assert response.status_code == 303

    cid = response.headers["location"].removeprefix("/c/").split("#")[0]
    messages = store.messages(cid)
    assert messages[0].role == "user" and messages[0].body == "개발자"  # 발화는 살아 있다
    assert messages[-1].role == "system"
    assert "답변을 만들지 못했습니다" in messages[-1].body


def test_seed_starts_cycle_and_records_it() -> None:
    store = InMemoryChatStore()
    started: list[tuple[str, SeedRequest]] = []

    client = _client(
        store,
        [
            _tool("confirm_topic", {"title": "개발자 연봉 협상", "summary": "협상 팁 3가지"}),
            AIMessage(content="초안을 만들게요."),
        ],
        start_cycle_fn=lambda cid, request: started.append((cid, request)),
    )
    cid = _start(client, text="연봉 얘기로 만들어줘")

    assert len(started) == 1
    assert started[0][0] == cid
    assert started[0][1].topic.title == "개발자 연봉 협상"
    system = [m for m in store.messages(cid) if m.role == "system"]
    assert "초안 제작을 시작했습니다" in system[-1].body


def test_seed_without_wiring_says_so() -> None:
    """콘텐츠 제작이 배선되지 않은 배치도 유효하다 — 다만 조용히 넘기지 않는다."""
    store = InMemoryChatStore()
    client = _client(
        store,
        [
            _tool("confirm_topic", {"title": "개발자 연봉 협상", "summary": "협상 팁"}),
            AIMessage(content="확정했습니다."),
        ],
    )
    cid = _start(client, text="만들어줘")

    system = [m for m in store.messages(cid) if m.role == "system"]
    assert "초안은 만들지 않습니다" in system[-1].body


def test_seed_start_failure_is_recorded() -> None:
    store = InMemoryChatStore()

    def boom(conversation_id: str, request: SeedRequest) -> None:
        raise RuntimeError("스레드 기동 실패")

    client = _client(
        store,
        [
            _tool("confirm_topic", {"title": "개발자 연봉", "summary": "팁"}),
            AIMessage(content="시작합니다."),
        ],
        start_cycle_fn=boom,
    )
    cid = _start(client, text="만들어줘")

    system = [m for m in store.messages(cid) if m.role == "system"]
    assert "시작하지 못했습니다" in system[-1].body


def test_blank_message_is_rejected_without_touching_the_thread() -> None:
    store = InMemoryChatStore()
    client = _client(store, _SEARCH_SCRIPT)
    cid = _start(client)
    before = len(store.messages(cid))

    response = client.post(f"/c/{cid}/messages", data={"text": "   "})
    assert response.status_code == 400
    assert len(store.messages(cid)) == before


def test_blank_first_message_is_rejected() -> None:
    store = InMemoryChatStore()
    client = _client(store, [])
    response = client.post("/conversations", data={"text": ""})
    assert response.status_code == 400
    assert store.list_conversations() == ()


def test_unknown_conversation_is_404() -> None:
    store = InMemoryChatStore()
    client = _client(store, [])
    assert client.get("/c/없는-id").status_code == 404
    assert client.post("/c/없는-id/messages", data={"text": "안녕"}).status_code == 404


def test_index_lists_past_conversations() -> None:
    store = InMemoryChatStore()
    client = _client(store, _SEARCH_SCRIPT)
    _start(client)

    page = client.get("/")
    assert page.status_code == 200
    assert "개발자" in page.text


def _start(client: TestClient, *, text: str = "개발자") -> str:
    response = client.post("/conversations", data={"text": text}, follow_redirects=False)
    assert response.status_code == 303
    return response.headers["location"].removeprefix("/c/").split("#")[0]


# ── 미디어 중계 라우트 ─────────────────────────────────────────────────


def test_media_route_serves_bytes_by_asset_id() -> None:
    store = InMemoryChatStore()
    client = _client(
        store, [], load_media_fn=lambda aid: (b"PNGDATA", "image/png") if aid == "ma-1" else None
    )
    ok = client.get("/media/ma-1")
    assert ok.status_code == 200
    assert ok.content == b"PNGDATA"
    assert ok.headers["content-type"].startswith("image/png")
    assert client.get("/media/없는-id").status_code == 404


def test_media_route_serves_mp4_with_a_video_content_type() -> None:
    """수동 영상 다운로드가 여기 걸려 있다 — 브라우저가 mp4를 재생/저장할 근거."""
    client = _client(InMemoryChatStore(), [], load_media_fn=lambda aid: (b"MP4DATA", "video/mp4"))
    ok = client.get("/media/ma-1")
    assert ok.status_code == 200
    assert ok.headers["content-type"].startswith("video/mp4")


def test_video_download_gets_an_mp4_filename() -> None:
    """확장자는 MIME에서 딴다 — .png로 내려주면 사람이 파일을 못 연다."""
    client = _client(InMemoryChatStore(), [], load_media_fn=lambda aid: (b"MP4DATA", "video/mp4"))
    got = client.get("/media/ma-1", params={"download": "1", "name": "쇼츠"})
    disposition = got.headers["content-disposition"]
    assert disposition.startswith("attachment")
    assert ".mp4" in disposition


def test_media_route_is_404_when_unwired() -> None:
    """미배선 배치에서도 화면은 살아 있어야 한다(카드가 자리표시로 그려진다)."""
    client = _client(InMemoryChatStore(), [])
    assert client.get("/media/ma-1").status_code == 404


def test_media_store_failure_does_not_500_the_page() -> None:
    def boom(asset_id: str) -> tuple[bytes, str] | None:
        raise OSError("저장소 장애")

    client = _client(InMemoryChatStore(), [], load_media_fn=boom)
    assert client.get("/media/ma-1").status_code == 404


# ── 수동 발행용 내보내기 라우트 ────────────────────────────────────────


def _export_item() -> ExportItem:
    return ExportItem(
        content_item_id="ci-1",
        topic_title="개발자 포트폴리오 작성법",
        channel_label="instagram @demo",
        platform="instagram",
        content_status="approved",
        body="훅: 시작이 반이다\n#개발자",
        media_asset_id="ma-1",
    )


def _export_client(**kwargs: Any) -> TestClient:
    item = _export_item()
    return _client(
        InMemoryChatStore(),
        [],
        load_export_fn=kwargs.pop("load_export_fn", lambda cid: item if cid == "ci-1" else None),
        **kwargs,
    )


def test_export_page_renders_and_404s_for_unknown() -> None:
    client = _export_client()
    ok = client.get("/export/ci-1")
    assert ok.status_code == 200
    assert "개발자 포트폴리오 작성법" in ok.text
    assert client.get("/export/없는-id").status_code == 404


def test_export_page_is_404_when_unwired() -> None:
    assert _client(InMemoryChatStore(), []).get("/export/ci-1").status_code == 404


def test_caption_download_is_utf8_attachment() -> None:
    r = _export_client().get("/export/ci-1/caption.txt")
    assert r.status_code == 200
    assert r.content.decode("utf-8") == _export_item().body
    disposition = r.headers["content-disposition"]
    assert disposition.startswith("attachment;")
    # 한글 파일명은 RFC 5987로 실어야 헤더에서 깨지지 않는다.
    assert "filename*=UTF-8''" in disposition


def test_attachment_header_keeps_an_ascii_fallback_name() -> None:
    """제목이 전부 비ASCII여도 이름 없는 다운로드가 되지 않아야 한다."""
    r = _export_client().get("/export/ci-1/caption.txt")
    disposition = r.headers["content-disposition"]
    ascii_part = disposition.split('filename="')[1].split('"')[0]
    assert ascii_part
    assert ascii_part.isascii()


def test_media_download_flag_switches_to_attachment() -> None:
    client = _export_client(load_media_fn=lambda aid: (b"PNG", "image/png"))
    inline = client.get("/media/ma-1")
    assert "content-disposition" not in inline.headers
    attached = client.get("/media/ma-1", params={"download": "1", "name": "카드"})
    assert attached.headers["content-disposition"].startswith("attachment;")
    assert attached.content == b"PNG"


def test_export_lookup_failure_is_404_not_500() -> None:
    def boom(content_item_id: str) -> ExportItem | None:
        raise OSError("DB 장애")

    client = _export_client(load_export_fn=boom)
    assert client.get("/export/ci-1").status_code == 404
    assert client.get("/export/ci-1/caption.txt").status_code == 404
