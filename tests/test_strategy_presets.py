"""전략 묶음(app/core/strategy_presets.py) 테스트 — 임시 DB로 적용/판별/보유 종목 규칙 고정을 검증한다.

실행: python tests/test_strategy_presets.py
"""
import os
import sys
import tempfile

# Add project root to sys.path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app.core import strategy_presets as sp
from app.core import market_regime as mr
from app.core.trade_strategy import evaluate_exits, position_cfg
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
    check(f'{key}: 고정 규칙 키를 전부 가짐', set(sp.PRESET_CFG_ATTRS) <= set(v), v)
good_v = sp.STRATEGY_PRESETS['good']['values']
bad_v = sp.STRATEGY_PRESETS['bad']['values']
check('좋음은 손실을 더 버팀(-8%)', good_v['tight_stop_initial_pct'] == 8.0, good_v)
check('나쁨이 좋음보다 손절·익절 모두 짧음',
      bad_v['tight_stop_initial_pct'] < good_v['tight_stop_initial_pct']
      and bad_v['take_profit_pct'] < sp.STRATEGY_PRESETS['neutral']['values']['take_profit_pct'] < good_v['take_profit_pct'])

# ── position_cfg: 고정 규칙이 있으면 그 값만 덮어쓴다
class Base:
    TRADE_TAKE_PROFIT_PCT = 30.0
    TRADE_STOP_LOSS_PCT = 5.0


over = position_cfg(Base, {'exit_rule': '{"TRADE_TAKE_PROFIT_PCT": 4.0}'})
check('고정 규칙 값이 우선', over.TRADE_TAKE_PROFIT_PCT == 4.0)
check('나머지는 기본 설정에서', over.TRADE_STOP_LOSS_PCT == 5.0 and getattr(over, 'NOPE', 'x') == 'x')
check('규칙 없으면 기본 설정 그대로', position_cfg(Base, {}) is Base)
check('깨진 규칙은 무시', position_cfg(Base, {'exit_rule': '{broken'}) is Base)

with tempfile.TemporaryDirectory() as tmp:
    db_manager.DB_PATH = os.path.join(tmp, 'test.db')
    db_manager.init_db()
    from app.backtest.strategy_config import strategy_config  # DB 설정 → TRADE_* 네임스페이스(실매매와 같은 모양)

    before = db_manager.get_trade_strategy_settings()
    check('기본 설정은 어느 묶음과도 다름(직접 설정)', sp.match_preset(before) is None, before)

    db_manager.set_trade_strategy_settings(max_position_krw=70_000, max_concurrent_positions=3,
                                           rsi_exit_enabled=True, dca_max_count=4)
    result = sp.apply_preset('bad')
    after = db_manager.get_trade_strategy_settings()
    check('적용하면 묶음 값으로 바뀜', after['tight_stop_enabled'] is True and after['tight_stop_initial_pct'] == 2.0
          and after['tight_stop_trail_pct'] == 1.5 and after['take_profit_pct'] == 4.0, after)
    check('금액·종목 수·RSI 매도·물타기 횟수는 그대로',
          after['max_position_krw'] == 70_000 and after['max_concurrent_positions'] == 3
          and after['rsi_exit_enabled'] is True and after['dca_max_count'] == 4, after)
    check('적용 후 판별 = 나쁨', sp.match_preset(after) == 'bad' and sp.match_preset(result['settings']) == 'bad')
    check('보유 종목이 없으면 고정할 것도 없음', result['locked'] == [], result['locked'])

    sp.apply_preset('good')
    check('다른 묶음으로 바꾸면 판별도 바뀜', sp.match_preset(db_manager.get_trade_strategy_settings()) == 'good')

    db_manager.set_trade_strategy_settings(take_profit_pct=25.0)
    check('묶음 값 하나를 손으로 바꾸면 직접 설정', sp.match_preset(db_manager.get_trade_strategy_settings()) is None)

    try:
        sp.apply_preset('nope')
        check('없는 묶음은 거부', False)
    except ValueError:
        check('없는 묶음은 거부', True)

    # ── 보유 종목은 산 시점 규칙 유지: 좋음(-8%)에서 산 종목이 -5%일 때 나쁨(-2%)을 눌러도 안 팔린다
    sp.apply_preset('good')
    db_manager.upsert_paper_position('upbit', 'live', 'KRW-OLD', 10, 100.0)
    locked = sp.apply_preset('bad')['locked']
    check('적용 직전 보유 종목에 규칙 고정', locked == ['KRW-OLD'], locked)
    db_manager.upsert_paper_position('upbit', 'live', 'KRW-NEW', 10, 100.0)  # 나쁨 적용 뒤에 산 종목

    positions = {p['ticker']: p for p in db_manager.get_paper_positions('upbit', 'live')}
    check('upsert가 고정 규칙을 지우지 않음', positions['KRW-OLD']['exit_rule'] and not positions['KRW-NEW']['exit_rule'],
          positions)
    cfg = strategy_config(from_db=True)
    decisions = {d.ticker: d for d in evaluate_exits(list(positions.values()), lambda t: 95.0, cfg)}
    check('이전에 산 종목은 좋음 규칙(-8%)으로 -5%에서 보유', decisions['KRW-OLD'].action == 'HOLD', decisions['KRW-OLD'])
    check('새로 산 종목은 나쁨 규칙(-2%)으로 -5%에서 매도', decisions['KRW-NEW'].action == 'SELL'
          and 'tight_stop_loss' in decisions['KRW-NEW'].reason, decisions['KRW-NEW'])

    again = sp.apply_preset('neutral')['locked']
    check('이미 고정된 종목은 다시 덮어쓰지 않고, 새 종목만 고정', again == ['KRW-NEW'], again)
    positions = {p['ticker']: p for p in db_manager.get_paper_positions('upbit', 'live')}
    import json
    check('처음 고정한 규칙(좋음) 유지',
          json.loads(positions['KRW-OLD']['exit_rule'])['TRADE_TIGHT_STOP_INITIAL_PCT'] == 8.0, positions['KRW-OLD'])
    db_manager.delete_paper_position('upbit', 'live', 'KRW-OLD')
    db_manager.upsert_paper_position('upbit', 'live', 'KRW-OLD', 5, 90.0)
    check('전량 매도 후 다시 사면 규칙 없이 시작',
          not db_manager.get_paper_position('upbit', 'live', 'KRW-OLD')['exit_rule'])

    # ── "보유 중인 종목에도 바로 적용": 고정 규칙을 풀고 새 묶음을 바로 따른다
    db_manager.upsert_paper_position('upbit', 'live', 'KRW-HELD', 10, 100.0)
    sp.apply_preset('neutral')
    db_manager.upsert_paper_position('upbit', 'live', 'KRW-HELD2', 10, 100.0)
    sp.apply_preset('good')  # KRW-HELD2에 적용 직전 규칙(애매, 손절 -3%) 고정
    held = db_manager.get_paper_position('upbit', 'live', 'KRW-HELD2')
    check('기본 적용은 보유 종목에 직전 규칙 고정', json.loads(held['exit_rule'])['TRADE_TIGHT_STOP_INITIAL_PCT'] == 3.0, held)
    res = sp.apply_preset('good', include_held=True)
    check('include_held면 고정 규칙 해제', {'KRW-HELD', 'KRW-HELD2'} <= set(res['released']) and res['locked'] == []
          and not db_manager.get_paper_position('upbit', 'live', 'KRW-HELD')['exit_rule'], res)
    cfg = strategy_config(from_db=True)
    held = db_manager.get_paper_position('upbit', 'live', 'KRW-HELD')
    d = evaluate_exits([held], lambda t: 95.0, cfg)[0]
    check('해제된 종목은 새 묶음(좋음 -8%)으로 -5%에서 보유', d.action == 'HOLD', d)

    # ── 시장 판단 알림에 적용 중인 묶음이 같이 적힌다
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
