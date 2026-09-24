"""전략 묶음(app/core/strategy_presets.py) 테스트 — 임시 DB로 적용/판별을 검증한다.

실행: python tests/test_strategy_presets.py
"""
import os
import sys
import tempfile

# Add project root to sys.path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app.core import strategy_presets as sp
from app.core import market_regime as mr
from app.utils import db_manager

FAILED = []


def check(name, cond, detail=''):
    print(f"{'PASS' if cond else 'FAIL'} {name}{(' — ' + str(detail)) if detail else ''}")
    if not cond:
        FAILED.append(name)


# ── 묶음 정의: 세 묶음 모두 tight stop 켜고 물타기 계열은 끈다
for key, p in sp.STRATEGY_PRESETS.items():
    v = p['values']
    check(f'{key}: 짧은 손절 모드 켜짐 · 회복형/되돌림 익절 꺼짐',
          v['tight_stop_enabled'] and not v['recovery_dca_enabled'] and not v['trailing_tp_enabled'], v)
    check(f'{key}: 금액/종목 수는 건드리지 않음',
          'max_position_krw' not in v and 'max_concurrent_positions' not in v, v)
check('나쁨이 좋음보다 손절·익절 모두 짧음',
      sp.STRATEGY_PRESETS['bad']['values']['tight_stop_initial_pct'] < sp.STRATEGY_PRESETS['good']['values']['tight_stop_initial_pct']
      and sp.STRATEGY_PRESETS['bad']['values']['take_profit_pct'] < sp.STRATEGY_PRESETS['neutral']['values']['take_profit_pct']
      < sp.STRATEGY_PRESETS['good']['values']['take_profit_pct'])

with tempfile.TemporaryDirectory() as tmp:
    db_manager.DB_PATH = os.path.join(tmp, 'test.db')
    db_manager.init_db()

    before = db_manager.get_trade_strategy_settings()
    check('기본 설정은 어느 묶음과도 다름(직접 설정)', sp.match_preset(before) is None, before)

    db_manager.set_trade_strategy_settings(max_position_krw=70_000, max_concurrent_positions=3,
                                           rsi_exit_enabled=True, dca_max_count=4)
    saved = sp.apply_preset('bad')
    after = db_manager.get_trade_strategy_settings()
    check('적용하면 묶음 값으로 바뀜', after['tight_stop_enabled'] is True and after['tight_stop_initial_pct'] == 2.0
          and after['tight_stop_trail_pct'] == 1.5 and after['take_profit_pct'] == 4.0, after)
    check('금액·종목 수·RSI 매도·물타기 횟수는 그대로',
          after['max_position_krw'] == 70_000 and after['max_concurrent_positions'] == 3
          and after['rsi_exit_enabled'] is True and after['dca_max_count'] == 4, after)
    check('적용 후 판별 = 나쁨', sp.match_preset(after) == 'bad' and sp.match_preset(saved) == 'bad')

    sp.apply_preset('good')
    check('다른 묶음으로 바꾸면 판별도 바뀜', sp.match_preset(db_manager.get_trade_strategy_settings()) == 'good')

    db_manager.set_trade_strategy_settings(take_profit_pct=25.0)
    check('묶음 값 하나를 손으로 바꾸면 직접 설정', sp.match_preset(db_manager.get_trade_strategy_settings()) is None)

    try:
        sp.apply_preset('nope')
        check('없는 묶음은 거부', False)
    except ValueError:
        check('없는 묶음은 거부', True)

    # 시장 판단 알림에 적용 중인 묶음이 같이 적힌다
    sp.apply_preset('good')
    change = {'from_regime': 'good', 'to_regime': 'bad', 'score': -3,
              'detail': mr.score_regime({'price': 90, 'ma20_day': 100, 'rsi_day': 40, 'rsi_4h': 40, 'up_ratio': 50})}
    msg = mr.format_change_message(change, *mr._active_preset())
    check('판단과 묶음이 다르면 바꾸라고 안내', '지금 적용 중인 전략 묶음: 🟢 좋음' in msg
          and '🔴 나쁨 묶음을 적용하세요' in msg, msg)
    sp.apply_preset('bad')
    msg = mr.format_change_message(change, *mr._active_preset())
    check('같으면 같다고만', '판단과 같음' in msg and '적용하세요' not in msg, msg)
    check('묶음 조회를 못 하면 그 줄은 생략', '전략 묶음' not in mr.format_change_message(change), '')

print()
if FAILED:
    print(f"실패 {len(FAILED)}건: {FAILED}")
    sys.exit(1)
print('모두 통과')
