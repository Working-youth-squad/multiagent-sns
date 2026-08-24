"""hybrid 승인 화면 FastAPI 앱 (C9, FR-E1·FR-Q3의 UI 접점, 10-웹-알림 §3).

`create_app(store)`는 이미 배선된 `ApprovalStore`를 받아 라우트 4개를 얹는다 — DB
연결 수명주기는 호출자 몫(운영은 `scripts/run_approve_web.py`가 단일 커넥션을 열어
주입, 테스트는 `InMemoryApprovalStore`로 DB 없이 돈다). 이 조립 방식은
[sns.runner.cycle]이 `render_media`·`assess_quality`를 주입받는 것과 같은 규율.

승인/반려의 실제 원장 효과(발행 게이트 반전)는 전부 [sns.web.approve.store]에
있다 — 이 모듈은 HTTP 어댑터(요청 파싱 → 스토어 호출 → HTML 렌더)일 뿐이다.
"""

from fastapi import FastAPI, Form
from fastapi.responses import HTMLResponse, RedirectResponse

from sns.web.approve.render import render_detail, render_list, render_not_found
from sns.web.approve.store import ApprovalNotFound, ApprovalStore


def create_app(store: ApprovalStore) -> FastAPI:
    app = FastAPI(title="hybrid 승인 대기")

    @app.get("/", response_class=HTMLResponse)
    def list_pending() -> HTMLResponse:
        return HTMLResponse(render_list(store.list_pending()))

    @app.get("/items/{content_item_id}", response_class=HTMLResponse)
    def item_detail(content_item_id: str) -> HTMLResponse:
        item = store.get_pending(content_item_id)
        if item is None:
            return HTMLResponse(render_not_found(), status_code=404)
        return HTMLResponse(render_detail(item))

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

    return app
