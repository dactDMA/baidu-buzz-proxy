import pytest

from baidu_buzz_proxy.services.quota import GIB, QuotaSnapshot, evaluate_quota


def test_quota_accepts_job_with_available_space() -> None:
    snapshot = QuotaSnapshot(total_bytes=5_000 * GIB, used_bytes=3_800 * GIB)

    decision = evaluate_quota(
        snapshot=snapshot,
        job_size_bytes=250 * GIB,
        active_reserved_bytes=250 * GIB,
    )

    assert decision.accepted is True
    assert decision.available_bytes == 650 * GIB


def test_quota_rejects_job_when_reserve_would_be_used() -> None:
    snapshot = QuotaSnapshot(total_bytes=5_000 * GIB, used_bytes=4_500 * GIB)

    decision = evaluate_quota(
        snapshot=snapshot,
        job_size_bytes=250 * GIB,
        active_reserved_bytes=0,
    )

    assert decision.accepted is False
    assert decision.available_bytes == 200 * GIB


def test_quota_rejects_invalid_snapshot() -> None:
    with pytest.raises(ValueError, match="exceed"):
        QuotaSnapshot(total_bytes=100, used_bytes=101)
