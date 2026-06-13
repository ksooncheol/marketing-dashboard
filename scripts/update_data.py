#!/usr/bin/env python3
"""
대시보드 일별 자동 업데이트 스크립트
매일 실행: Google Sheets 최신 데이터 → index.html 반영
"""

import json, re, os
from datetime import date, timedelta
from collections import defaultdict
from googleapiclient.discovery import build

SHEET_ID = "1LImApdHZcM7WcfU9yy45eftZl9Gz0ombMUxbbS4m7e4"
START    = "2026-05-01"
HTML_PATH = os.path.join(os.path.dirname(__file__), "..", "index.html")

DAYS_KR = ["월", "화", "수", "목", "금", "토", "일"]

# ── 인증 ──────────────────────────────────────────────

def get_service():
    creds_json = os.environ.get("GOOGLE_CREDENTIALS")
    if creds_json:
        from google.oauth2.service_account import Credentials
        creds = Credentials.from_service_account_info(
            json.loads(creds_json),
            scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"]
        )
    else:
        from google.auth import default
        creds, _ = default(scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"])
    return build("sheets", "v4", credentials=creds, cache_discovery=False)

def get_range(svc, tab, rng):
    res = svc.spreadsheets().values().get(
        spreadsheetId=SHEET_ID,
        range=f"{tab}!{rng}",
        valueRenderOption="UNFORMATTED_VALUE"
    ).execute()
    return res.get("values", [])

# ── 파싱 헬퍼 ─────────────────────────────────────────

def parse_date(v):
    if isinstance(v, (int, float)):
        return (date(1899, 12, 30) + timedelta(days=int(v))).strftime("%Y-%m-%d")
    s = str(v).strip()
    return s if re.match(r"\d{4}-\d{2}-\d{2}", s) else None

def parse_int(v):
    try:
        return int(str(v).replace(",", "").strip())
    except Exception:
        return 0

def make_dates(start, end):
    result, d = [], date.fromisoformat(start)
    e = date.fromisoformat(end) if isinstance(end, str) else end
    while d <= e:
        result.append(d.strftime("%Y-%m-%d"))
        d += timedelta(days=1)
    return result

def day_kr(d_str):
    return DAYS_KR[date.fromisoformat(d_str).weekday()]

def make_cum(arr, dates):
    result, run, mo = [], 0, None
    for i, v in enumerate(arr):
        m = dates[i][:7]
        if m != mo:
            run = 0; mo = m
        run += v
        result.append(run)
    return result

def may_cum(cum_arr, dates):
    idx = next((i for i, d in enumerate(dates) if d == "2026-05-31"), None)
    return cum_arr[idx] if idx is not None else 0

# ── 데이터 fetch ──────────────────────────────────────

def fetch_adjust(svc, dates):
    """adjust 시트: A=날짜, C=유입수, D=제휴사명"""
    rows = get_range(svc, "adjust", "A2:D")
    by_p = defaultdict(lambda: defaultdict(int))
    for row in rows:
        if len(row) < 3: continue
        d = parse_date(row[0])
        if not d or d < START: continue
        partner = (str(row[3]).strip() if len(row) > 3 else "") or "기타"
        if "#N/A" in partner: partner = "기타"
        by_p[partner][d] += parse_int(row[2])

    SIMPLE_PAY = {"카카오페이", "토스페이", "네이버페이"}
    total    = [sum(by_p[p].get(d, 0) for p in by_p) for d in dates]
    pay_only = [sum(by_p[p].get(d, 0) for p in SIMPLE_PAY) for d in dates]
    by_partner = {p: [by_p[p].get(d, 0) for d in dates] for p in by_p}
    return total, pay_only, by_partner

def fetch_prm(svc, dates):
    """주문_쿠폰발급사용 시트: F=날짜(시리얼), H=주문수, I=제휴사"""
    rows = get_range(svc, "주문_쿠폰발급사용", "F2:I")
    by_p = defaultdict(lambda: defaultdict(int))
    for row in rows:
        if len(row) < 3: continue
        d = parse_date(row[0])  # 시리얼 숫자 → YYYY-MM-DD 변환
        if not d or d < START: continue
        partner = (str(row[3]).strip() if len(row) > 3 else "") or "기타"
        if "#N/A" in partner: partner = "기타"
        # 우리비씨 → 우리카드로 통합
        if partner == "우리비씨": partner = "우리카드"
        by_p[partner][d] += parse_int(row[2])

    SIMPLE_PAY = {"카카오페이", "토스페이", "네이버페이"}
    total    = [sum(by_p[p].get(d, 0) for p in by_p) for d in dates]
    pay_only = [sum(by_p[p].get(d, 0) for p in SIMPLE_PAY) for d in dates]
    by_partner = {p: [by_p[p].get(d, 0) for d in dates] for p in by_p}
    return total, pay_only, by_partner

def fetch_clb(svc, dates):
    """배민클럽_무료이용권등록 + 배민클럽_토스API"""
    rows_free = get_range(svc, "배민클럽_무료이용권등록", "A2:F")
    rows_api  = get_range(svc, "배민클럽_토스API", "A2:H")

    by_new = defaultdict(lambda: defaultdict(int))
    by_rej = defaultdict(lambda: defaultdict(int))

    for row in rows_free:
        if len(row) < 4: continue
        d = parse_date(row[0])
        if not d or d < START: continue
        partner = (str(row[5]).strip() if len(row) > 5 else "") or "기타"
        if "#N/A" in partner: partner = "기타"
        if partner == "우리카드(비씨)": partner = "우리카드"
        by_new[partner][d] += parse_int(row[2]) if len(row) > 2 else 0
        by_rej[partner][d] += parse_int(row[3]) if len(row) > 3 else 0

    for row in rows_api:
        if len(row) < 7: continue
        d = parse_date(row[0])
        if not d or d < START: continue
        by_new["토스페이"][d] += parse_int(row[6])
        by_rej["토스페이"][d] += parse_int(row[7]) if len(row) > 7 else 0

    all_p = sorted(set(by_new) | set(by_rej))
    clb_new = [sum(by_new[p].get(d, 0) for p in all_p) for d in dates]
    clb_rej = [sum(by_rej[p].get(d, 0) for p in all_p) for d in dates]
    clb_tot = [a + b for a, b in zip(clb_new, clb_rej)]

    by_partner = {}
    for p in all_p:
        n = [by_new[p].get(d, 0) for d in dates]
        r = [by_rej[p].get(d, 0) for d in dates]
        by_partner[p] = [a + b for a, b in zip(n, r)]

    return clb_tot, clb_new, by_partner

def fetch_insights(svc):
    """고객 인사이트 시트"""
    def parse_insight(rows, date_col, cnt_col, seg_col, partner_col):
        data = defaultdict(lambda: defaultdict(lambda: defaultdict(int)))
        for row in rows:
            mc = max(date_col, cnt_col, seg_col, partner_col)
            if len(row) <= mc: continue
            d = parse_date(row[date_col])
            if not d or d < START: continue
            seg     = str(row[seg_col]).strip()
            partner = str(row[partner_col]).strip()
            if not seg or not partner or "#N/A" in partner: continue
            data[d][partner][seg] += parse_int(row[cnt_col])
        return {d: {p: dict(s) for p, s in pd.items()} for d, pd in data.items()}

    return (
        parse_insight(get_range(svc, "유입adjust_고객인사이트", "A2:F"), 0, 3, 2, 5),
        parse_insight(get_range(svc, "프로모션주문_고객인사이트", "A2:E"), 0, 3, 2, 4),
    )

# ── HTML 치환 ─────────────────────────────────────────

def replace_const(html, name, value):
    v_str = json.dumps(value, ensure_ascii=False)
    pattern = rf'const {re.escape(name)}=[^;\n]+;'
    result, n = re.subn(pattern, f'const {name}={v_str};', html)
    if n == 0:
        print(f"  [WARN] const {name} not found")
    return result

def replace_tot_field(html, field, arr):
    pattern = rf'(?<![\'"\w]){re.escape(field)}:\[[^\]]*\]'
    result, n = re.subn(pattern, f'{field}:{json.dumps(arr)}', html, count=1)
    if n == 0:
        print(f"  [WARN] TOT.{field} not found")
    return result

def pad_tot_arrays(html, target_len):
    """TOT 배열과 전사 독립 배열을 DATES 길이에 맞게 0으로 패딩"""
    # TOT 오브젝트 내 배열
    for field in ['adj', 'prm', 'clb', 'clbN', 'clbR']:
        pattern = rf'(?<![\'"\w]){re.escape(field)}:\[([^\]]+)\]'
        m = re.search(pattern, html)
        if m:
            arr = json.loads('[' + m.group(1) + ']')
            if len(arr) < target_len:
                arr += [0] * (target_len - len(arr))
                html = re.sub(pattern, f'{field}:{json.dumps(arr)}', html, count=1)
    # 전사 독립 상수
    for name in ['ADJ_TOT', 'PRM_TOT', 'CLB_TOT',
                 'CUM_ADJ_TOT', 'CUM_PRM_TOT', 'CUM_CLB_TOT']:
        pattern = rf'const {re.escape(name)}=\[([^\]]+)\];'
        m = re.search(pattern, html)
        if m:
            arr = json.loads('[' + m.group(1) + ']')
            if len(arr) < target_len:
                arr += [0] * (target_len - len(arr))
                html = re.sub(pattern, f'const {name}={json.dumps(arr)};', html)
    return html

def replace_partner_field(html, partner, field, arr):
    escaped = re.escape(partner)
    def sub(m):
        return re.sub(
            rf'{re.escape(field)}:\[[^\]]*\]',
            f'{field}:{json.dumps(arr)}',
            m.group(0)
        )
    pattern = rf"'{escaped}':\{{[^\n]+\}}"
    result, n = re.subn(pattern, sub, html)
    if n == 0:
        print(f"  [WARN] PARTNER {partner} not found")
    return result

# ── 메인 ──────────────────────────────────────────────

KNOWN_PARTNERS = [
    "카카오페이", "토스페이", "네이버페이",
    "신한카드", "삼성카드", "KB국민카드", "우리카드", "비씨카드", "하나카드", "나라사랑카드",
    "KT", "LGU+",
    "케이뱅크",
    "멜론", "토스증권", "롯데백화점",
]

def main():
    yesterday = (date.today() - timedelta(days=1)).strftime("%Y-%m-%d")
    dates = make_dates(START, yesterday)
    days  = [day_kr(d) for d in dates]
    n = len(dates)
    print(f"날짜 범위: {dates[0]} ~ {dates[-1]} ({n}일)")

    svc = get_service()

    print("adjust 시트 로드 중...")
    adj_tot, adj_pay, p_adj = fetch_adjust(svc, dates)

    print("주문_쿠폰발급사용 시트 로드 중...")
    prm_tot, prm_pay, p_prm = fetch_prm(svc, dates)

    print("배민클럽 시트 로드 중...")
    clb_tot, clb_new, p_clb = fetch_clb(svc, dates)

    print("고객 인사이트 시트 로드 중...")
    insight_adj, insight_prm = fetch_insights(svc)

    # 누적 배열
    cum_adj_tot = make_cum(adj_tot, dates)
    cum_adj_pay = make_cum(adj_pay, dates)
    cum_prm_tot = make_cum(prm_tot, dates)
    cum_prm_pay = make_cum(prm_pay, dates)
    cum_clb     = make_cum(clb_tot, dates)

    # 5월 누적 (전월 비교 기준)
    may_adj_tot = may_cum(cum_adj_tot, dates)
    may_adj_pay = may_cum(cum_adj_pay, dates)
    may_prm_tot = may_cum(cum_prm_tot, dates)
    may_prm_pay = may_cum(cum_prm_pay, dates)
    may_clb     = may_cum(cum_clb, dates)

    print("index.html 업데이트 중...")
    with open(HTML_PATH, encoding="utf-8") as f:
        html = f.read()

    # 전사 배열 길이를 DATES 길이에 맞게 패딩 (전사 데이터 없는 날짜는 0)
    html = pad_tot_arrays(html, n)

    # DATES, DAYS
    html = replace_const(html, "DATES", dates)
    html = replace_const(html, "DAYS", days)

    # selIdx 주석 업데이트
    html = re.sub(
        r'let selIdx = DATES\.length - 1;[^\n]*',
        f'let selIdx = DATES.length - 1; // {yesterday}',
        html
    )

    # TOT 오브젝트 내 제휴 배열
    html = replace_tot_field(html, "adjP", adj_pay)
    html = replace_tot_field(html, "prmP", prm_pay)
    html = replace_tot_field(html, "clbP", clb_tot)
    html = replace_tot_field(html, "clbNP", clb_new)

    # 독립 상수들
    html = replace_const(html, "ADJ_AFF",     adj_pay)
    html = replace_const(html, "PRM_AFF",     prm_pay)
    html = replace_const(html, "CLB_AFF",     clb_tot)
    html = replace_const(html, "ADJ_AFF_TOT", adj_tot)
    html = replace_const(html, "PRM_AFF_TOT", prm_tot)

    html = replace_const(html, "CUM_ADJ_AFF",     cum_adj_pay)
    html = replace_const(html, "CUM_PRM_AFF",     cum_prm_pay)
    html = replace_const(html, "CUM_CLB_AFF",     cum_clb)
    html = replace_const(html, "CUM_ADJ_AFF_TOT", cum_adj_tot)
    html = replace_const(html, "CUM_PRM_AFF_TOT", cum_prm_tot)

    html = replace_const(html, "MAY_ADJ_AFF_TOT", may_adj_tot)
    html = replace_const(html, "MAY_PRM_AFF_TOT", may_prm_tot)

    # MAY_ADJ_AFF, MAY_ADJ_TOT (같은 줄) - 개별 치환
    html = re.sub(r'MAY_ADJ_AFF=\d+', f'MAY_ADJ_AFF={may_adj_pay}', html)
    html = re.sub(r'MAY_PRM_AFF=\d+', f'MAY_PRM_AFF={may_prm_pay}', html)
    html = re.sub(r'MAY_CLB_AFF=\d+', f'MAY_CLB_AFF={may_clb}', html)

    # PARTNERS 개별 배열
    zeros = [0] * n
    for partner in KNOWN_PARTNERS:
        html = replace_partner_field(html, partner, "adj", p_adj.get(partner, zeros))
        html = replace_partner_field(html, partner, "prm", p_prm.get(partner, zeros))
        html = replace_partner_field(html, partner, "clb", p_clb.get(partner, zeros))

    # 고객 인사이트
    html = replace_const(html, "INSIGHT_ADJ", insight_adj)
    html = replace_const(html, "INSIGHT_PRM", insight_prm)

    with open(HTML_PATH, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"완료! {yesterday} 기준 데이터로 업데이트됨.")

if __name__ == "__main__":
    main()
