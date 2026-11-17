"""상태 점검(app/core/health.py) 판정 테스트 — 시각을 직접 넣어 검증한다(외부 API·운영 DB 사용 없음).

    python tests/test_health.py     (pytest 로도 실행 가능)
"""
import os
import sqlite3
import sys
import tempfile
from datetime import datetime

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app.core import health
from app.core.health import (
    expected_weekday_date, judge_freshness, overall_status, summarize_log_lines,
)
from app.utils import db_manager

ALWAYS = {'key': 'coin_screening', 'label': '코인', 'table': 't', 'column': 'updated_at',
          'schedule': 'always', 'max_age_min': 90, 'process': 'coin-analysis-bot'}
DAILY = {'key': 'market_cap', 'label': '시총', 'table': 't', 'column': 'date',
         'schedule': 'kr_weekday', 'due': (16, 10), 'process': 'stock-bot'}

# 2026-11-16 = 월요일, 2026-11-13 = 금요일
MON_MORNING = datetime(2026, 11, 16, 9, 0)
MON_EVENING = datetime(2026, 11, 16, 17, 0)
SUNDAY = datetime(2026, 11, 15, 12, 0)


def test_always_within_limit_is_ok():
    r = judge_freshness(ALWAYS, '2026-11-16 08:00:00', MON_MORNING)
    assert r['status'] == 'ok' and r['age_minutes'] == 60


def test_always_over_limit_is_stale_and_names_process():
    r = judge_freshness(ALWAYS, '2026-11-16 07:00:00', MON_MORNING)
    assert r['status'] == 'stale' and 'coin-analysis-bot' in r['note']


def test_empty_and_unreadable():
    assert judge_freshness(ALWAYS, None, MON_MORNING)['status'] == 'empty'
    assert judge_freshness(ALWAYS, 'abc', MON_MORNING)['status'] == 'unreadable'


def test_expected_date_before_due_is_previous_weekday():
    assert expected_weekday_date(MON_MORNING, (16, 10)).isoformat() == '2026-11-13'


def test_expected_date_after_due_is_today():
    assert expected_weekday_date(MON_EVENING, (16, 10)).isoformat() == '2026-11-16'


def test_expected_date_on_weekend_is_friday():
    assert expected_weekday_date(SUNDAY, (16, 10)).isoformat() == '2026-11-13'


def test_daily_friday_data_ok_until_monday_due_then_stale():
    assert judge_freshness(DAILY, '2026-11-13', MON_MORNING)['status'] == 'ok'
    assert judge_freshness(DAILY, '2026-11-13', SUNDAY)['status'] == 'ok'
    late = judge_freshness(DAILY, '2026-11-13', MON_EVENING)
    assert late['status'] == 'stale' and late['expected'] == '2026-11-16' and '공휴일' in late['note']


def test_daily_accepts_compact_date_format():
    assert judge_freshness(DAILY, '20261116', MON_EVENING)['status'] == 'ok'


def test_log_counts_only_recent_window_and_keeps_error_lines():
    lines = [
        '2026-11-14 08:00:00,000 - ERROR - 오래된 오류',
        '2026-11-16 08:00:00,000 - INFO - 정상',
        '2026-11-16 08:10:00,000 - WARNING - 경고',
        '2026-11-16 08:20:00,000 - ERROR - 최근 오류',
        'Traceback (most recent call last):',
    ]
    s = summarize_log_lines(lines, MON_MORNING)
    assert s['error_count'] == 1 and s['warning_count'] == 1
    assert s['recent_errors'] == ['2026-11-16 08:20:00,000 - ERROR - 최근 오류']
    assert s['last_line_at'] == '2026-11-16 08:20:00'


def test_overall_warns_on_stale_failing_or_no_runs():
    ok_f, stale_f = [{'status': 'ok'}], [{'status': 'ok'}, {'status': 'stale'}]
    ok_jobs = {'total_runs': 3, 'failing': []}
    assert overall_status(ok_f, ok_jobs) == 'ok'
    assert overall_status(stale_f, ok_jobs) == 'warn'
    assert overall_status(ok_f, {'total_runs': 3, 'failing': [{'job_name': 'x'}]}) == 'warn'
    assert overall_status(ok_f, {'total_runs': 0, 'failing': []}) == 'warn'


def test_load_freshness_reads_db_and_reports_missing_table():
    tmp = tempfile.mkdtemp()
    original = db_manager.DB_PATH
    db_manager.DB_PATH = os.path.join(tmp, 'test.db')
    try:
        conn = sqlite3.connect(db_manager.DB_PATH)
        conn.execute('CREATE TABLE coin_screening_daily (ticker TEXT, updated_at TEXT)')
        conn.execute("INSERT INTO coin_screening_daily VALUES ('KRW-BTC', '2026-11-16 08:30:00')")
        conn.commit()
        conn.close()
        by_key = {r['key']: r for r in health.load_freshness(MON_MORNING)}
        assert by_key['coin_screening']['status'] == 'ok'
        assert by_key['market_cap']['status'] == 'missing'
        assert len(by_key) == len(health.FRESHNESS_RULES)
    finally:
        db_manager.DB_PATH = original


def test_readonly_connection_rejects_writes():
    tmp = tempfile.mkdtemp()
    original = db_manager.DB_PATH
    db_manager.DB_PATH = os.path.join(tmp, 'test.db')
    try:
        sqlite3.connect(db_manager.DB_PATH).close()
        conn = health._connect_readonly()
        try:
            conn.execute('CREATE TABLE x (a TEXT)')
            raise AssertionError('읽기 전용 연결인데 쓰기가 됐다')
        except sqlite3.OperationalError:
            pass
        finally:
            conn.close()
    finally:
        db_manager.DB_PATH = original


if __name__ == '__main__':
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    failed = []
    for name, fn in sorted(globals().items()):
        if name.startswith('test_') and callable(fn):
            try:
                fn()
                print(f'  통과  {name}')
            except Exception as e:  # noqa: BLE001 — 실패 내용을 모아 한 번에 보여준다
                failed.append(name)
                print(f'  실패  {name}: {e!r}')
    print()
    if failed:
        print(f'실패 {len(failed)}건: {failed}')
        sys.exit(1)
    print('모두 통과')
