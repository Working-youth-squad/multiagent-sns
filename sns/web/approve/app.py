"""hybrid 승인 화면 FastAPI 앱 (C9, FR-E1·FR-Q3의 UI 접점, 10-웹-알림 §3).

`create_app(store)`는 이미 배선된 `ApprovalStore`를 받아 라우트를 얹는다 — DB
연결 수명주기는 호출자 몫(운영은 `scripts/run_approve_web.py`가 단일 커넥션을 열어
주입, 테스트는 `InMemoryApprovalStore`로 DB 없이 돈다). 이 조립 방식은
[sns.runner.cycle]이 `render_media`·`assess_quality`를 주입받는 것과 같은 규율.

승인/반려의 실제 원장 효과(발행 게이트 반전)는 전부 [sns.web.approve.store]에
있다 — 이 모듈은 HTTP 어댑터(요청 파싱 → 스토어 호출 → HTML 렌더)일 뿐이다.

영상 재렌더(`rerender_video` 주입 시): 컷별 자막·나레이션 수정 → spec 검증 →
**동기 렌더**(TTS 포함, 수십 초 — 로컬 단일 사용자 도구라 큐 없이 스레드풀에서
기다린다) → 원장 갱신. 항목은 종결되지 않고 새 미디어로 다시 승인 대기가 된다.
"""

from collections.abc import Callable, Mapping
from dataclasses import replace
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import url2pathname

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from starlette.concurrency import run_in_threadpool

from sns.render.video.spec import VideoSpecError, parse_video_spec
from sns.tools.contracts import MediaAsset
from sns.web.approve.render import render_detail, render_list, render_not_found
from sns.web.approve.store import ApprovalNotFound, ApprovalStore

# 재렌더 seam — spec을 받아 (자산, 품질 상태, 품질 리포트)를 돌려준다.
# 조립은 scripts/run_approve_web.py 몫(TTS·ffmpeg·저장소 배선).
RerenderVideo = Callable[
    [Mapping[str, object]], tuple[MediaAsset, str, Mapping[str, object] | None]
]


def _edited_spec(spec: Mapping[str, object], form: Mapping[str, object]) -> dict[str, object]:
    """폼 값(topic, subtitle_i, narration_i)만 갈아 끼운 새 spec — 나머지 필드 보존."""
    slides: list[object] = []
    raw_slides = spec.get("slides")
    for i, slide in enumerate(raw_slides if isinstance(raw_slides, list) else []):
        new_slide = dict(slide) if isinstance(slide, Mapping) else {}
        for key in ("subtitle", "narration"):
            value = form.get(f"{key}_{i}")
            if value is not None:
                new_slide[key] = str(value).strip()
        slides.append(new_slide)
    edited: dict[str, object] = {**spec, "slides": slides}
    topic = form.get("topic")
    if topic is not None:
        edited["topic"] = str(topic).strip()
    return edited


def _local_media_path(storage_url: str) -> Path | None:
    """저장소 URL → 로컬 파일 경로. file:// URI와 평문 경로 두 규약을 모두 받는다.

    경로는 원장(DB)에서 오지 사용자 입력이 아니라 traversal 방어 대상이 아니다.
    로컬이 아닌 저장소(mem:// 등)는 None — 미리보기만 빠지고 검수는 계속된다.
    """
    if storage_url.startswith("file://"):
        return Path(url2pathname(urlparse(storage_url).path))
    if "://" in storage_url:
        return None
    return Path(storage_url)


def create_app(store: ApprovalStore, rerender_video: RerenderVideo | None = None) -> FastAPI:
    app = FastAPI(title="hybrid 승인 대기")

    @app.get("/", response_class=HTMLResponse)
    def list_pending() -> HTMLResponse:
        return HTMLResponse(render_list(store.list_pending()))

    @app.get("/items/{content_item_id}", response_class=HTMLResponse)
    def item_detail(content_item_id: str, rerendered: int = 0) -> HTMLResponse:
        item = store.get_pending(content_item_id)
        if item is None:
            return HTMLResponse(render_not_found(), status_code=404)
        notice = "재렌더 완료 — 새 영상을 확인한 뒤 승인하세요." if rerendered else None
        return HTMLResponse(
            render_detail(item, rerender_enabled=rerender_video is not None, notice=notice)
        )

    @app.get("/items/{content_item_id}/media", response_model=None)
    def item_media(content_item_id: str) -> Response:
        """검수 대상 미디어 바이트 서빙 — 상세 화면의 <video>/<img>가 읽는다.

        브라우저는 로컬 file:// 경로를 열 수 없어(보안 정책) 경로 텍스트만 보여주면
        영상을 안 보고 승인하게 된다 — 미리보기가 검수 도구의 요점이다.
        """
        item = store.get_pending(content_item_id)
        if item is None or item.media_storage_url is None:
            return HTMLResponse(render_not_found(), status_code=404)
        path = _local_media_path(item.media_storage_url)
        if path is None or not path.exists():
            return HTMLResponse(render_not_found(), status_code=404)
        media_type = "video/mp4" if item.media_kind == "video" else "image/png"
        return Response(content=path.read_bytes(), media_type=media_type)

    # response_model=None: 반환형이 Response 서브클래스 합집합이라 FastAPI가 이걸
    # Pydantic 응답 모델로 추론하려다 실패한다 — 우리는 이미 완성된 Response를
    # 직접 돌려주므로 자동 직렬화가 필요 없다.
    @app.post("/items/{content_item_id}/approve", response_model=None)
    def approve(content_item_id: str, body: str = Form(...)) -> HTMLResponse | RedirectResponse:
        try:
            store.approve(content_item_id, body=body)
        except ApprovalNotFound:
            return HTMLResponse(render_not_found(), status_code=404)
        return RedirectResponse("/", status_code=303)

    @app.post("/items/{content_item_id}/reject", response_model=None)
    def reject(
        content_item_id: str, reason: str = Form(default="")
    ) -> HTMLResponse | RedirectResponse:
        try:
            store.reject(content_item_id, reason=reason or "사유 미기재")
        except ApprovalNotFound:
            return HTMLResponse(render_not_found(), status_code=404)
        return RedirectResponse("/", status_code=303)

    @app.post("/items/{content_item_id}/rerender", response_model=None)
    async def rerender(content_item_id: str, request: Request) -> HTMLResponse | RedirectResponse:
        # 컷 수가 항목마다 달라 폼 필드를 고정 선언할 수 없다 — Request에서 직접 읽는다.
        item = store.get_pending(content_item_id)
        if rerender_video is None or item is None or item.media_spec is None:
            return HTMLResponse(render_not_found(), status_code=404)

        form = await request.form()
        new_spec = _edited_spec(item.media_spec, dict(form))
        edited_item = replace(item, media_spec=new_spec)  # 오류 시 수정값 유지 재표시
        try:
            parse_video_spec(new_spec)  # 글자수 상한 등 — 렌더(유료 TTS) 전에 끊는다
        except VideoSpecError as exc:
            return HTMLResponse(
                render_detail(edited_item, rerender_enabled=True, error=str(exc)),
                status_code=422,
            )

        try:
            # 렌더·TTS·게이트 어느 실패든 사용자의 수정값과 함께 화면에 표면화한다 —
            # 스택트레이스 500으로 수정 내용을 잃게 하지 않는다.
            asset, quality_status, quality_report = await run_in_threadpool(
                rerender_video, new_spec
            )
        except Exception as exc:  # noqa: BLE001
            return HTMLResponse(
                render_detail(edited_item, rerender_enabled=True, error=f"재렌더 실패 — {exc}"),
                status_code=502,
            )

        try:
            store.update_media(
                content_item_id,
                media_spec=new_spec,
                storage_url=asset.storage_url,
                checksum=asset.checksum,
                quality_status=quality_status,
                quality_report=quality_report,
            )
        except ApprovalNotFound:
            return HTMLResponse(render_not_found(), status_code=404)
        return RedirectResponse(f"/items/{content_item_id}?rerendered=1", status_code=303)

    return app
