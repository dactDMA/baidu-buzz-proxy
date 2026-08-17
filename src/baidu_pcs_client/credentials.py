from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from baidu_pcs_client.errors import BaiduPCSAuthenticationError

DEFAULT_PAN_USER_AGENT = (
    "netdisk;P2SP;3.0.0.8;netdisk;11.12.3;ANG-AN00;android-android;10.0;"
    "JSbridge4.4.0;jointBridge;1.1.0;"
)


def parse_cookie_header(value: str) -> dict[str, str]:
    cookies: dict[str, str] = {}
    for part in value.replace("\r", "").replace("\n", "").split(";"):
        name, separator, cookie_value = part.strip().partition("=")
        if separator and name:
            cookies[name] = cookie_value
    return cookies


@dataclass(frozen=True, slots=True)
class Credentials:
    cookies: dict[str, str]
    app_id: int = 266719
    uid: int | None = None
    pcs_host: str = "pcs.baidu.com"
    pan_user_agent: str = DEFAULT_PAN_USER_AGENT

    def __post_init__(self) -> None:
        if not self.cookies.get("BDUSS"):
            raise BaiduPCSAuthenticationError("load credentials", "BDUSS is missing")

    def cookie_header(self) -> str:
        return "; ".join(f"{name}={value}" for name, value in self.cookies.items())

    @classmethod
    def from_cookie_header(
        cls,
        value: str,
        *,
        app_id: int = 266719,
        uid: int | None = None,
        pcs_host: str = "pcs.baidu.com",
        pan_user_agent: str = DEFAULT_PAN_USER_AGENT,
    ) -> Credentials:
        return cls(
            cookies=parse_cookie_header(value),
            app_id=app_id,
            uid=uid,
            pcs_host=pcs_host,
            pan_user_agent=pan_user_agent,
        )

    @classmethod
    def from_pcs_config(cls, path: Path | str) -> Credentials:
        config_path = Path(path)
        try:
            payload: dict[str, Any] = json.loads(config_path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError) as error:
            raise BaiduPCSAuthenticationError(
                "load credentials", f"could not read {config_path}"
            ) from error

        active_uid = int(payload.get("baidu_active_uid") or 0)
        users = payload.get("baidu_user_list")
        if not isinstance(users, list):
            raise BaiduPCSAuthenticationError("load credentials", "baidu_user_list is invalid")
        active = next(
            (
                user
                for user in users
                if isinstance(user, dict) and int(user.get("uid") or 0) == active_uid
            ),
            None,
        )
        if active is None:
            raise BaiduPCSAuthenticationError("load credentials", "active Baidu user is missing")

        cookies = parse_cookie_header(str(active.get("cookies") or ""))
        for name, key in (("BDUSS", "bduss"), ("STOKEN", "stoken"), ("SBOXTKN", "sboxtkn")):
            value = str(active.get(key) or "")
            if value:
                cookies.setdefault(name, value)

        return cls(
            cookies=cookies,
            app_id=int(payload.get("appid") or 266719),
            uid=active_uid or None,
            pcs_host=str(payload.get("pcs_addr") or "pcs.baidu.com"),
            pan_user_agent=str(payload.get("pan_ua") or DEFAULT_PAN_USER_AGENT),
        )
