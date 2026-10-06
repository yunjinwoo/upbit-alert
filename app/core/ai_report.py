"""AI 분석용 리포트 — 최근 N거래일 동안 스케줄이 쌓은 데이터를 마크다운 파일 하나로 묶는다.

내려받아 Claude/ChatGPT 같은 AI에게 그대로 올리고 "이번 주 계속 눈에 띈 종목은?" 같은 질문을 하라고
만든 것. 그래서 표를 그냥 나열하지 않고 **종목 하나 = 한 줄**로 여러 수집 결과를 합쳐 둔다
(HTS 조회 상위·상승률 순위·관심종목 상위·Signal Score·종목별 수급·토스 스크리닝·업종). 여러 목록에
여러 번 걸린 종목이 위로 온다.

읽기 전용 — 이미 저장된 DB만 읽고 새 API 호출은 하지 않는다.
날짜 형식이 테이블마다 조금씩 달라도 되도록 "각 테이블의 최근 N개 날짜"로 기간을 자른다.
"""
import sqlite3
from datetime import datetime

from app.config import Config
from app.utils import db_manager

DEFAULT_DAYS = 5
MAX_DAYS = 30
STOCK_TABLE_LIMIT = 60

QUESTIONS = [
    "여러 날·여러 목록에 반복해서 등장한 종목은 무엇이고, 공통점(업종, 수급 주체)은 무엇인가요?",
    "Signal Score 등급이 오르거나 내린 종목과 그 이유로 보이는 지표는?",
    "외국인·기관 순매수가 이어지는데 아직 상승률 순위에는 안 걸린 종목이 있나요?",
    "업종지수 흐름과 개별 종목 흐름이 엇갈리는 곳은?",
    "자동매매 성과에서 손실이 몰린 패턴이 있나요? 수집 데이터와 연결되는 부분은?",
]


def _num(v):
    try:
        return float(str(v).replace(',', ''))
    except (TypeError, ValueError):
        return None


def _fmt_num(v, digits=0):
    n = _num(v)
    if n is None:
        return '-'
    return f"{n:,.{digits}f}"


def _fmt_rate(v):
    n = _num(v)
    return '-' if n is None else f"{n:+.2f}%"


def _md_cell(v) -> str:
    return str('-' if v is None or v == '' else v).replace('|', '/').replace('\n', ' ')


def _md_table(headers: list, rows: list) -> str:
    if not rows:
        return '_(데이터 없음)_'
    out = ['| ' + ' | '.join(headers) + ' |', '|' + '---|' * len(headers)]
    out += ['| ' + ' | '.join(_md_cell(c) for c in r) + ' |' for r in rows]
    return '\n'.join(out)


def _recent_dates(cur, table: str, days: int) -> list:
    try:
        cur.execute(f"SELECT DISTINCT date FROM {table} WHERE date IS NOT NULL AND date != '' "
                    f"ORDER BY date DESC LIMIT ?", (days,))
        return [r[0] for r in cur.fetchall()]
    except sqlite3.OperationalError:  # 테이블이 아직 없는 서버
        return []


def _rows(cur, sql: str, params=()) -> list:
    try:
        cur.execute(sql, params)
        return [dict(r) for r in cur.fetchall()]
    except sqlite3.OperationalError:
        return []


def _in(dates: list) -> str:
    return ','.join('?' * len(dates)) or "''"


def load_report_data(days: int = DEFAULT_DAYS) -> dict:
    """리포트에 들어갈 원본 행을 테이블별로 읽는다(각 테이블의 최근 days개 날짜)."""
    conn = sqlite3.connect(db_manager.DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    data = {}

    def by_dates(key, table, sql_tail):
        dates = _recent_dates(cur, table, days)
        data[key + '_dates'] = sorted(dates)
        data[key] = _rows(cur, f"SELECT * FROM {table} WHERE date IN ({_in(dates)}) {sql_tail}", dates) if dates else []

    by_dates('hts', 'stock_hts_top_view_hourly', 'ORDER BY date, hour, rank')
    by_dates('gainers', 'stock_top_gainers_hourly', 'ORDER BY date, hour, rank')
    by_dates('interest', 'stock_top_interest_daily', 'ORDER BY date, rank')
    by_dates('signal', 'signal_score_daily', 'ORDER BY date, total_score DESC')
    by_dates('stock_investor', 'stock_investor_daily', 'ORDER BY date')
    by_dates('investor_trend', 'investor_trend_daily', 'ORDER BY date, mrkt_div, invr_cls_code')
    by_dates('sector_index', 'sector_index_daily', 'ORDER BY date, sector_code')

    sector_dates = _recent_dates(cur, 'sector_stocks_daily', 1)
    data['sector_stocks'] = _rows(cur, "SELECT code, sector_name FROM sector_stocks_daily WHERE date = ?",
                                  sector_dates) if sector_dates else []
    data['screening'] = _rows(cur, "SELECT * FROM stock_screening_daily")
    data['memo'] = _rows(cur, "SELECT code, memo, created_at FROM stock_memo ORDER BY created_at")
    data['regime_history'] = _rows(cur, "SELECT changed_at, from_regime, to_regime, score FROM market_regime_history "
                                        "ORDER BY changed_at DESC LIMIT 10")
    conn.close()
    return data


def build_stock_rows(data: dict) -> list:
    """수집 결과를 종목 단위로 합친다. 반환은 '등장 목록 수 → 총 등장 횟수' 순으로 정렬된 dict 리스트."""
    stocks = {}

    def get(code, name):
        s = stocks.setdefault(code, {'code': code, 'name': name, 'hts': 0, 'hts_best': None, 'gainers': 0,
                                     'gainers_max_rate': None, 'interest_best': None, 'signal': [],
                                     'frgn': 0.0, 'orgn': 0.0, 'prsn': 0.0, 'investor_days': 0,
                                     'screening': None, 'sector': None, 'memo': None})
        if name and not s['name']:
            s['name'] = name
        return s

    for r in data.get('hts', []):
        s = get(r.get('code'), r.get('name'))
        s['hts'] += 1
        if r.get('rank') is not None and (s['hts_best'] is None or r['rank'] < s['hts_best']):
            s['hts_best'] = r['rank']
    for r in data.get('gainers', []):
        s = get(r.get('code'), r.get('name'))
        s['gainers'] += 1
        rate = _num(r.get('change_rate'))
        if rate is not None and (s['gainers_max_rate'] is None or rate > s['gainers_max_rate']):
            s['gainers_max_rate'] = rate
    for r in data.get('interest', []):
        s = get(r.get('code'), r.get('name'))
        if r.get('rank') is not None and (s['interest_best'] is None or r['rank'] < s['interest_best']):
            s['interest_best'] = r['rank']
    for r in data.get('signal', []):
        if r.get('grade') in ('A', 'B') or r.get('code') in stocks:
            get(r.get('code'), r.get('name'))['signal'].append(r)

    # 아래는 이미 목록에 오른 종목에만 덧붙이는 정보(이것만으로 종목을 새로 만들지 않는다)
    for r in data.get('stock_investor', []):
        s = stocks.get(r.get('code'))
        if s:
            s['frgn'] += _num(r.get('frgn_ntby_tr_pbmn')) or 0.0
            s['orgn'] += _num(r.get('orgn_ntby_tr_pbmn')) or 0.0
            s['prsn'] += _num(r.get('prsn_ntby_tr_pbmn')) or 0.0
            s['investor_days'] += 1
    for r in data.get('screening', []):
        s = stocks.get(r.get('ticker'))
        if s:
            reasons = [label for key, label in (('breakout_1d', '일봉 돌파'), ('momentum_confluence', '모멘텀'))
                       if r.get(key)]
            if r.get('near_ma200') and r.get('above_cloud'):
                reasons.append('200선 근접+구름 위')
            s['screening'] = '/'.join(reasons) or '조건 없음'
    for r in data.get('sector_stocks', []):
        s = stocks.get(r.get('code'))
        if s:
            s['sector'] = r.get('sector_name') if not s['sector'] else s['sector']
    for r in data.get('memo', []):
        s = stocks.get(r.get('code'))
        if s:
            s['memo'] = r.get('memo')  # 오래된 순으로 읽었으니 마지막 = 최신 메모

    for s in stocks.values():
        s['signal'].sort(key=lambda r: r.get('date') or '')
        s['list_count'] = sum(bool(x) for x in (s['hts'], s['gainers'], s['interest_best'],
                                                 any(r.get('grade') in ('A', 'B') for r in s['signal'])))
        s['appearances'] = s['hts'] + s['gainers'] + (1 if s['interest_best'] else 0)
    return sorted((s for s in stocks.values() if s['code']),
                  key=lambda s: (-s['list_count'], -s['appearances'], s['name'] or ''))


def _signal_trend(rows: list) -> str:
    return ' → '.join(f"{(r.get('date') or '')[5:]} {r.get('grade') or '-'}{r.get('total_score') if r.get('total_score') is not None else ''}"
                      for r in rows) or '-'


def _period(dates: list) -> str:
    return f"{dates[0]} ~ {dates[-1]} ({len(dates)}일)" if dates else '없음'


def build_ai_report(data: dict, days: int, performance: dict = None, job_text: str = None, now: datetime = None) -> str:
    now = now or datetime.now()
    stocks = build_stock_rows(data)
    lines = [
        f"# 국내주식 수집 데이터 리포트 (최근 {days}거래일)",
        "",
        f"생성 시각: {now.strftime('%Y-%m-%d %H:%M')} · upbit-alert 앱이 스케줄로 모은 데이터를 그대로 정리한 파일입니다.",
        "",
        "## AI에게 부탁하는 것",
        "",
        "아래 데이터만 근거로 분석해 주세요. 데이터에 없는 내용은 추측이라고 표시해 주세요. "
        "결론보다 근거(어느 표의 어떤 숫자인지)를 먼저 보여 주세요. 매수·매도 권유가 아니라 관찰 정리가 목적입니다.",
        "",
        "물어보고 싶은 질문 예시:",
        *[f"{i}. {q}" for i, q in enumerate(QUESTIONS, 1)],
        "",
        "## 데이터 설명",
        "",
        f"- HTS 조회 상위 20: 평일 장중 매시 수집. 기간 {_period(data.get('hts_dates', []))}",
        f"- 상승률 순위: 평일 9·12·15·18시 스냅샷. 기간 {_period(data.get('gainers_dates', []))}",
        f"- 관심종목 등록 상위: 평일 16시 하루 1회. 기간 {_period(data.get('interest_dates', []))}",
        f"- Signal Score: 평일 15:40 계산 (A 80점 이상, B 65점 이상). 기간 {_period(data.get('signal_dates', []))}",
        f"- 종목별 투자자 순매수: 단위 백만원(KIS 원본). 기간 {_period(data.get('stock_investor_dates', []))}",
        "- 토스 스크리닝: 가장 최근 1회 결과",
        "",
        "## 1. 종목별 종합 (여러 목록에 걸린 종목이 위)",
        "",
        "`목록 수` = HTS 조회 상위 / 상승률 순위 / 관심종목 상위 / Signal Score A·B 중 몇 곳에 걸렸는지. "
        "`HTS`·`상승률` 칸은 기간 중 등장한 시간대 수.",
        "",
    ]
    lines.append(_md_table(
        ['종목', '코드', '업종', '목록 수', 'HTS(최고순위)', '상승률(최고등락)', '관심 최고순위',
         'Signal Score 추이', '외국인 순매수', '기관 순매수', '개인 순매수', '토스 스크리닝', '메모'],
        [[s['name'], s['code'], s['sector'], s['list_count'],
          f"{s['hts']}회 ({s['hts_best']}위)" if s['hts'] else '-',
          f"{s['gainers']}회 ({_fmt_rate(s['gainers_max_rate'])})" if s['gainers'] else '-',
          f"{s['interest_best']}위" if s['interest_best'] else '-',
          _signal_trend(s['signal']),
          _fmt_num(s['frgn']) if s['investor_days'] else '-',
          _fmt_num(s['orgn']) if s['investor_days'] else '-',
          _fmt_num(s['prsn']) if s['investor_days'] else '-',
          s['screening'], (s['memo'] or '')[:40] or None]
         for s in stocks[:STOCK_TABLE_LIMIT]]))
    if len(stocks) > STOCK_TABLE_LIMIT:
        lines += ['', f"_(그 밖에 {len(stocks) - STOCK_TABLE_LIMIT}종목은 한 목록에만 잠깐 등장해 생략)_"]

    lines += ['', '## 2. 시장 전체 투자자 순매수 (프로그램 매매동향, 백만원)', '']
    market_name = {'1': '코스피', '4': '코스닥'}
    lines.append(_md_table(
        ['날짜', '시장', '투자자', '순매수 금액'],
        [[r.get('date'), market_name.get(str(r.get('mrkt_div')), r.get('mrkt_div')), r.get('invr_cls_name'),
          _fmt_num(r.get('all_ntby_amt'))] for r in data.get('investor_trend', [])]))

    lines += ['', '## 3. 업종지수 (최근일 등락률 순)', '']
    sector_rows = data.get('sector_index', [])
    last_date = max((r.get('date') or '' for r in sector_rows), default=None)
    latest = sorted((r for r in sector_rows if r.get('date') == last_date),
                    key=lambda r: _num(r.get('change_rate')) or 0, reverse=True)
    history = {}
    for r in sector_rows:
        history.setdefault(r.get('sector_code'), []).append(_fmt_rate(r.get('change_rate')))
    lines.append(_md_table(
        ['업종', '종가', '등락률', '기간 중 일별 등락률', '심리도'],
        [[r.get('sector_name'), _fmt_num(r.get('close'), 2), _fmt_rate(r.get('change_rate')),
          ' / '.join(history.get(r.get('sector_code'), [])), r.get('psychology_index')] for r in latest]))

    lines += ['', '## 4. 코인 시장 국면 변경 이력 (최근 10회)', '']
    lines.append(_md_table(['변경 시각', '이전', '이후', '점수'],
                           [[r.get('changed_at'), r.get('from_regime'), r.get('to_regime'), r.get('score')]
                            for r in data.get('regime_history', [])]))

    if performance:
        lines += ['', '## 자동매매 성과 (실현손익, 기간 내 청산 기준)', '']
        rows = []
        for label, perf in performance.items():
            sm = perf['summary']
            rows.append([label, sm['closed_count'], sm['open_count'],
                         f"{sm['win_rate']:.0f}%" if sm['win_rate'] is not None else '-',
                         _fmt_num(sm['net_pnl_krw']), _fmt_num(sm['max_drawdown_krw'])])
        lines.append(_md_table(['계좌', '청산', '보유 중', '승률', '순손익(원, 수수료 추정 차감)', '최대 낙폭(원)'], rows))
        for label, perf in performance.items():
            if perf['by_signal']:
                lines += ['', f"**{label} — 진입 신호별**", '']
                lines.append(_md_table(['진입 신호', '건수', '승률', '누적 손익(원)'],
                                       [[g.get('key'), g.get('count'),
                                         f"{g['win_rate']:.0f}%" if g.get('win_rate') is not None else '-',
                                         _fmt_num(g.get('total_pnl_krw'))] for g in perf['by_signal']]))

    if job_text:
        lines += ['', '## 스케줄 실행 상태 (최근 24시간)', '', job_text]

    lines += ['', '---', '※ 앱이 수집·계산한 지표 정리이며 매수 권유가 아닙니다.', '']
    return '\n'.join(lines)


def load_performance(date_from: str) -> dict:
    """업비트/토스 실거래·모의 계좌 중 체결 기록이 있는 것만 성과 요약."""
    from app.core.trade_performance import build_performance
    out = {}
    for broker, label, fees in (('upbit', '업비트', (Config.TRADE_FEE_RATE_UPBIT_BUY, Config.TRADE_FEE_RATE_UPBIT_SELL)),
                                ('toss', '토스', (Config.TRADE_FEE_RATE_TOSS_BUY, Config.TRADE_FEE_RATE_TOSS_SELL))):
        for mode, mode_label in (('live', '실거래'), ('paper', '모의')):
            try:
                rows = db_manager.get_trade_fill_rows(broker, mode)
            except Exception:
                continue
            if rows:
                out[f"{label} {mode_label}"] = build_performance(rows, *fees, date_from=date_from)
    return out


def generate_ai_report(days: int = DEFAULT_DAYS) -> str:
    from app.core.stock_digest import build_job_summary_text, load_job_summary
    days = max(1, min(int(days or DEFAULT_DAYS), MAX_DAYS))
    data = load_report_data(days)
    period_dates = data.get('signal_dates') or data.get('hts_dates') or []
    performance = load_performance(period_dates[0]) if period_dates else load_performance(None)
    return build_ai_report(data, days, performance=performance, job_text=build_job_summary_text(load_job_summary()))
