#!/bin/sh
set -eu

baidu_dir=/app/data/baidu
baidu_binary="$baidu_dir/BaiduPCS-Go"

mkdir -p "$baidu_dir"

if ! cmp -s /usr/local/libexec/BaiduPCS-Go "$baidu_binary"; then
    install -m 0755 /usr/local/libexec/BaiduPCS-Go "$baidu_binary"
fi

exec /sbin/tini -- "$@"
