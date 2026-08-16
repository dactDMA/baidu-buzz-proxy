#!/bin/sh
set -eu

baidu_dir=/app/data/baidu
baidu_binary="$baidu_dir/BaiduPCS-Go"
baidu_cookies_file=/run/secrets/baidu-cookies
managed_cookie_digest="$baidu_dir/managed-cookie.sha256"
baidu_config="$baidu_dir/pcs_config.json"
managed_login_backup="$baidu_dir/pcs_config.before-managed-login"

mkdir -p "$baidu_dir"

if ! cmp -s /usr/local/libexec/BaiduPCS-Go "$baidu_binary"; then
    install -m 0755 /usr/local/libexec/BaiduPCS-Go "$baidu_binary"
fi

quota_is_valid() {
    quota_output="$("$baidu_binary" quota 2>&1)"
    case "$quota_output" in
        *"用户名:"*"总空间:"*) return 0 ;;
        *) return 1 ;;
    esac
}

if [ -f "$managed_login_backup" ]; then
    if quota_is_valid; then
        rm -f "$managed_login_backup"
    else
        mv -f "$managed_login_backup" "$baidu_config"
    fi
fi

if [ -f "$baidu_cookies_file" ] && [ -s "$baidu_cookies_file" ]; then
    current_digest="$(sha256sum "$baidu_cookies_file" | awk '{print $1}')"
    stored_digest=""
    if [ -f "$managed_cookie_digest" ]; then
        stored_digest="$(sed -n '1p' "$managed_cookie_digest")"
    fi

    if [ "$current_digest" != "$stored_digest" ] || ! quota_is_valid; then
        had_previous_config=0
        if [ -f "$baidu_config" ]; then
            cp -p "$baidu_config" "$managed_login_backup"
            had_previous_config=1
        fi
        cookies="$(tr -d '\r\n' < "$baidu_cookies_file")"
        login_succeeded=0
        if "$baidu_binary" login -cookies="$cookies" >/dev/null 2>&1; then
            login_succeeded=1
        fi
        unset cookies
        if [ "$login_succeeded" -ne 1 ] || ! quota_is_valid; then
            if [ "$had_previous_config" -eq 1 ] && [ -f "$managed_login_backup" ]; then
                mv -f "$managed_login_backup" "$baidu_config"
            else
                rm -f "$baidu_config" "$managed_login_backup"
            fi
            echo "Managed Baidu cookie login failed" >&2
            exit 1
        fi
        temporary_digest="$managed_cookie_digest.tmp"
        printf '%s\n' "$current_digest" > "$temporary_digest"
        chmod 600 "$temporary_digest"
        mv -f "$temporary_digest" "$managed_cookie_digest"
        rm -f "$managed_login_backup"
    fi
fi

exec /sbin/tini -- "$@"
