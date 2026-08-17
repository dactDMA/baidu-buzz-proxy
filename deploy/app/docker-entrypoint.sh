#!/bin/sh
set -eu

baidu_dir=/app/data/baidu
baidu_cookies_secret=/run/secrets/baidu-cookies
baidu_config_secret=/run/secrets/baidu-pcs-config
baidu_cookies="$baidu_dir/cookies"
baidu_config="$baidu_dir/pcs_config.json"

mkdir -p "$baidu_dir"

if [ -s "$baidu_config_secret" ]; then
    if ! python -c 'import json, sys; value = json.load(open(sys.argv[1], encoding="utf-8-sig")); assert isinstance(value, dict)' "$baidu_config_secret"; then
        echo "Managed Baidu configuration is not valid JSON" >&2
        exit 1
    fi
    cp "$baidu_config_secret" "$baidu_config.tmp"
    chmod 600 "$baidu_config.tmp"
    mv -f "$baidu_config.tmp" "$baidu_config"
    rm -f "$baidu_cookies"
elif [ -s "$baidu_cookies_secret" ]; then
    tr -d '\r\n' < "$baidu_cookies_secret" > "$baidu_cookies.tmp"
    chmod 600 "$baidu_cookies.tmp"
    mv -f "$baidu_cookies.tmp" "$baidu_cookies"
    rm -f "$baidu_config"
elif [ ! -s "$baidu_config" ] && [ ! -s "$baidu_cookies" ]; then
    echo "A Baidu pcs_config.json or cookie header is required" >&2
    exit 1
fi

exec /sbin/tini -- "$@"
