FROM python:3.12-slim
# 영상 렌더 런타임 의존 — pip이 아니라 OS 바이너리/폰트다.
#   ffmpeg/ffprobe: renderer·quality가 shell-out (없으면 FileNotFoundError로 즉사)
#   fonts-noto-cjk: 한글 글리프 (없으면 자막·슬라이드가 두부(□)로 렌더)
#   fonts-dejavu-core: 코드 이미지용 고정폭(DejaVu Sans Mono). fonts-noto-cjk는 모노를
#     주지 않는다 — 없으면 render_code_square가 FontNotFoundError로 즉사한다.
#     DejaVu에 한글이 없는 건 의도된 것이다 — 한글 런은 위 Noto CJK로 폴백해 그린다.
RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg fonts-noto-cjk fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project --no-dev
COPY sns ./sns
RUN uv sync --frozen --no-dev
CMD ["uv", "run", "python", "-c", "import sns; print('sns ok')"]
