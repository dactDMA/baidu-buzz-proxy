import pytest

from baidu_buzz_proxy.services.baidu import BaiduError, parse_detailed_listing, parse_size


def test_parse_size() -> None:
    assert parse_size("1.50GB") == int(1.5 * 1024**3)
    assert parse_size("250 MiB") == 250 * 1024**2


def test_parse_detailed_listing() -> None:
    output = """
+---+-------+--------+----------+---+---+------+------------+
| # | FS_ID | APP_ID | 文件大小 | C | M | MD5  | 文件(目录) |
+---+-------+--------+----------+---+---+------+------------+
| 0 | 12345 | 250528 | 1.50GB   | x | x | abcd | base.rar   |
| 1 | 67890 | 250528 | -        | x | x |      | docs/      |
+---+-------+--------+----------+---+---+------+------------+
"""
    items = parse_detailed_listing(output, "/ProxyJobs/job", "/ProxyJobs/job")

    assert [(item.name, item.is_dir) for item in items] == [
        ("base.rar", False),
        ("docs", True),
    ]
    assert items[0].remote_path == "/ProxyJobs/job/base.rar"
    assert items[0].relative_path == "base.rar"
    assert items[0].size_bytes == int(1.5 * 1024**3)


def test_listing_rejects_unknown_output() -> None:
    with pytest.raises(BaiduError, match="unrecognized"):
        parse_detailed_listing("login required", "/tmp", "/tmp")
