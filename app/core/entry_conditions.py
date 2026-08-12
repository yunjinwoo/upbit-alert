"""정밀 매수조건(다중 시간대: 일봉/5분봉/1분봉) 판단 로직.

app/core/trade_strategy.py와 같은 이유로 순수 함수 위주로 구성 — 캔들 조회(get_price_fn 격의
get_candles_fn)만 콜백으로 주입받고, DB는 여기서 직접 건드리지 않는다. 실제 캔들 조회/DB 저장은
app/core/entry_condition_checker.py(오케스트레이션 레이어)에서 수행한다.

조건 3종(기본 비활성화, 대시보드에서 켜야 동작):
  - daily_above_ma: 일봉 종가가 N일 이동평균 이상
  - m5_ma_support: 5분봉이 N선에 지지받고 반등 — 저가가 이평선 근접까지 눌렸다가 종가는 이평선 위로 마감
  - m1_bb_breakout_volume: 1분봉이 볼린저밴드 상단을 거래량 동반 돌파

여러 조건이 켜져 있으면 logic_group('AND'/'OR')으로 결합한다:
  최종 통과 = (AND 그룹 조건 전부 통과) AND (OR 그룹이 비었거나 그중 하나 이상 통과)
  켜진 조건이 하나도 없으면(사용자가 아직 아무 조건도 안 켰으면) 항상 통과 — "정밀검사 대상"으로
  체크만 해두고 조건은 안 켠 상태는 추가 제약이 없는 것으로 취급한다.
"""
from typing import Callable, Optional
import pandas as pd


def check_daily_above_ma(df: Optional[pd.DataFrame], params: dict) -> dict:
    """일봉 종가가 N일 이동평균 이상인지. df는 마지막 행이 진행 중인 캔들인 일봉 OHLCV."""
    period = int(params.get('ma_period', 20))
    if df is None or len(df) < period + 1:
        return {'passed': False, 'message': '캔들 데이터 부족'}
    idx_now = len(df) - 2  # 마지막 확정 캔들
    ma = df['close'].iloc[idx_now - period + 1: idx_now + 1].mean()
    close_now = df['close'].iloc[idx_now]
    passed = bool(close_now >= ma)
    return {
        'passed': passed,
        'message': f'종가 {close_now:,.0f} vs {period}일선 {ma:,.0f} ({"이상" if passed else "미만"})',
    }


def check_m5_ma_support(df: Optional[pd.DataFrame], params: dict) -> dict:
    """5분봉이 N선에 지지받고 반등했는지 — 마지막 확정 캔들의 저가가 이평선에 근접(tolerance% 이내)
    했다가 종가는 이평선 위로 마감했으면 "지지받고 반등"으로 판단."""
    period = int(params.get('ma_period', 20))
    tolerance_pct = float(params.get('touch_tolerance_pct', 0.3))
    if df is None or len(df) < period + 1:
        return {'passed': False, 'message': '캔들 데이터 부족'}
    idx_now = len(df) - 2
    ma = df['close'].iloc[idx_now - period + 1: idx_now + 1].mean()
    low_now = df['low'].iloc[idx_now]
    close_now = df['close'].iloc[idx_now]
    touched = bool(low_now <= ma * (1 + tolerance_pct / 100))
    closed_above = bool(close_now >= ma)
    passed = touched and closed_above
    return {
        'passed': passed,
        'message': f'저가 {low_now:,.2f}/{period}선 {ma:,.2f} 근접={touched}, 종가마감위={closed_above}',
    }


def check_m1_bb_breakout_volume(df: Optional[pd.DataFrame], params: dict) -> dict:
    """1분봉이 볼린저밴드 상단을 거래량 동반 돌파했는지 — 마지막 확정 캔들의 종가가 상단밴드를
    넘고, 거래량이 직전 lookback 평균 대비 vol_ratio_threshold배 이상이면 통과."""
    bb_period = int(params.get('bb_period', 20))
    bb_mult = float(params.get('bb_mult', 2.0))
    vol_lookback = int(params.get('vol_lookback', 20))
    vol_ratio_threshold = float(params.get('vol_ratio_threshold', 2.0))
    min_len = max(bb_period, vol_lookback) + 1
    if df is None or len(df) < min_len + 1:
        return {'passed': False, 'message': '캔들 데이터 부족'}

    idx_now = len(df) - 2
    closes = df['close']
    window = closes.iloc[idx_now - bb_period + 1: idx_now + 1]
    ma = window.mean()
    std = window.std(ddof=0)
    upper = ma + std * bb_mult
    close_now = closes.iloc[idx_now]

    vol_now = df['volume'].iloc[idx_now]
    avg_vol = df['volume'].iloc[idx_now - vol_lookback: idx_now].mean()
    vol_ratio = vol_now / avg_vol if avg_vol else 0

    broke_upper = bool(close_now > upper)
    vol_ok = bool(vol_ratio >= vol_ratio_threshold)
    passed = broke_upper and vol_ok
    return {
        'passed': passed,
        'message': f'종가 {close_now:,.2f} vs 상단밴드 {upper:,.2f} 돌파={broke_upper}, 거래량비 {vol_ratio:.2f}배(기준 {vol_ratio_threshold}배)',
    }


# condition_key -> (판단 함수, 캔들 조회 파라미터: interval/count)
CONDITION_SPECS = {
    'daily_above_ma': {
        'fn': check_daily_above_ma,
        'interval': 'day',
        'count_fn': lambda params: int(params.get('ma_period', 20)) + 5,
    },
    'm5_ma_support': {
        'fn': check_m5_ma_support,
        'interval': 'minute5',
        'count_fn': lambda params: int(params.get('ma_period', 20)) + 5,
    },
    'm1_bb_breakout_volume': {
        'fn': check_m1_bb_breakout_volume,
        'interval': 'minute1',
        'count_fn': lambda params: max(int(params.get('bb_period', 20)), int(params.get('vol_lookback', 20))) + 5,
    },
}


def evaluate_conditions(ticker: str, condition_settings: list,
                         get_candles_fn: Callable[[str, str, int], Optional[pd.DataFrame]]) -> dict:
    """설정된(enabled=1) 조건들을 전부 계산해 AND/OR로 결합한 최종 판단을 반환.

    condition_settings: get_trade_condition_settings() 결과(list of dict, 각 condition_key/enabled/
    logic_group/params). get_candles_fn(ticker, interval, count) -> OHLCV DataFrame|None 콜백으로
    캔들 조회를 주입받아, 이 함수 자체는 여전히 네트워크 세부사항에 의존하지 않는다.
    반환: {'passed': bool, 'detail': {condition_key: {passed, message}, ...}}"""
    enabled = [c for c in condition_settings if c.get('enabled')]
    detail = {}
    if not enabled:
        return {'passed': True, 'detail': detail}

    and_results = []
    or_results = []
    for cond in enabled:
        key = cond['condition_key']
        spec = CONDITION_SPECS.get(key)
        if not spec:
            detail[key] = {'passed': False, 'message': '알 수 없는 조건'}
            and_results.append(False) if cond.get('logic_group') != 'OR' else or_results.append(False)
            continue
        params = cond.get('params') or {}
        count = spec['count_fn'](params)
        try:
            df = get_candles_fn(ticker, spec['interval'], count)
            result = spec['fn'](df, params)
        except Exception as e:
            result = {'passed': False, 'message': f'조건 계산 실패: {e}'}
        detail[key] = result

        if cond.get('logic_group') == 'OR':
            or_results.append(result['passed'])
        else:
            and_results.append(result['passed'])

    and_ok = all(and_results) if and_results else True
    or_ok = any(or_results) if or_results else True
    passed = bool(and_ok and or_ok)
    return {'passed': passed, 'detail': detail}
