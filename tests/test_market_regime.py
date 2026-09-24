"""시장 판단(app/core/market_regime.py) 테스트 — 네트워크 없이 가짜 값/캔들로 검증한다.

실행: python tests/test_market_regime.py
"""
import os
import sys
import tempfile
from datetime import datetime, timedelta

# Add project root to sys.path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import pandas as pd

from app.core import market_indicators as mi
from app.core import market_regime as mr

FAILED = []


def check(name, cond, detail=''):
    print(f"{'PASS' if cond else 'FAIL'} {name}{(' — ' + str(detail)) if detail else ''}")
    if not cond:
        FAILED.append(name)


def inputs(**kw):
    base = {'price': 100.0, 'ma20_day': 100.0, 'rsi_day': 50.0, 'rsi_4h': 50.0,
            'up_ratio': 50.0, 'change_rate_24h': 0.0}
    base.update(kw)
    return base


# ── score_regime: 점수와 국면
good = mr.score_regime(inputs(price=110, rsi_day=60, rsi_4h=58, up_ratio=65))
check('네 항목 모두 좋으면 +4 좋음', good['regime'] == 'good' and good['score'] == 4, good)

bad = mr.score_regime(inputs(price=90, rsi_day=40, rsi_4h=42, up_ratio=30))
check('네 항목 모두 나쁘면 -4 나쁨', bad['regime'] == 'bad' and bad['score'] == -4, bad)

edge = mr.score_regime(inputs(price=105, rsi_day=55))
check('+2 경계는 좋음(55는 +1에 포함)', edge['regime'] == 'good' and edge['score'] == 2, edge)

mixed = mr.score_regime(inputs(price=105, rsi_day=60, rsi_4h=40, up_ratio=50))
check('+1이면 애매', mixed['regime'] == 'neutral' and mixed['score'] == 1, mixed)

low_edge = mr.score_regime(inputs(price=95, up_ratio=40))
check('-2 경계는 나쁨(40%는 -1에 포함)', low_edge['regime'] == 'bad' and low_edge['score'] == -2, low_edge)

crash = mr.score_regime(inputs(price=110, rsi_day=60, rsi_4h=60, up_ratio=70, change_rate_24h=-5.5))
check('BTC 전일 대비 -5% 이하면 점수와 무관하게 나쁨', crash['regime'] == 'bad' and crash['crash'], crash)

missing = mr.score_regime(inputs(rsi_day=None, up_ratio=None, price=110))
check('항목이 2개만 있으면 판정 보류(None)', missing['regime'] is None and missing['available'] == 2, missing)
three = mr.score_regime(inputs(up_ratio=None, price=110, rsi_day=60))
check('항목이 3개면 판정(없는 항목은 0점)', three['regime'] == 'good' and three['available'] == 3, three)
check('없는 항목은 데이터 없음으로 표시',
      any(i['key'] == 'breadth' and i['text'] == '데이터 없음' for i in three['items']), three['items'])

# ── advance_state: 연속 확인
t0 = datetime(2026, 9, 24, 12, 0, 0)
st, ch = mr.advance_state(None, good, t0, 2)
check('처음 판정은 바로 확정 + 시작 알림', st['confirmed'] == 'good' and ch and ch['from_regime'] is None, (st, ch))

st, ch = mr.advance_state(st, mixed, t0 + timedelta(minutes=30), 2)
check('다른 판정 1회는 대기(알림 없음)', st['confirmed'] == 'good' and st['pending'] == 'neutral'
      and st['pending_count'] == 1 and ch is None, st)

st, ch = mr.advance_state(st, good, t0 + timedelta(minutes=60), 2)
check('다시 원래 판정이 나오면 대기 취소', st['pending'] is None and st['pending_count'] == 0 and ch is None, st)

st, _ = mr.advance_state(st, mixed, t0 + timedelta(minutes=90), 2)
st, ch = mr.advance_state(st, bad, t0 + timedelta(minutes=120), 2)
check('대기 중 다른 방향 판정이 오면 카운트 새로 시작', st['pending'] == 'bad' and st['pending_count'] == 1 and ch is None, st)
st, ch = mr.advance_state(st, bad, t0 + timedelta(minutes=150), 2)
check('같은 판정 2회 연속이면 확정 + 알림', st['confirmed'] == 'bad' and ch and ch['from_regime'] == 'good'
      and ch['to_regime'] == 'bad' and st['pending'] is None, (st, ch))
check('확정 시각 기록', st['confirmed_at'] == '2026-09-24 14:30:00', st['confirmed_at'])

st, ch = mr.advance_state(st, good, t0 + timedelta(minutes=180), 2)
st, ch = mr.advance_state(st, missing, t0 + timedelta(minutes=210), 2)
check('데이터 부족 판정은 대기 카운트를 건드리지 않음', st['pending'] == 'good' and st['pending_count'] == 1 and ch is None, st)
check('데이터 부족이어도 판정 시각은 갱신', st['checked_at'] == '2026-09-24 15:30:00', st['checked_at'])

st2, ch = mr.advance_state({'confirmed': 'good', 'pending': None, 'pending_count': 0}, crash, t0, 2)
check('급락은 연속 확인 없이 바로 나쁨 확정', st2['confirmed'] == 'bad' and ch and ch['to_regime'] == 'bad', st2)

# ── 슬랙 문구
msg = mr.format_change_message({'from_regime': 'good', 'to_regime': 'bad', 'score': -4, 'detail': bad})
check('변경 문구에 전후 국면', '🟢 좋음 → 🔴 *나쁨*' in msg, msg)
check('변경 문구에 점수와 항목 근거', '점수 -4' in msg and 'BTC 일봉 RSI: 40.0 (-1)' in msg, msg)
check('변경 문구에 참고 전략 + 자동 적용 안 함', '빠른 손절 · 빠른 익절 (자동 적용 안 함)' in msg, msg)
start_msg = mr.format_change_message({'from_regime': None, 'to_regime': 'neutral', 'score': 1, 'detail': mixed})
check('첫 판정은 시작 문구', start_msg.startswith('📊 시장 판단 시작: 현재 🟡 *애매*'), start_msg)
crash_msg = mr.format_change_message({'from_regime': 'good', 'to_regime': 'bad', 'score': 4, 'detail': crash})
check('급락 문구', '-5.50% 급락' in crash_msg, crash_msg)

# ── 20일선: 시장 지표 계산에 추가된 값
closes = [100.0] * 180 + [100.0 + i for i in range(20)]
df = pd.DataFrame({'open': closes, 'high': closes, 'low': closes, 'close': closes, 'volume': [1.0] * 200})
btc = mi.calc_btc_indicators(lambda t, i, c: df)
check('일봉 20일선 = 오늘 봉 포함 최근 20개 종가 평균', btc['ma20_day'] == round(sum(closes[-20:]) / 20, 2), btc['ma20_day'])

# collect_inputs가 카드와 같은 BTC 값을 판정 재료로 옮기는지
inp = mr.collect_inputs(get_candles_fn=lambda t, i, c: df, up_ratio_fn=lambda: 55.0)
check('collect_inputs가 BTC 값과 상승 비율을 채움',
      inp['price'] == 119.0 and inp['ma20_day'] == btc['ma20_day'] and inp['rsi_day'] is not None
      and inp['rsi_4h'] is not None and inp['up_ratio'] == 55.0, inp)

# ── run_market_regime_check: DB 저장 + 확정 변경 시에만 알림
from app.utils import db_manager

with tempfile.TemporaryDirectory() as tmp:
    db_manager.DB_PATH = os.path.join(tmp, 'test.db')
    sent = []
    check('판정 전에는 상태 없음', db_manager.get_market_regime_state() is None)

    mr.run_market_regime_check(inputs(price=110, rsi_day=60, rsi_4h=58, up_ratio=65), now=t0, notify=sent.append)
    check('첫 판정 → 시작 알림 1건', len(sent) == 1 and '시장 판단 시작' in sent[0], sent)

    mr.run_market_regime_check(inputs(price=110, rsi_day=60, rsi_4h=58, up_ratio=65),
                               now=t0 + timedelta(minutes=30), notify=sent.append)
    check('같은 판정 반복은 알림 없음', len(sent) == 1, sent)

    mr.run_market_regime_check(inputs(price=90, rsi_day=40), now=t0 + timedelta(minutes=60), notify=sent.append)
    saved = db_manager.get_market_regime_state()
    check('대기 상태가 DB에 저장됨', saved['pending'] == 'bad' and saved['pending_count'] == 1 and len(sent) == 1, saved)

    mr.run_market_regime_check(inputs(price=90, rsi_day=40), now=t0 + timedelta(minutes=90), notify=sent.append)
    saved = db_manager.get_market_regime_state()
    check('두 번째 연속 판정 → 확정 + 변경 알림', saved['confirmed'] == 'bad' and len(sent) == 2
          and '좋음 → 🔴 *나쁨*' in sent[1], (saved, sent))
    check('마지막 판정 결과(JSON)가 복원됨', saved['last_result']['score'] == -2
          and saved['last_result']['inputs']['price'] == 90, saved['last_result'])

    hist = db_manager.get_market_regime_history()
    check('변경 이력 2건(시작 + 변경), 최신순', [h['to_regime'] for h in hist] == ['bad', 'good']
          and hist[0]['from_regime'] == 'good', hist)

    snap = mr.get_market_regime_snapshot()
    check('대시보드 스냅샷에 상태/이력/라벨', snap['state']['confirmed'] == 'bad' and len(snap['history']) == 2
          and snap['regimes']['bad']['label'] == '나쁨' and snap['confirm_count'] == 2, snap)

    def boom(_):
        raise RuntimeError('slack down')

    mr.run_market_regime_check(inputs(price=110, rsi_day=60, rsi_4h=58, up_ratio=65, change_rate_24h=0),
                               now=t0 + timedelta(minutes=120), notify=boom)
    check('슬랙 실패해도 상태 저장은 유지', db_manager.get_market_regime_state()['pending'] == 'good')

print()
if FAILED:
    print(f"실패 {len(FAILED)}건: {FAILED}")
    sys.exit(1)
print('모두 통과')
