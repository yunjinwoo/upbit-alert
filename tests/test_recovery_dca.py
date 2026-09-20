"""회복형 분할 물타기(recovery DCA) 청산 판단 단위 테스트 — docs/auto-trade-recovery-dca.md.

evaluate_exits()/_evaluate_exit_recovery()는 순수 함수라 DB/네트워크/실주문 없이 값만으로 검증한다
(tests/test_trade_dry_run.py는 DB가 필요한 1사이클 통합 실행이라 성격이 다름).
실행: python tests/test_recovery_dca.py  — 모든 케이스가 통과하면 마지막에 ✅ 를 출력한다.
"""
import os
import sys
from datetime import datetime, timedelta
from types import SimpleNamespace

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app.core.trade_strategy import evaluate_exits, _evaluate_exit_recovery, TradeDecision

TS = '%Y-%m-%d %H:%M:%S'
NOW = datetime(2026, 9, 18, 12, 0, 0)


def cfg(**overrides):
    """대시보드 기본값과 같은 회복형 파라미터 묶음 — 케이스마다 필요한 값만 덮어쓴다."""
    base = dict(
        TRADE_MAX_POSITION_KRW=100_000,
        TRADE_TAKE_PROFIT_PCT=10.0,
        TRADE_STOP_LOSS_PCT=5.0,
        TRADE_STOP_LOSS_CONFIRM_CYCLES=1,
        TRADE_DCA_TRIGGER_PCT=10.0,
        TRADE_DCA_MAX_COUNT=2,
        TRADE_RSI_EXIT_ENABLED=False,
        TRADE_RSI_EXIT_OVERBOUGHT=80.0,
        TRADE_RECOVERY_DCA_ENABLED=True,
        TRADE_RECOVERY_DCA_TRIGGER_PCT=20.0,
        TRADE_RECOVERY_DCA_AMOUNT_KRW=50_000,
        TRADE_RECOVERY_DCA_COOLDOWN_MIN=60,
        TRADE_RECOVERY_TAKE_PROFIT_PCT=5.0,
        TRADE_RECOVERY_DCA_MAX_COUNT=3,
        TRADE_RECOVERY_MAX_INVESTED_KRW=250_000,
        TRADE_RECOVERY_TIME_STOP_DAYS=7,
        TRADE_RECOVERY_PARTIAL_STOP_PCT=30.0,
        TRADE_RECOVERY_PARTIAL_STOP_RATIO=20.0,
        TRADE_RECOVERY_PARTIAL_STOP_COOLDOWN_MIN=360,
        TRADE_MIN_ORDER_KRW=5_000,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def pos(avg=1000.0, qty=100.0, invested=100_000, recovery_count=0,
        last_dca_min_ago=None, last_partial_min_ago=None, entry_min_ago=10_000):
    """paper_positions 행 모양 — 시각은 "몇 분 전"으로 받아 NOW 기준 문자열로 변환한다."""
    def ago(minutes):
        return None if minutes is None else (NOW - timedelta(minutes=minutes)).strftime(TS)
    return {
        'ticker': 'KRW-TEST', 'qty': qty, 'avg_buy_price': avg,
        'peak_price': avg, 'below_stop_streak': 0, 'dca_count': 0,
        'first_entry_price': avg, 'total_invested_krw': invested,
        'recovery_dca_count': recovery_count,
        'entry_at': ago(entry_min_ago),
        'last_dca_at': ago(last_dca_min_ago),
        'last_partial_stop_at': ago(last_partial_min_ago),
        'recovery_partial_stop_count': 0,
    }


def decide(position, price, config=None, rsi=None) -> TradeDecision:
    return _evaluate_exit_recovery(position, price, config or cfg(), rsi_now=rsi, now=NOW)


failures = []


def check(label, got, expected):
    if got == expected:
        print(f"  ✓ {label}: {got}")
    else:
        failures.append(f"{label} — 기대 {expected}, 실제 {got}")
        print(f"  ✗ {label}: 기대 {expected}, 실제 {got}")


print("--- 익절 ---")
# 평단 1000, +5%면 1050. 경계값(딱 1050)도 매도여야 한다.
check('+5.0% 경계 → 회복형 익절 전량매도', decide(pos(), 1050.0).reason.split('(')[0], 'recovery_take_profit')
check('+4.9% → 아직 보유', decide(pos(), 1049.0).action, 'HOLD')
check('+10% 이상은 기존 익절 기준으로', decide(pos(), 1100.0).reason.split('(')[0], 'take_profit')
check('익절은 전량', decide(pos(), 1050.0).qty, 100.0)
check('익절은 부분매도 아님', decide(pos(), 1050.0).partial_sell, False)

print("--- 물타기 트리거 / 쿨다운 ---")
# -20% = 800원. 경계값 포함, 쿨다운은 최초 진입(entry_at)부터 센다.
check('-20.0% 경계 + 쿨다운 경과 → 물타기', decide(pos(), 800.0).action, 'DCA_BUY')
check('물타기 금액은 소액(5만원)', decide(pos(), 800.0).amount_krw, 50_000)
check('-19.9% → 물타기 대기', decide(pos(), 801.0).status, 'recovery_dca_pending')
check('-19.9%는 매수 아님', decide(pos(), 801.0).action, 'HOLD')
check('쿨다운 미경과(30/60분) → 대기', decide(pos(last_dca_min_ago=30), 800.0).action, 'HOLD')
check('쿨다운 미경과 상태 라벨', decide(pos(last_dca_min_ago=30), 800.0).status, 'recovery_dca_pending')
check('쿨다운 60분 경과 → 물타기', decide(pos(last_dca_min_ago=60), 800.0).action, 'DCA_BUY')
check('물타기는 회복형 플래그를 달고 나감', decide(pos(), 800.0).recovery, True)

print("--- 상한(횟수 / 투입액) ---")
check('3/3회 소진 → 물타기 안 함', decide(pos(recovery_count=3), 800.0).action, 'HOLD')
check('3/3회 소진 상태 라벨', decide(pos(recovery_count=3), 800.0).status, 'recovery_capped')
check('2/3회는 아직 가능', decide(pos(recovery_count=2), 800.0).action, 'DCA_BUY')
# 투입 210,000 + 다음 50,000 = 260,000 > 상한 250,000 → 금지. 200,000이면 정확히 상한이라 허용.
check('투입액 상한 초과 예정 → 금지', decide(pos(invested=210_000), 800.0).status, 'recovery_capped')
check('투입액 상한 경계(정확히 250,000) → 허용', decide(pos(invested=200_000), 800.0).action, 'DCA_BUY')

print("--- 소액 손절 (상한 소진 후에만) ---")
# -30% = 700원. 상한이 남아 있으면 이 구간에서도 물타기가 우선이어야 한다.
check('상한 남음 + -30% → 소액 손절 아니라 물타기', decide(pos(), 700.0).action, 'DCA_BUY')
capped = pos(recovery_count=3, last_dca_min_ago=400)
check('상한 소진 + -30% 경계 → 부분 매도', decide(capped, 700.0).action, 'SELL')
check('부분 매도 플래그', decide(capped, 700.0).partial_sell, True)
check('보유량 20%만 매도', round(decide(capped, 700.0).qty, 6), 20.0)
check('-29.9%면 아직 반등 대기', decide(capped, 701.0).status, 'recovery_capped')
check('소액 손절 간격 미경과(100/360분) → 대기',
      decide(pos(recovery_count=3, last_dca_min_ago=400, last_partial_min_ago=100), 700.0).action, 'HOLD')
check('소액 손절 간격 경과 → 매도',
      decide(pos(recovery_count=3, last_dca_min_ago=400, last_partial_min_ago=360), 700.0).action, 'SELL')
# 물타기 직후엔 아직 간격이 안 찼으므로 되팔지 않는다(마지막 물타기 시각부터 센다).
check('물타기 100분 뒤 상한 소진 상태 → 소액 손절 대기',
      decide(pos(recovery_count=3, last_dca_min_ago=100), 700.0).action, 'HOLD')

print("--- 최소 주문금액 처리 ---")
# 보유 30,000원(100개 @ 300원)에서 20%는 6,000원 → 최소주문 5,000원 이상이라 그대로 부분 매도.
small = pos(avg=1000.0, qty=100.0, recovery_count=3, last_dca_min_ago=400)
check('부분 매도액이 최소주문 이상 → 부분', decide(small, 300.0).partial_sell, True)
# 보유 20,000원에서 20%는 4,000원 → 5,000원으로 올려 팔고 남는 15,000원은 최소주문 이상 → 부분 유지.
tiny = pos(avg=1000.0, qty=20.0, recovery_count=3, last_dca_min_ago=400)
check('4,000원짜리 조각은 5,000원으로 올려 부분 매도', decide(tiny, 1000.0 * 0.7).partial_sell, True)
# 보유 8,000원 — 20%(1,600원)를 5,000원으로 올리면 남는 게 3,000원(최소주문 미만) → 전량.
dust = pos(avg=1000.0, qty=8.0, recovery_count=3, last_dca_min_ago=400)
check('쪼갤 수 없는 잔량 → 전량 매도', decide(dust, 1000.0 * 0.7).partial_sell, False)
check('쪼갤 수 없는 잔량 사유', decide(dust, 1000.0 * 0.7).reason.split('(')[0], 'recovery_partial_stop_all')

print("--- 시간 하드스톱 ---")
# 상한 소진 시각(마지막 물타기) 기준 7일 = 10,080분.
week = 7 * 24 * 60
check('상한 소진 후 7일 경과 + 손실 → 전량 정리',
      decide(pos(recovery_count=3, last_dca_min_ago=week), 900.0).reason.split('(')[0], 'recovery_time_stop')
check('7일 직전(-1분)은 아직 보유',
      decide(pos(recovery_count=3, last_dca_min_ago=week - 1), 900.0).action, 'HOLD')
check('상한이 남아 있으면 시간 하드스톱 안 걸림',
      decide(pos(recovery_count=0, last_dca_min_ago=week), 900.0).action, 'HOLD')
check('손실이 아니면(+2%) 시간 하드스톱 안 걸림',
      decide(pos(recovery_count=3, last_dca_min_ago=week), 1020.0).action, 'HOLD')
check('시간 하드스톱 0이면 비활성',
      decide(pos(recovery_count=3, last_dca_min_ago=week), 900.0, cfg(TRADE_RECOVERY_TIME_STOP_DAYS=0)).action, 'HOLD')

print("--- RSI 과매수 매도는 수익 구간에서만 ---")
rsi_cfg = cfg(TRADE_RSI_EXIT_ENABLED=True)
check('수익 구간 + RSI 85 → 매도', decide(pos(), 1020.0, rsi_cfg, rsi=85.0).action, 'SELL')
check('손실 구간 + RSI 85 → 매도 안 함', decide(pos(), 900.0, rsi_cfg, rsi=85.0).action, 'HOLD')

print("--- 모드 스위치 (evaluate_exits 분기) ---")
# 같은 상태(-20%)를 두 모드로 돌려 서로 다른 판단이 나오는지 확인.
positions = [pos()]
prices = {'KRW-TEST': 800.0}
on = evaluate_exits(positions, lambda t: prices[t], cfg())
check('모드 ON → 회복형 물타기', (on[0].action, on[0].recovery), ('DCA_BUY', True))
off = evaluate_exits(positions, lambda t: prices[t], cfg(TRADE_RECOVERY_DCA_ENABLED=False))
check('모드 OFF → 기존 로직(트레일링 손절 경로)', off[0].recovery, False)
check('모드 OFF에서 최고가 대비 -20%면 기존 물타기', off[0].action, 'DCA_BUY')
check('모드 OFF 물타기 금액은 기존대로 매수금액 전액', off[0].amount_krw, 100_000)

print("--- 시세 조회 실패 ---")
skipped = evaluate_exits([pos()], lambda t: None, cfg())
check('시세 없으면 SKIP', skipped[0].action, 'SKIP')

print()
if failures:
    print(f"❌ 실패 {len(failures)}건")
    for f in failures:
        print(f"  - {f}")
    sys.exit(1)
print("✅ 회복형 분할 물타기 판단 테스트 전부 통과")
