"""렌더 모듈의 계층 규율 — 도메인 전용 모듈이 중립 모듈의 의존성 뿌리가 되면 안 된다.

`code_image`가 먼저 쓰였다는 이유로 정사각 슬롯의 공통 상수(변·배경·테두리)를 갖고 있었고,
개념 그림·사진·spec이 전부 거기서 import 했다. 결과: **코드를 쓰지 않는 도메인도
`pygments`를 물었다.** 팩으로 `square_sources`에서 code를 빼도 import 그래프는 그대로라
아무 소용이 없다.

여기서 지키는 규율은 하나다 — **공통은 중립 모듈에, 도메인 전용은 잎사귀에.**
"""

import subprocess
import sys
import textwrap

# 코드를 쓰지 않는 도메인이 실제로 거치는 경로. 여기까지는 pygments가 없어야 한다.
NEUTRAL_IMPORTS = (
    "sns.render.square",
    "sns.render.concept_image",
    "sns.render.images.square",
    "sns.render.video.spec",
    "sns.render.video.renderer",
)


def _modules_after_importing(*names: str) -> set[str]:
    """새 인터프리터에서 주어진 모듈만 import 한 뒤 적재된 최상위 모듈 이름."""
    script = textwrap.dedent(f"""
        import sys
        for name in {list(names)!r}:
            __import__(name)
        print("\\n".join(sorted({{m.split(".")[0] for m in sys.modules}})))
    """)
    out = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, check=True)
    return set(out.stdout.split())


def test_neutral_render_path_does_not_pull_pygments() -> None:
    """코드 없는 도메인이 문법 강조 라이브러리를 물 이유가 없다.

    렌더러는 `code`가 실제로 있는 컷에서만 코드 이미지를 그린다 — 그때 지연 import 한다.
    """
    assert "pygments" not in _modules_after_importing(*NEUTRAL_IMPORTS)


def test_shared_square_constants_live_in_a_neutral_module() -> None:
    """정사각 슬롯 규격은 도메인 전용 모듈이 아니라 중립 모듈이 정한다."""
    from sns.render import square

    assert square.DEFAULT_SIZE > 0
    assert len(square.BACKGROUND) == 3
    assert len(square.EDGE) == 3


def test_code_line_limit_agrees_between_spec_and_renderer() -> None:
    """`MAX_CODE_LINES`가 두 곳에 있다 — spec이 pygments를 물지 않으려는 대가다.

    복제는 어긋날 수 있으니 여기서 강제한다. spec의 상한이 더 느슨해지면 파서를 통과한
    코드가 렌더에서 터지고, 더 빡빡해지면 렌더할 수 있는 코드를 파서가 거부한다.
    """
    from sns.render.code_image import MAX_CODE_LINES as renderer_limit
    from sns.render.video.spec import MAX_CODE_LINES as spec_limit

    assert spec_limit == renderer_limit


def test_domain_neutral_modules_do_not_import_code_image() -> None:
    """import 그래프가 뒤집혔는지 소스로 확인한다 — 잎사귀가 뿌리가 되면 안 된다."""
    import ast
    import pathlib

    offenders: list[str] = []
    for rel in ("render/concept_image.py", "render/images/square.py", "render/video/spec.py"):
        path = pathlib.Path("sns") / rel
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == "sns.render.code_image":
                offenders.append(f"{rel}:{node.lineno}")
    assert not offenders, f"중립 모듈이 코드 이미지에 의존: {offenders}"
