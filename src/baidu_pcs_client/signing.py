from __future__ import annotations

import hashlib
import time

_LOCATE_SECRET = b"ebrcUYiuxaZv2XGu7KIYKxUrqfnOfpDF"


def locate_download_signature(bduss: str, uid: int, now: int | None = None) -> dict[str, str]:
    timestamp = int(time.time()) if now is None else now
    devuid = hashlib.md5(bduss.encode()).hexdigest().upper() + "|0"  # noqa: S324
    seed = b"".join(
        (
            hashlib.sha1(bduss.encode()).hexdigest().encode(),  # noqa: S324
            str(uid).encode(),
            _LOCATE_SECRET,
            str(timestamp).encode(),
            devuid.encode(),
        )
    )
    signature = hashlib.sha1(seed).hexdigest()  # noqa: S324
    return {
        "time": str(timestamp),
        "rand": signature,
        "devuid": devuid,
        "cuid": devuid,
    }
