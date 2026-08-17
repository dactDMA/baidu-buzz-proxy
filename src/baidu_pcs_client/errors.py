from __future__ import annotations

import string

_ERROR_MESSAGES = {
    -62: "Baidu requires a CAPTCHA before this request can continue",
    -33: "Baidu allows at most 999 items in one operation",
    -31: "Baidu could not save the shared files",
    -30: "A file or folder with the same name already exists",
    -12: "The extraction code is incorrect",
    -11: "The Baidu session cookie is invalid",
    -9: "The requested file does not exist",
    -7: "The share was deleted or cancelled",
    -6: "The Baidu session has expired; refresh the account credentials",
    -4: "The Baidu login is invalid; refresh the account credentials",
    2: "Baidu is temporarily unable to use this destination; try again later",
    3: "The Baidu account is not signed in",
    4: "Baidu reported a storage error; try again later",
    105: "The Baidu share link is invalid or no longer exists",
    112: "The Baidu page expired; retry the request",
    113: "Baidu rejected the request signature",
    132: "Baidu requires account security verification before this operation",
    9019: "The Baidu access token is missing or expired",
    31045: "The Baidu account session has expired",
    31061: "The file or folder already exists",
    31066: "The file or folder does not exist",
    31362: "Baidu rejected the request signature",
}


def english_error_message(operation: str, code: int, remote_message: str) -> str:
    if operation == "verify share" and code in {-12, -9}:
        return "The extraction code is incorrect"
    if operation == "list share" and code == -9:
        return "The share requires a valid extraction code"
    known = _ERROR_MESSAGES.get(code)
    if known:
        return known
    if remote_message and all(character in string.printable for character in remote_message):
        return remote_message
    return "Baidu rejected the request"


class BaiduPCSClientError(RuntimeError):
    def __init__(
        self, operation: str, message: str | None = None, *, code: int | None = None
    ) -> None:
        if message is None:
            operation, message = "Baidu", operation
        self.operation = operation
        self.code = code
        detail = f"{operation}: {message}"
        if code is not None:
            detail += f" (code {code})"
        super().__init__(detail)


class BaiduPCSAuthenticationError(BaiduPCSClientError):
    pass


class BaiduPCSNetworkError(BaiduPCSClientError):
    pass
