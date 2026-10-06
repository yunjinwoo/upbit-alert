from app.core.stock_digest import build_stock_digest_text, collect_stock_digest


def test_empty_returns_blank():
    assert build_stock_digest_text(collect_stock_digest([], [])) == ''


def test_overlap_listed_first_and_c_grade_excluded():
    signal = [
        {'date': '2026-10-01', 'code': '005930', 'name': '삼성전자', 'grade': 'A', 'total_score': 85,
         'momentum_score': 20, 'supply_demand_score': 25, 'risk_penalty_score': 0},
        {'date': '2026-10-01', 'code': '000660', 'name': 'SK하이닉스', 'grade': 'B', 'total_score': 70,
         'momentum_score': 15, 'supply_demand_score': 20, 'risk_penalty_score': -5},
        {'date': '2026-10-01', 'code': '035720', 'name': '카카오', 'grade': 'C', 'total_score': 55,
         'momentum_score': 10, 'supply_demand_score': 10, 'risk_penalty_score': 0},
    ]
    screening = [
        {'ticker': '005930', 'name': '삼성전자', 'change_rate': 2.5, 'breakout_1d': 1},
        {'ticker': '035420', 'name': 'NAVER', 'change_rate': -0.4, 'near_ma200': 1, 'above_cloud': 1},
    ]
    digest = collect_stock_digest(signal, screening)
    assert [r['code'] for r in digest['both']] == ['005930']
    text = build_stock_digest_text(digest)
    assert '기준일 2026-10-01' in text
    assert text.index('관심 1순위') < text.index('Signal Score A/B 상위')
    assert '삼성전자(005930) A등급 85점 · 일봉 돌파' in text
    assert '카카오' not in text
    assert 'NAVER(035420) -0.40% · 200선 근접+구름 위' in text
    assert '매수 권유가 아닙니다' in text


from app.core.stock_digest import build_job_summary_text, summarize_job_runs


def _run(job, ok, t, desc=None, err=None, trigger='auto'):
    return {'job_name': job, 'description': desc or job, 'start_time': t, 'success': 1 if ok else 0,
            'error_message': err, 'trigger_type': trigger}


def test_job_summary_all_ok_is_one_line():
    rows = [_run('hts_top_view', True, '2026-10-05 09:10:00'), _run('remote_sync', True, '2026-10-05 20:00:00')]
    text = build_job_summary_text(summarize_job_runs(rows))
    assert text == '✅ 최근 24시간 스케줄 2개 · 실행 2회 모두 정상'


def test_job_summary_failing_and_recovered():
    rows = [
        _run('top_gainers', False, '2026-10-05 15:10:00', desc='상승률 순위 (15시)', err='timeout'),
        _run('top_gainers', True, '2026-10-05 15:14:00', desc='상승률 순위 (15시)'),
        _run('remote_sync', False, '2026-10-05 20:00:00', desc='원격 동기화', err='HTTP 502\nBad Gateway'),
        _run('hts_top_view', True, '2026-10-05 10:10:00'),
    ]
    summary = summarize_job_runs(rows)
    assert [j['job_name'] for j in summary['failing']] == ['remote_sync']
    assert [j['job_name'] for j in summary['recovered']] == ['top_gainers']
    text = build_job_summary_text(summary)
    assert '⚠️ 원격 동기화 — 실패 1회, 마지막 실행(2026-10-05 20:00:00)도 실패: HTTP 502 Bad Gateway' in text
    assert '↻ 실패 후 재시도로 정상: 상승률 순위 (15시) 1회' in text
    assert 'hts_top_view' not in text


def test_job_summary_ignores_manual_runs():
    rows = [_run('remote_sync', False, '2026-10-05 21:00:00', err='x', trigger='manual'),
            _run('upbit_live', True, '2026-10-05 21:05:00', trigger='auto_live')]
    summary = summarize_job_runs(rows)
    assert summary['total_runs'] == 1 and not summary['failing']


def test_job_summary_no_runs_warns():
    assert '실행 기록이 없습니다' in build_job_summary_text(summarize_job_runs([]))
