"""코인 당일 순위 이력(app/core/upbit_ranking.py) 테스트 — 네트워크 없이 가짜 시세로 검증한다.

실행: python tests/test_coin_ranking_history.py
"""
import os
import sys
import tempfile
from datetime import datetime, timedelta

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app.utils import db_manager
from app.core import upbit_ranking as ur

FAILED = []


def check(name, cond, detail=''):
    print(f"{'PASS' if cond else 'FAIL'} {name}{(' — ' + str(detail)) if detail else ''}")
    if not cond:
        FAILED.append(name)


def fake_tickers(rates: dict, values: dict = None):
    """{심볼: 등락률%} → 업비트 /v1/ticker/all 응답 모양. 거래대금은 따로 안 주면 등락률 순서와 반대로."""
    values = values or {}
    def fn():
        return [
            {'market': f'KRW-{sym}', 'trade_price': 1000, 'signed_change_rate': rate / 100,
             'acc_trade_price': values.get(sym, 1e9 - rate * 1e6), 'acc_trade_price_24h': 0}
            for sym, rate in rates.items()
        ] + [{'market': 'BTC-XRP', 'trade_price': 1, 'signed_change_rate': 9.9, 'acc_trade_price': 9e20}]
    return fn


KST = ur.KST
base = datetime(2026, 9, 1, 23, 55, tzinfo=KST)
rates = {f'C{i:02d}': 30 - i for i in range(25)}  # C00이 1위 … C24가 25위

with tempfile.TemporaryDirectory() as tmp:
    db_manager.DB_PATH = os.path.join(tmp, 'test.db')

    # ── 스냅샷 타이밍: 55분 전엔 안 찍고, 같은 시엔 한 번만
    check('55분 전엔 건너뜀', ur.capture_ranking_snapshot(base.replace(minute=30), fake_tickers(rates)) is None)
    saved = ur.capture_ranking_snapshot(base, fake_tickers(rates))
    check('상승률·거래대금 각 20개 저장', saved == 40, saved)
    check('같은 시 두 번째는 건너뜀', ur.capture_ranking_snapshot(base.replace(minute=58), fake_tickers(rates)) is None)
    rows = db_manager.get_coin_ranking_history('gainers', '2026-09-01')
    check('BTC 마켓은 제외', all(r['ticker'].startswith('KRW-') for r in rows))
    check('1위는 C00', rows[0]['ticker'] == 'KRW-C00' and rows[0]['rank'] == 1, rows[0])

    # ── 9/2: 12시엔 C15가 급등해 1위, 23시엔 다시 밀려남 (장중에만 들어온 종목)
    day2 = base + timedelta(days=1)
    r2 = dict(rates, C15=99)
    ur.capture_ranking_snapshot(day2.replace(hour=12), fake_tickers(r2))
    ur.capture_ranking_snapshot(day2, fake_tickers(rates))

    h = ur.get_ranking_history('gainers', top=10, now=day2)
    c15 = next((c for c in h['coins'] if c['ticker'] == 'KRW-C15'), None)
    check('장중에만 든 종목도 목록에 나옴', c15 is not None)
    cell = c15['days']['2026-09-02']
    check('장중 종목은 마감 순위 없음·최고 1위·1시간',
          cell['close_rank'] is None and cell['best_rank'] == 1 and cell['hours_in'] == 1 and cell['hours_total'] == 2, cell)
    check('장중 종목 일수 0/1', c15['days_close_in'] == 0 and c15['days_in'] == 1 and not c15['now_in'], c15)
    c00 = next(c for c in h['coins'] if c['ticker'] == 'KRW-C00')
    check('C00 이틀 연속 마감 1위, NOW', c00['days_close_in'] == 2 and c00['days']['2026-09-01']['close_rank'] == 1
          and c00['now_in'], c00)
    check('C00의 9/2 12시엔 C15에 밀려 2위 → 마감은 1위, 최고 1위',
          c00['days']['2026-09-02']['close_rank'] == 1 and c00['days']['2026-09-02']['best_rank'] == 1)
    check('날짜는 최신이 앞', [d['date'] for d in h['dates']] == ['2026-09-02', '2026-09-01'], h['dates'])
    check('11위 이하(C10~)는 top 10 표에 없음', all(c['ticker'] != 'KRW-C12' for c in h['coins']))
    check('정렬: 등장일 많은 종목이 먼저', h['coins'][0]['days_in'] == 2 and h['coins'][-1]['ticker'] == 'KRW-C15')

    h20 = ur.get_ranking_history('gainers', top=20, now=day2)
    check('top 20으로 보면 C12도 나옴', any(c['ticker'] == 'KRW-C12' for c in h20['coins']))

    tv = ur.get_ranking_history('trade_value', top=10, now=day2)
    check('거래대금 순위는 따로 계산(C24가 1위)', tv['coins'] and
          any(c['ticker'] == 'KRW-C24' and c['best_rank'] == 1 for c in tv['coins']))

    # ── 보관 기간: 15일 지나면 9/1·9/2가 지워짐
    later = base + timedelta(days=16)
    ur.capture_ranking_snapshot(later, fake_tickers(rates))
    left = db_manager.get_coin_ranking_history('gainers', '2000-01-01')
    check('15일 지난 날짜 삭제', {r['date'] for r in left} == {'2026-09-17'}, {r['date'] for r in left})
    keep = base + timedelta(days=16 + 14)  # 9/17로부터 14일 뒤 = 보관 15일째
    ur.capture_ranking_snapshot(keep, fake_tickers(rates))
    left = {r['date'] for r in db_manager.get_coin_ranking_history('gainers', '2000-01-01')}
    check('15일째 날짜는 남김', left == {'2026-09-17', '2026-10-01'}, left)

    try:
        ur.get_ranking_history('volume')
        check('알 수 없는 종류는 ValueError', False)
    except ValueError:
        check('알 수 없는 종류는 ValueError', True)

print()
if FAILED:
    print(f"{len(FAILED)}개 실패: {FAILED}")
    sys.exit(1)
print("모두 통과")
