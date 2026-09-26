"""업비트 자동매매 1단계 — 모의매매(dry-run) 오케스트레이션.

app/core/upbit_market_analysis.run_coin_screening_loop()와 동일한 패턴(1회 실행 함수 +
while-True 루프 래퍼)을 따른다. 실주문은 절대 발생하지 않는다 — 실제 체결은 전부
PaperBroker(app/core/brokers/paper_broker.py)가 DB 가상 원장에만 반영한다.

python main.py trade 로 독립 프로세스 실행. main.py의 start_all()에는 포함하지 않는다
(알림/모니터링 프로세스와 장애를 격리하기 위함).
"""
import secrets
import time
from datetime import datetime, timedelta
from types import SimpleNamespace

import pyupbit

from app.config import Config
from app.utils.logger import get_logger
from app.utils.slack import send_slack_msg
from app.core.brokers.base import TradeCycleBusyError
from app.core.brokers.paper_broker import PaperBroker
from app.core.brokers.upbit_live_broker import UpbitLiveBroker
from app.core.brokers.upbit_account import get_real_krw_balance
from app.core.strategy_presets import match_preset
from app.core.trade_strategy import evaluate_entries, evaluate_exits, evaluate_accumulation, invested_gauge_fields
from app.core.exit_conditions import compute_rsi
from app.utils.db_manager import (
    get_or_create_paper_account,
    get_paper_positions,
    get_paper_position,
    upsert_paper_position,
    set_position_dca_enabled,
    delete_paper_position,
    get_coin_screening_candidates,
    get_coin_downside_candidates,
    get_downside_watchlist_tickers,
    DOWNSIDE_SIGNAL_KEYS,
    DOWNSIDE_GATE_KEYS,
    ENTRY_SIGNAL_KEYS,
    save_trade_order_log,
    save_job_run_log,
    get_trade_engine_settings,
    set_engine_last_cycle_at,
    get_approved_candidate_tickers,
    get_last_sell_times,
    get_watchlist_tickers,
    get_trade_strategy_settings,
    update_position_tracking,
    mark_position_dca_used,
    record_position_entry_cost,
    backfill_position_cost_basis,
    mark_recovery_dca_used,
    mark_recovery_partial_stop,
    get_condition_status_map,
    get_trade_condition_settings,
    conditions_gate_active,
    try_acquire_trade_cycle_lock,
    release_trade_cycle_lock,
    conditions_enabled,
    get_accumulate_settings,
    get_active_accumulate_tickers,
    get_last_accumulate_buy_times,
)

logger = get_logger()

JOB_NAME = "auto_trade_upbit_paper"
JOB_NAME_LIVE = "auto_trade_upbit_live"

RSI_EXIT_INTERVAL = 'minute15'  # RSI 매도조건은 15분봉 기준(사용자 요청) — 대시보드에서 바꿀 수 없는 고정값


def _candidate_reason(cand: dict) -> str:
    """진입 후보(cand)가 get_coin_screening_candidates()의 어떤 조건으로 뽑혔는지 사람이 읽을 문자열로
    돌려준다 — 대시보드 "근거" 컬럼과 매매 판단 로그(trade_strategy.py)가 공유하는 우선순위.
    한 후보가 여러 조건을 동시에 만족해도 대표 사유 하나만 고른다(우선순위: 돌파 > 200선+구름 조합 > 모멘텀)."""
    if cand.get('breakout_4h'):
        return 'breakout_4h'
    if cand.get('breakout_1d'):
        return 'breakout_1d'
    if cand.get('near_ma200') and cand.get('above_cloud'):
        return 'near_ma200+above_cloud'
    if cand.get('near_ma200') and cand.get('above_cloud_1d'):
        return 'near_ma200+above_cloud_1d'
    return 'momentum_confluence'


def _effective_strategy_config() -> SimpleNamespace:
    """DB에 저장된 매매 전략 파라미터(없으면 app/config.py의 TRADE_* 기본값)를
    trade_strategy.py가 기대하는 속성 이름(TRADE_STOP_LOSS_PCT 등)으로 감싼 네임스페이스를 만든다.
    trade_strategy.py는 DB에 직접 접근하지 않는 순수 함수로 유지하기 위해, DB 조회는 여기(오케스트레이션
    레이어)에서만 하고 evaluate_entries/evaluate_exits에는 이 네임스페이스를 Config 대신 넘긴다."""
    s = get_trade_strategy_settings()
    return SimpleNamespace(
        TRADE_MAX_POSITION_KRW=s['max_position_krw'],
        TRADE_MAX_CONCURRENT_POSITIONS=s['max_concurrent_positions'],
        TRADE_STOP_LOSS_PCT=s['stop_loss_pct'],
        TRADE_TAKE_PROFIT_PCT=s['take_profit_pct'],
        TRADE_STOP_LOSS_CONFIRM_CYCLES=s['stop_loss_confirm_cycles'],
        TRADE_DCA_TRIGGER_PCT=s['dca_trigger_pct'],
        TRADE_DCA_MAX_COUNT=s['dca_max_count'],
        TRADE_RSI_EXIT_ENABLED=s['rsi_exit_enabled'],
        TRADE_RSI_EXIT_PERIOD=s['rsi_exit_period'],
        TRADE_RSI_EXIT_OVERBOUGHT=s['rsi_exit_overbought'],
        TRADE_RSI_EXIT_MIN_PROFIT_PCT=s['rsi_exit_min_profit_pct'],
        TRADE_TRAILING_TP_ENABLED=s['trailing_tp_enabled'],
        TRADE_TRAILING_TP_ARM_PCT=s['trailing_tp_arm_pct'],
        TRADE_TRAILING_TP_FLOOR_PCT=s['trailing_tp_floor_pct'],
        TRADE_TIGHT_STOP_ENABLED=s['tight_stop_enabled'],
        TRADE_TIGHT_STOP_INITIAL_PCT=s['tight_stop_initial_pct'],
        TRADE_TIGHT_STOP_ARM_PCT=s['tight_stop_arm_pct'],
        TRADE_TIGHT_STOP_TRAIL_PCT=s['tight_stop_trail_pct'],
        # 회복형 분할 물타기(docs/auto-trade-recovery-dca.md) — 기본값은 꺼짐.
        # TRADE_MIN_ORDER_KRW는 대시보드에서 바꾸는 값이 아니라 거래소 제약이라 Config에서 직접 읽는다
        # (부분 매도 금액이 이 밑으로 내려가면 쪼개지 않고 전량 매도).
        TRADE_RECOVERY_DCA_ENABLED=s['recovery_dca_enabled'],
        TRADE_RECOVERY_DCA_TRIGGER_PCT=s['recovery_dca_trigger_pct'],
        TRADE_RECOVERY_DCA_AMOUNT_KRW=s['recovery_dca_amount_krw'],
        TRADE_RECOVERY_DCA_COOLDOWN_MIN=s['recovery_dca_cooldown_min'],
        TRADE_RECOVERY_TAKE_PROFIT_PCT=s['recovery_take_profit_pct'],
        TRADE_RECOVERY_DCA_MAX_COUNT=s['recovery_dca_max_count'],
        TRADE_RECOVERY_MAX_INVESTED_KRW=s['recovery_max_invested_krw'],
        TRADE_RECOVERY_TIME_STOP_DAYS=s['recovery_time_stop_days'],
        TRADE_RECOVERY_PARTIAL_STOP_PCT=s['recovery_partial_stop_pct'],
        TRADE_RECOVERY_PARTIAL_STOP_RATIO=s['recovery_partial_stop_ratio'],
        TRADE_RECOVERY_PARTIAL_STOP_COOLDOWN_MIN=s['recovery_partial_stop_cooldown_min'],
        TRADE_MIN_ORDER_KRW=Config.TRADE_MIN_ORDER_KRW,
        TRADE_REENTRY_BLOCK_HOURS=s['reentry_block_hours'],
    )


def _build_rsi_map(tickers, cfg) -> dict:
    """RSI 매도조건(cfg.TRADE_RSI_EXIT_ENABLED)이 켜져 있을 때만 보유 종목들의 15분봉 RSI를 조회해
    {ticker: RSI값|None} 맵을 만든다. evaluate_exits()를 순수 함수로 유지하기 위해(직접 네트워크
    접근 없음) 호출부(run_trade_cycle 등)에서 미리 만들어 넘긴다.

    대상이 "지금 보유 중인 포지션"뿐이라(최대 동시보유 종목수로 제한됨) entry_condition_checker.py처럼
    별도 루프로 뺄 필요 없이 매 사이클 직접 조회해도 API 호출량 부담이 크지 않다."""
    if not cfg.TRADE_RSI_EXIT_ENABLED or not tickers:
        return {}
    period = int(cfg.TRADE_RSI_EXIT_PERIOD)
    count = period * 5 + 5  # EWM 평활이 안정될 만큼 워밍업 여유를 둠
    rsi_map = {}
    for ticker in tickers:
        try:
            df = pyupbit.get_ohlcv(ticker, interval=RSI_EXIT_INTERVAL, count=count)
            rsi_map[ticker] = compute_rsi(df, period)
        except Exception as e:
            logger.error(f"[{ticker}] RSI 매도조건 캔들 조회 실패: {e}")
            rsi_map[ticker] = None
    return rsi_map


def _execute(decision, broker):
    """판단 1건을 실행하고 결과를 trade_order_log에 기록한다. HOLD/SKIP은 주문 없이 기록만 남기되,
    청산 판단(evaluate_exits)에서 나온 HOLD는 다음 사이클을 위한 트레일링 추적값(peak/연속카운트)을
    DB에 갱신해야 한다. 실행 결과(OrderResult, BUY/SELL이 아니면 None)를 반환한다 — 호출부가 "이번
    사이클에 실제로 매도가 체결됐는지" 등을 판단할 때 쓴다(run_trade_cycle의 청산→진입 분리 참고)."""
    result = None
    if decision.action in ('BUY', 'DCA_BUY'):
        result = broker.buy_market(decision.ticker, decision.amount_krw, reason=decision.reason)
    elif decision.action == 'SELL':
        result = broker.sell_market(decision.ticker, decision.qty, reason=decision.reason)

    if result is not None:
        final_decision = decision.action if result.success else 'SKIP'
        reason = decision.reason if result.success else f"{decision.reason} (실패: {result.message})"
        cash_after = broker.get_cash_balance()

        save_trade_order_log(
            broker=broker.broker_name, mode=broker.mode, ticker=decision.ticker,
            decision=final_decision, reason=reason,
            price=result.price, qty=result.qty, amount_krw=result.amount_krw,
            cash_balance_after=cash_after, pnl_krw=decision.pnl_krw, pnl_pct=decision.pnl_pct,
        )

        # 매수 성공 시 누적 투입액/최초 진입가 기록 — 회복형 분할 물타기의 투입 상한 판정에 쓰인다
        # (docs/auto-trade-recovery-dca.md). 회복형 모드를 안 쓰더라도 값은 계속 쌓아둔다 — 나중에 켜는
        # 순간부터 상한 판정이 맞아야 하므로. 실거래 최초 매수는 아직 추적 행이 없어 갱신에 실패할 수
        # 있는데(행은 사이클 끝의 _reconcile_live_positions()가 만든다) 그 경우는 거기서 근사값으로 채운다.
        if decision.action in ('BUY', 'DCA_BUY') and result.success:
            record_position_entry_cost(
                broker.broker_name, broker.mode, decision.ticker,
                amount_krw=result.amount_krw or decision.amount_krw or 0, price=result.price,
            )

        # 물타기 성공 시 횟수/쿨다운 갱신 + 트레일링 기준점(peak)·연속카운트를 새 평단 시점으로 리셋.
        # 실패(현금 부족 등)했으면 카운트를 늘리지 않음 — 다음 사이클에 조건이 유지되면 다시 시도됨.
        # 회복형 물타기는 트리거/금액이 다른 별도 카운터(recovery_dca_count)로 센다.
        if decision.action == 'DCA_BUY' and result.success:
            if decision.recovery:
                mark_recovery_dca_used(broker.broker_name, broker.mode, decision.ticker, new_peak_price=result.price)
            else:
                mark_position_dca_used(broker.broker_name, broker.mode, decision.ticker, new_peak_price=result.price)

        # 회복형 소액 손절(보유량 일부만 매도)은 매도 후에도 포지션이 남는다 — 반복 간격(쿨다운)을
        # 재기 위해 실행 시각을 기록한다. 전량 매도는 추적 행 자체가 사라지므로 기록할 필요가 없다.
        if decision.action == 'SELL' and decision.partial_sell and result.success:
            mark_recovery_partial_stop(broker.broker_name, broker.mode, decision.ticker)

        if result.success and Config.TRADE_SLACK_ALERT:
            side_label = {'BUY': '매수', 'DCA_BUY': '물타기 매수', 'SELL': '매도'}[decision.action]
            # 회복형 모드는 같은 action이라도 성격이 달라서(소액을 나눠 사고, 일부만 덜어냄) 라벨로 구분한다 —
            # Slack만 보고도 "큰 손절이 나간 게 아니라 일부만 덜어냈다"는 걸 알 수 있어야 함
            if decision.recovery:
                if decision.action == 'DCA_BUY':
                    side_label = '회복형 물타기 매수'
                elif decision.action == 'SELL':
                    side_label = '소액 손절 매도(일부)' if decision.partial_sell else '회복형 매도'
            mode_label = '🔴 실거래' if broker.mode == 'live' else '모의매매'
            # 실거래는 주문 직후 체결 확인이 지연되면 price/qty가 아직 비어있을 수 있다
            # (UpbitLiveBroker._wait_for_fill 타임아웃) — 그 경우 상세 수치 없이 접수 사실만 알린다.
            if result.price is not None and result.qty is not None:
                send_slack_msg(
                    f"[{mode_label}] {side_label} {decision.ticker} {result.qty:.6f}개 @ {result.price:,.0f}원 "
                    f"(총 {(result.amount_krw or 0):,.0f}원, {decision.reason})"
                )
            else:
                send_slack_msg(
                    f"[{mode_label}] {side_label} {decision.ticker} 주문 접수됨 (체결 확인 지연 — 총 "
                    f"{(result.amount_krw or 0):,.0f}원, {decision.reason})"
                )
    else:
        # HOLD / SKIP — 실주문 없이 판단 근거만 감사로그로 남김
        save_trade_order_log(
            broker=broker.broker_name, mode=broker.mode, ticker=decision.ticker,
            decision=decision.action, reason=decision.reason,
            price=decision.price, qty=decision.qty, amount_krw=decision.amount_krw,
            cash_balance_after=None, pnl_krw=decision.pnl_krw, pnl_pct=decision.pnl_pct,
        )
        # evaluate_exits에서 나온 HOLD는 peak_price가 채워져 있음(evaluate_entries의 HOLD/SKIP은
        # None) — 다음 사이클 트레일링 손절 판단을 위해 최고가/연속카운트를 DB에 반영
        if decision.action == 'HOLD' and decision.peak_price is not None:
            update_position_tracking(
                broker.broker_name, broker.mode, decision.ticker,
                peak_price=decision.peak_price, below_stop_streak=decision.streak or 0,
            )

    return result


def _reconcile_live_positions(broker) -> None:
    """실거래(mode='live') 전용 — 실제 업비트 잔고 중 "봇이 관리할 종목"만 paper_positions
    테이블(mode='live' 행)과 동기화한다.

    broker.get_positions()는 실계좌에 있는 코인을 전부 반환하지만(승인 여부와 무관), 여기서는
    그중 (a) 현재 실거래 승인 체크된 종목이거나 (b) 이미 이 봇이 사서 추적 중이던 종목만 골라서
    반영한다 — 그래야 사용자가 이 기능과 무관하게 보유 중인 다른 코인들이 실거래 스위치를 켜는
    순간 갑자기 손절/익절 대상이 되는 일을 막을 수 있다(대상 범위는 항상 opt-in).

    paper_positions는 원래 모의매매 가상 원장이지만 (broker, mode, ticker) 단위로 저장되므로,
    트레일링 손절 추적값(peak_price/below_stop_streak)과 물타기 상태(dca_enabled/dca_used/dca_count)를
    실거래에도 그대로 재사용할 수 있다 — qty/avg_buy_price만 매 사이클 실제 잔고로 덮어쓰고
    (upsert_paper_position은 peak_price를 명시하지 않으면 기존 값을 보존한다), 나머지 트래킹 필드는
    건드리지 않는다. 전량 매도돼(수동 매도 포함) 더 이상 안 보이는 종목은 추적 행도 같이 지운다."""
    real_positions = {p.ticker: p for p in broker.get_positions() if p.qty > 0}
    approved_tickers = get_approved_candidate_tickers(broker.broker_name, broker.mode)
    tracked_tickers = {row['ticker'] for row in get_paper_positions(broker.broker_name, broker.mode)}
    # 모아가기 코인은 봇이 팔지 않으므로 추적 행을 만들거나 지우지 않는다(docs/auto-trade-accumulate.md).
    in_scope = (approved_tickers | tracked_tickers) - get_active_accumulate_tickers(broker.broker_name)

    for ticker in in_scope:
        pos = real_positions.get(ticker)
        if pos:
            upsert_paper_position(broker.broker_name, broker.mode, ticker, pos.qty, pos.avg_buy_price)
            # 회복형 분할 물타기 상태(최초 진입가/누적 투입액)가 비어 있는 행만 실제 잔고 기준 근사값으로
            # 채운다 — 이 기능이 생기기 전에 산 종목과, 방금 실거래 최초 매수로 만들어진 행이 대상.
            # 이미 값이 있으면 건드리지 않는다(docs/auto-trade-recovery-dca.md, 함수 docstring 참고).
            backfill_position_cost_basis(
                broker.broker_name, broker.mode, ticker, qty=pos.qty, avg_buy_price=pos.avg_buy_price,
            )
        else:
            delete_paper_position(broker.broker_name, broker.mode, ticker)


def run_trade_cycle(broker=None, trigger_type: str = None) -> dict:
    """1사이클: 보유 포지션 청산 판단 → 진입 후보 매수 판단 → 전부 실행/기록.

    trigger_type을 넘기면(예: 'manual') job_run_log에도 이 사이클 실행을 기록한다(동기화 관리
    페이지에서 확인 가능). run_auto_trade_loop()는 자체적으로 'auto'를 넘겨 기존과 동일하게 기록하고,
    대시보드의 "지금 즉시 실행" 버튼은 'manual'을 넘긴다. None이면(테스트 등) 기록을 생략한다.

    이번 사이클에 실제로 매도가 체결됐으면 진입 판단 자체를 건너뛴다 — 매도로 회수한 현금을
    같은 사이클에 곧바로 다른(또는 같은) 종목 매수에 재투입하지 않도록 하기 위함(자동 루프/수동
    "지금 즉시 실행" 둘 다 동일하게 적용, 아래 sold_this_cycle 참고). 신규 진입은 다음 사이클에
    최신 현금/후보로 다시 판단한다."""
    start_time = datetime.now()
    success = True
    error_message = None
    is_live = False  # finally에서 참조하므로 try 진입 전에 기본값을 잡아둔다(PaperBroker() 생성 자체가
                      # 실패하는 등 broker.mode 접근 전에 예외가 나도 UnboundLocalError로 원래 예외가
                      # 가려지는 걸 막기 위함)
    lock_acquired = False  # 마찬가지로 finally에서 참조 — 락을 실제로 잡았을 때만 해제 시도
    lock_holder = secrets.token_hex(8)
    result = {'exit_decisions': 0, 'entry_decisions': 0, 'entry_skipped_due_to_sell': False}
    try:
        broker = broker or PaperBroker()
        is_live = broker.mode == 'live'

        # 백그라운드 루프(run_live_trade_loop/run_auto_trade_loop, 별도 프로세스)와 대시보드의 "지금
        # 즉시 실행"/강제매수가 같은 broker/mode에 대해 동시에 사이클을 돌리면 서로 stale한 잔고를 보고
        # 중복 매수/dca_count 이중 증가로 이어질 수 있다 — DB 락으로 겹치는 실행을 막는다(자세한 이유는
        # try_acquire_trade_cycle_lock() docstring 참고). force_buy()는 이 락을 거치지 않는 별도
        # 경로라 여전히 겹칠 수 있지만, force_buy 자체엔 이미 체결 확인 재시도가 있어 영향이 제한적이다.
        if not try_acquire_trade_cycle_lock(broker.broker_name, broker.mode, lock_holder):
            raise TradeCycleBusyError(f"{broker.broker_name}/{broker.mode} 사이클이 이미 실행 중입니다 — 이번 실행은 건너뜁니다.")
        lock_acquired = True

        strategy_cfg = _effective_strategy_config()  # 매 사이클마다 새로 읽어 대시보드 설정 변경을 즉시 반영

        if is_live:
            _reconcile_live_positions(broker)  # 실제 잔고 → paper_positions(mode='live') 트래킹 행 동기화

        # 모아가기 코인(켜져 있을 때만)은 일반 매매에서 뺀다 — 자동 매도하지 않고, 일반 진입 후보도 아니며,
        # 최대 보유 종목 수에도 세지 않는다. 매수는 아래 ③에서 따로 판단한다(docs/auto-trade-accumulate.md).
        accumulate_settings = get_accumulate_settings(broker.broker_name)
        accumulate_tickers = set(accumulate_settings['tickers']) if accumulate_settings['enabled'] else set()

        # ① 청산 판단 (손절/익절/RSI 과매수) — 먼저 처리해 현금을 회수한 뒤 진입 판단에 반영
        positions = [p for p in get_paper_positions(broker.broker_name, broker.mode) if p['ticker'] not in accumulate_tickers]
        rsi_map = _build_rsi_map([p['ticker'] for p in positions], strategy_cfg)
        exit_decisions = evaluate_exits(positions, broker.get_current_price, strategy_cfg, rsi_map=rsi_map)
        exit_results = [_execute(decision, broker) for decision in exit_decisions]
        sold_this_cycle = any(
            d.action == 'SELL' and r is not None and r.success
            for d, r in zip(exit_decisions, exit_results)
        )

        if is_live:
            _reconcile_live_positions(broker)  # 방금 청산 실행분을 반영(수량 변화/전량 매도 등)

        # ② 진입 판단 (신규 매수) — 매도가 체결된 사이클이면 건너뛴다(위 docstring 참고).
        entry_decisions = []
        if not sold_this_cycle:
            # 청산 반영된 최신 잔고/포지션으로 재조회
            positions = [p for p in get_paper_positions(broker.broker_name, broker.mode) if p['ticker'] not in accumulate_tickers]
            if is_live:
                account = {'cash_balance': broker.get_cash_balance()}  # 가상 원장이 아니라 실제 KRW 잔고
            else:
                account = get_or_create_paper_account(broker.broker_name, broker.mode, Config.TRADE_INITIAL_CASH_KRW)
            candidates = [c for c in get_coin_screening_candidates() if c['ticker'] not in accumulate_tickers]

            # 대시보드에서 특정 종목만 체크(수동 승인)했다면 그 종목만 진입 대상으로 좁힌다.
            # 모의매매는 아무것도 체크 안 했으면(빈 집합) 기존처럼 전체 후보를 대상으로 하지만,
            # 실거래는 반대로 안전 기본값을 쓴다 — 승인한 종목이 하나도 없으면 후보가 아무리 많아도
            # 절대 매수하지 않는다(opt-in 화이트리스트 필수, "체크 안 했는데 전체 매수"는 실거래에서 금지).
            approved_tickers = get_approved_candidate_tickers(broker.broker_name, broker.mode)
            if approved_tickers:
                candidates = [c for c in candidates if c['ticker'] in approved_tickers]
            elif is_live:
                candidates = []

            # 실거래 이중 안전장치: "매매 대상"(watchlist, 1단계) 체크 없이는 "실거래 승인"(approved,
            # 2단계)이 켜져 있어도 매수하지 않는다. 화면(get_live_dashboard_summary)의 "🔴 실거래" 표
            # 자체가 watchlist로 미리 좁혀 보여주므로 정상 사용 시 굳이 걸릴 일은 없지만, watchlist를
            # 나중에 뺐는데 approved가 남아있는 경우(과거 승인 잔재) 등을 대비한 방어적 재확인.
            if is_live:
                watchlist_tickers = get_watchlist_tickers(broker.broker_name, broker.mode)
                candidates = [c for c in candidates if c['ticker'] in watchlist_tickers]

            # 정밀 매수조건(일봉/5분봉/1분봉) — entry_condition_checker.py가 별도 루프로 캐시해둔 결과만
            # 읽는다(여기서 직접 캔들을 재조회하지 않음). 조건을 하나라도 켜두면 여기까지 온 후보
            # 전체가 이 게이트를 통과해야 한다(예전엔 종목별 "정밀검사" 체크가 필요했다).
            # 단, "관찰 모드"(conditions_observe_only)면 게이트를 걸지 않는다 — 검사와 화면 라벨은
            # 그대로 돌아가고 매수만 평소처럼 진행된다(conditions_gate_active 참고).
            conditions_active = conditions_gate_active(broker.broker_name)
            condition_status_map = get_condition_status_map(broker.broker_name, broker.mode)

            # 매도 후 재매수 대기(기본 꺼짐) — 대기 시간 안에 판 종목의 마지막 매도 시각만 넘긴다.
            last_sell_at = {}
            if strategy_cfg.TRADE_REENTRY_BLOCK_HOURS > 0:
                since = (datetime.now() - timedelta(hours=strategy_cfg.TRADE_REENTRY_BLOCK_HOURS)).strftime('%Y-%m-%d %H:%M:%S')
                last_sell_at = get_last_sell_times(broker.broker_name, broker.mode, since)

            entry_decisions = evaluate_entries(
                candidates, positions, account['cash_balance'], broker.get_current_price, strategy_cfg,
                conditions_active=conditions_active, condition_status_map=condition_status_map,
                last_sell_at=last_sell_at,
            )
            for decision in entry_decisions:
                _execute(decision, broker)

            # ③ 모아가기 — 정밀조건을 통과한 등록 코인을 정해둔 금액만큼 산다(코인별 interval_hours에 1번).
            if accumulate_tickers:
                accumulate_decisions = evaluate_accumulation(
                    accumulate_settings['tickers'], broker.get_cash_balance(),
                    accumulate_settings['amount_krw'], accumulate_settings['interval_hours'],
                    conditions_enabled=conditions_enabled(broker.broker_name),
                    condition_status_map=condition_status_map,
                    last_buy_at=get_last_accumulate_buy_times(broker.broker_name, broker.mode),
                    get_price_fn=broker.get_current_price,
                )
                for decision in accumulate_decisions:
                    _execute(decision, broker)
                entry_decisions = entry_decisions + accumulate_decisions

            if is_live:
                _reconcile_live_positions(broker)  # 방금 신규 매수/물타기 체결분을 트래킹 행에 반영
        else:
            logger.info(
                f"[{broker.broker_name}/{broker.mode}] 이번 사이클에 매도가 체결돼 신규 진입 판단은 "
                "건너뜁니다(다음 사이클에 재평가)."
            )

        result = {
            'exit_decisions': len(exit_decisions),
            'entry_decisions': len(entry_decisions),
            'entry_skipped_due_to_sell': sold_this_cycle,
        }
    except Exception as e:
        success = False
        error_message = str(e)
        raise
    finally:
        # 대시보드의 "마지막 실행/다음 실행 예정" 하트비트 — run_live_trade_loop()의 자동 사이클뿐
        # 아니라 "지금 즉시 실행" 버튼(trigger_type='manual_live')으로 수동 실행했을 때도 "방금 실제로
        # 한 번 처리했다"는 사실을 반영해야 하므로, 루프 쪽이 아니라 여기(모든 실거래 사이클의 공통
        # 종료 지점)에서 기록한다. is_live는 try 진입 전 기본값(False)이 있어 broker 생성 자체가
        # 실패해도 안전하게 False로 남는다(그 경우 broker.mode에 접근할 일이 없으므로 broker가
        # 실제로 무엇이었는지도 문제되지 않는다).
        if is_live:
            set_engine_last_cycle_at(broker.broker_name, broker.mode)

        if trigger_type is not None:
            end_time = datetime.now()
            try:
                save_job_run_log(
                    # is_live 여부와 무관하게 항상 모의매매로 고정 기록되던 버그 수정 — 실거래 사이클도
                    # job_name/description이 paper 사이클과 똑같이 남으면 동기화 관리 페이지에서 실거래
                    # 엔진이 멈춰도 알아챌 방법이 없다.
                    job_name=JOB_NAME_LIVE if is_live else JOB_NAME,
                    description='업비트 실거래 사이클' if is_live else '업비트 모의매매(dry-run) 사이클',
                    api_used='pyupbit(private, 실주문)' if is_live else 'pyupbit(public)',
                    start_time=start_time.strftime('%Y-%m-%d %H:%M:%S'),
                    end_time=end_time.strftime('%Y-%m-%d %H:%M:%S'),
                    success=success,
                    count=result['exit_decisions'] + result['entry_decisions'],
                    error_message=error_message,
                    trigger_type=trigger_type,
                )
            except Exception as e:
                logger.error(f"job_run_log 기록 실패: {e}")

        if lock_acquired:
            release_trade_cycle_lock(broker.broker_name, broker.mode, lock_holder)

    return result


def get_dashboard_summary() -> dict:
    """/auto-trade 대시보드용 읽기 전용 요약 — 계좌/포지션(현재가·평가손익 포함)/최근 로그/진입 후보.
    현재가 조회는 여기서만(웹 요청 처리 중) 수행하며, 매매 판단/주문 실행은 절대 하지 않는다."""
    broker = PaperBroker()
    account = get_or_create_paper_account(broker.broker_name, broker.mode, Config.TRADE_INITIAL_CASH_KRW)
    positions = get_paper_positions(broker.broker_name, broker.mode)

    # 시세 조회 결과를 캐싱해서 아래 evaluate_exits() 미리보기 계산에 재사용(종목당 네트워크 호출 1회로 유지)
    price_cache = {}

    def cached_price(ticker):
        if ticker not in price_cache:
            price_cache[ticker] = broker.get_current_price(ticker)
        return price_cache[ticker]

    for pos in positions:
        price = cached_price(pos['ticker'])
        pos['current_price'] = price
        if price:
            pos['eval_amount'] = price * pos['qty']
            pos['pnl_krw'] = (price - pos['avg_buy_price']) * pos['qty']
            pos['pnl_pct'] = (price - pos['avg_buy_price']) / pos['avg_buy_price'] * 100 if pos['avg_buy_price'] else None
        else:
            pos['eval_amount'] = pos['pnl_krw'] = pos['pnl_pct'] = None

    # 다음 사이클에 실제로 어떤 판단(정상 보유/손절 대기/물타기 대기/손절/익절)이 내려질지 대시보드에
    # 미리 보여주기 위해, 실행 로직과 완전히 같은 evaluate_exits()를 읽기 전용으로 한 번 더 돌린다
    # (순수 함수라 DB/주문에 영향 없음 — 실제 갱신은 run_trade_cycle()의 다음 실행에서만 일어남).
    strategy_cfg = _effective_strategy_config()
    rsi_map = _build_rsi_map([p['ticker'] for p in positions], strategy_cfg)
    preview_by_ticker = {d.ticker: d for d in evaluate_exits(positions, cached_price, strategy_cfg, rsi_map=rsi_map)}
    for pos in positions:
        preview = preview_by_ticker.get(pos['ticker'])
        pos['next_action'] = preview.action if preview else None
        pos['next_reason'] = preview.reason if preview else None
        pos['next_status'] = preview.status if preview else None

    # 매매 대상(진입 후보) — coin_screening_daily 스냅샷 기준, 다음 사이클에 evaluate_entries()가
    # 실제로 보게 될 후보와 동일한 목록. 가격은 스크리닝 시점 값(최대 30분 전)이라 실시간 시세는 아님 —
    # 여기서 후보마다 현재가를 새로 조회하면 종목 수만큼 네트워크 호출이 늘어 페이지가 느려지므로 생략.
    held_tickers = {p['ticker'] for p in positions}
    approved_tickers = get_approved_candidate_tickers(broker.broker_name, broker.mode)
    condition_status_map = get_condition_status_map(broker.broker_name, broker.mode)
    candidates = get_coin_screening_candidates()
    for cand in candidates:
        ticker = cand['ticker']
        cand['already_held'] = ticker in held_tickers
        cand['candidate_reason'] = _candidate_reason(cand)
        cand['approved'] = ticker in approved_tickers
        status = condition_status_map.get(ticker)
        cand['condition_passed'] = status['passed'] if status else None
        cand['condition_detail'] = status['detail'] if status else None
        cand['condition_checked_at'] = status['checked_at'] if status else None

    # 하나라도 체크돼 있으면 "선택 매매 모드"임을 대시보드에 알려주기 위한 플래그
    candidates_filtered = bool(approved_tickers)

    # 매매 판단 로그는 이 요약(폴링될 때마다 재조회되는 API)엔 포함하지 않음 — 감사/이력 조회는
    # 실시간성이 필요 없어서 /auto-trade/logs 별도 페이지(get_trade_order_log_api)로 분리했다.
    # 대시보드가 체크박스 토글 등으로 load()를 재호출할 때마다 100건씩 다시 렌더링되는 부담을 줄임.
    engine_enabled = get_trade_engine_settings()['enabled']
    strategy_settings = get_trade_strategy_settings()
    condition_settings = get_trade_condition_settings()

    # 대시보드 "스크리닝 근거 조건" 안내 카드용 — coin_screening_daily(4시간봉) 필터가 실제로 쓰는
    # 임계값(app/config.py, app/core/upbit_market_analysis.py의 VOL_RATIO_THRESHOLD와 동일 소스).
    # 이 값들은 DB화돼 있지 않은 Config 고정값이라 읽기만 한다(대시보드에서 수정 불가, 참고용).
    screening_thresholds = {
        'breakout_vol_ratio_threshold': Config.UPBIT_THRESHOLDS['minutes240'],
        'breakout_vol_lookback': Config.COIN_BREAKOUT_VOL_LOOKBACK,
        'breakout_rate_threshold': Config.COIN_BREAKOUT_RATE_THRESHOLD,
        'breakout_1d_vol_ratio_threshold': Config.UPBIT_THRESHOLDS['day'],
        'breakout_1d_vol_lookback': Config.COIN_BREAKOUT_VOL_LOOKBACK_1D,
        'breakout_1d_rate_threshold': Config.COIN_BREAKOUT_RATE_THRESHOLD_1D,
        'ma200_near_pct': Config.COIN_MA200_NEAR_PCT,
    }

    # 실제 업비트 계좌 현금 잔고(읽기 전용, 참고용) — 위 account['cash_balance']는 여전히
    # 모의매매용 가상 원장이며, 이 값은 매매 판단/사이징에 전혀 쓰이지 않는다.
    real_krw_balance = get_real_krw_balance()

    return {
        'account': account, 'positions': positions,
        'candidates': candidates, 'candidates_filtered': candidates_filtered,
        'engine_enabled': engine_enabled, 'settings': strategy_settings,
        'conditions': condition_settings, 'screening_thresholds': screening_thresholds,
        'real_krw_balance': real_krw_balance,
    }


def get_live_dashboard_summary() -> dict:
    """/auto-trade 대시보드용 읽기 전용 요약 — 진짜 업비트 계좌(UpbitLiveBroker)를 조회만 하고
    매매 판단/주문 실행은 절대 하지 않는다.

    두 표를 위한 데이터를 함께 반환한다:
      - all_candidates: "🎯 매매 대상 코인" 표(1단계) — 오늘 스크리닝 후보 전체(80개 안팎)에
        watchlist(관심 등록) 체크 상태만 표시. 여기서 관심 등록해야 아래 표에 나타난다.
      - candidates: "🔴 실거래" 표(2단계) — watchlist에 등록된 종목만 담고, 그 안에서 실제로
        보유 중이면 잔고(수량/평단/현재가/평가손익)를, 아직 미보유면 다음 사이클에 매수 판단
        대상이 된다는 걸 알 수 있게 held=False로 표시. approved(실거래 승인)는 이 표에서만 켤 수 있다.

    (모의매매는 더 이상 화면에서 쓰지 않기로 해서 — /auto-trade 페이지에서 관련 섹션을 걷어냄 —
    watchlist/approved 둘 다 이 페이지 단독으로 완결된다. 예전 paper 승인 여부와는 무관하게 동작.)"""
    broker = UpbitLiveBroker()
    cash_balance = broker.get_cash_balance()
    real_positions = {p.ticker: p for p in broker.get_positions() if p.qty > 0}

    approved_tickers = get_approved_candidate_tickers(broker.broker_name, broker.mode)
    watchlist_tickers = get_watchlist_tickers(broker.broker_name, broker.mode)
    # 정밀 매수조건(진입 게이트) — run_trade_cycle()이 진입 판단에서 읽는 것과 같은 broker/mode로
    # 조회해야 화면과 실제 판단이 어긋나지 않는다(entry_condition_checker.py가 이 mode로 채운다).
    condition_status_map = get_condition_status_map(broker.broker_name, broker.mode)
    # 이 봇과 무관하게 보유 중인 다른 코인들(실거래 기능과 상관없이 원래 갖고 있던 코인)까지
    # 화면에 다 나열하면 진짜 매매 대상이 뭔지 헷갈리므로, "승인했거나 이미 봇이 추적 중인" 종목만
    # extra_positions 후보로 본다 — _reconcile_live_positions()가 관리하는 범위와 동일한 기준
    # (watchlist와 무관 — 이미 산 건 관심 등록을 나중에 빼도 계속 손절/익절 관리됨).
    tracked_tickers = {row['ticker'] for row in get_paper_positions(broker.broker_name, broker.mode)}
    # 모아가기 코인은 실거래 표가 아니라 "🪙 모아가기" 카드에만 보여준다(봇이 팔지 않는 종목이라
    # 손절/익절 미리보기가 의미 없음) — run_trade_cycle이 일반 매매에서 빼는 범위와 같다.
    accumulate_settings = get_accumulate_settings(broker.broker_name)
    accumulate_tickers = set(accumulate_settings['tickers']) if accumulate_settings['enabled'] else set()
    in_scope_tickers = (approved_tickers | tracked_tickers) - accumulate_tickers

    # 보유 중인 후보(watchlist 여부와 무관 — 두 표 모두 같은 시세를 재사용하도록 캐싱)의 현재가는
    # 종목당 네트워크 호출 1회로 유지한다.
    price_cache = {}

    def cached_price(ticker):
        if ticker not in price_cache:
            price_cache[ticker] = broker.get_current_price(ticker)
        return price_cache[ticker]

    all_candidates = get_coin_screening_candidates()
    for cand in all_candidates:
        ticker = cand['ticker']
        cand['watchlist'] = ticker in watchlist_tickers
        cand['candidate_reason'] = _candidate_reason(cand)
        # 하락위험 목록과 동일한 방식의 신호 필터 체크박스용 — near_ma200/above_cloud는 실제 진입
        # 조건에서는 "둘 다" 필요하지만, 필터/뱃지 표시는 각각 따로 켜고 끌 수 있게 별개 신호로 노출한다.
        cand['signals'] = [k for k in ENTRY_SIGNAL_KEYS if cand.get(k)]
        pos = real_positions.get(ticker)
        cand['already_held'] = pos is not None
        if pos:
            price = cached_price(ticker)
            cand['pnl_pct'] = (price - pos.avg_buy_price) / pos.avg_buy_price * 100 if price and pos.avg_buy_price else None
        else:
            cand['pnl_pct'] = None

    # 보유 중인 종목의 손절/익절/물타기 추적 상태(peak_price/dca_enabled/dca_count)는 paper_positions
    # 테이블(mode='live')에서 가져온다 — _reconcile_live_positions()/force_buy()가 이 행을 만들어둔다.
    # 여기서 evaluate_exits()를 한 번 더(읽기 전용) 돌려서 다음 사이클에 실제로 어떤 판단이 내려질지
    # 미리 보여준다(순수 함수라 DB/주문에 영향 없음).
    strategy_cfg = _effective_strategy_config()
    strategy_settings = get_trade_strategy_settings(broker.broker_name)  # 아래 응답 필드들이 공유(DB 조회 1회)
    per_position_cap_krw = strategy_settings['per_position_cap_krw']
    tracking_rows = {row['ticker']: row for row in get_paper_positions(broker.broker_name, broker.mode)}

    # 투입원금 게이지 필드: invested_gauge_fields(qty, avg_buy_price, per_position_cap_krw)
    # (app/core/trade_strategy.py — 토스와 공용). 업비트 평단은 그동안의 모든 물타기가 반영된
    # 값이라 평단×수량 = 현재 이 종목에 들어가 있는 실제 원금이다(이미 판 부분은 빠져 있음).

    def _held_extra_fields(ticker, pos):
        tracked = tracking_rows.get(ticker)
        # 회복형 분할 물타기 상태(횟수/누적 투입액/마지막 실행 시각)도 함께 넘긴다 — 빠지면 아래
        # evaluate_exits() 미리보기가 "한 번도 안 물탄 포지션"으로 오해해서 실제 사이클과 다른 판단을
        # 화면에 보여준다(docs/auto-trade-recovery-dca.md).
        return {
            'dca_enabled': bool(tracked['dca_enabled']) if tracked else False,
            'dca_count': tracked['dca_count'] if tracked else 0,
            'peak_price': tracked['peak_price'] if tracked and tracked.get('peak_price') else pos.avg_buy_price,
            'below_stop_streak': tracked['below_stop_streak'] if tracked else 0,
            'entry_at': tracked.get('entry_at') if tracked else None,
            'first_entry_price': tracked.get('first_entry_price') if tracked else None,
            'total_invested_krw': tracked.get('total_invested_krw') if tracked else 0,
            'recovery_dca_count': tracked.get('recovery_dca_count') if tracked else 0,
            'last_dca_at': tracked.get('last_dca_at') if tracked else None,
            'last_partial_stop_at': tracked.get('last_partial_stop_at') if tracked else None,
            'recovery_partial_stop_count': tracked.get('recovery_partial_stop_count') if tracked else 0,
            # 전략 묶음을 바꾸기 전에 산 종목의 고정 청산 규칙 — 빠지면 미리보기가 새 묶음 기준으로 판단한다
            'exit_rule': tracked.get('exit_rule') if tracked else None,
            'exit_rule_locked': bool(tracked.get('exit_rule')) if tracked else False,
        }

    def _condition_fields(ticker):
        """마지막 정밀조건 검사 결과(표의 "정밀검사" 칸에 라벨로 표시). condition_passed가 None이면
        "아직 검사 결과 없음" = 조건이 켜져 있다면 다음 사이클에 SKIP된다는 뜻이라, 화면에서 검사
        루프가 도는지 눈으로 확인할 수 있게 한다. 후보 행과 보유 전용 행(extra_positions)이 같은
        값을 써야 한다 — 한쪽만 채우면 그 행만 다른 상태로 그려진다."""
        status = condition_status_map.get(ticker)
        return {
            'condition_passed': status['passed'] if status else None,
            'condition_detail': status['detail'] if status else None,
            'condition_checked_at': status['checked_at'] if status else None,
        }

    preview_positions = []
    for ticker, pos in real_positions.items():
        if ticker not in in_scope_tickers:
            continue
        extra = _held_extra_fields(ticker, pos)
        preview_positions.append({
            'ticker': ticker, 'qty': pos.qty, 'avg_buy_price': pos.avg_buy_price, **extra,
        })
    rsi_map = _build_rsi_map([p['ticker'] for p in preview_positions], strategy_cfg)
    preview_by_ticker = {d.ticker: d for d in evaluate_exits(preview_positions, cached_price, strategy_cfg, rsi_map=rsi_map)}

    # 매도 후 재매수 대기(기본 꺼짐) — 미보유 행에 "몇 시간 더 기다리는지"를 보여주기 위함.
    # 판정은 run_trade_cycle과 같은 기준(trade_order_log의 마지막 SELL 시각)이다.
    reentry_block_hours = strategy_cfg.TRADE_REENTRY_BLOCK_HOURS or 0
    reentry_last_sell = {}
    if reentry_block_hours > 0:
        since = (datetime.now() - timedelta(hours=reentry_block_hours)).strftime('%Y-%m-%d %H:%M:%S')
        reentry_last_sell = get_last_sell_times(broker.broker_name, broker.mode, since)

    candidates = [c for c in all_candidates if c['ticker'] in watchlist_tickers and c['ticker'] not in accumulate_tickers]
    for cand in candidates:
        ticker = cand['ticker']
        cand['approved'] = ticker in approved_tickers
        cand['reentry_wait_hours'] = None
        if ticker in reentry_last_sell:
            waited = (datetime.now() - datetime.strptime(reentry_last_sell[ticker][:19], '%Y-%m-%d %H:%M:%S')).total_seconds() / 3600
            if waited < reentry_block_hours:
                cand['reentry_wait_hours'] = round(reentry_block_hours - waited, 1)
        cand.update(_condition_fields(ticker))
        pos = real_positions.get(ticker)
        if pos:
            price = cached_price(ticker)
            cand['held'] = True
            cand['qty'] = pos.qty
            cand['avg_buy_price'] = pos.avg_buy_price
            cand['current_price'] = price
            cand['eval_amount'] = price * pos.qty if price else None
            cand['pnl_pct'] = (price - pos.avg_buy_price) / pos.avg_buy_price * 100 if price and pos.avg_buy_price else None
            cand.update(_held_extra_fields(ticker, pos))
            cand.update(invested_gauge_fields(pos.qty, pos.avg_buy_price, per_position_cap_krw))
            preview = preview_by_ticker.get(ticker)
            cand['next_action'] = preview.action if preview else None
            cand['next_status'] = preview.status if preview else None
        else:
            cand['held'] = False
            cand['qty'] = cand['avg_buy_price'] = cand['current_price'] = cand['eval_amount'] = cand['pnl_pct'] = None
            cand['dca_enabled'] = False
            cand['dca_count'] = 0
            cand['cost_basis'] = cand['invested_ratio'] = None
            cand['next_action'] = cand['next_status'] = None

    # 승인했(었)거나 이미 봇이 추적 중인데 오늘 스크리닝 후보 목록엔 없는(예: 예전에 매수해서
    # 계속 보유 중인) 종목도 화면에서 놓치지 않도록 별도로 붙인다. 봇과 무관한 다른 보유 코인은
    # in_scope_tickers에 없으므로 여기 나타나지 않는다.
    candidate_tickers = {c['ticker'] for c in candidates}
    extra_positions = []
    for ticker in in_scope_tickers:
        if ticker in candidate_tickers:
            continue
        pos = real_positions.get(ticker)
        if not pos:
            continue
        price = broker.get_current_price(ticker)
        preview = preview_by_ticker.get(ticker)
        extra_positions.append({
            'ticker': ticker, 'qty': pos.qty, 'avg_buy_price': pos.avg_buy_price,
            'current_price': price, 'eval_amount': price * pos.qty if price else None,
            'pnl_pct': (price - pos.avg_buy_price) / pos.avg_buy_price * 100 if price and pos.avg_buy_price else None,
            'approved': ticker in approved_tickers,
            'next_action': preview.action if preview else None,
            'next_status': preview.status if preview else None,
            **_condition_fields(ticker),
            **_held_extra_fields(ticker, pos),
            **invested_gauge_fields(pos.qty, pos.avg_buy_price, per_position_cap_krw),
        })

    # ── 하락위험 코인 관심목록(Downside Watch) — 진입 후보의 거울상. 표시 전용이라
    # 매매 판단/주문과 완전히 분리돼 있고, evaluate_exits/_reconcile_live_positions는 안 건드린다.
    # docs/auto-trade-downside-watch.md. Phase 2(관심 등록분을 봇이 처리)는 별도.
    downside_watchlist_tickers = get_downside_watchlist_tickers(broker.broker_name, broker.mode)
    downside_candidates = get_coin_downside_candidates()
    for cand in downside_candidates:
        ticker = cand['ticker']
        cand['signals'] = [k for k in DOWNSIDE_SIGNAL_KEYS if cand.get(k)]
        # 정렬용 개수는 gates=True 신호만 센다(rsi_overbought는 상승장에도 떠서 제외).
        cand['signal_count'] = sum(1 for k in DOWNSIDE_GATE_KEYS if cand.get(k))
        cand['downside_watchlist'] = ticker in downside_watchlist_tickers
        pos = real_positions.get(ticker)
        cand['already_held'] = pos is not None
        if pos:
            price = cached_price(ticker)
            cand['pnl_pct'] = (price - pos.avg_buy_price) / pos.avg_buy_price * 100 if price and pos.avg_buy_price else None
        else:
            cand['pnl_pct'] = None
    downside_candidates.sort(key=lambda c: c['signal_count'], reverse=True)  # 동수는 기존 거래대금 순 유지(안정 정렬)

    # ── 모아가기 카드 — 등록 코인별 보유 현황 + 다음 사이클 판단 미리보기(evaluate_accumulation, 순수 함수).
    # 꺼져 있어도 등록해둔 코인과 보유 현황은 보여준다(켜기 전에 확인할 수 있게).
    last_accumulate_buy = get_last_accumulate_buy_times(broker.broker_name, broker.mode)
    accumulate_preview = {d.ticker: d for d in evaluate_accumulation(
        accumulate_settings['tickers'], cash_balance or 0,
        accumulate_settings['amount_krw'], accumulate_settings['interval_hours'],
        conditions_enabled=any(c['enabled'] for c in get_trade_condition_settings(broker.broker_name)),
        condition_status_map=condition_status_map, last_buy_at=last_accumulate_buy,
        get_price_fn=cached_price,
    )}
    accumulate_rows = []
    for ticker in accumulate_settings['tickers']:
        pos = real_positions.get(ticker)
        price = cached_price(ticker)
        preview = accumulate_preview.get(ticker)
        accumulate_rows.append({
            'ticker': ticker,
            'qty': pos.qty if pos else None,
            'avg_buy_price': pos.avg_buy_price if pos else None,
            'current_price': price,
            'eval_amount': price * pos.qty if pos and price else None,
            'pnl_pct': (price - pos.avg_buy_price) / pos.avg_buy_price * 100 if pos and price and pos.avg_buy_price else None,
            'last_buy_at': last_accumulate_buy.get(ticker),
            'next_action': preview.action if preview else None,
            'next_reason': preview.reason if preview else None,
            **_condition_fields(ticker),
        })

    engine_settings = get_trade_engine_settings(broker.broker_name, broker.mode)
    return {
        'accumulate': {**accumulate_settings, 'rows': accumulate_rows},
        'engine_enabled': engine_settings['enabled'],
        'last_cycle_at': engine_settings['last_cycle_at'],
        'loop_interval_sec': get_trade_strategy_settings(broker.broker_name)['loop_interval_sec'],
        'cash_balance': cash_balance,
        'all_candidates': all_candidates,
        'candidates': candidates,
        'extra_positions': extra_positions,
        'downside_candidates': downside_candidates,
        'dca_max_count': strategy_cfg.TRADE_DCA_MAX_COUNT,
        'per_position_cap_krw': per_position_cap_krw,
        # 회복형 분할 물타기가 켜져 있으면 표의 "물타기 n/m" 표시가 세는 카운터 자체가 달라진다
        # (recovery_dca_count/recovery_dca_max_count) — 화면이 어느 쪽을 보여줄지 정할 수 있게 같이 내려준다.
        'recovery_dca_enabled': bool(strategy_cfg.TRADE_RECOVERY_DCA_ENABLED),
        'recovery_dca_max_count': strategy_cfg.TRADE_RECOVERY_DCA_MAX_COUNT,
        # 짧은 손절 · 긴 수익 모드가 켜져 있으면 물타기를 아예 하지 않으므로 표의 "물타기 n/m" 칸과
        # 상태 뱃지가 달라진다(docs/auto-trade-tight-stop.md) — 화면이 구분할 수 있게 같이 내려준다.
        # 지금 설정이 어느 전략 묶음(좋음/애매/나쁨)과 같은지 — 표 위 청산 요약 줄에 같이 보여준다
        'strategy_preset': match_preset(strategy_settings),
        'tight_stop_enabled': bool(getattr(strategy_cfg, 'TRADE_TIGHT_STOP_ENABLED', False)),
        'tight_stop_initial_pct': getattr(strategy_cfg, 'TRADE_TIGHT_STOP_INITIAL_PCT', None),
        'tight_stop_arm_pct': getattr(strategy_cfg, 'TRADE_TIGHT_STOP_ARM_PCT', None),
        'tight_stop_trail_pct': getattr(strategy_cfg, 'TRADE_TIGHT_STOP_TRAIL_PCT', None),
        # 정밀 매수조건 설정(브로커 단위 — mode 구분 없음). 화면에서 조건별 on/off·파라미터를 수정한다.
        'conditions': get_trade_condition_settings(broker.broker_name),
        'condition_check_interval_sec': strategy_settings['condition_check_interval_sec'],
        # 관찰 모드면 조건이 켜져 있어도 매수를 막지 않는다 — 표의 정밀검사 라벨과 요약 문구가
        # "참고용"임을 화면에서 구분해 보여주기 위해 같이 내려준다.
        'conditions_observe_only': bool(strategy_settings['conditions_observe_only']),
    }


def force_buy(ticker: str, broker=None) -> dict:
    """대시보드 "강제 매수" 버튼 — 진입 후보 여부/정밀조건/최대 동시보유 등 모든 필터를 건너뛰고
    "1종목당 매수금액" 만큼 지금 즉시 시장가 매수(모의)한다. 이미 보유 중이면 buy_market이 알아서
    기존 포지션에 합산(평단 재계산)한다. 실패(시세 조회 실패/현금 부족 등)해도 예외를 던지지 않고
    trade_order_log에 SKIP으로 기록 후 결과를 반환한다."""
    broker = broker or PaperBroker()
    settings = get_trade_strategy_settings()
    was_already_held = get_paper_position(broker.broker_name, broker.mode, ticker) is not None
    result = broker.buy_market(ticker, settings['max_position_krw'], reason='강제매수(수동)')

    # 실거래는 buy_market이 실주문만 내고 DB 추적 행(paper_positions)은 안 건드린다(PaperBroker와
    # 달리 — buy_market 자체가 이미 가상 원장인 paper와 구조가 다름). 강제매수는 승인/watchlist
    # 여부와 무관하게 성공할 수 있는데, 추적 행이 없으면 다음 사이클의 _reconcile_live_positions()가
    # "관리 범위 밖"으로 보고 계속 무시해서 손절/익절/물타기가 영원히 안 걸리는 사고가 난다.
    # 그래서 성공 시 여기서 실제 잔고를 다시 조회해 즉시 추적 행을 만든다 — "직접 매수하고
    # 손절/익절/물타기는 자동으로" 워크플로우가 실제로 동작하려면 필수.
    #
    # buy_market은 체결 확인(polling)이 max_wait_sec(업비트 8초) 안에 안 끝나도 주문 자체는 접수됐다고
    # 보고 success=True를 반환한다 — 이 경우 거래소 잔고에도 아직 반영이 안 됐을 수 있으므로, 바로
    # 한 번만 조회해서 못 찾으면 포기하지 않고 짧게 재시도한다. approved_tickers에 없는 종목이면
    # 이 추적 행 생성이 사실상 유일한 진입점이라(다음 사이클 _reconcile_live_positions도 opt-in
    # 범위 밖이라 못 잡음), 여기서 놓치면 물타기/손절/익절이 영원히 안 걸리는 사고로 이어진다.
    if result.success and broker.mode == 'live':
        matched = None
        for attempt in range(5):  # 최대 5회(0,1,2,3초 간격) = 최초 조회 포함 총 ~6초까지 재시도
            for pos in broker.get_positions():
                if pos.ticker == ticker:
                    matched = pos
                    break
            if matched:
                break
            if attempt < 4:
                time.sleep(1.5)
        if matched:
            upsert_paper_position(broker.broker_name, broker.mode, ticker, matched.qty, matched.avg_buy_price)
        else:
            # 재시도 끝에도 잔고에 안 잡힘 — 체결 확인이 유난히 오래 걸리는 케이스. 조용히 넘어가면
            # 이 종목은 영원히 추적 대상 밖으로 남으므로, 최소한 운영자가 알아채고 수동 확인/재시도할
            # 수 있도록 에러 로그와 Slack 알림을 남긴다.
            message = f"[강제매수] {ticker} 주문은 성공했지만 잔고 반영 확인 지연으로 추적 행을 못 만들었습니다 — 수동으로 확인 후 필요시 다시 강제매수하거나 관리자에게 문의하세요."
            logger.error(message)
            send_slack_msg(message)

    # 이미 보유 중인 종목에 강제매수로 평단을 낮췄다면(물타기와 동일한 효과) 트레일링 손절 기준점도
    # 새 평단으로 리셋해야 한다 — 전략이 직접 수행하는 DCA_BUY는 실행 직후 mark_position_dca_used()로
    # peak_price/below_stop_streak을 리셋하는데, force_buy는 _execute()를 거치지 않고 여기서 직접
    # buy_market만 부르기 때문에 그 리셋이 빠져 있었다. 리셋을 안 하면 예: 150원에 사서 peak_price=150인
    # 채로 100원에 강제매수해 평단이 120원으로 낮아져도 peak_price는 여전히 150으로 남아, 방금 평단을
    # 낮춘 직후인데도 "최고가 대비 -33%"로 계산되어 곧바로 손절 연속확인이 시작되는 사고로 이어진다.
    if result.success and was_already_held:
        updated_pos = get_paper_position(broker.broker_name, broker.mode, ticker)
        if updated_pos and updated_pos.get('avg_buy_price'):
            update_position_tracking(
                broker.broker_name, broker.mode, ticker,
                peak_price=updated_pos['avg_buy_price'], below_stop_streak=0,
            )

    # 강제매수로 들어간 돈도 회복형 분할 물타기의 누적 투입액(투입 상한 판정)에 반영해야 한다 —
    # 여기서 빼먹으면 손으로 평단을 낮춘 만큼이 상한 계산에서 사라져, 상한을 이미 넘겼는데도 자동
    # 물타기가 계속 허용된다(docs/auto-trade-recovery-dca.md). 회복형 물타기 "횟수"는 늘리지 않는다 —
    # 수동 매수는 전략이 센 물타기 횟수가 아니므로.
    if result.success:
        record_position_entry_cost(
            broker.broker_name, broker.mode, ticker,
            amount_krw=result.amount_krw or settings['max_position_krw'], price=result.price,
        )

    final_decision = 'BUY' if result.success else 'SKIP'
    reason = result.message if result.success else f"강제매수(수동) 실패: {result.message}"
    cash_after = broker.get_cash_balance()
    save_trade_order_log(
        broker=broker.broker_name, mode=broker.mode, ticker=ticker,
        decision=final_decision, reason=reason,
        price=result.price, qty=result.qty, amount_krw=result.amount_krw,
        cash_balance_after=cash_after, pnl_krw=None, pnl_pct=None,
    )
    return {
        'success': result.success, 'message': result.message,
        'ticker': ticker, 'price': result.price, 'qty': result.qty, 'amount_krw': result.amount_krw,
    }


def force_sell(ticker: str, broker=None) -> dict:
    """대시보드 "강제 매도" 버튼 — 손절/익절 조건과 무관하게 보유 중인 종목을 지금 즉시 전량
    시장가로 매도(모의)한다. 손절 연속확인이나 물타기 대기를 기다리지 않고 바로 정리하고 싶을 때
    쓴다. 보유 중이 아니거나 실패(시세 조회 실패 등)해도 예외를 던지지 않고 trade_order_log에
    SKIP으로 기록 후 결과를 반환한다."""
    broker = broker or PaperBroker()
    position = get_paper_position(broker.broker_name, broker.mode, ticker)
    if not position or not position.get('qty'):
        message = f"{ticker} 보유 포지션이 없습니다."
        save_trade_order_log(
            broker=broker.broker_name, mode=broker.mode, ticker=ticker,
            decision='SKIP', reason=f"강제매도(수동) 실패: {message}",
            price=None, qty=None, amount_krw=None,
            cash_balance_after=broker.get_cash_balance(), pnl_krw=None, pnl_pct=None,
        )
        return {'success': False, 'message': message, 'ticker': ticker, 'price': None, 'qty': None, 'amount_krw': None}

    sell_qty = position['qty']
    avg_buy_price = position['avg_buy_price']
    result = broker.sell_market(ticker, sell_qty, reason='강제매도(수동)')

    pnl_krw = pnl_pct = None
    if result.success and result.price and avg_buy_price:
        pnl_krw = (result.price - avg_buy_price) * (result.qty or sell_qty)
        pnl_pct = (result.price - avg_buy_price) / avg_buy_price * 100

    # PaperBroker.sell_market()은 내부적으로 이미 추적 행(전량 매도 시 삭제, 일부만 체결 시 남은
    # 수량으로 갱신)까지 처리한다. 실거래는 sell_market이 실주문만 내고 추적 행은 안 건드리므로
    # (app/core/brokers/upbit_live_broker.py — buy_market과 동일한 이유) 여기서 실제 잔고를 다시
    # 조회해 반영한다. 체결 확인이 지연될 수 있어(sell_market이 요청한 수량 전부가 아니라 locked를
    # 제외한 일부만 매도했을 수도 있음) force_buy와 동일하게 짧게 재시도한다.
    if result.success and broker.mode == 'live':
        matched = None
        for attempt in range(5):  # 최대 5회(0,1,2,3초 간격) = 최초 조회 포함 총 ~6초까지 재시도
            positions_now = broker.get_positions()
            matched = next((p for p in positions_now if p.ticker == ticker), None)
            if matched is None or matched.qty < sell_qty - 1e-9:
                break  # 잔고에 매도가 반영됨(전량 매도돼 사라졌거나 수량이 줄었음)
            if attempt < 4:
                time.sleep(1.5)
        if matched and matched.qty > 1e-9:
            upsert_paper_position(broker.broker_name, broker.mode, ticker, matched.qty, matched.avg_buy_price)
        else:
            delete_paper_position(broker.broker_name, broker.mode, ticker)

    final_decision = 'SELL' if result.success else 'SKIP'
    reason = result.message if result.success else f"강제매도(수동) 실패: {result.message}"
    cash_after = broker.get_cash_balance()
    save_trade_order_log(
        broker=broker.broker_name, mode=broker.mode, ticker=ticker,
        decision=final_decision, reason=reason,
        price=result.price, qty=result.qty, amount_krw=result.amount_krw,
        cash_balance_after=cash_after, pnl_krw=pnl_krw, pnl_pct=pnl_pct,
    )
    return {
        'success': result.success, 'message': result.message,
        'ticker': ticker, 'price': result.price, 'qty': result.qty, 'amount_krw': result.amount_krw,
    }


def run_auto_trade_loop():
    """⚠️ 폐지됨 — 업비트 모의매매(paper) 자동매매 루프는 더 이상 돌지 않는다.

    이제 업비트는 실거래 2단계(매매 대상 → 실거래 승인)만 쓴다. PR #51에서 화면과 배포 자동시작
    (deploy.yml의 trade-bot)을 제거했지만, 서버에 유령 PM2 프로세스가 남아 5분마다 가상 매매
    이력을 계속 쌓는 문제가 있어 루프 자체를 여기서 막는다. 백엔드(PaperBroker/DB/run_trade_cycle)와
    수동 "지금 즉시 실행", 실거래 루프(run_live_trade_loop)는 영향 없다.

    프로세스는 즉시 종료하지 않고 idle 상태로 유지한다 — 바로 exit하면 PM2 autorestart가 재시작을
    폭주시키므로. 이 프로세스(trade-bot)는 PM2에서 제거해야 한다: pm2 delete trade-bot && pm2 save.
    (다시 켜려면 — 권장하지 않음 — 이 함수를 git 이력에서 복원)"""
    logger.warning(
        "⚠️ 업비트 모의매매(paper) 자동매매 루프는 폐지됨 — idle 유지, 매매/이력 기록 없음. "
        "PM2에서 제거하세요: pm2 delete trade-bot && pm2 save"
    )
    while True:
        time.sleep(3600)


def run_live_trade_loop(interval_sec: int = None):
    """업비트 실거래 자동매매 루프 — python main.py live_trade 로 완전히 별도 프로세스로 실행.
    run_auto_trade_loop()(모의)와 동일한 패턴이지만 broker=UpbitLiveBroker()로 실제 주문을 내고,
    실행 on/off는 별도 스위치(trade_engine_settings, mode='live')로 모의매매와 독립적으로 제어된다."""
    try:
        broker = UpbitLiveBroker()
    except RuntimeError as e:
        logger.error(f"업비트 실거래 엔진을 시작할 수 없습니다: {e}")
        return

    fixed_interval = interval_sec
    logger.info(
        "🔴 업비트 실거래 자동매매 엔진 시작 — 실주문 발생 가능. "
        "대시보드의 '실거래 실행' 스위치와 실거래 승인 체크박스로 제어."
    )

    while True:
        current_interval = fixed_interval or get_trade_strategy_settings()['loop_interval_sec']

        if not get_trade_engine_settings(broker.broker_name, broker.mode)['enabled']:
            logger.info("⏸️ 실거래 엔진 일시중지 상태 — 이번 사이클 건너뜀 (대시보드에서 다시 켤 수 있음)")
            time.sleep(current_interval)
            continue

        try:
            # "마지막 실행" 하트비트는 run_trade_cycle() 자신의 finally에서 기록한다(수동 "지금 즉시
            # 실행" 버튼과 공통 경로로 합치기 위함) — 여기서 따로 또 기록하지 않는다.
            run_trade_cycle(broker=broker, trigger_type='auto_live')
        except Exception as e:
            logger.error(f"실거래 루프 오류: {e}")

        time.sleep(current_interval)


if __name__ == "__main__":
    run_auto_trade_loop()
