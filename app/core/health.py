"""서버 상태 점검(읽기 전용) — 수집 데이터가 제때 들어왔는지, 스케줄 작업이 실패했는지, 로그에 오류가
쌓였는지를 한 번에 모아 /api/health 로 내려준다. 매일 도는 자동 점검(에이전트)이 서버 상태를 보는 통로.

- 이 모듈은 DB를 읽기 전용(mode=ro)으로만 열고, 외부 API를 부르지 않으며, 매매 관련 함수도 부르지 않는다.
- 판정(judge_*)은 순수 함수라 tests/test_health.py 에서 시각을 넣어 검증한다.
- 공휴일은 모른다 — 평일 휴장일엔 주식 수집 테이블이 stale 로 나올 수 있다(note 에 적어 둔다).
"""
import os
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

from app.core.stock_digest import build_job_summary_text, load_job_summary
from app.utils import db_manager

# schedule
#   'always'     : 24시간 도는 수집 — 마지막 저장 시각이 max_age_min 분 안이어야 한다.
#   'kr_weekday' : 평일에만 도는 수집 — due(시, 분)가 지난 평일엔 오늘 날짜 행이, 그 전엔 직전 평일 행이 있어야 한다.
#                  due 는 실제 수집 시각에 여유(약 30분)를 더한 값.
FRESHNESS_RULES = [
    {'key': 'coin_screening', 'label': '코인 스크리닝(30분 주기)', 'table': 'coin_screening_daily',
     'column': 'updated_at', 'schedule': 'always', 'max_age_min': 90, 'process': 'coin-analysis-bot'},
    {'key': 'stock_screening', 'label': '국내주식 스크리닝(30분 주기)', 'table': 'stock_screening_daily',
     'column': 'updated_at', 'schedule': 'always', 'max_age_min': 90, 'process': 'toss-analysis-bot'},
    {'key': 'hts_top_view', 'label': 'HTS 조회상위(평일 9~15시 매시 10분)', 'table': 'stock_hts_top_view_hourly',
     'column': 'date', 'schedule': 'kr_weekday', 'due': (9, 40), 'process': 'stock-bot'},
    {'key': 'top_gainers', 'label': '상승률 순위(평일 9/12/15/18시 10분)', 'table': 'stock_top_gainers_hourly',
     'column': 'date', 'schedule': 'kr_weekday', 'due': (9, 40), 'process': 'stock-bot'},
    {'key': 'market_cap', 'label': '시가총액(평일 15:40)', 'table': 'stock_market_cap_daily',
     'column': 'date', 'schedule': 'kr_weekday', 'due': (16, 10), 'process': 'stock-bot'},
    {'key': 'signal_score', 'label': 'Signal Score(평일 15:40)', 'table': 'signal_score_daily',
     'column': 'date', 'schedule': 'kr_weekday', 'due': (16, 10), 'process': 'stock-bot'},
    {'key': 'top_interest', 'label': '관심종목 상위(평일 16시)', 'table': 'stock_top_interest_daily',
     'column': 'date', 'schedule': 'kr_weekday', 'due': (16, 30), 'process': 'stock-bot'},
    {'key': 'investor_trend', 'label': '투자자별 매매동향(평일 14/19시)', 'table': 'investor_trend_daily',
     'column': 'date', 'schedule': 'kr_weekday', 'due': (19, 40), 'process': 'stock-bot'},
    {'key': 'sector_index', 'label': '업종 일자별지수(평일 14/19시)', 'table': 'sector_index_daily',
     'column': 'date', 'schedule': 'kr_weekday', 'due': (19, 40), 'process': 'stock-bot'},
]

HOLIDAY_NOTE = '공휴일(휴장일)이면 정상일 수 있음'
LOG_TAIL_BYTES = 512 * 1024
LOG_WINDOW_HOURS = 24
LOG_RECENT_ERRORS = 20
LOG_LINE_MAX_LEN = 300
TS_FORMAT = '%Y-%m-%d %H:%M:%S'


def _parse_ts(value):
    """'2026-10-05 21:00:00[,123]' / '2026-10-05' / '20261005' → datetime. 못 읽으면 None."""
    if not value:
        return None
    text = str(value).strip()
    for fmt, length in ((TS_FORMAT, 19), ('%Y-%m-%dT%H:%M:%S', 19), ('%Y-%m-%d', 10), ('%Y%m%d', 8)):
        try:
            return datetime.strptime(text[:length], fmt)
        except ValueError:
            continue
    return None


def expected_weekday_date(now: datetime, due: tuple):
    """평일 수집 테이블에 지금 있어야 하는 가장 최근 날짜. due 가 지난 평일이면 오늘, 아니면 직전 평일."""
    day = now.date()
    if day.weekday() < 5 and (now.hour, now.minute) >= tuple(due):
        return day
    day -= timedelta(days=1)
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    return day


def judge_freshness(rule: dict, latest, now: datetime) -> dict:
    """규칙 1개 + 테이블의 최신 값 → 판정. status: ok / stale / empty(행 없음) / unreadable(값 형식 모름)."""
    result = {'key': rule['key'], 'label': rule['label'], 'table': rule['table'], 'process': rule.get('process'),
              'latest': latest, 'status': 'ok', 'note': None}
    if latest is None:
        result['status'] = 'empty'
        result['note'] = '행이 하나도 없음'
        return result
    parsed = _parse_ts(latest)
    if parsed is None:
        result['status'] = 'unreadable'
        result['note'] = f'날짜 형식을 읽지 못함: {latest}'
        return result

    if rule['schedule'] == 'always':
        age_min = int((now - parsed).total_seconds() // 60)
        result['age_minutes'] = age_min
        result['expected'] = f"{rule['max_age_min']}분 이내"
        if age_min > rule['max_age_min']:
            result['status'] = 'stale'
            result['note'] = f"{age_min}분 전이 마지막 — {rule.get('process')} 프로세스가 멈췄는지 확인"
    else:
        expected = expected_weekday_date(now, rule['due'])
        result['expected'] = expected.strftime('%Y-%m-%d')
        if parsed.date() < expected:
            result['status'] = 'stale'
            result['note'] = f"{expected} 자료가 없음 — {HOLIDAY_NOTE}"
    return result


def _connect_readonly():
    uri = Path(db_manager.DB_PATH).resolve().as_uri() + '?mode=ro'
    return sqlite3.connect(uri, uri=True, timeout=5)


def load_freshness(now: datetime) -> list:
    """규칙마다 MAX(column) 한 번씩만 읽는다. 테이블이 없으면 status=missing."""
    results = []
    try:
        conn = _connect_readonly()
    except sqlite3.Error as e:
        return [{'key': 'db', 'label': 'DB 열기', 'status': 'missing', 'note': f'DB를 열 수 없음: {e}'}]
    try:
        for rule in FRESHNESS_RULES:
            try:
                # table/column 은 위 상수에서만 온다(사용자 입력 아님)
                latest = conn.execute(f"SELECT MAX({rule['column']}) FROM {rule['table']}").fetchone()[0]
            except sqlite3.Error as e:
                results.append({'key': rule['key'], 'label': rule['label'], 'table': rule['table'],
                                'process': rule.get('process'), 'latest': None, 'status': 'missing', 'note': str(e)})
                continue
            results.append(judge_freshness(rule, latest, now))
    finally:
        conn.close()
    return results


def summarize_log_lines(lines: list, now: datetime) -> dict:
    """app.log 꼬리(lines) → 최근 LOG_WINDOW_HOURS 시간의 ERROR/WARNING 건수와 최근 오류 줄.
    시각이 없는 줄(트레이스백 이어지는 줄 등)은 바로 앞 줄의 시각·레벨을 따른다."""
    since = now - timedelta(hours=LOG_WINDOW_HOURS)
    errors, warnings, recent, last_ts = 0, 0, [], None
    in_window = False
    for line in lines:
        ts = _parse_ts(line[:19]) if len(line) >= 19 and line[4:5] == '-' else None
        if ts is None:
            continue
        last_ts = ts
        in_window = ts >= since
        if not in_window:
            continue
        if ' - ERROR - ' in line:
            errors += 1
            recent.append(line.rstrip()[:LOG_LINE_MAX_LEN])
        elif ' - WARNING - ' in line:
            warnings += 1
    return {
        'window_hours': LOG_WINDOW_HOURS,
        'last_line_at': last_ts.strftime(TS_FORMAT) if last_ts else None,
        'error_count': errors,
        'warning_count': warnings,
        'recent_errors': recent[-LOG_RECENT_ERRORS:],
    }


def load_log_summary(now: datetime, log_path: str = None) -> dict:
    """app.log 끝 LOG_TAIL_BYTES 만 읽는다(10MB 로테이션 파일 전체를 읽지 않음)."""
    path = log_path or os.path.join(os.getcwd(), 'app.log')
    try:
        size = os.path.getsize(path)
        with open(path, 'rb') as f:
            f.seek(max(0, size - LOG_TAIL_BYTES))
            text = f.read().decode('utf-8', errors='replace')
    except OSError as e:
        return {'status': 'missing', 'note': f'로그 파일을 읽을 수 없음: {e}'}
    lines = text.splitlines()
    if size > LOG_TAIL_BYTES:
        lines = lines[1:]  # 중간에서 잘린 첫 줄은 버린다
    summary = summarize_log_lines(lines, now)
    summary['size_bytes'] = size
    summary['tail_only'] = size > LOG_TAIL_BYTES
    return summary


def overall_status(freshness: list, jobs: dict) -> str:
    """ok / warn — 수집이 밀렸거나 마지막 실행이 실패한 스케줄이 있으면 warn.
    로그 오류 건수는 판정에 넣지 않는다(종목 1개 지표 계산 실패처럼 흔한 오류가 섞여 있어 사람이/에이전트가 읽고 판단)."""
    if any(f['status'] != 'ok' for f in freshness):
        return 'warn'
    if jobs.get('failing') or not jobs.get('total_runs'):
        return 'warn'
    return 'ok'


def build_health(now: datetime = None) -> dict:
    now = now or datetime.now()
    freshness = load_freshness(now)
    try:
        jobs = load_job_summary(now)
        jobs = {'window_hours': 24, 'total_runs': jobs['total_runs'], 'job_count': jobs['job_count'],
                'failing': jobs['failing'], 'recovered': jobs['recovered'], 'text': build_job_summary_text(jobs)}
    except sqlite3.Error as e:
        jobs = {'total_runs': 0, 'job_count': 0, 'failing': [], 'recovered': [], 'text': f'작업 이력을 읽을 수 없음: {e}'}
    return {
        'checked_at': now.strftime(TS_FORMAT),
        'overall': overall_status(freshness, jobs),
        'freshness': freshness,
        'jobs': jobs,
        'log': load_log_summary(now),
    }
