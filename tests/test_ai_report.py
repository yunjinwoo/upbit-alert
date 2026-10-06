from datetime import datetime

from app.core.ai_report import build_ai_report, build_stock_rows


def _data():
    return {
        'hts': [{'date': '2026-10-01', 'hour': 10, 'rank': 3, 'code': '005930', 'name': '삼성전자'},
                {'date': '2026-10-02', 'hour': 11, 'rank': 1, 'code': '005930', 'name': '삼성전자'}],
        'gainers': [{'date': '2026-10-02', 'hour': 12, 'rank': 2, 'code': '005930', 'name': '삼성전자', 'change_rate': '4.5'},
                    {'date': '2026-10-02', 'hour': 12, 'rank': 9, 'code': '035720', 'name': '카카오', 'change_rate': '2.0'}],
        'interest': [],
        'signal': [{'date': '2026-10-02', 'code': '005930', 'name': '삼성전자', 'grade': 'A', 'total_score': 82},
                   {'date': '2026-10-02', 'code': '999999', 'name': '무관', 'grade': 'C', 'total_score': 40}],
        'stock_investor': [{'date': '2026-10-01', 'code': '005930', 'frgn_ntby_tr_pbmn': '1,000', 'orgn_ntby_tr_pbmn': '-200',
                            'prsn_ntby_tr_pbmn': '-800'},
                           {'date': '2026-10-01', 'code': '000000', 'frgn_ntby_tr_pbmn': '5'}],
        'screening': [{'ticker': '005930', 'breakout_1d': 1}],
        'sector_stocks': [{'code': '005930', 'sector_name': '전기전자'}],
        'memo': [{'code': '005930', 'memo': '옛 메모'}, {'code': '005930', 'memo': '최신 메모'}],
        'investor_trend': [], 'sector_index': [], 'regime_history': [],
    }


def test_stock_rows_merge_lists_and_rank_multi_list_first():
    rows = build_stock_rows(_data())
    assert [r['code'] for r in rows] == ['005930', '035720']  # C등급만 있는 종목·수급만 있는 종목은 새로 만들지 않음
    top = rows[0]
    assert top['list_count'] == 3 and top['hts'] == 2 and top['hts_best'] == 1
    assert top['gainers_max_rate'] == 4.5 and top['frgn'] == 1000 and top['orgn'] == -200
    assert top['screening'] == '일봉 돌파' and top['sector'] == '전기전자' and top['memo'] == '최신 메모'


def test_report_markdown_has_prompt_table_and_disclaimer():
    text = build_ai_report(_data(), 5, job_text='✅ 모두 정상', now=datetime(2026, 10, 6, 20, 0))
    assert text.startswith('# 국내주식 수집 데이터 리포트 (최근 5거래일)')
    assert 'AI에게 부탁하는 것' in text
    assert '| 삼성전자 | 005930 | 전기전자 | 3 | 2회 (1위) | 1회 (+4.50%) | - | 10-02 A82 | 1,000 | -200 | -800 | 일봉 돌파 | 최신 메모 |' in text
    assert '✅ 모두 정상' in text and '매수 권유가 아닙니다' in text
    assert '## 자동매매 성과' not in text  # 체결 기록이 없으면 섹션 자체를 생략
