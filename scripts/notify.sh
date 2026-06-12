#!/bin/bash
# 매일 오전 10시: 대시보드 업데이트 결과 알림

LAST_RUN_FILE="/Users/soon/marketing-dashboard/scripts/last_run.txt"
TODAY=$(date '+%Y-%m-%d')
LAST_RUN=$(cat "$LAST_RUN_FILE" 2>/dev/null || echo "")

if [ "$LAST_RUN" = "$TODAY" ]; then
  osascript -e 'display notification "'"$TODAY"' 데이터 정상 반영됨 ✅" with title "마케팅 대시보드" subtitle "업데이트 완료"'
else
  osascript -e 'display notification "업데이트 미완료 — 로그 확인 필요 ⚠️" with title "마케팅 대시보드" subtitle "마지막 실행: '"$LAST_RUN"'"'
fi
