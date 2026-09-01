"""자동매매 진입/청산 판단 로직 — 순수 함수만 둔다 (DB/네트워크 직접 접근 없음, 테스트 용이성 목적).

시세 조회는 get_price_fn(ticker) -> float|None 콜백으로 주입받는다(브로커 구현체에 의존하지 않기 위함).
1단계는 업비트 모의매매 전용이라 규칙이 단순하다:
  - 진입: coin_screening_daily에서 걸러진 후보 중 미보유 종목을 고정 금액으로 매수
  - 청산(트레일링 손절 + 연속 확인 + 항상-먼저-물타기):
    1) 익절(평단 대비 +take_profit_pct)은 항상 우선 확인 — 무조건 즉시 매도
    2) 트레일링 손절: 진입가가 아니라 "보유 중 최고가(peak_price)" 대비 하락률이 stop_loss_pct
       이상이면 손절 조건 성립. 이 조건이 stop_loss_confirm_cycles회 연속으로 유지돼야 실제로
       매도한다(1캔들 노이즈로 바로 잘리는 걸 완화) — 그 전까지는 HOLD로 "대기 중" 상태만 기록.
    3) 연속 확인까지 끝났는데 그 포지션이 아직 물타기를 dca_max_count회 다 안 썼으면(종목별
       체크박스와 무관하게 항상), 곧바로 손절하지 않고 평단 대비 -dca_trigger_pct(기본 -10%)까지
       한 번 더 기다린다. 거기 도달하면 매도 대신 "매매기준(포지션당 매수금액)"으로 추가매수
       (DCA_BUY)해서 평단을 낮추고, 트레일링 기준점(peak_price)과 연속 카운트를 리셋해 그 새
       평단 기준으로 손절/익절 판단을 다시 시작한다.
       (물타기는 포지션당 dca_max_count회까지만 — 그 횟수에 도달하면 일반 손절과 동일하게 동작하는
       안전장치. 무제한으로 계속 물타면 하락장에서 손실이 무한정 커질 수 있기 때문)
"""
from dataclasses import dataclass
from typing import Callable, List, Optional


@dataclass
class TradeDecision:
    """매매 판단 1건 (BUY/SELL/HOLD/SKIP/DCA_BUY 전부 감사로그로 남기기 위해 판단 자체를 값으로 표현).

    peak_price/streak: 청산 판단(evaluate_exits)에서만 채워지는, 다음 사이클을 위해 DB에 다시
    저장해야 할 트레일링 손절 추적값. status는 대시보드에 "대기 상태"를 보여주기 위한 값
    (None=평시, 'stop_pending'=손절 조건 연속확인 대기, 'dca_pending'=물타기 트리거 대기)."""
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


def evaluate_exits(positions: List[dict], get_price_fn: Callable[[str], Optional[float]], cfg) -> List[TradeDecision]:
    """보유 포지션마다 익절/트레일링 손절(연속 확인 포함)/물타기 여부를 판단한다.

    물타기는 종목별 dca_enabled 체크박스와 무관하게 항상 먼저 시도한다 — 손절 조건이 연속확인까지
    끝나도, dca_max_count회를 아직 안 썼으면 곧바로 팔지 않고 -dca_trigger_pct까지 한 번 더
    기다렸다가 물탄다. dca_max_count번을 다 쓴 뒤에야(또는 dca_max_count가 0이면 처음부터) 일반
    손절이 적용된다 — "무조건 한 번은 물타 본다"는 정책. dca_enabled 필드는 더 이상 이 판단에
    쓰이지 않는다(대시보드 체크박스는 과거 이력 표시용으로만 남아있을 수 있음)."""
    decisions = []
    for pos in positions:
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

        peak = max(pos.get('peak_price') or avg_price, price)
        drawdown_from_peak_pct = (peak - price) / peak * 100 if peak else 0.0
        pnl_krw = (price - avg_price) * qty
        pnl_pct = (price - avg_price) / avg_price * 100 if avg_price else 0.0

        # ① 익절 — 물타기/트레일링과 무관하게 항상 우선
        if pnl_pct >= cfg.TRADE_TAKE_PROFIT_PCT:
            decisions.append(TradeDecision(
                ticker, 'SELL', reason=f'take_profit({pnl_pct:.2f}%)',
                price=price, qty=qty, pnl_krw=pnl_krw, pnl_pct=pnl_pct,
            ))
            continue

        # ② 트레일링 손절 조건(최고가 대비 하락률) 미충족 — 정상 보유, 연속 카운트 리셋
        if drawdown_from_peak_pct < cfg.TRADE_STOP_LOSS_PCT:
            decisions.append(TradeDecision(
                ticker, 'HOLD', reason=f'pnl {pnl_pct:.2f}% (최고가대비 -{drawdown_from_peak_pct:.2f}%)',
                price=price, qty=qty, pnl_krw=pnl_krw, pnl_pct=pnl_pct,
                peak_price=peak, streak=0,
            ))
            continue

        # ③ 트레일링 손절 조건 충족 — 연속 확인 카운트 증가
        new_streak = streak + 1
        if new_streak < cfg.TRADE_STOP_LOSS_CONFIRM_CYCLES:
            decisions.append(TradeDecision(
                ticker, 'HOLD', reason=f'stop_pending({new_streak}/{cfg.TRADE_STOP_LOSS_CONFIRM_CYCLES}, 최고가대비 -{drawdown_from_peak_pct:.2f}%)',
                price=price, qty=qty, pnl_krw=pnl_krw, pnl_pct=pnl_pct,
                peak_price=peak, streak=new_streak, status='stop_pending',
            ))
            continue

        # ④ 연속 확인까지 끝남 — 아직 dca_max_count에 안 닿았으면(체크박스와 무관) -dca_trigger_pct까지
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


def evaluate_entries(candidates: List[dict], positions: List[dict], cash_balance: float,
                      get_price_fn: Callable[[str], Optional[float]], cfg,
                      condition_watch_tickers: set = None, condition_status_map: dict = None) -> List[TradeDecision]:
    """진입 후보(coin_screening_daily 필터 결과) 중 신규 매수 대상을 판단한다.
    한 사이클 안에서 여러 종목을 연속 매수할 수 있으므로, 판단 도중 보유 종목 수/가상 현금을
    누적 반영해가며 계산한다(실제 체결은 auto_trader.py가 순차 실행).

    condition_watch_tickers: 대시보드에서 "정밀검사" 체크된 티커 집합(entry_conditions.py 참고).
    이 집합에 없는 후보는 기존과 동일하게(추가 제약 없이) 판단한다 — 정밀조건은 opt-in.
    condition_status_map: entry_condition_checker.py가 캐시해둔 {ticker: {passed, detail, checked_at}}.
    watch 대상인데 아직 검사 결과가 없거나(검사 루프가 안 떠 있음) 통과 못 했으면 SKIP — DB 접근은
    호출부(auto_trader.py)에서 이미 끝났고, 여기선 값만 읽는 순수 함수로 유지."""
    decisions = []
    held_tickers = {p['ticker'] for p in positions}
    open_count = len(positions)
    condition_watch_tickers = condition_watch_tickers or set()
    condition_status_map = condition_status_map or {}

    for cand in candidates:
        ticker = cand['ticker']

        if ticker in held_tickers:
            decisions.append(TradeDecision(ticker, 'SKIP', reason='이미 보유 중'))
            continue
        if open_count >= cfg.TRADE_MAX_CONCURRENT_POSITIONS:
            decisions.append(TradeDecision(ticker, 'SKIP', reason='최대 동시보유 종목 수 도달'))
            continue
        if cash_balance < cfg.TRADE_MAX_POSITION_KRW:
            decisions.append(TradeDecision(ticker, 'SKIP', reason='가상 현금 부족'))
            continue

        if ticker in condition_watch_tickers:
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

        base_reason = 'breakout_4h' if cand.get('breakout_4h') else 'near_ma200+above_cloud'
        reason = f'{base_reason}+정밀조건충족' if ticker in condition_watch_tickers else base_reason
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
