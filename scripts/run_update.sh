#!/bin/bash
# 대시보드 일별 자동 업데이트 스크립트
# - 매일 오전 9시 실행
# - 맥이 꺼져 있어 9시를 놓쳤으면 켜질 때 자동 실행

set -e

REPO_DIR="/Users/soon/marketing-dashboard"
VENV="/Users/soon/dashboard/venv"
LOG="$REPO_DIR/scripts/update.log"
LAST_RUN_FILE="$REPO_DIR/scripts/last_run.txt"

TODAY=$(date '+%Y-%m-%d')
LAST_RUN=$(cat "$LAST_RUN_FILE" 2>/dev/null || echo "")

# 오늘 이미 실행했으면 스킵
if [ "$LAST_RUN" = "$TODAY" ]; then
  echo "$(date '+%Y-%m-%d %H:%M:%S') 이미 오늘 실행됨 (스킵)" >> "$LOG"
  exit 0
fi

echo "=== $(date '+%Y-%m-%d %H:%M:%S') 업데이트 시작 ===" >> "$LOG"

# Python 환경 활성화 (Flask 앱 venv 재사용)
source "$VENV/bin/activate"

# 데이터 업데이트
cd "$REPO_DIR"
python scripts/update_data.py >> "$LOG" 2>&1

# 변경사항 있을 때만 커밋 & 푸시
git add index.html
if git diff --staged --quiet; then
  echo "변경사항 없음" >> "$LOG"
else
  git commit -m "대시보드 자동 업데이트 $TODAY"
  git push >> "$LOG" 2>&1
  echo "푸시 완료" >> "$LOG"
fi

# 실행 날짜 기록
echo "$TODAY" > "$LAST_RUN_FILE"

echo "=== 완료 ===" >> "$LOG"
