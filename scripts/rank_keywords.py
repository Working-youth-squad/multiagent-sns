"""질의어 하나로 3소스 트렌드 키워드 랭킹을 뽑는다 (04-트렌드조사 §5).

`sns.research.rank_keywords`의 얇은 CLI 껍데기다. 챗봇이 파이썬 밖(다른 프로세스·다른
언어)에서 붙을 때 쓰는 경계이기도 하다 — `--json`은 `ranking_to_dict`를 그대로 찍으므로
함수 호출 결과와 모양이 같다.

자동완성 3종은 전부 무인증이라 **API 키 없이 바로 돈다**.

실행:
    uv run python scripts/rank_keywords.py 개발자
    uv run python scripts/rank_keywords.py 개발자 --json
    uv run python scripts/rank_keywords.py 개발자 --min-present 2   # 교차검증된 것만
    uv run python scripts/rank_keywords.py 개발자 --no-band          # 필터 끄기
    uv run python scripts/rank_keywords.py 개발자 --band 10 90       # 경계값 바꾸기

종료 코드: 0 정상(후보 0건 포함 — 관측 결과지 오류가 아니다) / 1 전 소스 실패 /
2 인자 사용 오류(argparse 관례).
"""

import argparse
import json
import sys

from sns.research import KEYWORD_SOURCES, KeywordRanking, rank_keywords, ranking_to_dict


def _positive_int(raw: str) -> int:
    value = int(raw)
    if value < 1:
        raise argparse.ArgumentTypeError(f"1 이상이어야 한다: {value}")
    return value


def _percentile(raw: str) -> float:
    value = float(raw)
    if not 0.0 <= value <= 100.0:
        raise argparse.ArgumentTypeError(f"퍼센타일은 0~100 이어야 한다: {value}")
    return value


def render(ranking: KeywordRanking) -> str:
    """사람이 읽는 표 — 근거(소스 수·관측 평균 등수·불일치)를 숨기지 않는다."""
    lines = [
        f"질의어 {ranking.query}",
        f"filter_mode={ranking.filter_mode} — {ranking.reason}",
        f"소스 ok={list(ranking.sources_ok)} failed={list(ranking.sources_failed)}",
        "",
        f"{'#':>2}  {'키워드':<26}{'소스':>4}{'obs_mean':>10}{'rank_std':>11}",
    ]
    for i, c in enumerate(ranking.candidates, 1):
        std = "미정의" if c.rank_std is None else f"{c.rank_std:.4f}"
        lines.append(f"{i:>2}  {c.text:<26}{c.present_count:>4}{c.observed_mean:>10.4f}{std:>11}")
    if not ranking.candidates:
        lines.append("    (후보 없음 — --no-band 로 전량을 확인해 볼 것)")
    if ranking.dropped:
        # `if s.rank_std`로 거르면 하위 꼬리(정확히 0.0)가 통째로 사라진다 — None만 뺀다.
        cut = ", ".join(
            f"{s.text}({s.rank_std:.3f})" for s in ranking.dropped if s.rank_std is not None
        )
        lines += ["", f"밴드 밖 {len(ranking.dropped)}건: {cut}"]
    if ranking.unscored:
        lines += ["", f"미판정(관측 1건이라 불일치를 잴 수 없음) {len(ranking.unscored)}건"]
    if ranking.excluded:
        hit = ", ".join(f"{t}←{k}" for t, k in ranking.excluded)
        lines += ["", f"제외 {len(ranking.excluded)}건: {hit}"]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    # Windows 콘솔 기본 코드페이지(cp949)가 한글·em대시를 못 찍어 죽는 것을 막는다.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(
        prog="rank_keywords",
        description="질의어 1개 → 3소스 등수 통계 + 표준편차 밴드",
    )
    parser.add_argument("query", help="질의어 (예: 개발자)")
    parser.add_argument(
        "--source",
        action="append",
        dest="sources",
        choices=list(KEYWORD_SOURCES),
        help=f"쓸 소스만 지정(반복 가능). 기본 {list(KEYWORD_SOURCES)}",
    )
    parser.add_argument("--limit", type=_positive_int, default=20, help="소스별 수집 깊이")
    parser.add_argument("--top", type=_positive_int, default=10, help="후보 상한")
    parser.add_argument(
        "--min-present",
        type=_positive_int,
        default=1,
        help="후보가 등장해야 하는 최소 소스 수(2면 교차검증된 것만)",
    )
    parser.add_argument("--no-band", action="store_true", help="밴드 필터 끄기")
    parser.add_argument(
        "--band",
        nargs=2,
        type=_percentile,
        metavar=("LOW", "HIGH"),
        default=[25.0, 75.0],
        help="밴드 퍼센타일 경계 (기본 25 75)",
    )
    parser.add_argument(
        "--exclude", action="append", help="제외 키워드(반복 가능). 미지정이면 거르지 않는다"
    )
    parser.add_argument("--json", action="store_true", help="사람용 표 대신 JSON")
    args = parser.parse_args(argv)

    low, high = args.band
    if low > high:
        parser.error(f"밴드 경계가 뒤집혔다: {low} > {high}")

    ranking = rank_keywords(
        args.query,
        sources=args.sources,
        limit=args.limit,
        top=args.top,
        band=not args.no_band,
        percentiles=(low, high),
        min_present=args.min_present,
        exclude=args.exclude,
    )
    if args.json:
        print(json.dumps(ranking_to_dict(ranking), ensure_ascii=False, indent=2))
    else:
        print(render(ranking))
    return 0 if ranking.sources_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
