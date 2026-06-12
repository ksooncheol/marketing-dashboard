#!/bin/bash
# 대시보드 일별 자동 업데이트 스크립트
# launchd가 매일 실행

set -e

REPO_DIR="/Users/soon/marketing-dashboard"
VENV="/Users/soon/dashboard/venv"
LOG="$REPO_DIR/scripts/update.log"

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
  git commit -m "대시보드 자동 업데이트 $(date +'%Y-%m-%d')"
  git push >> "$LOG" 2>&1
  echo "푸시 완료" >> "$LOG"
fi

echo "=== 완료 ===" >> "$LOG"
