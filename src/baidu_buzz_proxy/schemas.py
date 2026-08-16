from pydantic import BaseModel, Field, HttpUrl, field_validator


class CreateJobRequest(BaseModel):
    share_url: HttpUrl
    extraction_code: str = Field(default="", max_length=16)
    turnstile_token: str = Field(default="", max_length=4096)

    @field_validator("share_url")
    @classmethod
    def validate_baidu_host(cls, value: HttpUrl) -> HttpUrl:
        host = (value.host or "").lower()
        if host not in {"pan.baidu.com", "yun.baidu.com"}:
            raise ValueError("Only public Baidu Netdisk links are supported")
        return value


class SelectItemsRequest(BaseModel):
    creator_key: str = Field(min_length=16, max_length=128)
    item_ids: list[int] = Field(default_factory=list, max_length=10000)
    select_all: bool = False
    output_name: str = Field(default="", max_length=200)


class CancelJobRequest(BaseModel):
    creator_key: str = Field(default="", max_length=128)


class AdminLoginRequest(BaseModel):
    access_token: str = Field(min_length=1, max_length=1024)
