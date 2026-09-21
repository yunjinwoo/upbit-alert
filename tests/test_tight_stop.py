"""짧은 손절 · 긴 수익 모드(app/core/trade_strategy.py의 evaluate_exits) 단위 테스트 —
DB/네트워크 없이 가짜 포지션과 시세로 검증한다.

실행: python tests/test_tight_stop.py
"""
import os
import sys
from types import SimpleNamespace

# Add project root to sys.path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app.core.trade_strategy import evaluate_exits
from app.core.trade_performance import normalize_exit_reason

FAILED = []


def cfg(**overrides):
    """전략 설정 네임스페이스(auto_trader._effective_strategy_config()가 만드는 것과 같은 모양)."""
    base = dict(
        TRADE_MAX_POSITION_KRW=100_000, TRADE_MAX_CONCURRENT_POSITIONS=5,
        TRADE_STOP_LOSS_PCT=5.0, TRADE_TAKE_PROFIT_PCT=10.0,
        TRADE_STOP_LOSS_CONFIRM_CYCLES=1, TRADE_DCA_TRIGGER_PCT=10.0, TRADE_DCA_MAX_COUNT=2,
        TRADE_RSI_EXIT_ENABLED=False, TRADE_RSI_EXIT_OVERBOUGHT=80.0,
        TRADE_TRAILING_TP_ENABLED=False, TRADE_TRAILING_TP_ARM_PCT=3.0, TRADE_TRAILING_TP_FLOOR_PCT=2.0,
        TRADE_TIGHT_STOP_ENABLED=True, TRADE_TIGHT_STOP_INITIAL_PCT=2.0,
        TRADE_TIGHT_STOP_ARM_PCT=5.0, TRADE_TIGHT_STOP_TRAIL_PCT=8.0,
        TRADE_RECOVERY_DCA_ENABLED=False,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def decide(price, peak, c=None, avg_price=100.0, **pos_overrides):
    """평단 100원짜리 포지션 1건을, 보유 중 최고가 peak / 현재가 price 상태로 판단시킨다."""
    position = {'ticker': 'KRW-TEST', 'qty': 10.0, 'avg_buy_price': avg_price, 'peak_price': peak}
    position.update(pos_overrides)
    return evaluate_exits([position], lambda t: price, c or cfg())[0]


def check(label, got, expected):
    if got == expected:
        print(f'  ✅ {label} = {got!r}')
    else:
        print(f'  ❌ {label} = {got!r} (기대: {expected!r})')
        FAILED.append(label)


print('--- [테스트] 수익 전환 전 — 짧은 손절 ---')
d = decide(price=98.0, peak=100.0)  # 평단 대비 -2.0%(기준과 동일)
check('-2%면 즉시 매도', d.action, 'SELL')
check('청산 사유 분류', normalize_exit_reason(d.reason), 'tight_stop_loss')
check('-1.9%면 보유', (decide(price=98.1, peak=100.0).action, decide(price=98.1, peak=100.0).status),
      ('HOLD', 'tight_watch'))
# 짧게 끊는 게 목적이라 연속 확인(stop_loss_confirm_cycles)을 기다리지 않는다
check('연속확인 설정과 무관하게 즉시 매도',
      decide(price=98.0, peak=100.0, c=cfg(TRADE_STOP_LOSS_CONFIRM_CYCLES=3)).action, 'SELL')
# 고점 대비로는 많이 밀렸어도 아직 전환 전이면 평단 기준으로만 본다
check('전환 전에는 고점대비 -2.9%여도 보유(+1% 구간)',
      (decide(price=101.0, peak=104.0).action, decide(price=101.0, peak=104.0).status),
      ('HOLD', 'tight_watch'))

print('\n--- [테스트] 전환 후 — 긴 트레일링 ---')
# 고점 +10% → 손절선 = 110 × (1-8%) = 101.2
check('고점대비 -7.3%는 보유',
      (decide(price=102.0, peak=110.0).action, decide(price=102.0, peak=110.0).status),
      ('HOLD', 'tight_trailing'))
d = decide(price=101.0, peak=110.0)
check('고점대비 -8.2%면 매도', d.action, 'SELL')
check('청산 사유 분류', normalize_exit_reason(d.reason), 'tight_trail_exit')
check('전환 후 이익 확정(+1%)', round(d.pnl_pct, 2), 1.0)

print('\n--- [테스트] 전환 뒤에는 본전 아래로 손절선을 내리지 않음 ---')
# 고점 +6%(전환 기준 5% 초과)지만 트레일링 손절선은 106×0.92=97.52로 평단 아래 → 손절선은 평단 100
d = decide(price=99.9, peak=106.0)
check('본전 아래로 내려오면 매도', d.action, 'SELL')
check('청산 사유 분류', normalize_exit_reason(d.reason), 'tight_breakeven_exit')
check('본전 위면 보유', (decide(price=100.5, peak=106.0).action, decide(price=100.5, peak=106.0).status),
      ('HOLD', 'tight_trailing'))

print('\n--- [테스트] 물타기를 하지 않는다 ---')
# 기존 로직이면 -12%는 물타기 트리거(-10%) 도달이라 DCA_BUY가 나가는 자리
d = decide(price=88.0, peak=100.0)
check('물타기 대신 손절', d.action, 'SELL')
check('청산 사유 분류', normalize_exit_reason(d.reason), 'tight_stop_loss')
check('물타기 이력이 있어도 동일', decide(price=88.0, peak=100.0, dca_count=1).action, 'SELL')

print('\n--- [테스트] 되돌림 익절은 이 모드에서 동작하지 않음 ---')
# 되돌림 익절(3%→2%)이 켜져 있어도 수익을 끊지 않는다 — "수익은 길게"와 방향이 반대라 이 모드가 이긴다
c = cfg(TRADE_TRAILING_TP_ENABLED=True)
check('고점4%→현재2%여도 보유', (decide(price=102.0, peak=104.0, c=c).action,
                                   decide(price=102.0, peak=104.0, c=c).status), ('HOLD', 'tight_watch'))

print('\n--- [테스트] 다른 청산조건과의 우선순위 ---')
check('목표 익절이 우선', normalize_exit_reason(decide(price=110.0, peak=110.0).reason), 'take_profit')
c = cfg(TRADE_RSI_EXIT_ENABLED=True)
d = evaluate_exits([{'ticker': 'KRW-TEST', 'qty': 10.0, 'avg_buy_price': 100.0, 'peak_price': 104.0}],
                   lambda t: 103.0, c, rsi_map={'KRW-TEST': 85.0})[0]
check('RSI 과매수 매도가 우선', normalize_exit_reason(d.reason), 'rsi_exit')
# 회복형 모드는 청산 판단 전체를 대신하므로 둘 다 켜면 회복형이 이긴다
c = cfg(TRADE_RECOVERY_DCA_ENABLED=True, TRADE_RECOVERY_DCA_TRIGGER_PCT=20.0,
        TRADE_RECOVERY_TAKE_PROFIT_PCT=5.0, TRADE_RECOVERY_DCA_MAX_COUNT=3,
        TRADE_RECOVERY_DCA_AMOUNT_KRW=50_000, TRADE_RECOVERY_DCA_COOLDOWN_MIN=60,
        TRADE_RECOVERY_MAX_INVESTED_KRW=250_000, TRADE_RECOVERY_TIME_STOP_DAYS=7,
        TRADE_RECOVERY_PARTIAL_STOP_PCT=30.0, TRADE_RECOVERY_PARTIAL_STOP_RATIO=20.0,
        TRADE_RECOVERY_PARTIAL_STOP_COOLDOWN_MIN=360, TRADE_MIN_ORDER_KRW=5_000)
check('회복형이 켜져 있으면 회복형이 우선', decide(price=98.0, peak=100.0, c=c).recovery, True)

print('\n--- [테스트] 꺼져 있으면 기존 동작 그대로 ---')
c = cfg(TRADE_TIGHT_STOP_ENABLED=False)
check('-2%는 손절 조건도 아님(트레일링 -5%)',
      (decide(price=98.0, peak=100.0, c=c).action, decide(price=98.0, peak=100.0, c=c).status),
      ('HOLD', None))
check('-12%는 기존대로 물타기', decide(price=88.0, peak=100.0, c=c).action, 'DCA_BUY')

print('\n--- [테스트] 설정이 없는 구버전 cfg 호환 ---')
legacy = SimpleNamespace(TRADE_MAX_POSITION_KRW=100_000, TRADE_STOP_LOSS_PCT=5.0,
                         TRADE_TAKE_PROFIT_PCT=10.0, TRADE_STOP_LOSS_CONFIRM_CYCLES=1,
                         TRADE_DCA_TRIGGER_PCT=10.0, TRADE_DCA_MAX_COUNT=2)
check('TRADE_TIGHT_STOP_* 속성이 없어도 동작', decide(price=98.0, peak=100.0, c=legacy).action, 'HOLD')

if FAILED:
    print(f'\n❌ 실패 {len(FAILED)}건: {FAILED}')
    sys.exit(1)
print('\n✅ 짧은 손절 · 긴 수익 테스트 전부 통과')
