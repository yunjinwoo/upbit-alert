"""전략 묶음(좋음 / 애매 / 나쁨) — 매매 기준 설정의 청산 값을 한 번에 바꾸는 프리셋.

시장 판단(app/core/market_regime.py)은 표시와 알림만 하고, 어떤 전략을 쓸지는 사람이 고른다. 이 모듈은
그 "고르기"를 버튼 한 번으로 만들어 준다 — 묶음을 적용하면 trade_strategy_settings(업비트)의 해당 값들이
덮어써지고, 실거래 루프가 다음 사이클부터 그 값으로 판다. 시장 판단이 바뀐다고 자동으로 적용되지는 않는다.

묶음은 청산 규칙만 바꾼다. 1종목당 매수금액 · 동시 보유 수 · 루프 주기 · RSI 과매수 매도 · 정밀 매수조건은
건드리지 않는다(자금 규모와 매수 조건은 사람이 따로 정한 값이라). 세 묶음 모두 짧은 손절 · 긴 수익(tight stop)
모드를 켜고 폭만 다르게 둔다 — 이 모드는 물타기와 되돌림 익절을 쓰지 않아서, "빠른 손절"이 물타기로 늘어지는
일이 없다(docs/auto-trade-tight-stop.md). 회복형 분할 물타기는 정반대 성격이라 세 묶음 모두 끈다.

지금 적용 중인 묶음은 따로 저장하지 않고 현재 설정값과 묶음 값을 비교해서 알아낸다(match_preset) — 묶음을
적용한 뒤 값을 하나라도 손으로 바꾸면 "직접 설정"으로 보이는 게 맞기 때문이다.
"""
from typing import Optional

STRATEGY_PRESETS = {
    'good': {
        'label': '좋음', 'emoji': '🟢', 'name': '손절 짧게 · 수익 길게',
        'summary': '손절 -3%, +5% 넘으면 고점 대비 -10%까지 보유, 익절 +30%',
        'values': {
            'tight_stop_enabled': True, 'tight_stop_initial_pct': 3.0,
            'tight_stop_arm_pct': 5.0, 'tight_stop_trail_pct': 10.0,
            'take_profit_pct': 30.0, 'trailing_tp_enabled': False, 'recovery_dca_enabled': False,
        },
    },
    'neutral': {
        'label': '애매', 'emoji': '🟡', 'name': '당일 상승률 · 거래대금 위주',
        'summary': '손절 -3%, +4% 넘으면 고점 대비 -4%까지 보유, 익절 +6%',
        'values': {
            'tight_stop_enabled': True, 'tight_stop_initial_pct': 3.0,
            'tight_stop_arm_pct': 4.0, 'tight_stop_trail_pct': 4.0,
            'take_profit_pct': 6.0, 'trailing_tp_enabled': False, 'recovery_dca_enabled': False,
        },
    },
    'bad': {
        'label': '나쁨', 'emoji': '🔴', 'name': '빠른 손절 · 빠른 익절',
        'summary': '손절 -2%, +3% 넘으면 고점 대비 -1.5%까지 보유, 익절 +4%',
        'values': {
            'tight_stop_enabled': True, 'tight_stop_initial_pct': 2.0,
            'tight_stop_arm_pct': 3.0, 'tight_stop_trail_pct': 1.5,
            'take_profit_pct': 4.0, 'trailing_tp_enabled': False, 'recovery_dca_enabled': False,
        },
    },
}


def _same(a, b) -> bool:
    if isinstance(b, bool):
        return bool(a) == b
    try:
        return abs(float(a) - float(b)) < 1e-9
    except (TypeError, ValueError):
        return False


def match_preset(settings: dict) -> Optional[str]:
    """현재 매매 설정이 어느 묶음과 정확히 같은지. 어느 것과도 다르면 None(직접 설정)."""
    for key, preset in STRATEGY_PRESETS.items():
        if all(_same(settings.get(k), v) for k, v in preset['values'].items()):
            return key
    return None


def preset_text(key: Optional[str]) -> str:
    p = STRATEGY_PRESETS.get(key)
    return f"{p['emoji']} {p['label']}({p['name']})" if p else '직접 설정(묶음과 다름)'


def apply_preset(key: str, broker: str = 'upbit') -> dict:
    """묶음 값을 매매 설정에 덮어쓴다. 저장된 전체 설정을 돌려준다."""
    if key not in STRATEGY_PRESETS:
        raise ValueError(f'알 수 없는 전략 묶음: {key}')
    from app.utils.db_manager import set_trade_strategy_settings
    return set_trade_strategy_settings(broker=broker, **STRATEGY_PRESETS[key]['values'])
