#!/usr/bin/env python3
"""
대시보드 일별 자동 업데이트 스크립트
매일 실행: Google Sheets 최신 데이터 → index.html 반영
"""

import json, re, os, time
from datetime import date, timedelta
from collections import defaultdict
from googleapiclient.discovery import build
import trino as trino_lib

# ── Trino 연결 ────────────────────────────────────────
def get_trino_conn():
    user = open(os.path.expanduser("~/.trino_user")).read().strip()
    pw   = open(os.path.expanduser("~/.trino_password")).read().strip()
    return trino_lib.dbapi.connect(
        host='trino-gateway-auth.ds.woowa.in',
        port=443,
        user=user,
        http_scheme='https',
        auth=trino_lib.auth.BasicAuthentication(user, pw),
        catalog='iceberg',
        schema='default',
    )

# ── campaign_name → 제휴사명 매핑 (통합INDEX Z·AB열 기준 whitelist) ─────
CAMPAIGN_MAP = {
    'aff_tosspay':                              '토스페이',
    'aff-amore':                                '아모레',
    'aff-bccard':                               '우리카드',
    'aff-hncard':                               '하나카드',
    'aff-kakaopay':                             '카카오페이',
    'aff-kbank':                                '케이뱅크',
    'aff-kbcard':                               'KB국민카드',
    'aff-kt':                                   'KT',
    'aff-lguplus':                              'LGU+',
    'aff-lotte':                                '롯데백화점',
    'aff-melon':                                '멜론',
    'aff-nav erpay':                            '네이버페이',
    'aff-naverpay':                             '네이버페이',
    'aff-shcard':                               '신한카드',
    'aff-sscard':                               '삼성카드',
    'aff-toss_tossetc_mix_pts_toss-coupon':     '토스페이',
    'aff-tosspa':                               '토스페이',
    'aff-tosspay':                              '토스페이',
    'aff-tossplace':                            '토스플레이스',
    'aff-tosssec':                              '토스증권',
    'aff-wrcard':                               '비씨카드',
    'kakaopay':                                 '카카오페이',
    'pts-affiliate_toss-api-ci-registration':   '토스페이',
    'pts-affiliate_toss-api-join-baemin':       '토스페이',
    'pts-affiliate_toss-api-member-bmclub':     '토스페이',
    'samsung_card':                             '삼성카드',
}

# SQL IN 절에 사용할 campaign_name 목록
VALID_CAMPAIGNS = list(CAMPAIGN_MAP.keys())

def map_partner(campaign_name, adgroup_name=''):
    """campaign_name → 제휴사명 변환 (whitelist 기반)"""
    return CAMPAIGN_MAP.get(campaign_name, '기타')

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

def get_range(svc, tab, rng, retries=3, delay=10):
    """타임아웃/일시 오류 시 최대 retries회 재시도"""
    for attempt in range(1, retries + 1):
        try:
            res = svc.spreadsheets().values().get(
                spreadsheetId=SHEET_ID,
                range=f"{tab}!{rng}",
                valueRenderOption="UNFORMATTED_VALUE"
            ).execute()
            return res.get("values", [])
        except Exception as e:
            if attempt < retries:
                print(f"  [재시도 {attempt}/{retries}] {tab} 오류: {e} — {delay}초 후 재시도")
                time.sleep(delay)
                delay *= 2  # 지수 백오프: 10s → 20s → 40s
            else:
                print(f"  [실패] {tab} 최종 오류: {e}")
                raise

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
    """Trino로 전체 기간 유입 실적 조회
    - 총계: 전체 캠페인 중복 제거 후 COUNT(DISTINCT user_id)
    - 파트너별: 각 파트너 캠페인 내 COUNT(DISTINCT user_id)
    """
    IN_CLAUSE = ','.join(f"'{c}'" for c in VALID_CAMPAIGNS)
    WHERE = f"""
      date(date_add('hour', 9, engagement_time))
          BETWEEN date('{START}') AND DATE(NOW() + INTERVAL '9' HOUR) - INTERVAL '1' DAY
      AND date(part_date)
          BETWEEN date('{START}') - INTERVAL '1' DAY
              AND DATE(NOW() + INTERVAL '9' HOUR) - INTERVAL '1' DAY
      AND a.campaign_name IN ({IN_CLAUSE})
    """
    SQL = f"""
SELECT
    cast(CAST(date_add('hour', 9, engagement_time) AS date) as varchar) AS part_date,
    cast(a.campaign_name as varchar) as campaign_name,
    count(distinct user_id) AS mem_cnt
FROM hive.dhcentraladjust.cmdf_adjust_raw_data_main_installs a
WHERE {WHERE}
GROUP BY 1, 2
ORDER BY 1, 2
"""
    by_p = defaultdict(lambda: defaultdict(int))
    try:
        conn = get_trino_conn()
        cur  = conn.cursor()
        cur.execute(SQL)
        rows = cur.fetchall()
        print(f"  Trino 유입 실적: {len(rows)}행 수신")
        for part_date, campaign_name, mem_cnt in rows:
            if not part_date or part_date < START: continue
            partner = map_partner(campaign_name)
            if partner == '기타': continue
            by_p[partner][part_date] += int(mem_cnt)
    except Exception as e:
        print(f"  [WARN] Trino 유입 실적 실패: {e} → 0으로 채움")

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

def fetch_insight_adj_trino(yesterday: str, existing: dict) -> dict:
    """Trino에서 어제 유입 고객 세그먼트 가져와 기존 데이터에 추가"""
    SQL = f"""
WITH base AS (
  SELECT
    CAST(date_add('hour', 9, engagement_time) AS DATE) AS part_date,
    a.campaign_name,
    user_id AS mem_no
  FROM dhcentraladjust.cmdf_adjust_raw_data_main_installs a
  WHERE date(date_add('hour', 9, engagement_time)) = DATE '{yesterday}'
    AND date(part_date) BETWEEN DATE '{yesterday}' - INTERVAL '1' DAY
                             AND DATE '{yesterday}'
    AND a.campaign_name IN ({','.join(f"'{c}'" for c in VALID_CAMPAIGNS)})
),
base_dedup AS (
  SELECT DISTINCT part_date, campaign_name, mem_no FROM base
),
order_history AS (
  SELECT bd.part_date, bd.campaign_name, bd.mem_no,
    COUNT(CASE WHEN o.part_date >= CAST(bd.part_date - INTERVAL '30' DAY AS VARCHAR)
                AND o.part_date < CAST(bd.part_date AS VARCHAR) THEN 1 END) AS ord_cnt_1m,
    COUNT(CASE WHEN o.part_date >= CAST(bd.part_date - INTERVAL '90' DAY AS VARCHAR)
                AND o.part_date < CAST(bd.part_date AS VARCHAR) THEN 1 END) AS ord_cnt_3m,
    COUNT(CASE WHEN o.part_date >= CAST(bd.part_date - INTERVAL '180' DAY AS VARCHAR)
                AND o.part_date < CAST(bd.part_date AS VARCHAR) THEN 1 END) AS ord_cnt_6m
  FROM base_dedup bd
  LEFT JOIN sborder_mart.order_master o
    ON bd.mem_no = o.mem_no
    AND o.part_date >= '2025-06-01'
    AND o.part_date < CAST(bd.part_date AS VARCHAR)
    AND o.is_test = 0 AND o.is_closed = 1
  GROUP BY bd.part_date, bd.campaign_name, bd.mem_no
),
classified AS (
  SELECT part_date, campaign_name, mem_no,
    CASE
      WHEN ord_cnt_1m >= 10 THEN '활성+고빈도'
      WHEN ord_cnt_1m >= 4  THEN '활성+중빈도'
      WHEN ord_cnt_1m >= 1  THEN '활성+저빈도'
      WHEN ord_cnt_3m >= 1  THEN '단기이탈'
      WHEN ord_cnt_6m >= 1  THEN '장기이탈'
      ELSE '초장기이탈'
    END AS seg
  FROM order_history
)
SELECT
  CAST(part_date AS VARCHAR)     AS part_date,
  CAST(campaign_name AS VARCHAR) AS campaign_name,
  CAST(seg AS VARCHAR)           AS seg,
  COUNT(DISTINCT mem_no)         AS uv
FROM classified
GROUP BY part_date, campaign_name, seg
ORDER BY part_date, campaign_name, seg
"""
    result = dict(existing)  # 기존 데이터 복사
    try:
        conn = get_trino_conn()
        cur  = conn.cursor()
        cur.execute(SQL)
        rows = cur.fetchall()
        print(f"  Trino 유입 인사이트: {len(rows)}행 수신")

        day_data = defaultdict(lambda: defaultdict(int))
        for part_date, campaign_name, seg, uv in rows:
            partner = map_partner(campaign_name)
            if partner == '기타': continue
            day_data[partner][seg] += int(uv)

        if day_data:
            result[yesterday] = {p: dict(segs) for p, segs in day_data.items()}
    except Exception as e:
        print(f"  [WARN] Trino 유입 인사이트 실패: {e} → 기존 데이터 유지")
    return result


def fetch_insights(svc):
    """주문 고객 인사이트 (Google Sheets)"""
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

    return parse_insight(get_range(svc, "프로모션주문_고객인사이트", "A2:E"), 0, 3, 2, 4)


def fetch_tot_prm_trino(dates):
    """Trino로 전사 프로모션 주문수 가져오기"""
    SQL = f"""
SELECT
    part_date,
    COUNT(*) AS cnt
FROM sborder_mart.order_master
WHERE part_date >= '{START}'
  AND part_date < CAST(CURRENT_DATE AS VARCHAR)
  AND is_test = 0
  AND is_closed = 1
  AND service_type IN ('BAEMIN', 'BAERA')
GROUP BY part_date
ORDER BY part_date
"""
    by_date = {}
    try:
        conn = get_trino_conn()
        cur  = conn.cursor()
        cur.execute(SQL)
        for part_date, cnt in cur.fetchall():
            by_date[str(part_date)] = int(cnt)
        print(f"  Trino 전사 프로모션: {len(by_date)}일치 수신")
    except Exception as e:
        print(f"  [WARN] Trino 전사 프로모션 실패: {e}")
    return [by_date.get(d, 0) for d in dates]


def fetch_tot_clb_sheets(svc, dates):
    """실적(D) 82~84행에서 전사 배민클럽 가져오기"""
    # 21행 = 날짜 헤더, H열부터 (22행은 요일)
    dates_raw = get_range(svc, "실적(D)", "H21:BZ21")
    dates_row = dates_raw[0] if dates_raw else []

    # 날짜 → 컬럼 인덱스 매핑
    date_map = {}
    for i, v in enumerate(dates_row):
        d = parse_date(v)
        if d:
            date_map[d] = i

    def row_arr(row_num):
        raw = get_range(svc, "실적(D)", f"H{row_num}:BZ{row_num}")
        data_row = raw[0] if raw else []
        return [parse_int(data_row[date_map[d]]) if d in date_map and date_map[d] < len(data_row) else 0
                for d in dates]

    try:
        clb  = row_arr(82)  # 전사 전체
        clbN = row_arr(83)  # 신규
        clbR = row_arr(84)  # 재구독
        print(f"  실적(D) 전사 배민클럽: 로드 완료")
    except Exception as e:
        print(f"  [WARN] 전사 배민클럽 로드 실패: {e}")
        zeros = [0] * len(dates)
        clb, clbN, clbR = zeros, zeros, zeros
    return clb, clbN, clbR


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

def replace_partner_monthly(html, partner, m_adj, m_prm, m_clb):
    """파트너의 mAdj, mPrm, mClb 월별 누계 업데이트"""
    escaped = re.escape(partner)
    def sub(m):
        line = m.group(0)
        for fname, val in [('mAdj', m_adj), ('mPrm', m_prm), ('mClb', m_clb)]:
            line = re.sub(
                rf'{re.escape(fname)}:\{{[^}}]+\}}',
                f'{fname}:{json.dumps(val)}',
                line
            )
        return line
    pattern = rf"'{escaped}':\{{[^\n]+\}}"
    result, n = re.subn(pattern, sub, html)
    if n == 0:
        print(f"  [WARN] PARTNER monthly {partner} not found")
    return result

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

    print("Trino 유입 실적 쿼리 중...")
    adj_tot, adj_pay, p_adj = fetch_adjust(svc, dates)

    print("주문_쿠폰발급사용 시트 로드 중...")
    prm_tot, prm_pay, p_prm = fetch_prm(svc, dates)

    print("배민클럽 시트 로드 중...")
    clb_tot, clb_new, p_clb = fetch_clb(svc, dates)

    print("주문 고객 인사이트 시트 로드 중...")
    insight_prm = fetch_insights(svc)

    print("유입 고객 인사이트 Trino 쿼리 중...")
    with open(HTML_PATH, encoding="utf-8") as _f:
        _html_tmp = _f.read()
    _m = re.search(r'const INSIGHT_ADJ=({.*?});', _html_tmp, re.DOTALL)
    existing_adj = json.loads(_m.group(1)) if _m else {}
    insight_adj = fetch_insight_adj_trino(yesterday, existing_adj)

    print("전사 프로모션 주문수 Trino 쿼리 중...")
    tot_prm = fetch_tot_prm_trino(dates)

    print("전사 배민클럽 시트 로드 중...")
    tot_clb, tot_clbN, tot_clbR = fetch_tot_clb_sheets(svc, dates)

    # 누적 배열 (제휴)
    cum_adj_tot = make_cum(adj_tot, dates)
    cum_adj_pay = make_cum(adj_pay, dates)
    cum_prm_tot = make_cum(prm_tot, dates)
    cum_prm_pay = make_cum(prm_pay, dates)
    cum_clb     = make_cum(clb_tot, dates)

    # 누적 배열 (전사)
    cum_tot_prm = make_cum(tot_prm, dates)
    cum_tot_clb = make_cum(tot_clb, dates)

    # 5월 누적 (전월 비교 기준)
    may_adj_tot = may_cum(cum_adj_tot, dates)
    may_adj_pay = may_cum(cum_adj_pay, dates)
    may_prm_tot = may_cum(cum_prm_tot, dates)
    may_prm_pay = may_cum(cum_prm_pay, dates)
    may_clb     = may_cum(cum_clb, dates)

    print("index.html 업데이트 중...")
    with open(HTML_PATH, encoding="utf-8") as f:
        html = f.read()

    # 전사 prm, clb 배열 업데이트 (Trino + Sheets)
    html = replace_tot_field(html, "prm", tot_prm)
    html = replace_tot_field(html, "clb", tot_clb)
    html = replace_tot_field(html, "clbN", tot_clbN)
    html = replace_tot_field(html, "clbR", tot_clbR)
    html = replace_const(html, "PRM_TOT", tot_prm)
    html = replace_const(html, "CLB_TOT", tot_clb)
    html = replace_const(html, "CUM_PRM_TOT", cum_tot_prm)
    html = replace_const(html, "CUM_CLB_TOT", cum_tot_clb)
    html = re.sub(r'MAY_PRM_TOT=\d+', f'MAY_PRM_TOT={may_cum(cum_tot_prm, dates)}', html)
    html = re.sub(r'MAY_CLB_TOT=\d+', f'MAY_CLB_TOT={may_cum(cum_tot_clb, dates)}', html)

    # 나머지 전사 배열 길이를 DATES 길이에 맞게 패딩 (adj 등)
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

    # 월별 구간 인덱스
    may_end   = next((i for i, d in enumerate(dates) if d == "2026-05-31"), len(dates)-1)
    jun_start = may_end + 1

    # PARTNERS 개별 배열 + 월별 누계
    zeros = [0] * n
    for partner in KNOWN_PARTNERS:
        adj_arr = p_adj.get(partner, zeros)
        prm_arr = p_prm.get(partner, zeros)
        clb_arr = p_clb.get(partner, zeros)

        html = replace_partner_field(html, partner, "adj", adj_arr)
        html = replace_partner_field(html, partner, "prm", prm_arr)
        html = replace_partner_field(html, partner, "clb", clb_arr)

        # 파트너별 월 누계 (mAdj, mPrm, mClb)
        html = replace_partner_monthly(html, partner,
            m_adj={"may": sum(adj_arr[:may_end+1]), "jun": sum(adj_arr[jun_start:])},
            m_prm={"may": sum(prm_arr[:may_end+1]), "jun": sum(prm_arr[jun_start:])},
            m_clb={"may": sum(clb_arr[:may_end+1]), "jun": sum(clb_arr[jun_start:])},
        )

    # 고객 인사이트
    html = replace_const(html, "INSIGHT_ADJ", insight_adj)
    html = replace_const(html, "INSIGHT_PRM", insight_prm)

    with open(HTML_PATH, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"완료! {yesterday} 기준 데이터로 업데이트됨.")

if __name__ == "__main__":
    main()
