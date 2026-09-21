"""캐시된 캔들을 메모리에 올려 "그 시각 기준"의 시세와 순위를 돌려준다.

백테스트에서 제일 틀리기 쉬운 곳이 여기다. 일봉만 보고 "그날 상승률 상위 10"을 고르면 장 마감 뒤에야
알 수 있는 순위로 아침에 산 셈이 되어(look-ahead) 성과가 실제보다 크게 부풀려진다. 그래서 순위는
반드시 "지금까지의" 데이터로만 계산한다 — 시각 T의 상승률은 전일 종가 대비 T시점 종가, 거래대금은
그날 KST 00시부터 T까지 누적분이다. 둘 다 업비트 화면의 정의와 같고, 차이는 "마감 기준이냐 진행
중이냐"뿐이다.

전일 종가는 분봉에 실려오지 않으므로 같은 종목의 일봉에서 가져온다(업비트가 일봉에 prev_closing_price를
직접 준다). 그래서 수집기가 분봉과 일봉을 항상 같이 받는다.
"""
from typing import Dict, List, Optional

from app.backtest import candle_store as store
from app.backtest.candle_store import INTERVAL_SECONDS, kst_date

# 순위 기준 — CLI의 --selection 값과 같은 문자열을 쓴다
SELECT_GAINERS = 'gainers'          # 당일 상승률 상위
SELECT_TRADE_VALUE = 'trade_value'  # 당일 거래대금 상위

SELECTION_LABELS = {
    SELECT_GAINERS: '당일 상승률 상위',
    SELECT_TRADE_VALUE: '당일 거래대금 상위',
}


class MarketData:
    """한 번 만들어두고 시각을 바꿔가며 조회하는 읽기 전용 뷰."""

    def __init__(self, interval: str, start_ts: int, end_ts: int,
                 markets: List[str] = None, db_path: str = None):
        self.interval = interval
        self.start_ts = int(start_ts)
        self.end_ts = int(end_ts)
        self.step = INTERVAL_SECONDS[interval]

        # 전일 종가는 구간 첫날에도 있어야 하니 일봉은 하루 더 앞에서부터 읽는다
        daily = store.load_candles('days', self.start_ts - 3 * 86400, self.end_ts, markets, db_path)
        self._prev_close = self._build_prev_close(daily)

        raw = store.load_candles(interval, self.start_ts, self.end_ts, markets, db_path)
        self._series: Dict[str, dict] = {}
        ts_set = set()
        for market, candles in raw.items():
            series = self._build_series(market, candles)
            if series['ts']:
                self._series[market] = series
                ts_set.update(series['ts'])
        self.timestamps = sorted(ts_set)

    # ---- 준비 ----------------------------------------------------------------

    @staticmethod
    def _build_prev_close(daily: Dict[str, List[dict]]) -> Dict[str, Dict[str, float]]:
        """{종목: {KST날짜: 그날의 전일 종가}}.

        업비트가 일봉에 실어 주는 prev_closing_price를 그대로 쓴다 — 업비트 화면의 등락률과 같은 값을
        쓰기 위해서다. 그 값이 비어 있으면(오래된 종목 등) 직전 일봉의 종가로 대신한다.
        """
        out: Dict[str, Dict[str, float]] = {}
        for market, candles in daily.items():
            by_date: Dict[str, float] = {}
            prev_close = None
            for c in candles:
                date = kst_date(c['ts'])
                value = c['prev_close'] if c['prev_close'] else prev_close
                if value:
                    by_date[date] = float(value)
                prev_close = c['close']
            out[market] = by_date
        return out

    def _build_series(self, market: str, candles: List[dict]) -> dict:
        """종목 하나의 시계열 — 시각별 종가/시가와, 그 시점까지의 누적 거래대금·상승률.

        누적 거래대금은 KST 날짜가 바뀔 때마다 0으로 되돌린다(업비트의 "당일 거래대금"이 KST 00시
        기준이라서). 상승률 계산에 쓸 전일 종가가 없는 구간은 None으로 둬서 상승률 순위에서 빠지게
        한다 — 없는 값을 0으로 채우면 신규 상장 종목이 하위권에 잘못 끼어든다.
        """
        ts_list, open_list, close_list, cum_list, chg_list = [], [], [], [], []
        prev_closes = self._prev_close.get(market, {})
        current_date, cum = None, 0.0
        for c in candles:
            date = kst_date(c['ts'])
            if date != current_date:
                current_date, cum = date, 0.0
            cum += float(c['value'] or 0)
            base = prev_closes.get(date)
            ts_list.append(c['ts'])
            open_list.append(c['open'])
            close_list.append(c['close'])
            cum_list.append(cum)
            chg_list.append((c['close'] - base) / base * 100 if base else None)
        return {
            'ts': ts_list, 'open': open_list, 'close': close_list,
            'cum_value': cum_list, 'change_rate': chg_list,
            'pos': {t: i for i, t in enumerate(ts_list)},
        }

    # ---- 조회 ----------------------------------------------------------------

    @property
    def markets(self) -> List[str]:
        return sorted(self._series.keys())

    def price(self, market: str, ts: int) -> Optional[float]:
        """시각 ts에 닫힌 봉의 종가 — 판단 시점에 볼 수 있는 마지막 값이다."""
        series = self._series.get(market)
        if not series:
            return None
        i = series['pos'].get(ts)
        return series['close'][i] if i is not None else None

    def fill_price(self, market: str, ts: int) -> Optional[float]:
        """체결가 — ts에 판단했으면 실제 주문은 그 다음 봉에서 나가므로 다음 봉의 시가로 채운다.

        종가로 사고파는 걸로 계산하면 "그 봉의 종가를 보고 그 봉의 종가에 샀다"가 되어 미세하게
        미래를 당겨쓴다. 다음 봉이 없으면(구간 끝) 어쩔 수 없이 종가로 청산한다.
        """
        series = self._series.get(market)
        if not series:
            return None
        i = series['pos'].get(ts + self.step)
        if i is not None:
            return series['open'][i]
        return self.price(market, ts)

    def closes_until(self, market: str, ts: int, count: int) -> List[float]:
        """ts까지(포함) 확정된 종가 count개 — RSI 같은 지표 계산용. 미래 봉은 절대 넘기지 않는다."""
        series = self._series.get(market)
        if not series:
            return []
        i = series['pos'].get(ts)
        if i is None:
            return []
        return series['close'][max(0, i - count + 1): i + 1]

    def snapshot(self, ts: int) -> List[dict]:
        """그 시각의 전 종목 상태 — 업비트 순위 화면을 그때로 되감은 것."""
        rows = []
        for market, series in self._series.items():
            i = series['pos'].get(ts)
            if i is None:
                continue
            rows.append({
                'ticker': market,
                'price': series['close'][i],
                'change_rate': series['change_rate'][i],
                'trade_value': series['cum_value'][i],
            })
        return rows

    def rank(self, ts: int, selection: str, limit: int = 10,
             min_trade_value_krw: float = 0) -> List[dict]:
        """그 시각 기준 상위 종목. 상승률 기준일 때 전일 종가가 없는 종목은 제외한다.

        min_trade_value_krw는 유동성 필터다 — 거래대금이 거의 없는 종목은 상승률만 크게 튀는 일이
        잦은데, 호가가 비어 있어 실제로는 그 가격에 살 수 없다. 백테스트에서만 들어오는 가짜 수익을
        막으려면 걸러 주는 게 맞다(기본값 0 = 끄기).
        """
        rows = self.snapshot(ts)
        if min_trade_value_krw:
            rows = [r for r in rows if (r['trade_value'] or 0) >= min_trade_value_krw]
        if selection == SELECT_GAINERS:
            rows = [r for r in rows if r['change_rate'] is not None]
            rows.sort(key=lambda r: r['change_rate'], reverse=True)
        elif selection == SELECT_TRADE_VALUE:
            rows.sort(key=lambda r: r['trade_value'] or 0, reverse=True)
        else:
            raise ValueError(f"알 수 없는 순위 기준: {selection}")
        return rows[:limit]
