"""백테스트에 넘길 매매 설정(TRADE_* 네임스페이스) 만들기.

app/core/auto_trader.py의 _effective_strategy_config()와 같은 모양을 만든다 — trade_strategy.py는
DB를 모르는 순수 함수라 이 네임스페이스만 받으면 되고, 실매매든 백테스트든 같은 값을 넣으면 같은
판단이 나온다. auto_trader를 그대로 임포트하지 않는 이유는 그 모듈이 pyupbit(실주문 브로커)를 끌고
오기 때문이다 — 백테스트는 네트워크도 거래소 SDK도 필요 없어야 한다.

기본값은 app/config.py의 TRADE_*이고, --from-db를 주면 대시보드에 저장된 실제 설정을 읽어
"지금 내 설정으로 과거를 돌리면" 이 된다.
"""
from types import SimpleNamespace

from app.config import Config

# 대시보드 설정 키 → trade_strategy가 읽는 속성 이름. Config의 기본값과 짝을 이룬다.
_FIELDS = [
    ('TRADE_MAX_POSITION_KRW', 'max_position_krw'),
    ('TRADE_MAX_CONCURRENT_POSITIONS', 'max_concurrent_positions'),
    ('TRADE_STOP_LOSS_PCT', 'stop_loss_pct'),
    ('TRADE_TAKE_PROFIT_PCT', 'take_profit_pct'),
    ('TRADE_STOP_LOSS_CONFIRM_CYCLES', 'stop_loss_confirm_cycles'),
    ('TRADE_DCA_TRIGGER_PCT', 'dca_trigger_pct'),
    ('TRADE_DCA_MAX_COUNT', 'dca_max_count'),
    ('TRADE_RSI_EXIT_ENABLED', 'rsi_exit_enabled'),
    ('TRADE_RSI_EXIT_PERIOD', 'rsi_exit_period'),
    ('TRADE_RSI_EXIT_OVERBOUGHT', 'rsi_exit_overbought'),
    ('TRADE_TRAILING_TP_ENABLED', 'trailing_tp_enabled'),
    ('TRADE_TRAILING_TP_ARM_PCT', 'trailing_tp_arm_pct'),
    ('TRADE_TRAILING_TP_FLOOR_PCT', 'trailing_tp_floor_pct'),
    ('TRADE_TIGHT_STOP_ENABLED', 'tight_stop_enabled'),
    ('TRADE_TIGHT_STOP_INITIAL_PCT', 'tight_stop_initial_pct'),
    ('TRADE_TIGHT_STOP_ARM_PCT', 'tight_stop_arm_pct'),
    ('TRADE_TIGHT_STOP_TRAIL_PCT', 'tight_stop_trail_pct'),
    ('TRADE_RECOVERY_DCA_ENABLED', 'recovery_dca_enabled'),
    ('TRADE_RECOVERY_DCA_TRIGGER_PCT', 'recovery_dca_trigger_pct'),
    ('TRADE_RECOVERY_DCA_AMOUNT_KRW', 'recovery_dca_amount_krw'),
    ('TRADE_RECOVERY_DCA_COOLDOWN_MIN', 'recovery_dca_cooldown_min'),
    ('TRADE_RECOVERY_TAKE_PROFIT_PCT', 'recovery_take_profit_pct'),
    ('TRADE_RECOVERY_DCA_MAX_COUNT', 'recovery_dca_max_count'),
    ('TRADE_RECOVERY_MAX_INVESTED_KRW', 'recovery_max_invested_krw'),
    ('TRADE_RECOVERY_TIME_STOP_DAYS', 'recovery_time_stop_days'),
    ('TRADE_RECOVERY_PARTIAL_STOP_PCT', 'recovery_partial_stop_pct'),
    ('TRADE_RECOVERY_PARTIAL_STOP_RATIO', 'recovery_partial_stop_ratio'),
    ('TRADE_RECOVERY_PARTIAL_STOP_COOLDOWN_MIN', 'recovery_partial_stop_cooldown_min'),
]


def strategy_config(from_db: bool = False, overrides: dict = None) -> SimpleNamespace:
    """TRADE_* 네임스페이스를 만든다. overrides는 {'TRADE_TAKE_PROFIT_PCT': 7.0} 형태."""
    values = {attr: getattr(Config, attr) for attr, _ in _FIELDS}
    values['TRADE_MIN_ORDER_KRW'] = Config.TRADE_MIN_ORDER_KRW

    if from_db:
        # db_manager는 임포트만으로 DB 파일을 건드리므로 필요할 때만 들여온다
        from app.utils.db_manager import get_trade_strategy_settings
        saved = get_trade_strategy_settings()
        for attr, key in _FIELDS:
            if key in saved and saved[key] is not None:
                values[attr] = saved[key]

    for key, value in (overrides or {}).items():
        if value is not None:
            values[key] = value
    return SimpleNamespace(**values)


def describe(cfg: SimpleNamespace) -> str:
    """리포트 머리말에 적을 한 줄 요약 — 어떤 설정으로 돌린 결과인지 나중에 봐도 알 수 있게."""
    parts = [
        f"1종목 {cfg.TRADE_MAX_POSITION_KRW:,.0f}원",
        f"동시 {cfg.TRADE_MAX_CONCURRENT_POSITIONS}종목",
        f"익절 +{cfg.TRADE_TAKE_PROFIT_PCT}%",
        f"트레일링 손절 -{cfg.TRADE_STOP_LOSS_PCT}%",
        f"물타기 {cfg.TRADE_DCA_MAX_COUNT}회(-{cfg.TRADE_DCA_TRIGGER_PCT}%)",
    ]
    if cfg.TRADE_RSI_EXIT_ENABLED:
        parts.append(f"RSI 매도 {cfg.TRADE_RSI_EXIT_OVERBOUGHT}")
    if cfg.TRADE_TRAILING_TP_ENABLED:
        parts.append(f"되돌림 익절 {cfg.TRADE_TRAILING_TP_ARM_PCT}→{cfg.TRADE_TRAILING_TP_FLOOR_PCT}%")
    if cfg.TRADE_RECOVERY_DCA_ENABLED:
        parts.append(f"회복형 물타기 {cfg.TRADE_RECOVERY_DCA_MAX_COUNT}회")
    return ', '.join(parts)
