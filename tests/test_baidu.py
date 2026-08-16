import pytest

from baidu_buzz_proxy.services.baidu import (
    BaiduError,
    parse_detailed_listing,
    parse_metadata,
    parse_size,
)


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


def test_parse_aligned_detailed_listing() -> None:
    output = (
        "当前目录: /\n"
        "----\n"
        "  #       FS ID        APP ID  文件大小       创建日期             修改日期"
        "            MD5(截图请打码)          文件(目录)\n"
        "  0   101372410808157  250528  -         2025-10-23 09:34:00"
        "  2026-08-16 01:00:55                            folder/\n"
        "  1      337297340389  250528  1.43GB    2025-12-01 17:27:52"
        "  2026-08-16 01:00:55  9d7c63460b0cd8f7962da5b24a57d348  base file.rar\n"
        "                       总: 1.43GB"
        "                                                   文件总数: 1, 目录总数: 1\n"
        "----\n"
    )

    items = parse_detailed_listing(output, "/ProxyJobs/job", "/ProxyJobs/job")

    assert [(item.fs_id, item.name, item.is_dir) for item in items] == [
        ("101372410808157", "folder", True),
        ("337297340389", "base file.rar", False),
    ]
    assert items[1].size_bytes == int(1.43 * 1024**3)


def test_empty_aligned_listing_is_valid() -> None:
    output = (
        "  #       FS ID        APP ID  文件大小       创建日期             修改日期"
        "            MD5(截图请打码)          文件(目录)\n"
        "                       总: 0B"
        "                                                      文件总数: 0, 目录总数: 0\n"
    )

    assert parse_detailed_listing(output, "/empty", "/empty") == []


def test_parse_aligned_file_metadata() -> None:
    output = """
[0] - [/base.rar] --------------

  类型              文件
  文件大小          1530435088, 1.425329GB
  fs_id             337297340389
"""

    assert parse_metadata(output) == ("337297340389", 1530435088)


def test_parse_aligned_directory_metadata() -> None:
    output = """
[0] - [/folder] --------------

  类型            目录
  fs_id           101372410808157
"""

    assert parse_metadata(output) == ("101372410808157", 0)
