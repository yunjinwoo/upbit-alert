from app.core.stock_nightly_digest import build_nightly_stock_digest


def test_empty_returns_blank():
    assert build_nightly_stock_digest([], []) == ''


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
    text = build_nightly_stock_digest(signal, screening)
    assert '기준일 2026-10-01' in text
    assert text.index('관심 1순위') < text.index('Signal Score A/B 상위')
    assert '삼성전자(005930) A등급 85점 · 일봉 돌파' in text
    assert '카카오' not in text
    assert 'NAVER(035420) -0.40% · 200선 근접+구름 위' in text
    assert '매수 권유가 아닙니다' in text
