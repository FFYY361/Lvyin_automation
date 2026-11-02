#!/usr/bin/env bash
# Source after the existing successful backup/weather steps, under backup.lock.
if [[ "$(TZ=Asia/Shanghai date +%u)" == "1" ]]; then
    {
        printf '\n[%s] ai_cleanup_start\n' "$(date -Iseconds)"
        if curl --fail --silent --show-error --noproxy '*' \
            --connect-timeout 2 --max-time 5 http://127.0.0.1:3001/api/health >/dev/null; then
            if (cd "$REPO_ROOT" && "$ENV_ROOT/bin/python" -m backend cleanup-ai-results); then
                printf '[%s] ai_cleanup_exit=0\n' "$(date -Iseconds)"
            else
                printf '[%s] ai_cleanup_failed backup_status=success\n' "$(date -Iseconds)"
            fi
        else
            printf '[%s] ai_cleanup_skipped=website_unhealthy\n' "$(date -Iseconds)"
        fi
    } >> "$APP_ROOT/logs/ai-cleanup.log" 2>&1
fi
# A cleanup/logging failure must not change the backup exit code.
true
