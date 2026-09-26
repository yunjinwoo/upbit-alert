"""자동매매 진입/청산 판단 로직 — 순수 함수만 둔다 (DB/네트워크 직접 접근 없음, 테스트 용이성 목적).

시세 조회는 get_price_fn(ticker) -> float|None 콜백으로 주입받는다(브로커 구현체에 의존하지 않기 위함).
1단계는 업비트 모의매매 전용이라 규칙이 단순하다:
  - 진입: coin_screening_daily에서 걸러진 후보 중 미보유 종목을 고정 금액으로 매수
  - 청산(트레일링 손절 + 연속 확인 + 항상-먼저-물타기):
    1) 익절(평단 대비 +take_profit_pct)은 항상 우선 확인 — 무조건 즉시 매도
    2) RSI 과매수 매도(rsi_exit_enabled 켜져 있을 때만): 15분봉 RSI가 rsi_exit_overbought 이상이고
       평단 대비 수익률이 rsi_exit_min_profit_pct 이상이면 즉시 매도 — 손절처럼 연속 확인을 기다리지
       않는다(익절과 동급 우선순위). 수익률 조건이 없던 때는 손실 중에도 RSI만 보고 팔려서(2026-09-24
       CVC -3.7%) 손절이 한 번 더 생기는 꼴이었다.
       RSI 값 자체는 app/core/exit_conditions.py가 계산하고 auto_trader.py가 미리 조회해 rsi_map으로
       넘긴다(evaluate_exits는 순수 함수로 유지하기 위해 여기서 직접 캔들을 조회하지 않음).
    3) 고점 대비 되돌림 익절(trailing_tp_enabled 켜져 있을 때만): 목표 수익률(1)에 못 닿았더라도
       고점 수익률(최고가 기준 평단 대비 수익률)이 trailing_tp_arm_pct 이상 올라간 적이 있고 현재
       수익률이 trailing_tp_floor_pct 이하로 되돌아왔으면 즉시 매도해 이익을 확정한다(연속 확인 없음).
       고점 수익률은 트레일링 손절이 이미 쓰고 있는 peak_price로 계산하므로 별도 추적값이 없다.
       비교 대상인 현재 수익률은 매 사이클 새로 조회한 시세 기준이고, 아직 수익 구간일 때만(현재
       수익률 > 0) 판다 — 루프 주기 사이에 급락해 이미 손실이면 이 조건으로 팔지 않고 아래의
       트레일링 손절/물타기 흐름을 그대로 탄다.
    4) 트레일링 손절: 진입가가 아니라 "보유 중 최고가(peak_price)" 대비 하락률이 stop_loss_pct
       이상이면 손절 조건 성립. 이 조건이 stop_loss_confirm_cycles회 연속으로 유지돼야 실제로
       매도한다(1캔들 노이즈로 바로 잘리는 걸 완화) — 그 전까지는 HOLD로 "대기 중" 상태만 기록.
    5) 연속 확인까지 끝났는데 그 포지션이 아직 물타기를 dca_max_count회 다 안 썼으면(종목별
       체크박스와 무관하게 항상), 곧바로 손절하지 않고 평단 대비 -dca_trigger_pct(기본 -10%)까지
       한 번 더 기다린다. 거기 도달하면 매도 대신 "매매기준(포지션당 매수금액)"으로 추가매수
       (DCA_BUY)해서 평단을 낮추고, 트레일링 기준점(peak_price)과 연속 카운트를 리셋해 그 새
       평단 기준으로 손절/익절 판단을 다시 시작한다.
       (물타기는 포지션당 dca_max_count회까지만 — 그 횟수에 도달하면 일반 손절과 동일하게 동작하는
       안전장치. 무제한으로 계속 물타면 하락장에서 손실이 무한정 커질 수 있기 때문)

짧은 손절 · 긴 수익(tight stop) 모드가 켜져 있으면(cfg.TRADE_TIGHT_STOP_ENABLED) 위 3)~5)가
_evaluate_exit_tight_stop()으로 대체된다 — 손실을 끊는 폭과 수익을 지키는 폭을 분리해서, 아직
수익 전환 전이면 평단 대비 -initial_pct에서 바로 끊고, 고점 수익률이 arm_pct를 넘긴 뒤에는 고점 대비
trail_pct까지(단 본전 아래로는 내려가지 않게) 버틴다. 물타기는 이 모드에서 동작하지 않는다.
기본값은 꺼짐. 설계와 결정 근거는 docs/auto-trade-tight-stop.md.

회복형 분할 물타기(recovery DCA) 모드가 켜져 있으면(cfg.TRADE_RECOVERY_DCA_ENABLED) 위 청산 로직
전체가 _evaluate_exit_recovery()로 대체된다 — 트레일링 손절 없이 "깊은 하락에서 소액 물타기 → 새
평단 조금 위에서 소폭 익절"을 반복하고, 물타기 상한을 다 쓴 뒤에만 소액 손절/시간 하드스톱으로
정리한다. 기본값은 꺼짐. 설계와 결정 근거는 docs/auto-trade-recovery-dca.md.
"""
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, List, Optional


@dataclass
class TradeDecision:
    """매매 판단 1건 (BUY/SELL/HOLD/SKIP/DCA_BUY 전부 감사로그로 남기기 위해 판단 자체를 값으로 표현).

    peak_price/streak: 청산 판단(evaluate_exits)에서만 채워지는, 다음 사이클을 위해 DB에 다시
    저장해야 할 트레일링 손절 추적값. status는 대시보드에 "대기 상태"를 보여주기 위한 값
    (None=평시, 'stop_pending'=손절 조건 연속확인 대기, 'dca_pending'=물타기 트리거 대기,
    'trailing_tp_armed'=고점 수익률이 트리거를 넘어 되돌림 익절 감시 중,
    'tight_watch'=짧은 손절 모드에서 아직 수익 전환 전(짧은 손절선 적용 중),
    'tight_trailing'=짧은 손절 모드에서 고점 수익률이 전환 기준을 넘어 긴 트레일링 적용 중)."""
    ticker: str
    action: str  # 'BUY' / 'SELL' / 'HOLD' / 'SKIP' / 'DCA_BUY'
    reason: str
    price: Optional[float] = None
    qty: Optional[float] = None
    amount_krw: Optional[float] = None
    pnl_krw: Optional[float] = None
    pnl_pct: Optional[float] = None
    peak_price: Optional[float] = None
    streak: Optional[int] = None
    status: Optional[str] = None
    recovery: bool = False       # 회복형 분할 물타기 모드의 판단인지 — 체결 후 갱신할 상태가 달라서
                                 # auto_trader._execute()가 이 플래그로 분기한다(회복형 카운터/쿨다운)
    partial_sell: bool = False   # 전량이 아닌 일부 매도(회복형 소액 손절)인지 — 매도 후 포지션이 남는다


TS_FORMAT = '%Y-%m-%d %H:%M:%S'


def _parse_ts(value) -> Optional[datetime]:
    """DB에 문자열로 저장된 시각('YYYY-MM-DD HH:MM:SS')을 datetime으로. 비었거나 형식이 깨졌으면 None."""
    if not value:
        return None
    try:
        return datetime.strptime(str(value)[:19], TS_FORMAT)
    except ValueError:
        return None


def _minutes_since(value, now: datetime) -> Optional[float]:
    """value(문자열 시각) 이후 지난 시간(분). 파싱 불가/빈 값이면 None — 호출부는 "제한 없음"으로 취급한다."""
    ts = _parse_ts(value)
    return None if ts is None else (now - ts).total_seconds() / 60


def _evaluate_exit_recovery(pos: dict, price: float, cfg, rsi_now: float = None,
                            now: datetime = None) -> TradeDecision:
    """회복형 분할 물타기(recovery DCA) 모드의 청산 판단 1건 — 순수 함수(시각조차 인자로 받음).

    설계/결정 근거는 docs/auto-trade-recovery-dca.md. 요약하면 "깊은 하락에서 소액으로 나눠 물타
    평단을 낮추고, 새 평단 조금 위에서 소폭 익절로 빠져나오는 걸 반복한다". 이 모드에서는 트레일링
    손절(최고가 대비 하락률)이 동작하지 않는다 — 큰 손절을 안 하는 게 이 모드의 전제라서.

    판정 순서(먼저 걸린 게 이김):
      ① 익절 — 기존 익절 기준(더 큰 목표)을 먼저 보고, 없으면 회복형 익절 기준(+5%)으로 전량 매도
      ② RSI 과매수 매도 — 켜져 있어도 "수익 구간"에서만. 손실 구간에서 RSI로 팔면 결국 큰 손절이
         되어 이 모드의 전제가 깨지므로 일부러 제외한다(기존 모드에서는 손익과 무관하게 매도)
      ③ 시간 하드스톱 — 물타기 상한을 다 쓴 뒤 N일이 지나도 손실이면 전량 정리(자본이 무기한 묶이는
         것에 대한 유일한 출구. 0이면 비활성)
      ④ 물타기 — 평단 대비 -트리거% 이하 + 쿨다운 경과 + 횟수·투입액 상한 이내면 소액 추가매수.
         평단이 내려가므로 다음 트리거(-20%)와 익절선(+5%) 모두 새 평단 기준으로 자동 이동한다
      ⑤ 소액 손절 — "물타기를 더 못 하게 된 뒤에만" 동작. 평단 대비 -부분손절% 이하면 보유량의 일부만
         덜어낸다. 물타기 가능 구간에서 같이 돌리면 같은 구간에서 사고 파는 게 겹치므로 상한 소진을
         전제 조건으로 뒀다
      ⑥ 그 외 HOLD — 상한을 다 썼으면 status='recovery_capped'(반등 대기), 아직 남았으면
         'recovery_dca_pending'(물타기 대기)

    트레일링 추적값(peak_price/below_stop_streak)은 이 모드의 판단에 쓰이지 않지만 HOLD마다 계속
    갱신해둔다 — 나중에 이 모드를 끄면 곧바로 트레일링 손절이 이 값을 참조하는데, 멈춰 있던 옛 최고가가
    남아 있으면 모드를 끈 직후에 바로 손절 연속확인이 시작되는 사고가 난다."""
    now = now or datetime.now()
    ticker = pos['ticker']
    qty = pos['qty']
    avg_price = pos['avg_buy_price']
    pnl_krw = (price - avg_price) * qty
    pnl_pct = (price - avg_price) / avg_price * 100 if avg_price else 0.0
    peak = max(pos.get('peak_price') or avg_price, price)

    def sell(reason, sell_qty=None, partial=False):
        return TradeDecision(
            ticker, 'SELL', reason=reason, price=price, qty=sell_qty if sell_qty is not None else qty,
            pnl_krw=pnl_krw, pnl_pct=pnl_pct, recovery=True, partial_sell=partial,
        )

    def hold(reason, status):
        return TradeDecision(
            ticker, 'HOLD', reason=reason, price=price, qty=qty, pnl_krw=pnl_krw, pnl_pct=pnl_pct,
            peak_price=peak, streak=0, status=status, recovery=True,
        )

    # ① 익절 — 기존 익절 기준이 더 높으므로 그쪽이 먼저 걸리면 더 크게 먹고 나온다
    if pnl_pct >= cfg.TRADE_TAKE_PROFIT_PCT:
        return sell(f'take_profit({pnl_pct:.2f}%)')
    recovery_tp = cfg.TRADE_RECOVERY_TAKE_PROFIT_PCT
    if recovery_tp > 0 and pnl_pct >= recovery_tp:
        return sell(f'recovery_take_profit({pnl_pct:.2f}% >= {recovery_tp:.2f}%)')

    # ② RSI 과매수 매도 — 수익 구간에서만(위 docstring 참고). 최소 수익률 설정이 있으면 그 이상에서만
    if rsi_exit_hit(cfg, rsi_now, pnl_pct, floor_pct=0.0):
        return sell(f'rsi_exit(RSI {rsi_now:.1f}>={cfg.TRADE_RSI_EXIT_OVERBOUGHT:.1f}, 평단대비 {pnl_pct:.2f}%)')

    # 물타기 여력 — 횟수와 누적 투입액 둘 다 남아 있어야 한다
    dca_count = pos.get('recovery_dca_count') or 0
    max_count = cfg.TRADE_RECOVERY_DCA_MAX_COUNT
    dca_amount = cfg.TRADE_RECOVERY_DCA_AMOUNT_KRW
    invested = pos.get('total_invested_krw') or 0
    max_invested = cfg.TRADE_RECOVERY_MAX_INVESTED_KRW
    count_left = dca_count < max_count
    budget_left = max_invested <= 0 or invested + dca_amount <= max_invested
    capped = not (count_left and budget_left)

    # 쿨다운/시간 하드스톱의 기준 시각 — 마지막 물타기, 없으면 최초 진입 시점.
    # 상한이 "횟수 소진"으로 찼으면 마지막 물타기 시각이 곧 상한 도달 시각이고, 투입액이 모자라
    # 한 번도 못 물탄 경우엔 진입 시점부터가 이미 상한 도달 상태다.
    last_dca_at = pos.get('last_dca_at') or pos.get('entry_at')

    # ③ 시간 하드스톱 — 상한을 다 쓴 뒤로 N일이 지났는데 여전히 손실이면 정리.
    # 손실이 아닐 때(0% ~ 익절선 사이)는 굳이 팔지 않고 반등을 더 기다린다.
    time_stop_days = getattr(cfg, 'TRADE_RECOVERY_TIME_STOP_DAYS', 0) or 0
    if capped and time_stop_days > 0 and pnl_pct < 0:
        minutes_capped = _minutes_since(last_dca_at, now)
        if minutes_capped is not None and minutes_capped >= time_stop_days * 24 * 60:
            return sell(
                f'recovery_time_stop(상한 소진 후 {minutes_capped / 1440:.1f}일 경과, 평단대비 {pnl_pct:.2f}%)'
            )

    # ④ 물타기 — 평단 대비 -트리거% 이하 + 쿨다운 경과
    trigger_pct = cfg.TRADE_RECOVERY_DCA_TRIGGER_PCT
    cooldown_min = cfg.TRADE_RECOVERY_DCA_COOLDOWN_MIN
    if not capped:
        if pnl_pct > -trigger_pct:
            return hold(
                f'recovery_dca_pending({dca_count + 1}/{max_count}회, 평단대비 {pnl_pct:.2f}%, '
                f'목표 -{trigger_pct:.2f}%)',
                'recovery_dca_pending',
            )
        elapsed_min = _minutes_since(last_dca_at, now)
        if elapsed_min is not None and elapsed_min < cooldown_min:
            return hold(
                f'recovery_dca_cooldown({dca_count + 1}/{max_count}회, 평단대비 {pnl_pct:.2f}%, '
                f'쿨다운 {elapsed_min:.0f}/{cooldown_min}분)',
                'recovery_dca_pending',
            )
        return TradeDecision(
            ticker, 'DCA_BUY',
            reason=f'recovery_dca({dca_count + 1}/{max_count}회, 평단대비 {pnl_pct:.2f}%, {dca_amount:,.0f}원)',
            price=price, amount_krw=dca_amount, pnl_krw=pnl_krw, pnl_pct=pnl_pct,
            peak_price=peak, streak=0, recovery=True,
        )

    # ⑤ 소액 손절 — 물타기를 더 못 하게 된 뒤에만(위 docstring 참고)
    cap_reason = f'{dca_count}/{max_count}회' if not count_left else f'투입 {invested:,.0f}원/{max_invested:,.0f}원'
    partial_pct = getattr(cfg, 'TRADE_RECOVERY_PARTIAL_STOP_PCT', 0) or 0
    if partial_pct > 0 and pnl_pct <= -partial_pct:
        partial_cooldown_min = getattr(cfg, 'TRADE_RECOVERY_PARTIAL_STOP_COOLDOWN_MIN', 0) or 0
        # 직전 소액 손절, 없으면 마지막 물타기(=상한 도달) 시점부터 간격을 센다 — 물탄 직후 곧바로
        # 일부를 되팔지 않게 하기 위함
        last_partial_at = pos.get('last_partial_stop_at') or last_dca_at
        elapsed_min = _minutes_since(last_partial_at, now)
        if elapsed_min is not None and elapsed_min < partial_cooldown_min:
            return hold(
                f'recovery_partial_cooldown(평단대비 {pnl_pct:.2f}%, '
                f'{elapsed_min:.0f}/{partial_cooldown_min}분)',
                'recovery_capped',
            )

        ratio = (getattr(cfg, 'TRADE_RECOVERY_PARTIAL_STOP_RATIO', 0) or 0) / 100
        min_order_krw = getattr(cfg, 'TRADE_MIN_ORDER_KRW', 0) or 0
        position_krw = qty * price
        sell_krw = position_krw * ratio
        # 거래소 최소 주문금액 때문에 너무 작은 조각은 주문 자체가 거부된다 — 최소 주문금액까지
        # 올려서 팔고, 그래도 남는 쪽이 최소 주문금액 미만이면 더는 쪼갤 수 없으니 전량 매도한다
        # (남긴 조각을 다음 번에 팔 수 없어 영원히 정리 못 하는 상태가 되는 걸 막음).
        if sell_krw < min_order_krw:
            sell_krw = min_order_krw
        if position_krw - sell_krw < min_order_krw:
            return sell(
                f'recovery_partial_stop_all(평단대비 {pnl_pct:.2f}%, 남길 수량이 최소주문금액 미만 — 전량, {cap_reason})'
            )
        sell_qty = qty * (sell_krw / position_krw) if position_krw else 0
        return sell(
            f'recovery_partial_stop(평단대비 {pnl_pct:.2f}%, 보유량 {sell_krw / position_krw * 100:.0f}% '
            f'≈ {sell_krw:,.0f}원, {cap_reason})',
            sell_qty=sell_qty, partial=True,
        )

    # ⑥ 상한 소진 — 반등(익절)을 기다리며 계속 보유
    return hold(f'recovery_capped({cap_reason}, 평단대비 {pnl_pct:.2f}%)', 'recovery_capped')


def _evaluate_exit_tight_stop(pos: dict, price: float, peak: float, cfg) -> TradeDecision:
    """짧은 손절 · 긴 수익(tight stop) 모드의 청산 판단 — docs/auto-trade-tight-stop.md.

    기존 로직은 손절폭(cfg.TRADE_STOP_LOSS_PCT) 하나가 "손실을 끊는 폭"과 "수익을 지키는 폭"을
    겸하고 있어서, 짧게 줄이면 오르던 종목도 금방 털리고 넓게 두면 손실이 커졌다. 여기서는 그 둘을
    수익 전환 여부로 갈라서 서로 다른 값을 쓴다.

      · 전환 전(고점 수익률 < arm_pct): 평단 대비 -initial_pct에서 즉시 매도 — "손절은 짧게".
        연속 확인(stop_loss_confirm_cycles)을 기다리지 않는다. 짧게 끊는 게 목적인데 몇 사이클을
        더 기다리면 그만큼 손실이 깊어지기 때문.
      · 전환 후(고점 수익률 >= arm_pct): 고점 대비 trail_pct까지 밀려도 들고 간다 — "수익은 길게".
        단 손절선은 평단(본전) 아래로 내려가지 않는다 — 한 번 arm_pct까지 벌어둔 포지션을 다시
        손실로 돌려보내면 짧은 손절을 둔 의미가 없어진다. 그래서 실제 손절선은
        max(고점×(1-trail_pct), 평단)이고, 고점이 충분히 높아져 트레일링 손절선이 평단 위로
        올라온 뒤부터 그 선이 같이 따라 올라간다.

    물타기(DCA)는 이 모드에서 하지 않는다 — 손실 종목에 원금을 더 넣는 건 "짧은 손절"과 정반대라
    같이 두면 손절이 다시 늘어진다. 익절(take_profit_pct)과 RSI 과매수 매도는 이 모드에서도 앞단에
    그대로 남아 있으므로, 수익을 길게 끌고 가려면 익절 기준을 충분히 높게 두거나 꺼야 한다.
    """
    ticker = pos['ticker']
    qty = pos['qty']
    avg_price = pos['avg_buy_price']
    initial_pct = getattr(cfg, 'TRADE_TIGHT_STOP_INITIAL_PCT', 2.0)
    arm_pct = getattr(cfg, 'TRADE_TIGHT_STOP_ARM_PCT', 5.0)
    trail_pct = getattr(cfg, 'TRADE_TIGHT_STOP_TRAIL_PCT', 8.0)

    pnl_krw = (price - avg_price) * qty
    pnl_pct = (price - avg_price) / avg_price * 100 if avg_price else 0.0
    peak_pnl_pct = (peak - avg_price) / avg_price * 100 if avg_price else 0.0
    drawdown_from_peak_pct = (peak - price) / peak * 100 if peak else 0.0

    # 고점 수익률이 한 번이라도 전환 기준을 넘었으면 트레일링 구간(현재가가 다시 내려와도 유지된다 —
    # peak_price가 보유 중 최고가라 값이 줄지 않기 때문)
    if peak_pnl_pct >= arm_pct:
        trail_stop_price = peak * (1 - trail_pct / 100)
        stop_price = max(trail_stop_price, avg_price)  # 본전 아래로는 손절선을 내리지 않는다
        if price <= stop_price:
            kind = 'trail' if trail_stop_price >= avg_price else 'breakeven'
            return TradeDecision(
                ticker, 'SELL',
                reason=f'tight_{kind}_exit(고점 {peak_pnl_pct:.2f}% → 현재 {pnl_pct:.2f}%, 허용 고점대비 -{trail_pct:.2f}%)',
                price=price, qty=qty, pnl_krw=pnl_krw, pnl_pct=pnl_pct,
            )
        return TradeDecision(
            ticker, 'HOLD',
            reason=f'tight_trailing(고점 {peak_pnl_pct:.2f}%, 현재 {pnl_pct:.2f}%, 고점대비 -{drawdown_from_peak_pct:.2f}%/{trail_pct:.2f}%)',
            price=price, qty=qty, pnl_krw=pnl_krw, pnl_pct=pnl_pct,
            peak_price=peak, streak=0, status='tight_trailing',
        )

    # 아직 전환 전 — 짧은 손절선만 본다(평단 대비)
    if pnl_pct <= -initial_pct:
        return TradeDecision(
            ticker, 'SELL',
            reason=f'tight_stop_loss(평단대비 {pnl_pct:.2f}%, 기준 -{initial_pct:.2f}%)',
            price=price, qty=qty, pnl_krw=pnl_krw, pnl_pct=pnl_pct,
        )
    return TradeDecision(
        ticker, 'HOLD',
        reason=f'tight_watch(평단대비 {pnl_pct:.2f}%, 손절 -{initial_pct:.2f}% / 전환 +{arm_pct:.2f}%)',
        price=price, qty=qty, pnl_krw=pnl_krw, pnl_pct=pnl_pct,
        peak_price=peak, streak=0, status='tight_watch',
    )


class _CfgOverlay:
    """기본 설정(cfg) 위에 일부 값만 덮어쓴 읽기 전용 설정 — 나머지 속성은 기본 설정에서 읽는다."""

    def __init__(self, base, overrides: dict):
        self._base = base
        self._overrides = overrides

    def __getattr__(self, name):
        overrides = self.__dict__.get('_overrides') or {}
        if name in overrides:
            return overrides[name]
        return getattr(self.__dict__['_base'], name)


def rsi_exit_hit(cfg, rsi_now, pnl_pct: float, floor_pct: float = None) -> bool:
    """RSI 과매수 매도 조건 — RSI가 과매수 기준 이상이고 수익률이 최소 수익률 이상일 때만 True.
    최소 수익률은 cfg.TRADE_RSI_EXIT_MIN_PROFIT_PCT, 값이 없으면 floor_pct(None이면 수익률 조건 없음)."""
    if not getattr(cfg, 'TRADE_RSI_EXIT_ENABLED', False):
        return False
    overbought = getattr(cfg, 'TRADE_RSI_EXIT_OVERBOUGHT', None)
    if rsi_now is None or overbought is None or rsi_now < overbought:
        return False
    min_profit = getattr(cfg, 'TRADE_RSI_EXIT_MIN_PROFIT_PCT', None)
    if min_profit is None:
        min_profit = floor_pct
    if floor_pct is not None and min_profit is not None:
        min_profit = max(min_profit, floor_pct)
    return min_profit is None or pnl_pct >= min_profit


def position_cfg(cfg, pos: dict):
    """포지션에 고정된 청산 규칙(exit_rule)이 있으면 그 값을 cfg 위에 덮어쓴 설정을, 없으면 cfg를 그대로 돌려준다.

    exit_rule은 전략 묶음(app/core/strategy_presets.py)을 적용하는 순간 이미 보유 중이던 포지션에 찍히는
    JSON({"TRADE_TIGHT_STOP_INITIAL_PCT": 5.0, ...})이다. 묶음을 바꿨다고 보유 종목이 새 손절선에 바로
    걸려 한꺼번에 팔리지 않게, 산 시점의 청산 규칙을 끝까지 쓰게 하려는 것. 값이 깨졌으면 무시한다."""
    rule = pos.get('exit_rule') if isinstance(pos, dict) else None
    if not rule:
        return cfg
    if isinstance(rule, str):
        try:
            import json
            rule = json.loads(rule)
        except ValueError:
            return cfg
    if not isinstance(rule, dict) or not rule:
        return cfg
    return _CfgOverlay(cfg, rule)


def evaluate_exits(positions: List[dict], get_price_fn: Callable[[str], Optional[float]], cfg,
                    rsi_map: dict = None, now: datetime = None) -> List[TradeDecision]:
    """보유 포지션마다 익절/RSI 과매수 매도/트레일링 손절(연속 확인 포함)/물타기 여부를 판단한다.

    물타기는 종목별 dca_enabled 체크박스와 무관하게 항상 먼저 시도한다 — 손절 조건이 연속확인까지
    끝나도, dca_max_count회를 아직 안 썼으면 곧바로 팔지 않고 -dca_trigger_pct까지 한 번 더
    기다렸다가 물탄다. dca_max_count번을 다 쓴 뒤에야(또는 dca_max_count가 0이면 처음부터) 일반
    손절이 적용된다 — "무조건 한 번은 물타 본다"는 정책. dca_enabled 필드는 더 이상 이 판단에
    쓰이지 않는다(대시보드 체크박스는 과거 이력 표시용으로만 남아있을 수 있음).

    고점 대비 되돌림 익절(cfg.TRADE_TRAILING_TP_*)도 여기서 같이 판단한다 — 고점 수익률은 트레일링
    손절이 쓰는 peak_price로 계산하므로 포지션에 추가 필드가 필요 없다. 이름 그대로 익절이라
    수익 구간에서만 매도하고, 손실로 돌아선 포지션은 기존 손절/물타기 흐름이 처리한다.

    rsi_map: {ticker: RSI값|None} — cfg.TRADE_RSI_EXIT_ENABLED가 켜져 있을 때 auto_trader.py가
    미리 조회해 넘긴다(app/core/exit_conditions.py 참고). 꺼져 있거나 값이 없으면 이 판단은 건너뛴다.

    cfg.TRADE_TIGHT_STOP_ENABLED가 켜져 있으면 익절/RSI 매도 다음부터(되돌림 익절 · 트레일링 손절 ·
    물타기 대신) _evaluate_exit_tight_stop()이 판단한다 — 짧은 손절 · 긴 수익 모드.

    cfg.TRADE_RECOVERY_DCA_ENABLED가 켜져 있으면 포지션마다 아래 판단 대신
    _evaluate_exit_recovery()를 쓴다(회복형 분할 물타기 — 트레일링 손절을 쓰지 않는 별도 모드).

    now: 회복형 모드의 쿨다운/시간 하드스톱이 기준으로 삼을 현재 시각. 실매매에서는 넘기지 않아
    벽시계(datetime.now())를 쓰지만, 백테스트(app/backtest/engine.py)는 과거의 한 시점을 돌리는
    것이라 반드시 그때의 시각을 넘겨야 한다 — 안 넘기면 "오늘"과 비교해 쿨다운이 항상 지난 것으로
    판정된다."""
    decisions = []
    rsi_map = rsi_map or {}
    base_cfg = cfg
    for pos in positions:
        # 전략 묶음을 바꾸기 전에 산 종목은 그때의 청산 규칙(exit_rule)을 그대로 쓴다 — position_cfg() 참고
        cfg = position_cfg(base_cfg, pos)
        recovery_mode = getattr(cfg, 'TRADE_RECOVERY_DCA_ENABLED', False)
        ticker = pos['ticker']
        qty = pos['qty']
        avg_price = pos['avg_buy_price']
        streak = pos.get('below_stop_streak') or 0
        dca_count = pos.get('dca_count') or 0
        dca_max_count = getattr(cfg, 'TRADE_DCA_MAX_COUNT', 1)

        price = get_price_fn(ticker)
        if not price:
            decisions.append(TradeDecision(ticker, 'SKIP', reason='시세 조회 실패'))
            continue

        # 회복형 분할 물타기 모드 — 아래 트레일링 손절/물타기 로직 전체를 대체한다
        # (docs/auto-trade-recovery-dca.md, _evaluate_exit_recovery() docstring 참고)
        if recovery_mode:
            decisions.append(_evaluate_exit_recovery(pos, price, cfg, rsi_now=rsi_map.get(ticker), now=now))
            continue

        peak = max(pos.get('peak_price') or avg_price, price)
        drawdown_from_peak_pct = (peak - price) / peak * 100 if peak else 0.0
        pnl_krw = (price - avg_price) * qty
        pnl_pct = (price - avg_price) / avg_price * 100 if avg_price else 0.0
        # 고점 수익률 — 보유 중 최고가로 평단 대비 수익률을 계산한 값(되돌림 익절 판단용)
        peak_pnl_pct = (peak - avg_price) / avg_price * 100 if avg_price else 0.0
        # 짧은 손절 · 긴 수익 모드 — 되돌림 익절(④)/트레일링 손절(⑤⑥)/물타기(⑦)를 대신한다.
        # 되돌림 익절은 수익을 3~4%에서 끊는 규칙이라 "수익은 길게"와 방향이 반대다. 그래서 두
        # 설정이 동시에 켜져 있어도 이 모드가 이기고, 되돌림 익절은 감시 표시조차 하지 않는다.
        tight_stop_enabled = bool(getattr(cfg, 'TRADE_TIGHT_STOP_ENABLED', False))
        trailing_tp_enabled = bool(getattr(cfg, 'TRADE_TRAILING_TP_ENABLED', False)) and not tight_stop_enabled
        trailing_tp_arm_pct = getattr(cfg, 'TRADE_TRAILING_TP_ARM_PCT', None)
        trailing_tp_floor_pct = getattr(cfg, 'TRADE_TRAILING_TP_FLOOR_PCT', None)
        trailing_tp_armed = bool(
            trailing_tp_enabled and trailing_tp_arm_pct is not None and peak_pnl_pct >= trailing_tp_arm_pct
        )

        # ① 익절 — 물타기/트레일링과 무관하게 항상 우선
        if pnl_pct >= cfg.TRADE_TAKE_PROFIT_PCT:
            decisions.append(TradeDecision(
                ticker, 'SELL', reason=f'take_profit({pnl_pct:.2f}%)',
                price=price, qty=qty, pnl_krw=pnl_krw, pnl_pct=pnl_pct,
            ))
            continue

        # ② RSI 과매수 매도 — RSI가 과매수 기준 이상이고 수익률이 최소 수익률 이상이면 즉시 매도(연속확인 없음).
        # 수익률이 모자라면 RSI는 무시하고 아래 손절/익절 흐름을 그대로 탄다
        rsi_now = rsi_map.get(ticker)
        if rsi_exit_hit(cfg, rsi_now, pnl_pct):
            decisions.append(TradeDecision(
                ticker, 'SELL',
                reason=f'rsi_exit(RSI {rsi_now:.1f}>={cfg.TRADE_RSI_EXIT_OVERBOUGHT:.1f}, 평단대비 {pnl_pct:.2f}%)',
                price=price, qty=qty, pnl_krw=pnl_krw, pnl_pct=pnl_pct,
            ))
            continue

        # ③ 짧은 손절 · 긴 수익 모드 — 아래 ④~⑦(되돌림 익절/트레일링 손절/물타기)를 전부 대신한다
        if tight_stop_enabled:
            decisions.append(_evaluate_exit_tight_stop(pos, price, peak, cfg))
            continue

        # ④ 고점 대비 되돌림 익절 — 목표 수익률에 못 닿았어도 고점 수익률이 arm_pct 이상 올라갔다가
        # 현재 수익률이 floor_pct 이하로 되돌아왔으면 즉시 이익 확정(연속 확인 없음).
        # 단 "익절"이므로 아직 수익 구간(pnl_pct > 0)일 때만 판다 — 루프 주기 사이에 급락해 이미
        # 손실로 돌아섰다면 이 조건으로 팔지 않고 아래의 트레일링 손절/물타기 흐름에 맡긴다
        # (그렇게 하지 않으면 원래 물타기로 버텼을 자리에서 손실 확정 매도가 나가버린다).
        if (trailing_tp_armed and trailing_tp_floor_pct is not None
                and 0 < pnl_pct <= trailing_tp_floor_pct):
            decisions.append(TradeDecision(
                ticker, 'SELL',
                reason=f'trailing_take_profit(고점 {peak_pnl_pct:.2f}% → 현재 {pnl_pct:.2f}%, 기준 {trailing_tp_arm_pct:.2f}%/{trailing_tp_floor_pct:.2f}%)',
                price=price, qty=qty, pnl_krw=pnl_krw, pnl_pct=pnl_pct,
            ))
            continue

        # ⑤ 트레일링 손절 조건(최고가 대비 하락률) 미충족 — 정상 보유, 연속 카운트 리셋
        if drawdown_from_peak_pct < cfg.TRADE_STOP_LOSS_PCT:
            armed_note = f', 되돌림익절 감시중(고점 {peak_pnl_pct:.2f}%)' if trailing_tp_armed else ''
            decisions.append(TradeDecision(
                ticker, 'HOLD',
                reason=f'pnl {pnl_pct:.2f}% (최고가대비 -{drawdown_from_peak_pct:.2f}%{armed_note})',
                price=price, qty=qty, pnl_krw=pnl_krw, pnl_pct=pnl_pct,
                peak_price=peak, streak=0,
                status='trailing_tp_armed' if trailing_tp_armed else None,
            ))
            continue

        # ⑥ 트레일링 손절 조건 충족 — 연속 확인 카운트 증가
        new_streak = streak + 1
        if new_streak < cfg.TRADE_STOP_LOSS_CONFIRM_CYCLES:
            decisions.append(TradeDecision(
                ticker, 'HOLD', reason=f'stop_pending({new_streak}/{cfg.TRADE_STOP_LOSS_CONFIRM_CYCLES}, 최고가대비 -{drawdown_from_peak_pct:.2f}%)',
                price=price, qty=qty, pnl_krw=pnl_krw, pnl_pct=pnl_pct,
                peak_price=peak, streak=new_streak, status='stop_pending',
            ))
            continue

        # ⑦ 연속 확인까지 끝남 — 아직 dca_max_count에 안 닿았으면(체크박스와 무관) -dca_trigger_pct까지
        # 한 번 더 대기, 다 썼으면 손절
        if dca_count < dca_max_count:
            if pnl_pct <= -cfg.TRADE_DCA_TRIGGER_PCT:
                decisions.append(TradeDecision(
                    ticker, 'DCA_BUY', reason=f'dca_buy({dca_count + 1}/{dca_max_count}회, 평단대비 {pnl_pct:.2f}%)',
                    price=price, amount_krw=cfg.TRADE_MAX_POSITION_KRW,
                    pnl_krw=pnl_krw, pnl_pct=pnl_pct, peak_price=peak, streak=new_streak,
                ))
            else:
                decisions.append(TradeDecision(
                    ticker, 'HOLD',
                    reason=f'dca_pending({dca_count + 1}/{dca_max_count}회, 평단대비 {pnl_pct:.2f}%, 목표 -{cfg.TRADE_DCA_TRIGGER_PCT:.2f}%)',
                    price=price, qty=qty, pnl_krw=pnl_krw, pnl_pct=pnl_pct,
                    peak_price=peak, streak=new_streak, status='dca_pending',
                ))
        else:
            decisions.append(TradeDecision(
                ticker, 'SELL', reason=f'stop_loss(최고가대비 -{drawdown_from_peak_pct:.2f}%)',
                price=price, qty=qty, pnl_krw=pnl_krw, pnl_pct=pnl_pct,
            ))
    return decisions


def _hours_since(at, now: datetime) -> Optional[float]:
    """at(문자열 '%Y-%m-%d %H:%M:%S' 또는 datetime)부터 now까지 몇 시간 지났는지. 못 읽으면 None."""
    if isinstance(at, str):
        try:
            at = datetime.strptime(at[:19], '%Y-%m-%d %H:%M:%S')
        except ValueError:
            return None
    if not isinstance(at, datetime):
        return None
    return (now - at).total_seconds() / 3600


def evaluate_entries(candidates: List[dict], positions: List[dict], cash_balance: float,
                      get_price_fn: Callable[[str], Optional[float]], cfg,
                      conditions_active: bool = False, condition_status_map: dict = None,
                      last_sell_at: dict = None, now: datetime = None) -> List[TradeDecision]:
    """진입 후보(coin_screening_daily 필터 결과) 중 신규 매수 대상을 판단한다.
    한 사이클 안에서 여러 종목을 연속 매수할 수 있으므로, 판단 도중 보유 종목 수/가상 현금을
    누적 반영해가며 계산한다(실제 체결은 auto_trader.py가 순차 실행).

    conditions_active: 정밀 매수조건(entry_conditions.py)이 하나라도 켜져 있는지. 켜져 있으면
    후보 전체가 이 게이트를 통과해야 한다 — 예전엔 종목별 "정밀검사" 체크(opt-in)였지만, 조건을
    켜놓고도 종목을 안 골라서 아무 데도 적용되지 않는 일이 잦아 "켜면 전부 적용"으로 바꿨다.
    조건을 하나도 안 켜면 False라 기존과 동일하게(추가 제약 없이) 판단한다.
    condition_status_map: entry_condition_checker.py가 캐시해둔 {ticker: {passed, detail, checked_at}}.
    조건이 켜져 있는데 아직 검사 결과가 없거나(검사 루프가 안 떠 있음) 통과 못 했으면 SKIP — DB 접근은
    호출부(auto_trader.py)에서 이미 끝났고, 여기선 값만 읽는 순수 함수로 유지.
    last_sell_at: {ticker: 마지막 매도 체결 시각('%Y-%m-%d %H:%M:%S' 문자열 또는 datetime)}.
    cfg.TRADE_REENTRY_BLOCK_HOURS가 0보다 크면 그 시간 안에 판 종목은 다시 사지 않는다(SKIP).
    now: 대기 시간 계산 기준 시각 — 백테스트는 과거 시각을 넘겨야 한다(안 넘기면 지금)."""
    decisions = []
    held_tickers = {p['ticker'] for p in positions}
    open_count = len(positions)
    condition_status_map = condition_status_map or {}
    reentry_block_hours = float(getattr(cfg, 'TRADE_REENTRY_BLOCK_HOURS', 0) or 0)
    last_sell_at = last_sell_at or {}
    now = now or datetime.now()

    for cand in candidates:
        ticker = cand['ticker']

        if ticker in held_tickers:
            decisions.append(TradeDecision(ticker, 'SKIP', reason='이미 보유 중'))
            continue
        if reentry_block_hours > 0 and ticker in last_sell_at:
            waited_hours = _hours_since(last_sell_at[ticker], now)
            if waited_hours is not None and waited_hours < reentry_block_hours:
                decisions.append(TradeDecision(
                    ticker, 'SKIP',
                    reason=f'매도 후 재매수 대기({waited_hours:.1f}/{reentry_block_hours:g}시간)',
                ))
                continue
        if open_count >= cfg.TRADE_MAX_CONCURRENT_POSITIONS:
            decisions.append(TradeDecision(ticker, 'SKIP', reason='최대 동시보유 종목 수 도달'))
            continue
        if cash_balance < cfg.TRADE_MAX_POSITION_KRW:
            decisions.append(TradeDecision(ticker, 'SKIP', reason='가상 현금 부족'))
            continue

        if conditions_active:
            status = condition_status_map.get(ticker)
            if not status:
                decisions.append(TradeDecision(ticker, 'SKIP', reason='정밀조건 검사 결과 없음(검사 루프 확인 필요)'))
                continue
            if not status.get('passed'):
                decisions.append(TradeDecision(ticker, 'SKIP', reason='정밀조건 미충족'))
                continue

        price = get_price_fn(ticker)
        if not price:
            decisions.append(TradeDecision(ticker, 'SKIP', reason='시세 조회 실패'))
            continue

        # entry_reason이 실려 있으면 그대로 쓴다 — 스크리닝이 아닌 다른 기준으로 고른 후보
        # (백테스트의 당일 상승률/거래대금 순위 등)가 스크리닝 신호 이름을 뒤집어쓰지 않게 하기 위함.
        base_reason = cand.get('entry_reason') or (
            'breakout_4h' if cand.get('breakout_4h')
            else 'breakout_1d' if cand.get('breakout_1d')
            else 'near_ma200+above_cloud_1d' if cand.get('near_ma200') and cand.get('above_cloud_1d') and not cand.get('above_cloud')
            else 'near_ma200+above_cloud'
        )
        reason = f'{base_reason}+정밀조건충족' if conditions_active else base_reason
        decisions.append(TradeDecision(
            ticker, 'BUY', reason=reason, price=price, amount_krw=cfg.TRADE_MAX_POSITION_KRW,
        ))

        # 다음 후보 판단에 이번 매수가 반영되도록 누적 갱신 (실제 체결 전 근사치)
        cash_balance -= cfg.TRADE_MAX_POSITION_KRW
        open_count += 1
        held_tickers.add(ticker)

    return decisions


def invested_gauge_fields(qty, avg_buy_price, per_position_cap_krw) -> dict:
    """가드레일 1단계 대시보드 표시용 — 이 종목에 지금 묶여 있는 투입원금(평단×수량)과 상한 대비
    비율. 순수 계산만 한다(DB/네트워크 접근 없음). 지금은 표시 전용이라 매매 판단에는 안 쓰인다.
    업비트/토스 get_live_dashboard_summary()가 공통으로 호출한다."""
    cost_basis = (qty or 0) * (avg_buy_price or 0)
    return {
        'cost_basis': cost_basis,
        'invested_ratio': (cost_basis / per_position_cap_krw) if per_position_cap_krw else None,
    }
