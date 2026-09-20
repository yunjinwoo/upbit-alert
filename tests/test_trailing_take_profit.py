"""고점 대비 되돌림 익절(app/core/trade_strategy.py의 evaluate_exits) 단위 테스트 —
DB/네트워크 없이 가짜 포지션과 시세로 검증한다.

실행: python tests/test_trailing_take_profit.py
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
        TRADE_RSI_EXIT_ENABLED=False,
        TRADE_TRAILING_TP_ENABLED=True, TRADE_TRAILING_TP_ARM_PCT=3.0, TRADE_TRAILING_TP_FLOOR_PCT=2.0,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def decide(price, peak, c=None, avg_price=100.0):
    """평단 100원짜리 포지션 1건을, 보유 중 최고가 peak / 현재가 price 상태로 판단시킨다."""
    position = {'ticker': 'KRW-TEST', 'qty': 10.0, 'avg_buy_price': avg_price, 'peak_price': peak}
    return evaluate_exits([position], lambda t: price, c or cfg())[0]


def check(label, got, expected):
    if got == expected:
        print(f'  ✅ {label} = {got!r}')
    else:
        print(f'  ❌ {label} = {got!r} (기대: {expected!r})')
        FAILED.append(label)


print('--- [테스트] 되돌림 익절 발동 ---')
d = decide(price=102.0, peak=104.0)  # 고점 +4% 찍고 현재 +2%
check('고점4%→현재2%면 매도', d.action, 'SELL')
check('청산 사유 분류', normalize_exit_reason(d.reason), 'trailing_take_profit')
# 사이클 사이 급락 — 매도 기준보다 더 내려가도 그 사이클에 매도
check('급락으로 기준 아래면 그래도 매도', decide(price=100.5, peak=104.0).action, 'SELL')

print('\n--- [테스트] 발동 안 하는 경우 ---')
d = decide(price=102.5, peak=104.0)  # 아직 매도 기준(2%) 위
check('현재 2.5%면 보유', (d.action, d.status), ('HOLD', 'trailing_tp_armed'))
d = decide(price=101.0, peak=102.9)  # 고점이 발동 기준(3%)에 못 미침
check('고점 2.9%면 감시 자체가 없음', (d.action, d.status), ('HOLD', None))
d = decide(price=105.0, peak=104.0)  # 상승 중 — 최고가 갱신
check('상승 중이면 보유 + 최고가 갱신', (d.action, d.peak_price), ('HOLD', 105.0))
d = decide(price=102.0, peak=104.0, c=cfg(TRADE_TRAILING_TP_ENABLED=False))
check('기능 꺼져 있으면 기존 동작 그대로', (d.action, d.status), ('HOLD', None))

print('\n--- [테스트] 다른 청산조건과의 우선순위 ---')
d = decide(price=110.0, peak=104.0)  # 목표 수익률 10% 도달
check('목표 익절이 우선', normalize_exit_reason(d.reason), 'take_profit')
d = decide(price=98.8, peak=104.0)  # 고점 대비 -5%(트레일링 손절 조건)이지만 되돌림 익절이 먼저
check('트레일링 손절보다 먼저 걸림', normalize_exit_reason(d.reason), 'trailing_take_profit')

print('\n--- [테스트] 설정이 없는 구버전 cfg 호환 ---')
legacy = SimpleNamespace(TRADE_MAX_POSITION_KRW=100_000, TRADE_STOP_LOSS_PCT=5.0,
                         TRADE_TAKE_PROFIT_PCT=10.0, TRADE_STOP_LOSS_CONFIRM_CYCLES=1,
                         TRADE_DCA_TRIGGER_PCT=10.0, TRADE_DCA_MAX_COUNT=2)
check('TRADE_TRAILING_TP_* 속성이 없어도 동작', decide(price=102.0, peak=104.0, c=legacy).action, 'HOLD')

if FAILED:
    print(f'\n❌ 실패 {len(FAILED)}건: {FAILED}')
    sys.exit(1)
print('\n✅ 되돌림 익절 테스트 전부 통과')
