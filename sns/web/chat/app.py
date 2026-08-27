"""키워드 챗봇 FastAPI 앱 — HTTP 어댑터 ([sns.web.onboarding.app] 규율 동형).

폼 POST → 서버가 대화에 append → 303 리다이렉트 → GET이 대화 전량을 다시 그린다.
JS 0줄이고 hidden input으로 이력을 실어 나르지 않는다. 새로고침·뒤로가기에 대화가
망가지지 않고(POST 재전송 없음), 클라이언트가 이력을 고칠 수 없다.

`create_app(store, model=..., ...)`이 협력자를 주입받는다:

- `model`: 대화 진행 LLM. **필수다** — 이 앱의 본체가 대화라, 없으면 띄울 이유가 없다
  (온보딩의 탈부착 협력자들과 다른 점이다. 거기선 인터뷰 완주가 본체였다).
- `rank_fn`: `rank_keywords` 대체 지점. 테스트가 네트워크 없이 도는 자리.
- `start_cycle_fn`: 확정된 주제로 콘텐츠 사이클을 띄운다(FR-W5). None이면 챗봇은
  키워드 조회에서 멈춘다 — 그것도 유효한 배치다.
- `load_media_fn`: 만들어진 카드 이미지를 대화에 보여주기 위한 조회. None이면 초안
  카드가 이미지 자리를 "이미지 없음"으로 그린다(대화는 그대로 동작한다).
- `load_export_fn`: 수동 발행용 내보내기 화면의 재료 조회. None이면 그 화면이 404다.
- `formats`·`methods`: **이 서버가 배선한 것**. 대화가 여기 없는 것을 고를 수 없다
  (Capability Gate를 대화까지 — [sns.chat.agent._capabilities_block]).

**LLM 실패가 사용자 발화를 삼키지 않는다.** 사용자 메시지를 먼저 append 한 뒤 턴을
돌리므로, 턴이 죽어도 발화는 대화에 남고 실패 사실이 system 메시지로 붙는다.
"""

from collections.abc import Callable, Sequence
from urllib.parse import quote

from fastapi import FastAPI, Form
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from langchain_core.language_models import BaseChatModel

from sns.chat.agent import RankFn, SeedRequest, ranking_payload, run_chat_turn
from sns.chat.drafts import ExportItem
from sns.chat.store import ChatStore, ConversationNotFound
from sns.research import rank_keywords
from sns.runner.formats import FormatChoice
from sns.tools.contracts import VideoMethod
from sns.web.chat.render import (
    ChatChannel,
    render_conversation,
    render_export,
    render_index,
    render_not_found,
)

StartCycleFn = Callable[[str, SeedRequest, str | None], None]
"""(conversation_id, 시드 요청, channel_id) → 콘텐츠 사이클 착수. 즉시 반환할 것 —
진행·결과는 구현이 system 메시지로 대화에 append 한다. 동기로 완주하면 브라우저가
분 단위로 멈춘다.

**주제만이 아니라 `SeedRequest`를 받는다.** 포맷·제작 방식이 주제와 함께 대화에서
정해지므로, 시그니처가 주제만 나르면 배선이 그 선택을 알 길이 없어 포맷을 하드코딩하게
된다 — 실제로 그랬다. `channel_id`는 대화가 묶인 채널이다 — None이면 예전처럼 모든
hybrid 채널에 시드한다(채널 없이 시작한 대화의 하위 호환)."""

ChannelsFn = Callable[[], Sequence[ChatChannel]]
"""채널 선택 UI의 재료 — hybrid 채널 + 프로필 기반 주제 제안. None이면 채널 없는
예전 화면 그대로다(테스트·경량 배치)."""

LoadExportFn = Callable[[str], ExportItem | None]
"""content_item_id → 수동 발행용 재료. None이면 내보내기 화면이 404.

대화 payload에 본문 전문을 싣지 않고 조회로 가져오는 이유: payload는 **그때의 기록**이라
승인 화면에서 사람이 고친 본문이 반영되지 않는다. 손으로 올릴 캡션은 최신이어야 한다."""

LoadMediaFn = Callable[[str], tuple[bytes, str] | None]
"""media_asset_id → (바이트, MIME). 없으면 None.

**id로만 받는다** — 저장소 URL을 쿼리스트링으로 받으면 그 라우트가 임의 파일·임의 호스트
읽기 통로가 된다(렌더 산출은 file:// URI다). id는 우리 DB가 발급한 uuid라 그 통로가 없다."""

TITLE_MAX = 40


def _made_of(request: SeedRequest) -> str:
    """사용자에게 되읽어 줄 "무엇으로 만드는지". 고른 것을 화면이 확인해 준다 —
    LLM의 답변만으로는 실제로 무엇이 배선에 넘어갔는지 알 수 없다."""
    if request.content_format == "card":
        return "카드로"
    return f"영상({request.method})으로" if request.method else "영상으로"


def create_app(
    store: ChatStore,
    *,
    model: BaseChatModel,
    rank_fn: RankFn = rank_keywords,
    start_cycle_fn: StartCycleFn | None = None,
    load_media_fn: LoadMediaFn | None = None,
    load_export_fn: LoadExportFn | None = None,
    channels_fn: ChannelsFn | None = None,
    exclude: Sequence[str] | None = None,
    formats: Sequence[FormatChoice] = ("card",),
    methods: Sequence[VideoMethod] = ("template",),
) -> FastAPI:
    app = FastAPI(title="키워드 챗봇")

    def _channels() -> tuple[ChatChannel, ...]:
        if channels_fn is None:
            return ()
        try:
            return tuple(channels_fn())
        except Exception:  # 채널 조회 실패가 대화 화면 전체를 죽이지 않는다
            return ()

    def advance(conversation_id: str, text: str) -> None:
        """한 턴 진행 — 사용자 발화 저장 → LLM → 랭킹 박제 → 답변 저장 → (시드면) 사이클."""
        store.append(conversation_id, role="user", body=text)
        history = store.messages(conversation_id)[:-1]  # 방금 넣은 발화는 user_text로 따로 준다

        try:
            turn = run_chat_turn(
                model,
                history=history,
                user_text=text,
                rank_fn=rank_fn,
                exclude=exclude,
                formats=formats,
                methods=methods,
            )
        except Exception as exc:  # 모델·네트워크 실패 — 대화는 살아 있어야 한다
            store.append(
                conversation_id,
                role="system",
                body=f"답변을 만들지 못했습니다: {exc}",
                payload={"kind": "turn_failed", "error": str(exc)},
            )
            return

        # 랭킹을 먼저 박제한다 — 화면에서 표가 해설보다 위에 오게(해설이 표를 가리키므로).
        # 원본 그대로다: LLM은 이 숫자를 본 적이 없다([sns.chat.agent] 참조).
        for ranking in turn.rankings:
            store.append(conversation_id, role="ranking", payload=ranking_payload(ranking))

        if turn.reply.strip():
            store.append(conversation_id, role="assistant", body=turn.reply)

        if turn.seed_request is not None:
            _seed(conversation_id, turn.seed_request)

    def _seed(conversation_id: str, request: SeedRequest) -> None:
        topic = request.topic
        if start_cycle_fn is None:
            store.append(
                conversation_id,
                role="system",
                body=(
                    f"주제 ‘{topic.title}’를 확정했습니다. "
                    "다만 이 서버에는 콘텐츠 제작이 연결돼 있지 않아 초안은 만들지 않습니다."
                ),
                payload={"kind": "seed_unwired", "title": topic.title},
            )
            return
        bound = store.get_conversation(conversation_id)
        try:
            start_cycle_fn(conversation_id, request, bound.channel_id if bound else None)
        except Exception as exc:  # 착수 실패도 대화에 남긴다 — 조용히 삼키지 않는다
            store.append(
                conversation_id,
                role="system",
                body=f"초안 제작을 시작하지 못했습니다: {exc}",
                payload={"kind": "seed_failed", "error": str(exc)},
            )
            return
        store.append(
            conversation_id,
            role="system",
            body=(
                f"주제 ‘{topic.title}’로 {_made_of(request)} 초안 제작을 시작했습니다. "
                "몇 분 걸립니다 — 이 페이지를 새로고침하면 진행 상황이 보입니다."
            ),
            payload={
                "kind": "seed_started",
                "title": topic.title,
                "content_format": request.content_format,
                "method": request.method,
            },
        )

    def _selected(channels: tuple[ChatChannel, ...], channel: str) -> str | None:
        """채널 선택 규칙 — 기본은 첫 채널, 'all'은 전체 보기, 모르는 id는 전체."""
        if channel == "all" or not channels:
            return None
        if any(c.channel_id == channel for c in channels):
            return channel
        return channels[0].channel_id if not channel else None

    @app.get("/", response_class=HTMLResponse)
    def index(channel: str = "") -> HTMLResponse:
        channels = _channels()
        return HTMLResponse(
            render_index(
                store.list_conversations(),
                channels=channels,
                selected=_selected(channels, channel),
            )
        )

    @app.post("/conversations", response_model=None)
    def start(
        text: str = Form(default=""), channel_id: str = Form(default="")
    ) -> HTMLResponse | RedirectResponse:
        channels = _channels()
        # 배선된 채널만 묶는다 — 폼 위조로 임의 id를 대화에 심는 통로를 막는다.
        bound = channel_id if any(c.channel_id == channel_id for c in channels) else None
        first = text.strip()
        if not first:
            return HTMLResponse(
                render_index(
                    store.list_conversations(),
                    channels=channels,
                    selected=bound,
                    error="메시지를 입력해주세요.",
                ),
                status_code=400,
            )
        conversation_id = store.create_conversation(channel_id=bound)
        # 제목은 첫 발화에서 딴다. LLM에 따로 묻지 않는 이유는 비용·지연이고, 목록
        # 화면용이라 정확할 필요가 없다.
        store.set_title(conversation_id, first[:TITLE_MAX])
        advance(conversation_id, first)
        return RedirectResponse(f"/c/{conversation_id}#bottom", status_code=303)

    @app.get("/c/{conversation_id}", response_class=HTMLResponse)
    def conversation(conversation_id: str) -> HTMLResponse:
        found = store.get_conversation(conversation_id)
        if found is None:
            return HTMLResponse(render_not_found(), status_code=404)
        label = None
        if found.channel_id:
            label = next((c.label for c in _channels() if c.channel_id == found.channel_id), None)
        return HTMLResponse(
            render_conversation(found, store.messages(conversation_id), channel_label=label)
        )

    @app.post("/c/{conversation_id}/messages", response_model=None)
    def send(conversation_id: str, text: str = Form(default="")) -> HTMLResponse | RedirectResponse:
        found = store.get_conversation(conversation_id)
        if found is None:
            return HTMLResponse(render_not_found(), status_code=404)
        message = text.strip()
        if not message:
            return HTMLResponse(
                render_conversation(
                    found, store.messages(conversation_id), error="메시지를 입력해주세요."
                ),
                status_code=400,
            )
        try:
            advance(conversation_id, message)
        except ConversationNotFound:  # 턴 도중 대화가 사라진 경우(동시 삭제)
            return HTMLResponse(render_not_found(), status_code=404)
        return RedirectResponse(f"/c/{conversation_id}#bottom", status_code=303)

    @app.get("/media/{asset_id}", response_model=None)
    def media(asset_id: str, download: str = "", name: str = "") -> Response:
        """초안 카드 이미지. 대화가 file:// URI를 직접 가리킬 수 없어 서버가 중계한다.

        `download=1`이면 첨부로 내려준다 — 수동 발행 경로에서 사람이 파일을 받아
        플랫폼 앱에 직접 올린다.
        """
        if load_media_fn is None:
            return Response(status_code=404)
        try:
            found = load_media_fn(asset_id)
        except Exception:  # 저장소 장애가 대화 화면 전체를 죽이지 않는다
            return Response(status_code=404)
        if found is None:
            return Response(status_code=404)
        data, media_type = found
        # 자산은 불변이다(checksum이 파일명) — 재방문마다 다시 받을 이유가 없다.
        headers = {"Cache-Control": "max-age=3600"}
        if download:
            ext = media_type.rsplit("/", 1)[-1]
            headers["Content-Disposition"] = _attachment(f"{name or asset_id}.{ext}")
        return Response(data, media_type=media_type, headers=headers)

    @app.get("/export/{content_item_id}", response_class=HTMLResponse)
    def export(content_item_id: str) -> HTMLResponse:
        """수동 발행용 내보내기 — 잘리지 않은 캡션 + 이미지 + 내려받기."""
        item = _export_item(content_item_id)
        if item is None:
            return HTMLResponse(render_not_found(), status_code=404)
        return HTMLResponse(render_export(item))

    @app.get("/export/{content_item_id}/caption.txt", response_model=None)
    def caption(content_item_id: str) -> Response:
        """캡션 원문 파일. 화면에서 복사해도 되지만, 파일이 있으면 옮겨 붙이기가 확실하다."""
        item = _export_item(content_item_id)
        if item is None:
            return Response(status_code=404)
        return Response(
            item.body.encode("utf-8"),
            media_type="text/plain; charset=utf-8",
            headers={"Content-Disposition": _attachment(f"{item.filename_stem}.txt")},
        )

    def _export_item(content_item_id: str) -> ExportItem | None:
        if load_export_fn is None:
            return None
        try:
            return load_export_fn(content_item_id)
        except Exception:  # 조회 실패를 500으로 흘리지 않는다 — 없는 것과 같이 취급
            return None

    return app


def _attachment(filename: str) -> str:
    """RFC 5987 — 한글 파일명이 헤더에서 깨지지 않게 UTF-8로 싣는다.

    ASCII 대체본을 함께 주는 이유는 `filename*`을 모르는 오래된 클라이언트 대비다.
    대체본이 비면(제목이 전부 비ASCII) 이름 없는 다운로드가 되므로 최소 이름을 남긴다.
    """
    ascii_name = filename.encode("ascii", "ignore").decode("ascii").lstrip("-") or "download"
    return f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(filename)}"
