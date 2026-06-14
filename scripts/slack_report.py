#!/usr/bin/env python3
"""
마케팅제휴파트 데일리 실적 Slack 리포트
- 매일 오전 10:30 실행
- #wg-제휴사업팀-마케팅제휴파트 채널에 발송
"""

import json, re, os, urllib.request

HTML_PATH      = os.path.join(os.path.dirname(__file__), "..", "index.html")
WEBHOOK_DM     = open(os.path.expanduser("~/.slack_webhook_dm")).read().strip()
# 팀 채널 웹훅이 있으면 사용, 없으면 DM으로 발송
_ch_path = os.path.expanduser("~/.slack_webhook_channel")
WEBHOOK_CH     = open(_ch_path).read().strip() if os.path.exists(_ch_path) else WEBHOOK_DM
DASHBOARD_URL  = "https://ksooncheol.github.io/marketing-dashboard/"

# ── 헬퍼 ──────────────────────────────────────────────

def fmt(n):
    try:
        return f"{int(n):,}"
    except:
        return "—"

def arrow(cur, prev):
    if not prev: return ""
    p = (cur - prev) / prev * 100
    sign = "▲" if p >= 0 else "▼"
    return f"{sign} {abs(p):.1f}%  _(전일 {fmt(prev)})_"

def read_array(html, name):
    m = re.search(rf'const {re.escape(name)}=\[([^\]]+)\]', html)
    return json.loads('[' + m.group(1) + ']') if m else []

def read_tot_field(html, field):
    m = re.search(rf'(?<![\'\"\\w]){re.escape(field)}:\[([^\]]+)\]', html)
    return json.loads('[' + m.group(1) + ']') if m else []

def send_webhook(url, text):
    data = json.dumps({"text": text}).encode()
    req  = urllib.request.Request(url, data=data,
                                  headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req) as r:
        return r.read().decode()

# ── 데이터 파싱 ───────────────────────────────────────

html = open(HTML_PATH, encoding="utf-8").read()

dates  = json.loads(re.search(r'const DATES=(\[.*?\]);', html).group(1))
days   = json.loads(re.search(r'const DAYS=(\[.*?\]);', html).group(1))
n      = len(dates) - 1          # 마지막 = 전일
d_str  = dates[n]
day_kr = days[n]

adj_tot = read_array(html, "ADJ_AFF_TOT")
prm_tot = read_array(html, "PRM_AFF_TOT")
clb_aff = read_array(html, "CLB_AFF")
cum_adj = read_array(html, "CUM_ADJ_AFF_TOT")
cum_prm = read_array(html, "CUM_PRM_AFF_TOT")
cum_clb = read_array(html, "CUM_CLB_AFF")
clb_np  = read_tot_field(html, "clbNP")

adj_day  = adj_tot[n];  adj_prev  = adj_tot[n-1]  if n > 0 else 0
prm_day  = prm_tot[n];  prm_prev  = prm_tot[n-1]  if n > 0 else 0
clb_day  = clb_aff[n];  clb_prev  = clb_aff[n-1]  if n > 0 else 0
clb_new  = clb_np[n]   if n < len(clb_np) else 0
clb_ren  = max(0, clb_day - clb_new)

# 상위 3사 (Adjust 유입)
pattern = r"'([^']+)':\{group:'[^']+',color:'[^']+',adj:\[([^\]]+)\]"
partner_adj = []
for name, arr_str in re.findall(pattern, html):
    arr = json.loads('[' + arr_str + ']')
    if n < len(arr) and arr[n] > 0:
        partner_adj.append((name, arr[n]))
partner_adj.sort(key=lambda x: -x[1])
top3 = partner_adj[:3]

# ── 메시지 작성 ───────────────────────────────────────

msg = f"""📊 *마케팅제휴파트 데일리 실적 — {d_str} ({day_kr})*
━━━━━━━━━━━━━━━━━━━━

📲 *Adjust 유입*
당일  *{fmt(adj_day)}*  {arrow(adj_day, adj_prev)}
당월 누계  *{fmt(cum_adj[n])}*

🛒 *프로모션 주문수*
당일  *{fmt(prm_day)}*  {arrow(prm_day, prm_prev)}
당월 누계  *{fmt(cum_prm[n])}*

⭐ *배민클럽 획득*
당일  *{fmt(clb_day)}*  {arrow(clb_day, clb_prev)}
신규 {fmt(clb_new)} / 재구독 {fmt(clb_ren)}
당월 누계  *{fmt(cum_clb[n])}*
━━━━━━━━━━━━━━━━━━━━
🏆 *상위 제휴사 (Adjust 유입)*
"""
for idx, (name, val) in enumerate(top3, 1):
    msg += f"{idx}. {name}: {fmt(val)}\n"

msg += f"━━━━━━━━━━━━━━━━━━━━\n🔗 대시보드: {DASHBOARD_URL}"

# ── 발송 ─────────────────────────────────────────────

try:
    send_webhook(WEBHOOK_CH, msg)
    dest = "팀 채널" if WEBHOOK_CH != WEBHOOK_DM else "개인 DM"
    print(f"리포트 발송 완료 ({dest}): {d_str}")
except Exception as e:
    print(f"리포트 발송 실패: {e}")
    exit(1)
