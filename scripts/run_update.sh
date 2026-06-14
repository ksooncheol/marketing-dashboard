#!/bin/bash
# 대시보드 일별 자동 업데이트 스크립트
# - 매일 오전 10시 실행
# - 맥이 꺼져 있어 10시를 놓쳤으면 켜질 때 자동 실행

REPO_DIR="/Users/soon/marketing-dashboard"
VENV="/Users/soon/dashboard/venv"
LOG="$REPO_DIR/scripts/update.log"
LAST_RUN_FILE="$REPO_DIR/scripts/last_run.txt"
SLACK_WEBHOOK=$(cat ~/.slack_webhook_dm 2>/dev/null)

TODAY=$(date '+%Y-%m-%d')
LAST_RUN=$(cat "$LAST_RUN_FILE" 2>/dev/null || echo "")

# Slack DM 발송 함수
slack_dm() {
  local msg="$1"
  if [ -n "$SLACK_WEBHOOK" ]; then
    curl -s -X POST "$SLACK_WEBHOOK" \
      -H "Content-Type: application/json" \
      -d "{\"text\":\"$msg\"}" > /dev/null
  fi
}

# 오늘 이미 실행했으면 스킵
if [ "$LAST_RUN" = "$TODAY" ]; then
  echo "$(date '+%Y-%m-%d %H:%M:%S') 이미 오늘 실행됨 (스킵)" >> "$LOG"
  exit 0
fi

echo "=== $(date '+%Y-%m-%d %H:%M:%S') 업데이트 시작 ===" >> "$LOG"

# Python 환경 활성화
source "$VENV/bin/activate"
cd "$REPO_DIR"

# 데이터 업데이트 (실패 시 최대 3회 재시도, 60초 간격)
UPDATE_OK=false
MAX_RETRY=3
for attempt in $(seq 1 $MAX_RETRY); do
  if python scripts/update_data.py >> "$LOG" 2>&1; then
    UPDATE_OK=true
    break
  fi
  if [ "$attempt" -lt "$MAX_RETRY" ]; then
    echo "$(date '+%Y-%m-%d %H:%M:%S') [재시도 $attempt/$MAX_RETRY] 60초 후 재시도..." >> "$LOG"
    sleep 60
  fi
done

if [ "$UPDATE_OK" = false ]; then
  echo "$(date '+%Y-%m-%d %H:%M:%S') [실패] $MAX_RETRY회 시도 모두 실패" >> "$LOG"
  slack_dm "❌ 대시보드 업데이트 실패 ($TODAY) — 로그 확인 필요"
  exit 1
fi

# 변경사항 있을 때만 커밋 & 푸시
git add index.html
if git diff --staged --quiet; then
  echo "변경사항 없음" >> "$LOG"
  slack_dm "✅ 대시보드 업데이트 완료 — $TODAY 기준 (변경사항 없음)"
else
  git commit -m "대시보드 자동 업데이트 $TODAY"
  if git push >> "$LOG" 2>&1; then
    echo "푸시 완료" >> "$LOG"
    slack_dm "✅ 대시보드 업데이트 완료 — $TODAY 기준 데이터 반영됨"
  else
    echo "푸시 실패" >> "$LOG"
    slack_dm "⚠️ 대시보드 업데이트됨 but 푸시 실패 ($TODAY) — 수동 push 필요"
  fi
fi

# 실행 날짜 기록
echo "$TODAY" > "$LAST_RUN_FILE"
echo "=== 완료 ===" >> "$LOG"
