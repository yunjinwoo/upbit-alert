"""적극 손절 · 익절 코인(fast exit) 검증 — docs/auto-trade-fast-exit.md.

기존 청산 규칙은 그대로 두고, 지정한 코인만 평단 대비 좁은 선(-stop_pct / +take_profit_pct)에 닿으면
먼저 즉시 판다. 선에 안 닿으면 기존 규칙이 그대로 판단한다.

    python tests/test_fast_exit.py

확인하는 것:
  1. 지정 코인만 좁은 선에서 즉시 매도(연속 확인·물타기 대기 없음), 다른 코인은 기존대로
  2. 선에 안 닿으면 기존 흐름 그대로(트레일링 추적값 갱신 포함)
  3. 폭 0 = 그쪽은 적극 청산 안 씀 / 꺼져 있거나 속성이 없는 cfg면 기존 동작
  4. 짧은 손절 · 회복형 모드보다도 먼저 본다
  5. 청산 사유 분류, DB 저장·복원(코인 목록 정리, 부분 갱신)
"""
import os
import sys
import tempfile
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.trade_strategy import evaluate_exits
from app.core.trade_performance import normalize_exit_reason


def cfg(**overrides):
    base = dict(
        TRADE_MAX_POSITION_KRW=100_000, TRADE_MAX_CONCURRENT_POSITIONS=5,
        TRADE_STOP_LOSS_PCT=5.0, TRADE_TAKE_PROFIT_PCT=10.0,
        TRADE_STOP_LOSS_CONFIRM_CYCLES=3, TRADE_DCA_TRIGGER_PCT=10.0, TRADE_DCA_MAX_COUNT=2,
        TRADE_RSI_EXIT_ENABLED=False, TRADE_TRAILING_TP_ENABLED=False,
        TRADE_TIGHT_STOP_ENABLED=False, TRADE_RECOVERY_DCA_ENABLED=False,
        TRADE_FAST_EXIT_ENABLED=True, TRADE_FAST_EXIT_TICKERS=['KRW-XRP'],
        TRADE_FAST_EXIT_STOP_PCT=3.0, TRADE_FAST_EXIT_TAKE_PROFIT_PCT=5.0,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def decide(price, ticker='KRW-XRP', peak=None, c=None, **pos):
    position = {'ticker': ticker, 'qty': 10.0, 'avg_buy_price': 100.0, 'peak_price': peak or 100.0}
    position.update(pos)
    return evaluate_exits([position], lambda t: price, c or cfg())[0]


def test_fast_lines():
    d = decide(97.0)
    assert d.action == 'SELL' and normalize_exit_reason(d.reason) == 'fast_stop_loss', d
    d = decide(105.0)
    assert d.action == 'SELL' and normalize_exit_reason(d.reason) == 'fast_take_profit', d
    # 다른 코인은 기존 규칙: -3%는 트레일링(-5%) 미달이라 보유, +5%는 익절(+10%) 미달이라 보유
    assert decide(97.0, ticker='KRW-SOL').action == 'HOLD'
    assert decide(105.0, ticker='KRW-SOL').action == 'HOLD'
    # 물타기 횟수가 남아 있고 연속 확인이 3회여도 기다리지 않는다
    assert decide(96.0, below_stop_streak=0, dca_count=0).action == 'SELL'
    print('✓ 지정 코인만 -3% / +5%에서 즉시 매도, 다른 코인은 기존대로')


def test_falls_through():
    d = decide(104.0, peak=104.0)
    assert d.action == 'HOLD' and d.peak_price == 104.0, '선 안이면 기존 흐름(고점 추적 포함)'
    # 기존 익절이 더 좁으면 기존 규칙이 그대로 판다
    d = decide(104.0, c=cfg(TRADE_TAKE_PROFIT_PCT=4.0, TRADE_FAST_EXIT_TAKE_PROFIT_PCT=8.0))
    assert d.action == 'SELL' and normalize_exit_reason(d.reason) == 'take_profit', d
    print('✓ 선에 안 닿으면 기존 규칙이 그대로 판단한다')


def test_zero_and_off():
    assert decide(90.0, c=cfg(TRADE_FAST_EXIT_STOP_PCT=0)).action == 'HOLD', '손절 폭 0 → 기존(연속확인 대기)'
    assert decide(105.0, c=cfg(TRADE_FAST_EXIT_STOP_PCT=0)).action == 'SELL', '익절은 그대로'
    assert decide(97.0, c=cfg(TRADE_FAST_EXIT_ENABLED=False)).action == 'HOLD', '꺼져 있으면 기존'
    assert decide(97.0, c=cfg(TRADE_FAST_EXIT_TICKERS='krw-xrp, KRW-SOL')).action == 'SELL', '문자열 목록도 인식'
    legacy = SimpleNamespace(TRADE_MAX_POSITION_KRW=100_000, TRADE_STOP_LOSS_PCT=5.0,
                             TRADE_TAKE_PROFIT_PCT=10.0, TRADE_STOP_LOSS_CONFIRM_CYCLES=1,
                             TRADE_DCA_TRIGGER_PCT=10.0, TRADE_DCA_MAX_COUNT=2)
    assert decide(97.0, c=legacy).action == 'HOLD', '속성 없는 cfg(토스 등)는 기존 동작'
    print('✓ 폭 0 / 꺼짐 / 구버전 cfg는 기존 동작')


def test_before_other_modes():
    c = cfg(TRADE_TIGHT_STOP_ENABLED=True, TRADE_TIGHT_STOP_INITIAL_PCT=8.0,
            TRADE_TIGHT_STOP_ARM_PCT=5.0, TRADE_TIGHT_STOP_TRAIL_PCT=8.0)
    d = decide(97.0, c=c)
    assert normalize_exit_reason(d.reason) == 'fast_stop_loss', d
    assert decide(97.0, ticker='KRW-SOL', c=c).status == 'tight_watch', '다른 코인은 짧은 손절 모드 그대로'
    c = cfg(TRADE_RECOVERY_DCA_ENABLED=True)
    assert normalize_exit_reason(decide(105.0, c=c).reason) == 'fast_take_profit'
    print('✓ 짧은 손절 · 회복형 모드보다 먼저 본다')


def test_db_settings():
    from app.utils import db_manager
    with tempfile.TemporaryDirectory() as tmp:
        db_manager.DB_PATH = os.path.join(tmp, 'test.db')
        db_manager.init_db()
        s = db_manager.get_trade_strategy_settings()
        assert s['fast_exit_enabled'] is False and s['fast_exit_tickers'] == [], s
        assert (s['fast_exit_stop_pct'], s['fast_exit_take_profit_pct']) == (3.0, 5.0)
        saved = db_manager.set_trade_strategy_settings(fast_exit_enabled=True, fast_exit_tickers='xrp, KRW-SOL, XRP',
                                                       fast_exit_stop_pct=2.5)
        assert saved['fast_exit_tickers'] == ['KRW-XRP', 'KRW-SOL'], saved['fast_exit_tickers']
        db_manager.set_trade_strategy_settings(take_profit_pct=7.0)  # 다른 값만 저장해도 유지
        again = db_manager.get_trade_strategy_settings()
        assert again['fast_exit_enabled'] is True and again['fast_exit_tickers'] == ['KRW-XRP', 'KRW-SOL']
        assert again['fast_exit_stop_pct'] == 2.5 and again['fast_exit_take_profit_pct'] == 5.0
        assert db_manager.set_trade_strategy_settings(fast_exit_tickers='')['fast_exit_tickers'] == [], '비우기'
    print('✓ DB: 코인 목록 정리(KRW- 붙이기·중복 제거), 부분 갱신, 비우기')


if __name__ == '__main__':
    test_fast_lines()
    test_falls_through()
    test_zero_and_off()
    test_before_other_modes()
    test_db_settings()
    print('\n전부 통과')
