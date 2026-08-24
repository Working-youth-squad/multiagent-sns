"""manual_assigned·manual_registered 알림 팩토리 — 순수 함수 검증(FR-E5)."""

from sns.notify.alerts import event_kind, event_payload, manual_assigned, manual_registered


def test_manual_assigned_is_info_severity_with_context() -> None:
    alert = manual_assigned(
        "instagram", channel_id="ch-1", topic_title="가을 산책", cycle_id="cy-1"
    )
    assert alert.kind == "manual_assigned"
    assert alert.severity == "info"
    assert event_kind(alert) == "notice"  # run_event.kind CHECK와 일치
    assert alert.context == {"channel_id": "ch-1", "topic_title": "가을 산책", "cycle_id": "cy-1"}


def test_manual_registered_is_info_severity_with_context() -> None:
    alert = manual_registered(
        "youtube", channel_id="ch-2", external_post_id="ext-1", publication_id="pub-1"
    )
    assert alert.kind == "manual_registered"
    assert alert.severity == "info"
    payload = event_payload(alert)
    assert payload["context"] == {
        "channel_id": "ch-2",
        "external_post_id": "ext-1",
        "publication_id": "pub-1",
    }
