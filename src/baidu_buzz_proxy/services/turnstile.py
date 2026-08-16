import httpx


class TurnstileError(RuntimeError):
    pass


async def verify_turnstile(secret: str, token: str, remote_ip: str | None) -> None:
    if not secret:
        return
    if not token:
        raise TurnstileError("Complete the anti-bot check")
    payload = {"secret": secret, "response": token}
    if remote_ip:
        payload["remoteip"] = remote_ip
    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.post(
            "https://challenges.cloudflare.com/turnstile/v0/siteverify", data=payload
        )
        response.raise_for_status()
    if not response.json().get("success"):
        raise TurnstileError("The anti-bot check was rejected")
