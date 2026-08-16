#!/bin/sh
set -eu

baidu_dir=/app/data/baidu
baidu_binary="$baidu_dir/BaiduPCS-Go"
baidu_cookies_file=/run/secrets/baidu-cookies
baidu_config_secret=/run/secrets/baidu-pcs-config
managed_cookie_digest="$baidu_dir/managed-cookie.sha256"
managed_config_digest="$baidu_dir/managed-config.sha256"
baidu_config="$baidu_dir/pcs_config.json"
managed_login_backup="$baidu_dir/pcs_config.before-managed-login"
login_attempts="${BBP_MANAGED_LOGIN_ATTEMPTS:-3}"
login_timeout_seconds="${BBP_MANAGED_LOGIN_TIMEOUT_SECONDS:-45}"
initial_retry_delay="${BBP_MANAGED_LOGIN_RETRY_DELAY_SECONDS:-5}"

case "$login_attempts:$login_timeout_seconds:$initial_retry_delay" in
    *[!0-9:]* | 0:* | *:0:*)
        echo "Managed Baidu login timing configuration is invalid" >&2
        exit 1
        ;;
esac

mkdir -p "$baidu_dir"

if ! cmp -s /usr/local/libexec/BaiduPCS-Go "$baidu_binary"; then
    install -m 0755 /usr/local/libexec/BaiduPCS-Go "$baidu_binary"
fi

quota_is_valid() {
    if ! quota_output="$(timeout "$login_timeout_seconds" "$baidu_binary" quota 2>&1)"; then
        return 1
    fi
    case "$quota_output" in
        *"用户名:"*"总空间:"*) return 0 ;;
        *) return 1 ;;
    esac
}

quota_is_valid_with_retries() {
    attempt=1
    retry_delay="$initial_retry_delay"
    while [ "$attempt" -le "$login_attempts" ]; do
        if quota_is_valid; then
            return 0
        fi
        if [ "$attempt" -lt "$login_attempts" ]; then
            echo "Baidu did not respond in time; retrying in ${retry_delay}s" >&2
            sleep "$retry_delay"
            retry_delay=$((retry_delay * 2))
        fi
        attempt=$((attempt + 1))
    done
    return 1
}

if [ -f "$managed_login_backup" ]; then
    if quota_is_valid; then
        rm -f "$managed_login_backup"
    else
        mv -f "$managed_login_backup" "$baidu_config"
    fi
fi

if [ -f "$baidu_config_secret" ] && [ -s "$baidu_config_secret" ]; then
    current_digest="$(sha256sum "$baidu_config_secret" | awk '{print $1}')"
    stored_digest=""
    if [ -f "$managed_config_digest" ]; then
        stored_digest="$(sed -n '1p' "$managed_config_digest")"
    fi

    if [ "$current_digest" != "$stored_digest" ] || [ ! -s "$baidu_config" ]; then
        if ! python -c 'import json, sys; value = json.load(open(sys.argv[1], encoding="utf-8-sig")); assert isinstance(value, dict)' "$baidu_config_secret"; then
            echo "Managed BaiduPCS-Go configuration is not valid JSON" >&2
            exit 1
        fi

        had_previous_config=0
        if [ -f "$baidu_config" ]; then
            cp -p "$baidu_config" "$managed_login_backup"
            had_previous_config=1
        fi
        cp "$baidu_config_secret" "$baidu_config.tmp"
        chmod 600 "$baidu_config.tmp"
        mv -f "$baidu_config.tmp" "$baidu_config"

        echo "Validating managed BaiduPCS-Go configuration" >&2
        if ! quota_is_valid_with_retries; then
            if [ "$had_previous_config" -eq 1 ] && [ -f "$managed_login_backup" ]; then
                mv -f "$managed_login_backup" "$baidu_config"
            else
                rm -f "$baidu_config" "$managed_login_backup"
            fi
            echo "Managed BaiduPCS-Go configuration validation failed" >&2
            exit 1
        fi

        temporary_digest="$managed_config_digest.tmp"
        printf '%s\n' "$current_digest" > "$temporary_digest"
        chmod 600 "$temporary_digest"
        mv -f "$temporary_digest" "$managed_config_digest"
        rm -f "$managed_login_backup" "$managed_cookie_digest"
    fi
elif [ -f "$baidu_cookies_file" ] && [ -s "$baidu_cookies_file" ]; then
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
        authenticated=0
        attempt=1
        retry_delay="$initial_retry_delay"
        while [ "$attempt" -le "$login_attempts" ]; do
            if [ "$had_previous_config" -eq 1 ] && [ -f "$managed_login_backup" ]; then
                cp -p "$managed_login_backup" "$baidu_config"
            else
                rm -f "$baidu_config"
            fi

            echo "Refreshing managed Baidu login (attempt $attempt/$login_attempts)" >&2
            if timeout "$login_timeout_seconds" \
                "$baidu_binary" login -cookies="$cookies" >/dev/null 2>&1 \
                && quota_is_valid; then
                authenticated=1
                break
            fi

            if [ "$attempt" -lt "$login_attempts" ]; then
                echo "Baidu did not respond in time; retrying in ${retry_delay}s" >&2
                sleep "$retry_delay"
                retry_delay=$((retry_delay * 2))
            fi
            attempt=$((attempt + 1))
        done
        unset cookies
        if [ "$authenticated" -ne 1 ]; then
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
