"""RSI 과매수 매도의 최소 수익률 조건(app/core/trade_strategy.py의 rsi_exit_hit) 테스트 —
DB/네트워크 없이 가짜 포지션과 시세로 검증한다.

2026-09-24 KRW-CVC가 45원에 사자마자 RSI 88.7로 44원(-3.7%)에 팔렸다. RSI가 높아도 수익률이
최소 수익률(rsi_exit_min_profit_pct) 아래면 팔지 않아야 한다.

실행: python tests/test_rsi_exit_min_profit.py
"""
import os
import sys
import tempfile
from types import SimpleNamespace

# Add project root to sys.path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app.core.trade_strategy import evaluate_exits

FAILED = []


def check(name, cond, detail=''):
    print(f"{'PASS' if cond else 'FAIL'} {name}{(' — ' + str(detail)) if detail else ''}")
    if not cond:
        FAILED.append(name)


def cfg(**overrides):
    base = dict(
        TRADE_MAX_POSITION_KRW=40_000, TRADE_MAX_CONCURRENT_POSITIONS=5,
        TRADE_STOP_LOSS_PCT=5.0, TRADE_TAKE_PROFIT_PCT=30.0,
        TRADE_STOP_LOSS_CONFIRM_CYCLES=1, TRADE_DCA_TRIGGER_PCT=10.0, TRADE_DCA_MAX_COUNT=0,
        TRADE_RSI_EXIT_ENABLED=True, TRADE_RSI_EXIT_OVERBOUGHT=80.0, TRADE_RSI_EXIT_MIN_PROFIT_PCT=1.0,
        TRADE_TRAILING_TP_ENABLED=False, TRADE_TRAILING_TP_ARM_PCT=3.0, TRADE_TRAILING_TP_FLOOR_PCT=2.0,
        TRADE_TIGHT_STOP_ENABLED=True, TRADE_TIGHT_STOP_INITIAL_PCT=8.0,
        TRADE_TIGHT_STOP_ARM_PCT=5.0, TRADE_TIGHT_STOP_TRAIL_PCT=10.0,
        TRADE_RECOVERY_DCA_ENABLED=False,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def decide(price, rsi, c=None, avg=45.0):
    position = {'ticker': 'KRW-CVC', 'qty': 879.12, 'avg_buy_price': avg, 'peak_price': max(avg, price)}
    return evaluate_exits([position], lambda t: price, c or cfg(), rsi_map={'KRW-CVC': rsi})[0]


# ── 실제 사례: 45원 매수 → 44원(-2.2%)에서 RSI 88.7
d = decide(44.0, 88.7)
check('CVC 사례: 손실 중이면 RSI가 높아도 안 팖', d.action == 'HOLD', d)
check('손절선(-8%) 위라 짧은 손절 규칙으로 보유', d.status and d.status.startswith('tight'), d.status)

d = decide(45.2, 88.7)  # +0.44%
check('본전 위라도 최소 수익률(+1%) 아래면 안 팖', d.action == 'HOLD', d)

d = decide(45.5, 88.7)  # +1.11%
check('최소 수익률 이상이면 RSI 매도', d.action == 'SELL' and d.reason.startswith('rsi_exit'), d)
check('매도 사유에 수익률이 적힘', '평단대비 +1.11%' in d.reason or '평단대비 1.11%' in d.reason, d.reason)

d = decide(45.5, 75.0)
check('RSI가 기준 아래면 수익이어도 RSI 매도 없음', d.action == 'HOLD', d)

d = decide(45.0, 88.7, cfg(TRADE_RSI_EXIT_MIN_PROFIT_PCT=0.0))
check('최소 수익률 0이면 본전에서 RSI 매도', d.action == 'SELL', d)

d = decide(44.0, 88.7, cfg(TRADE_RSI_EXIT_ENABLED=False))
check('RSI 매도를 끄면 판단에 안 끼어듦', d.action == 'HOLD', d)

# 손실이 손절선보다 깊으면 RSI와 상관없이 손절 규칙이 판다(RSI가 손절을 막지는 않음)
d = decide(41.0, 88.7)  # -8.9%
check('손절선 아래면 손절로 매도', d.action == 'SELL' and 'tight_stop_loss' in d.reason, d)

# ── 최소 수익률 값이 없는 cfg(옛 모양)는 기존처럼 수익률 조건 없이 판다
old = cfg()
del old.TRADE_RSI_EXIT_MIN_PROFIT_PCT
check('설정값이 없으면 기존 동작(수익률 무관)', decide(44.0, 88.7, old).action == 'SELL')

# ── 설정 저장/조회 + 실매매 cfg에 실리는지
from app.utils import db_manager

with tempfile.TemporaryDirectory() as tmp:
    db_manager.DB_PATH = os.path.join(tmp, 'test.db')
    db_manager.init_db()
    s = db_manager.get_trade_strategy_settings()
    check('기본값 +1%', s['rsi_exit_min_profit_pct'] == 1.0, s['rsi_exit_min_profit_pct'])
    db_manager.set_trade_strategy_settings(rsi_exit_enabled=True, rsi_exit_min_profit_pct=2.5)
    s = db_manager.get_trade_strategy_settings()
    check('저장한 값이 조회됨', s['rsi_exit_min_profit_pct'] == 2.5 and s['rsi_exit_enabled'] is True, s)
    db_manager.set_trade_strategy_settings(take_profit_pct=12.0)
    check('다른 값을 저장해도 유지', db_manager.get_trade_strategy_settings()['rsi_exit_min_profit_pct'] == 2.5)
    from app.backtest.strategy_config import strategy_config
    c = strategy_config(from_db=True)
    check('백테스트/실매매 cfg에 실림', c.TRADE_RSI_EXIT_MIN_PROFIT_PCT == 2.5, c.TRADE_RSI_EXIT_MIN_PROFIT_PCT)

    # 이 컬럼이 없던 기존 DB — 마이그레이션으로 1.0이 채워지는지
    import sqlite3
    old_path = os.path.join(tmp, 'old.db')
    db_manager.DB_PATH = old_path
    db_manager.init_db()
    conn = sqlite3.connect(old_path)
    conn.execute('ALTER TABLE trade_strategy_settings DROP COLUMN rsi_exit_min_profit_pct')
    conn.commit()
    conn.close()
    check('컬럼이 없으면 Config 기본값', db_manager.get_trade_strategy_settings()['rsi_exit_min_profit_pct'] == 1.0)
    db_manager.init_db()
    db_manager.set_trade_strategy_settings(rsi_exit_min_profit_pct=3.0)
    check('마이그레이션 후 저장 가능', db_manager.get_trade_strategy_settings()['rsi_exit_min_profit_pct'] == 3.0)

print()
if FAILED:
    print(f"실패 {len(FAILED)}건: {FAILED}")
    sys.exit(1)
print('모두 통과')
