import pytest
from pydantic import ValidationError

from baidu_buzz_proxy.schemas import CreateJobRequest


def test_share_url_without_scheme_defaults_to_https() -> None:
    request = CreateJobRequest(share_url="pan.baidu.com/s/1XwCADuMSQFXvpoE6W7TdLA")

    assert str(request.share_url) == "https://pan.baidu.com/s/1XwCADuMSQFXvpoE6W7TdLA"


def test_protocol_relative_share_url_defaults_to_https() -> None:
    request = CreateJobRequest(share_url="//yun.baidu.com/s/example")

    assert str(request.share_url) == "https://yun.baidu.com/s/example"


def test_non_baidu_host_without_scheme_is_rejected() -> None:
    with pytest.raises(ValidationError, match="Only public Baidu Netdisk links"):
        CreateJobRequest(share_url="example.com/s/not-baidu")
