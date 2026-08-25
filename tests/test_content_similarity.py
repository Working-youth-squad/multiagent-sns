"""콘텐츠 근접중복 검사 — 카드·영상 공통. 결정론, 네트워크 0.

기존 `check_card`에도 `content_similarity` 검사가 있었지만 **한 번도 동작한 적이 없다.**
유일한 호출부가 `recent_signatures`를 안 넘겨 기본값 빈 튜플로 항상 통과했고, 영상
게이트에는 검사 자체가 없었다. 그래서 어제와 거의 같은 Cursor 영상이 두 번 나갔다.

여기서는 **`media_spec` 하나로** 카드와 영상을 같이 다룬다 — 포맷별로 따로 만들면
한쪽만 배선되는 오늘 같은 일이 또 생긴다.
"""

from sns.quality.signature import (
    MAX_CONTENT_SIMILARITY,
    max_similarity,
    spec_signature,
    spec_texts,
)

VIDEO: dict[str, object] = {
    "topic": "리스트에서 in 쓰지 마세요",
    "slides": [
        {"subtitle": "왜 느린가", "narration": "in 연산자는 처음부터 끝까지 훑습니다."},
        {"subtitle": "해법", "narration": "셋으로 바꾸면 한 번입니다.", "code": "x = set(y)"},
    ],
}
CARD: dict[str, object] = {
    "hook": "3초컷",
    "title": "walrus",
    "body": ["a := 10", "if a > 5:"],
    "footer": "팔로우",
}


# ── 텍스트 추출 ───────────────────────────────────────────────────


def test_video_texts_cover_topic_subtitle_narration() -> None:
    where = {w for w, _ in spec_texts(VIDEO)}
    assert where == {"topic", "slides[0].subtitle", "slides[0].narration",
                     "slides[1].subtitle", "slides[1].narration"}  # fmt: skip


def test_card_texts_cover_every_field() -> None:
    where = {w for w, _ in spec_texts(CARD)}
    assert where == {"hook", "title", "body[0]", "body[1]", "footer"}


def test_code_is_not_part_of_the_signature() -> None:
    """같은 개념을 다른 코드로 설명한 것과, 같은 대본을 재탕한 것은 다르다.

    코드까지 지문에 넣으면 스니펫만 바꾼 재탕이 통과한다 — 판단 대상은 **말한 내용**이다.
    """
    assert "x = set(y)" not in dict(spec_texts(VIDEO)).values()


def test_malformed_spec_yields_nothing() -> None:
    assert spec_texts({"slides": "nope"}) == []
    assert spec_texts({}) == []


# ── 지문·유사도 ───────────────────────────────────────────────────


def test_identical_spec_is_fully_similar() -> None:
    assert max_similarity(spec_signature(VIDEO), (spec_signature(VIDEO),)) == 1.0


def test_rewording_the_same_script_stays_similar() -> None:
    """어제 대본을 살짝 바꿔 다시 낸 것 — 이게 실제로 일어난 일이다."""
    reworded = {
        "topic": "리스트에서 in 쓰지 마세요",
        "slides": [
            {"subtitle": "왜 느린가", "narration": "in 연산자는 처음부터 끝까지 훑습니다."},
            {"subtitle": "해법", "narration": "셋으로 바꾸면 한 번이면 됩니다."},
        ],
    }
    similarity = max_similarity(spec_signature(reworded), (spec_signature(VIDEO),))
    assert similarity > MAX_CONTENT_SIMILARITY, similarity


def test_different_topic_is_not_similar() -> None:
    other = {
        "topic": "ORM이 몰래 날리는 쿼리",
        "slides": [{"subtitle": "함정", "narration": "반복문마다 쿼리가 나갑니다."}],
    }
    assert max_similarity(spec_signature(other), (spec_signature(VIDEO),)) < 0.3


def test_no_history_means_no_similarity() -> None:
    assert max_similarity(spec_signature(VIDEO), ()) == 0.0


def test_compares_against_the_closest_of_many() -> None:
    far = spec_signature({"topic": "전혀 다른 주제", "slides": []})
    assert max_similarity(spec_signature(VIDEO), (far, spec_signature(VIDEO))) == 1.0


def test_signature_is_deterministic() -> None:
    assert spec_signature(VIDEO) == spec_signature(VIDEO)


def test_card_and_video_share_one_mechanism() -> None:
    """포맷별로 따로 만들면 한쪽만 배선되는 사고가 또 난다."""
    assert spec_signature(CARD)
    assert max_similarity(spec_signature(CARD), (spec_signature(CARD),)) == 1.0
