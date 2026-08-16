from dataclasses import dataclass

GIB = 1024**3


@dataclass(frozen=True, slots=True)
class QuotaSnapshot:
    total_bytes: int
    used_bytes: int

    def __post_init__(self) -> None:
        if self.total_bytes < 0 or self.used_bytes < 0:
            raise ValueError("Quota values cannot be negative")
        if self.used_bytes > self.total_bytes:
            raise ValueError("Used quota cannot exceed total quota")

    @property
    def free_bytes(self) -> int:
        return self.total_bytes - self.used_bytes


@dataclass(frozen=True, slots=True)
class QuotaDecision:
    accepted: bool
    available_bytes: int
    required_bytes: int


def evaluate_quota(
    snapshot: QuotaSnapshot,
    job_size_bytes: int,
    active_reserved_bytes: int,
    reserve_bytes: int = 300 * GIB,
) -> QuotaDecision:
    if job_size_bytes < 0 or active_reserved_bytes < 0 or reserve_bytes < 0:
        raise ValueError("Sizes cannot be negative")

    available = max(0, snapshot.free_bytes - active_reserved_bytes - reserve_bytes)
    return QuotaDecision(
        accepted=job_size_bytes <= available,
        available_bytes=available,
        required_bytes=job_size_bytes,
    )
