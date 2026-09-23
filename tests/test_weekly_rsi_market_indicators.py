"""주봉 RSI 정밀 매수조건(app/core/entry_conditions.py의 weekly_rsi_above)과 자동매매 화면
"시장 지표" 카드(app/core/market_indicators.py) 테스트 — 네트워크 없이 가짜 캔들/응답으로 검증한다.

실행: python tests/test_weekly_rsi_market_indicators.py
"""
import os
import sys
import tempfile

# Add project root to sys.path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import pandas as pd

from app.core.exit_conditions import compute_rsi
from app.core.entry_conditions import check_weekly_rsi_above, evaluate_conditions
from app.core import market_indicators as mi

FAILED = []


def check(name, cond, detail=''):
    print(f"{'PASS' if cond else 'FAIL'} {name}{(' — ' + str(detail)) if detail else ''}")
    if not cond:
        FAILED.append(name)


def candles(closes):
    return pd.DataFrame({
        'open': closes, 'high': closes, 'low': closes, 'close': closes,
        'volume': [1.0] * len(closes),
    })


# 오르내리며 완만히 오르는 주봉(RSI 60~70대) — 마지막 행이 진행 중인 이번 주 봉
wavy_up = [100 + i * 2 + (5 if i % 2 else -5) for i in range(60)]

# ── compute_rsi include_current: 진행 중 봉을 포함하면 마지막 행이 반영된다
closes = wavy_up[:-1] + [wavy_up[-2] * 0.7]  # 이번 주 봉이 30% 급락 중
closed_rsi = compute_rsi(candles(closes), 14)
live_rsi = compute_rsi(candles(closes), 14, include_current=True)
check('확정 봉 RSI는 진행 중 봉의 급락을 반영하지 않음', closed_rsi > 50, closed_rsi)
check('include_current=True면 진행 중 봉의 급락이 반영됨', live_rsi < closed_rsi - 10, (closed_rsi, live_rsi))
check('include_current 기본값은 기존 동작(확정 봉) 그대로',
      compute_rsi(candles(closes), 14) == compute_rsi(candles(closes), 14, include_current=False))

# ── check_weekly_rsi_above
short = check_weekly_rsi_above(candles(wavy_up[:20]), {'rsi_period': 14, 'threshold': 65})
check('주봉이 29개 미만이면 데이터 부족으로 미충족', not short['passed'] and '부족' in short['message'], short)

rising = check_weekly_rsi_above(candles([100 * 1.03 ** i for i in range(60)]), {'rsi_period': 14, 'threshold': 65})
check('꾸준히 오르는 주봉은 65 이상 통과', rising['passed'], rising)
check('메시지에 지난주 확정값이 같이 나옴', '지난주 확정' in rising['message'], rising['message'])

falling = check_weekly_rsi_above(candles([100 * 0.97 ** i for i in range(60)]), {'rsi_period': 14, 'threshold': 65})
check('꾸준히 내리는 주봉은 미충족', not falling['passed'], falling)

wavy_rsi = compute_rsi(candles(wavy_up), 14, include_current=True)
lo = check_weekly_rsi_above(candles(wavy_up), {'rsi_period': 14, 'threshold': wavy_rsi - 1})
hi = check_weekly_rsi_above(candles(wavy_up), {'rsi_period': 14, 'threshold': wavy_rsi + 1})
check('기준값 파라미터가 판정에 반영됨', lo['passed'] and not hi['passed'], (wavy_rsi, lo, hi))
check('파라미터 없으면 기본 기준 65', '기준 65' in check_weekly_rsi_above(candles(wavy_up), {})['message'])

# ── evaluate_conditions: 주봉 조건은 'week' 캔들 200개로 조회한다
calls = []


def fake_get_candles(ticker, interval, count):
    calls.append((ticker, interval, count))
    return candles([100 * 1.03 ** i for i in range(60)])


settings = [{'condition_key': 'weekly_rsi_above', 'enabled': 1, 'logic_group': 'AND',
             'params': {'rsi_period': 14, 'threshold': 65}}]
res = evaluate_conditions('KRW-BTC', settings, fake_get_candles)
check('evaluate_conditions가 주봉 조건을 판정', res['passed'] and 'weekly_rsi_above' in res['detail'], res)
check('주봉 캔들은 interval=week, count=200으로 조회', calls == [('KRW-BTC', 'week', 200)], calls)

# ── 시장 지표: BTC RSI(4시간봉/일봉/주봉) + 현재가/전일 대비
fake_by_interval = {
    'minute240': candles([100 * 0.99 ** i for i in range(200)]),
    'day': candles([100 + i for i in range(199)] + [300 * 1.05]),
    'week': candles([100 * 1.03 ** i for i in range(200)]),
}
btc_calls = []


def fake_btc_candles(ticker, interval, count):
    btc_calls.append((ticker, interval, count))
    return fake_by_interval[interval]


btc = mi.calc_btc_indicators(fake_btc_candles)
check('KRW-BTC 3개 시간대를 조회', [c[1] for c in btc_calls] == ['minute240', 'day', 'week']
      and all(c[0] == 'KRW-BTC' for c in btc_calls), btc_calls)
check('BTC 현재가는 일봉 마지막 종가', btc['price'] == 315.0, btc['price'])
check('전일 대비 등락률', btc['change_rate_24h'] == round((315 - 298) / 298 * 100, 2), btc['change_rate_24h'])
check('4시간봉 하락 → RSI 낮음', btc['rsi']['minute240']['value'] < 30, btc['rsi']['minute240'])
check('주봉 상승 → RSI 높음', btc['rsi']['week']['value'] > 70, btc['rsi']['week'])
check('마감 봉 값도 같이 반환', btc['rsi']['week']['prev_closed'] is not None)


def failing_candles(ticker, interval, count):
    raise RuntimeError('network down')


try:
    mi.calc_btc_indicators(failing_candles)
    check('캔들을 하나도 못 받으면 예외(빈 값 캐시 방지)', False)
except RuntimeError:
    check('캔들을 하나도 못 받으면 예외(빈 값 캐시 방지)', True)

# ── CoinGecko /global 응답 파싱
payload = {'data': {
    'market_cap_percentage': {'btc': 57.1234, 'eth': 12.3456, 'usdt': 4.1},
    'total_market_cap': {'usd': 3.9e12},
    'market_cap_change_percentage_24h_usd': -1.23456,
}}
dom = mi.parse_coingecko_global(payload)
check('BTC 도미넌스 파싱', dom['btc_dominance'] == 57.12, dom)
check('ETH 도미넌스 파싱', dom['eth_dominance'] == 12.35, dom)
check('전체 시총 24h 변화 파싱', dom['total_market_cap_change_24h'] == -1.23, dom)
check('빈 응답도 예외 없이 None', mi.parse_coingecko_global({})['btc_dominance'] is None)

# ── get_market_indicators: 소스별 캐시와 실패 시 이전 값 유지
counter = {'btc': 0, 'dom': 0}
state = {'dom_fail': False}


def fake_btc_fetch(get_fn):
    counter['btc'] += 1
    return {'price': 1.0, 'change_rate_24h': 0.0, 'rsi': {}}


def fake_dom_fetch():
    counter['dom'] += 1
    if state['dom_fail']:
        raise RuntimeError('coingecko 429')
    return {'btc_dominance': 57.0}


orig_calc, orig_dom = mi.calc_btc_indicators, mi._fetch_dominance
mi.calc_btc_indicators, mi._fetch_dominance = fake_btc_fetch, fake_dom_fetch
try:
    for slot in mi._cache.values():
        slot['value'], slot['fetched_at'] = None, 0.0
    r1 = mi.get_market_indicators()
    r2 = mi.get_market_indicators()
    check('TTL 안에서는 재조회하지 않음', counter == {'btc': 1, 'dom': 1}, counter)
    check('정상 응답', r1['dominance']['btc_dominance'] == 57.0 and not r1['dominance_stale'], r1)

    mi._cache['dominance']['fetched_at'] = 0.0  # 만료시킴
    state['dom_fail'] = True
    r3 = mi.get_market_indicators()
    check('도미넌스 조회 실패 시 이전 값 + stale 표시', r3['dominance']['btc_dominance'] == 57.0
          and r3['dominance_stale'] and 'coingecko 429' in r3['dominance_error'], r3)
    check('도미넌스 실패해도 BTC 쪽은 정상', r3['btc'] is not None and r3['btc_error'] is None, r3)

    mi._cache['dominance'].update(value=None, fetched_at=0.0)
    r4 = mi.get_market_indicators()
    check('이전 값도 없으면 None + 에러 사유', r4['dominance'] is None and r4['dominance_error'], r4)
finally:
    mi.calc_btc_indicators, mi._fetch_dominance = orig_calc, orig_dom

# ── DB 시딩: 주봉 RSI 조건은 업비트에만(토스 캔들 API엔 주봉이 없음), 기본 꺼짐
from app.utils import db_manager

with tempfile.TemporaryDirectory() as tmp:
    db_manager.DB_PATH = os.path.join(tmp, 'test.db')
    db_manager.init_db()
    db_manager.init_db()  # 두 번 불러도 중복 시딩 없음
    upbit = {c['condition_key']: c for c in db_manager.get_trade_condition_settings('upbit')}
    toss = {c['condition_key'] for c in db_manager.get_trade_condition_settings('toss')}
    w = upbit.get('weekly_rsi_above')
    check('업비트에 주봉 RSI 조건 시딩', w is not None, list(upbit))
    check('주봉 RSI 조건은 기본 꺼짐 + 기준 65', w and w['enabled'] == 0 and w['params'] == {'rsi_period': 14, 'threshold': 65}, w)
    check('토스에는 시딩 안 함', 'weekly_rsi_above' not in toss, toss)
    check('기존 3종 조건은 그대로', {'daily_above_ma', 'm5_ma_support', 'm1_bb_breakout_volume'} <= set(upbit) and
          {'daily_above_ma', 'm5_ma_support', 'm1_bb_breakout_volume'} <= toss)
    check('중복 시딩 없음', len(db_manager.get_trade_condition_settings('upbit')) == 4)

print()
if FAILED:
    print(f"{len(FAILED)}개 실패: {FAILED}")
    sys.exit(1)
print("모두 통과")
