"""청산(매도)조건 계산 — RSI 과매수 매도.

app/core/entry_conditions.py와 같은 이유로 순수 함수만 둔다 — 캔들 조회는 여기서 하지 않고
app/core/auto_trader.py(오케스트레이션 레이어)가 콜백 없이 직접 pyupbit로 조회해 이미 계산된
RSI 값만 app/core/trade_strategy.py의 evaluate_exits()에 넘긴다(entry_conditions.py의
get_candles_fn 콜백 패턴과 달리, 대상이 "보유 중인 소수 포지션"뿐이라 매 사이클 직접 조회해도
API 호출량이 크게 늘지 않기 때문).

RSI 자체 계산 로직은 app/core/upbit_market_analysis.py/app/core/toss_market_analysis.py의
_rsi()와 동일 공식(Wilder 방식과 유사한 EWM 평활)이다 — 저장소 관례상 계산 로직을 공유 모듈로
빼지 않고 필요한 곳마다 독립적으로 둔다(그 두 파일의 docstring 참고)."""
from typing import Optional
import pandas as pd


def compute_rsi(df: Optional[pd.DataFrame], period: int = 14) -> Optional[float]:
    """OHLCV DataFrame의 마지막 확정 캔들(idx_now = len(df) - 2, 마지막 행은 진행 중인 캔들로 봄)
    기준 RSI(period) 값. 데이터가 부족하면(워밍업 안 됨) None을 반환한다."""
    if df is None or len(df) < period * 2 + 1:
        return None
    idx_now = len(df) - 2
    closes = df['close'].iloc[:idx_now + 1]
    delta = closes.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, float('nan'))
    rsi = (100 - (100 / (1 + rs))).fillna(100)  # avg_loss가 0(구간 내내 상승만)이면 RSI=100으로 취급
    return float(rsi.iloc[-1])


def check_rsi_exit(rsi: Optional[float], overbought: float) -> dict:
    """RSI 매도조건 판단 — rsi가 overbought 이상이면 과매수로 보고 매도 트리거."""
    if rsi is None:
        return {'triggered': False, 'rsi': None, 'message': 'RSI 계산 불가(캔들 데이터 부족)'}
    triggered = bool(rsi >= overbought)
    return {
        'triggered': triggered,
        'rsi': round(rsi, 2),
        'message': f'RSI {rsi:.2f} vs 과매수 기준 {overbought:.1f} ({"충족" if triggered else "미충족"})',
    }
