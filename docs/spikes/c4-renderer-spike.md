# C4 스파이크 리포트 — 영상 렌더러 결정 (ffmpeg+ASS vs Remotion)

> 상위: [14-태스크분할](../plan/14-태스크분할.md) §YT C4 · [06-미디어렌더](../plan/06-미디어렌더.md) §2 렌더러 미결정
> 스파이크 코드: [`spikes/c4-renderer/`](../../spikes/c4-renderer/) (ffmpeg_sample.py · remotion/)
> 대상 버전: **ffmpeg 9.0** (winget Gyan.FFmpeg) · **Remotion 4.x** (Node 24), 2026-08-13

## 1. 결론 요약

**채택: ffmpeg + ASS 자막.** 동일 스펙(한국어 슬라이드 3장 + 자막, 1080×1920, 15s)을 양쪽으로 렌더해 비교:

| 기준 | ffmpeg+ASS | Remotion | 판정 |
|---|---|---|---|
| 렌더 시간 (15s 샘플) | **3.5s** | 113s (~32배) | ffmpeg 압승 — 하루 수십 건 생산 시 결정적 |
| 한글 자막 충실도 | ✅ Malgun Gothic, safe area 내 정확 배치 | ✅ 동급 (CSS라 표현력은 더 유연) | 무승부 |
| 결정론 (2회 렌더 바이트 동일) | ✅ (`-bitexact` 3종 + muxer `-bitexact` + `-threads 1` 필요) | ✅ (기본) | 무승부 |
| 운영 복잡도 | 바이너리 1개, CI `apt-get install ffmpeg` | Node+Chromium(headless shell), npm 248패키지, CI 비용 큼 | ffmpeg 승 |
| Python 호출 seam | subprocess 1회 | npx 사이드카 + 번들 관리 | ffmpeg 승 |

Remotion이 이기는 건 표현력(React/CSS 애니메이션)뿐 — FR-M2 "템플릿 코드 합성"의 슬라이드+자막 요구에는 과잉이고, 렌더 32배·운영 복잡도가 그 이점을 상쇄한다.

## 2. 검증 방법

1. 공통 스펙: 다크 배경, 한국어 타이틀 3장(장당 5s), 하단 safe-area(중앙 900×1400) 자막.
2. A: Pillow 슬라이드 PNG → `.ass` 자막 → `ffmpeg -f concat` + `subtitles=` 필터 1회 호출 (`spikes/c4-renderer/ffmpeg_sample.py` — 2회 렌더 sha256 비교 내장).
3. B: Remotion 컴포지션(`spikes/c4-renderer/remotion/`) → `npx remotion render` 2회 → sha256 비교.
4. 프레임 추출(7s 지점) 눈 검사로 자막·한글 렌더 확인.

## 3. 발견사항 (구현 시 알아야 할 것)

1. **ffmpeg 결정론은 공짜가 아님**: 기본 설정은 2회 렌더 바이트가 다름. `-fflags/-flags:v/-flags:a +bitexact` + muxer `-bitexact` + **`-threads 1`** 전부 걸어야 동일 바이트. 단일 스레드라도 15s 샘플 3.5s — 병목 아님.
2. **concat demuxer 규칙**: 마지막 이미지 항목은 `duration` 없이 한 번 더 반복해야 마지막 슬라이드가 잘리지 않음.
3. **Windows 경로**: `subtitles=` 필터는 경로 이스케이프가 고약함 → temp 작업 디렉토리에서 상대 경로로 호출하면 회피됨(OneDrive 한글 경로도 함께 회피).
4. **폰트**: Windows=Malgun Gothic, CI(Linux)=Noto Sans CJK(`fonts-noto-cjk` 설치, ci.yml 반영됨). Pillow 경로와 ASS 패밀리 이름을 쌍으로 관리.
5. Remotion도 결정론 자체는 기본 만족 — 후일 표현력이 필요해지면(브랜드 모션 그래픽 등) 재평가 여지는 있음.

## 4. 다음 단계

- 본 구현 `sns/render/video/`는 ffmpeg 경로로 진행 (이 PR).
- TTS(Chirp 3 HD)는 SSML `<mark>` 대신 **슬라이드 1장당 1회 합성** — WAV 길이가 곧 타이밍(06 §3의 단어 타임스탬프는 단어 하이라이트 도입 시 재검토).
- 06-미디어렌더 §2 미결정 항목을 본 문서로 종결 처리(팀 공유).
