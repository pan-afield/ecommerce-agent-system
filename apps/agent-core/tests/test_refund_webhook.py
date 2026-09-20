import pytest

from app.services.refund_webhook import (
    parse_refund_webhook_event,
    verify_refund_webhook_signature,
)

PAYLOAD = b'{"event":"refund.succeeded","idempotency_key":"idem-1"}'
SECRET = "sandbox-secret"
SIGNATURE = "3f6e5bbce685d54567c23999b3c59da1e4f52a720465f61bb543cf9584d9c34a"


def test_verify_refund_webhook_signature_accepts_matching_raw_payload() -> None:
    assert verify_refund_webhook_signature(PAYLOAD, SIGNATURE, SECRET) is True


def test_verify_refund_webhook_signature_rejects_modified_payload() -> None:
    modified_payload = PAYLOAD.replace(b"succeeded", b"failed")

    assert verify_refund_webhook_signature(modified_payload, SIGNATURE, SECRET) is False


def test_verify_refund_webhook_signature_rejects_wrong_secret_or_signature() -> None:
    assert verify_refund_webhook_signature(PAYLOAD, SIGNATURE, "other-secret") is False
    assert verify_refund_webhook_signature(PAYLOAD, "bad-signature", SECRET) is False


def test_verify_refund_webhook_signature_rejects_empty_inputs() -> None:
    assert verify_refund_webhook_signature(PAYLOAD, SIGNATURE, "") is False
    assert verify_refund_webhook_signature(PAYLOAD, "", SECRET) is False


def test_parse_refund_webhook_event_keeps_event_and_operation_ids_separate() -> None:
    event = parse_refund_webhook_event(
        {
            "event_id": "  evt-001  ",
            "status": "SUCCEEDED",
            "provider_reference": "provider-001",
            "idempotency_key": "refund:001",
        }
    )

    assert event.event_id == "evt-001"
    assert event.result.idempotency_key == "refund:001"
    assert event.result.provider_reference == "provider-001"


@pytest.mark.parametrize(
    "payload",
    [
        None,
        {"event_id": "", "status": "FAILED", "idempotency_key": "refund:001"},
        {"event_id": "   ", "status": "FAILED", "idempotency_key": "refund:001"},
        {"event_id": "evt-001", "status": "UNKNOWN", "idempotency_key": "refund:001"},
    ],
)
def test_parse_refund_webhook_event_rejects_invalid_payload(payload: object) -> None:
    with pytest.raises(RuntimeError, match="^Invalid refund webhook payload$"):
        parse_refund_webhook_event(payload)
