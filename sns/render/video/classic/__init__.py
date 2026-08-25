"""classic 영상 템플릿 — 그라데이션 배경 + 대형 타이포, 문장 단위 컷, Ken Burns 줌.

`sns.render.video`(3단 레이아웃)의 **이전 세대**다. 3단이 대체했지만 지웠다가는
비교도 되돌림도 못 하므로 자기 패키지에 그대로 보존한다. 태그 `renderer/classic-v1`이
원본이다.

**자동 사이클에 배선되어 있지 않다.** [sns.runner.cycle]은 3단만 쓴다. 이쪽을 돌리려면
`scripts/render_classic.py`로 손수 호출한다 — 두 모양을 눈으로 비교하거나, 코드·도표가
없는 주제에서 어느 쪽이 나은지 판단할 때.

3단과 `VideoSpec`·`Slide` 이름이 겹치지만 **모양이 다르다**. 여기 `Slide`는
`{title, body, narration}`, 3단은 `{subtitle, narration, code, ...}`. 패키지가 다르니
충돌하지 않는다 — 섞어 쓰지만 말 것.

폰트([sns.render.fonts])·줄바꿈([sns.render.text])·TTS([sns.render.video.tts])는
3단과 공유한다. 복제하지 않는다.
"""

from sns.render.video.classic.renderer import VideoRender, VideoRenderError, render_video
from sns.render.video.classic.spec import Cut, Slide, VideoSpec, VideoSpecError, parse_video_spec
from sns.render.video.classic.subtitles import build_ass

__all__ = [
    "Cut",
    "Slide",
    "VideoRender",
    "VideoRenderError",
    "VideoSpec",
    "VideoSpecError",
    "build_ass",
    "parse_video_spec",
    "render_video",
]
