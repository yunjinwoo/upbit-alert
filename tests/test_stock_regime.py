"""주식 시장 판단(app/core/stock_regime.py) 테스트 — 네트워크 없이 가짜 값으로 검증한다.

실행: python tests/test_stock_regime.py
"""
import os
import sys
import tempfile
from datetime import datetime, timedelta

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app.core import stock_regime as sr

FAILED = []


def check(name, cond, detail=''):
    print(f"{'PASS' if cond else 'FAIL'} {name}{(' — ' + str(detail)) if detail else ''}")
    if not cond:
        FAILED.append(name)


def inputs(**kw):
    base = {'kospi': 2600.0, 'kospi_ma20': 2600.0, 'kospi_rsi': 50.0, 'kosdaq_rsi': 50.0,
            'up_ratio': 50.0, 'kospi_change': 0.0, 'kosdaq': 850.0, 'kosdaq_change': 0.0}
    base.update(kw)
    return base


# ── 점수
good = sr.score_stock_regime(inputs(kospi=2700, kospi_rsi=60, kosdaq_rsi=58, up_ratio=65))
check('네 항목 모두 좋으면 +4 좋음', good['regime'] == 'good' and good['score'] == 4, good)
bad = sr.score_stock_regime(inputs(kospi=2500, kospi_rsi=34.1, kosdaq_rsi=42.8, up_ratio=46))
check('20일선 아래 + RSI 둘 다 낮으면 -3 나쁨', bad['regime'] == 'bad' and bad['score'] == -3, bad)
mixed = sr.score_stock_regime(inputs(kospi=2700, kospi_rsi=40))
check('+1 -1이면 애매', mixed['regime'] == 'neutral' and mixed['score'] == 0, mixed)
crash = sr.score_stock_regime(inputs(kospi=2700, kospi_rsi=60, kosdaq_rsi=60, up_ratio=70, kospi_change=-3.2))
check('코스피 -3% 이상 급락이면 점수와 무관하게 나쁨', crash['regime'] == 'bad' and crash['crash'], crash)
missing = sr.score_stock_regime({'kospi': None, 'kospi_ma20': None, 'kospi_rsi': None, 'kosdaq_rsi': 50, 'up_ratio': None})
check('값 있는 항목이 모자라면 판정 보류', missing['regime'] is None, missing)

# ── 20일선/RSI 계산 + 오늘 값 반영
closes = [100.0 + i for i in range(40)]
ma20, rsi = sr.calc_ma_rsi(closes)
check('20일선 = 마지막 20개 평균', ma20 == round(sum(closes[-20:]) / 20, 2), ma20)
check('계속 오르면 RSI 100', rsi == 100.0, rsi)
daily = [('20261113', 1.0), ('20261116', 2.0)]
check('마지막 행이 오늘이면 현재값으로 바꿈', sr.merge_today(daily, 9.0, '20261116') == [1.0, 9.0])
check('오늘 행이 없으면 뒤에 붙임', sr.merge_today(daily[:1], 9.0, '20261116') == [1.0, 9.0])

# ── 조회 재료 모으기(가짜 KIS)
now_data = {'0001': {'value': 2650.0, 'change': 0.5, 'up': 500, 'down': 400, 'flat': 100},
            '1001': {'value': 860.0, 'change': -0.2, 'up': 700, 'down': 800, 'flat': 100}}
inp = sr.collect_stock_inputs(now_fn=lambda c: now_data[c],
                              closes_fn=lambda c: [(f'202610{i:02d}', 2500.0 + i) for i in range(1, 31)],
                              today='20261116')
check('상승 비율 = 코스피+코스닥 합산', inp['up_ratio'] == round(1200 / 2600 * 100, 1), inp)
check('20일선/RSI 계산됨', inp['kospi_ma20'] is not None and inp['kospi_rsi'] is not None and inp['kosdaq_rsi'] is not None, inp)

def boom(c):
    raise RuntimeError('down')

inp2 = sr.collect_stock_inputs(now_fn=boom, closes_fn=lambda c: [], today='20261116')
check('조회 전부 실패해도 예외 없이 빈 재료', inp2['kospi'] is None and inp2['up_ratio'] is None, inp2)

# ── 장 시간
check('평일 10시는 장중', sr.is_market_hours(datetime(2026, 11, 16, 10, 0)))
check('평일 16시는 장 밖', not sr.is_market_hours(datetime(2026, 11, 16, 16, 0)))
check('토요일은 장 밖', not sr.is_market_hours(datetime(2026, 11, 14, 10, 0)))

# ── 슬랙 문구
bad['inputs'] = inputs(kospi=2500, kospi_change=-1.2, kosdaq=820.5, kosdaq_change=-2.0)
msg = sr.format_stock_change_message({'from_regime': 'neutral', 'to_regime': 'bad', 'score': -3, 'detail': bad})
check('변경 문구', msg.startswith('📊 주식 시장 판단 변경: 🟡 애매 → 🔴 *나쁨* (점수 -3)')
      and '• 코스피 일봉 RSI: 34.1 (-1)' in msg and '코스피 2,500.00 (-1.20%)' in msg, msg)

# ── 1회 판정 + 저장 + 알림
from app.utils import db_manager

with tempfile.TemporaryDirectory() as tmp:
    db_manager.DB_PATH = os.path.join(tmp, 'test.db')
    sent = []
    t0 = datetime(2026, 11, 16, 10, 0)
    check('장 밖이면 아무것도 안 함', sr.run_stock_regime_check(now=datetime(2026, 11, 16, 20, 0), notify=sent.append) is None
          and db_manager.get_stock_regime_state() is None)
    sr.run_stock_regime_check(inputs(kospi=2700, kospi_rsi=60, kosdaq_rsi=58, up_ratio=65), now=t0, notify=sent.append)
    check('첫 판정 → 시작 알림', len(sent) == 1 and '주식 시장 판단 시작' in sent[0], sent)
    sr.run_stock_regime_check(inputs(kospi=2500, kospi_rsi=40, kosdaq_rsi=40), now=t0 + timedelta(minutes=30), notify=sent.append)
    check('한 번만 나쁨이면 대기', db_manager.get_stock_regime_state()['pending'] == 'bad' and len(sent) == 1)
    sr.run_stock_regime_check(inputs(kospi=2500, kospi_rsi=40, kosdaq_rsi=40), now=t0 + timedelta(minutes=60), notify=sent.append)
    check('두 번 연속 → 확정 + 변경 알림', db_manager.get_stock_regime_state()['confirmed'] == 'bad' and len(sent) == 2
          and '좋음 → 🔴 *나쁨*' in sent[1], sent)
    snap = sr.get_stock_regime_snapshot()
    check('스냅샷에 상태와 이력 2건', snap['state']['confirmed'] == 'bad' and len(snap['history']) == 2, snap['history'])
    check('코인 판단 테이블은 건드리지 않음', db_manager.get_market_regime_state() is None)

print()
if FAILED:
    print(f"실패 {len(FAILED)}건: {FAILED}")
    sys.exit(1)
print('모두 통과')
