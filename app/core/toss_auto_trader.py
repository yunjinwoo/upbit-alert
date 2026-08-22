"""자동매매 — 토스증권(국내주식) 모의매매(dry-run) 오케스트레이션.
app/core/auto_trader.py(업비트)와 완전히 동일한 구조 — 브로커만 TossBroker로, 진입 후보 소스만
stock_screening_daily(app/core/toss_market_analysis.py)로 바꾸고, 모든 설정 조회/저장에
broker='toss'를 넘긴다. evaluate_entries/evaluate_exits(app/core/trade_strategy.py)는 브로커
비의존 순수 함수라 무수정으로 재사용한다. 실주문은 절대 발생하지 않는다 — 실제 체결은 전부
TossBroker(app/core/brokers/toss_broker.py)가 DB 가상 원장에만 반영한다.

python main.py toss_trade 로 독립 프로세스 실행. main.py의 start_all()에는 포함하지 않는다
(업비트 쪽과 동일한 이유로 프로세스 격리).
"""
import time
from datetime import datetime
from types import SimpleNamespace

from app.config import Config
from app.utils.logger import get_logger
from app.utils.slack import send_slack_msg
from app.core.brokers.toss_broker import TossBroker
from app.core.brokers.toss_live_broker import TossLiveBroker
from app.core.trade_strategy import evaluate_entries, evaluate_exits
from app.core import toss_market_analysis
from app.utils.db_manager import (
    get_or_create_paper_account,
    get_paper_positions,
    upsert_paper_position,
    delete_paper_position,
    get_stock_screening_candidates,
    save_trade_order_log,
    save_job_run_log,
    get_trade_engine_settings,
    set_engine_last_cycle_at,
    get_approved_candidate_tickers,
    get_watchlist_tickers,
    get_trade_strategy_settings,
    update_position_tracking,
    mark_position_dca_used,
    get_condition_watch_tickers,
    get_condition_status_map,
    get_trade_condition_settings,
)

logger = get_logger()

JOB_NAME = "auto_trade_toss_paper"
BROKER = "toss"


def _effective_strategy_config() -> SimpleNamespace:
    """app/core/auto_trader._effective_strategy_config()와 동일 — DB(broker='toss')에 저장된
    매매 전략 파라미터를 trade_strategy.py가 기대하는 속성 이름으로 감싼다."""
    s = get_trade_strategy_settings(BROKER)
    return SimpleNamespace(
        TRADE_MAX_POSITION_KRW=s['max_position_krw'],
        TRADE_MAX_CONCURRENT_POSITIONS=s['max_concurrent_positions'],
        TRADE_STOP_LOSS_PCT=s['stop_loss_pct'],
        TRADE_TAKE_PROFIT_PCT=s['take_profit_pct'],
        TRADE_STOP_LOSS_CONFIRM_CYCLES=s['stop_loss_confirm_cycles'],
        TRADE_DCA_TRIGGER_PCT=s['dca_trigger_pct'],
        TRADE_DCA_MAX_COUNT=s['dca_max_count'],
    )


def _execute(decision, broker):
    """app/core/auto_trader._execute()와 동일 로직(판단 1건 실행+감사로그 기록)."""
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

        if decision.action == 'DCA_BUY' and result.success:
            mark_position_dca_used(broker.broker_name, broker.mode, decision.ticker, new_peak_price=result.price)

        if result.success and Config.TRADE_SLACK_ALERT:
            side_label = {'BUY': '매수', 'DCA_BUY': '물타기 매수', 'SELL': '매도'}[decision.action]
            mode_label = '🔴 토스 실거래' if broker.mode == 'live' else '토스 모의매매'
            # 실거래는 주문 직후 체결 확인이 지연되면 price/qty가 아직 비어있을 수 있다
            # (TossLiveBroker._wait_for_fill 타임아웃) — 그 경우 상세 수치 없이 접수 사실만 알린다.
            if result.price is not None and result.qty is not None:
                send_slack_msg(
                    f"[{mode_label}] {side_label} {decision.ticker} {result.qty:.0f}주 @ {result.price:,.0f}원 "
                    f"(총 {(result.amount_krw or 0):,.0f}원, {decision.reason})"
                )
            else:
                send_slack_msg(
                    f"[{mode_label}] {side_label} {decision.ticker} 주문 접수됨 (체결 확인 지연 — 총 "
                    f"{(result.amount_krw or 0):,.0f}원, {decision.reason})"
                )
    else:
        save_trade_order_log(
            broker=broker.broker_name, mode=broker.mode, ticker=decision.ticker,
            decision=decision.action, reason=decision.reason,
            price=decision.price, qty=decision.qty, amount_krw=decision.amount_krw,
            cash_balance_after=None, pnl_krw=decision.pnl_krw, pnl_pct=decision.pnl_pct,
        )
        if decision.action == 'HOLD' and decision.peak_price is not None:
            update_position_tracking(
                broker.broker_name, broker.mode, decision.ticker,
                peak_price=decision.peak_price, below_stop_streak=decision.streak or 0,
            )

    return result


def _reconcile_live_positions(broker) -> None:
    """app/core/auto_trader._reconcile_live_positions()와 동일 — 실거래(mode='live') 전용, 실제
    토스 계좌 보유 종목 중 (a) 실거래 승인됐거나 (b) 이미 이 봇이 추적 중이던 종목만 골라
    paper_positions(mode='live') 트래킹 행에 동기화한다(watchlist를 나중에 빼도 이미 산 건 계속 추적)."""
    real_positions = {p.ticker: p for p in broker.get_positions() if p.qty > 0}
    approved_tickers = get_approved_candidate_tickers(broker.broker_name, broker.mode)
    tracked_tickers = {row['ticker'] for row in get_paper_positions(broker.broker_name, broker.mode)}
    in_scope = approved_tickers | tracked_tickers

    for ticker in in_scope:
        pos = real_positions.get(ticker)
        if pos:
            upsert_paper_position(broker.broker_name, broker.mode, ticker, pos.qty, pos.avg_buy_price)
        else:
            delete_paper_position(broker.broker_name, broker.mode, ticker)


def run_trade_cycle(broker=None, trigger_type: str = None) -> dict:
    """app/core/auto_trader.run_trade_cycle()과 동일 — 1사이클: 청산 판단 → 진입 판단 → 실행/기록.
    broker.mode == 'live'면 실제 계좌 잔고를 트래킹 행에 동기화하고, 승인(approved) + 관심등록
    (watchlist) 둘 다 켜진 종목만 신규 진입 대상으로 삼는다(업비트 실거래와 동일한 이중 안전장치).

    이번 사이클에 실제로 매도가 체결됐으면 진입 판단 자체를 건너뛴다(app/core/auto_trader.
    run_trade_cycle과 동일 — 매도로 회수한 현금을 같은 사이클에 곧바로 재투입하지 않도록)."""
    start_time = datetime.now()
    success = True
    error_message = None
    result = {'exit_decisions': 0, 'entry_decisions': 0, 'entry_skipped_due_to_sell': False}
    try:
        broker = broker or TossBroker()
        is_live = broker.mode == 'live'
        strategy_cfg = _effective_strategy_config()

        if is_live:
            _reconcile_live_positions(broker)

        # ① 청산 판단 (손절/익절)
        positions = get_paper_positions(broker.broker_name, broker.mode)
        exit_decisions = evaluate_exits(positions, broker.get_current_price, strategy_cfg)
        exit_results = [_execute(decision, broker) for decision in exit_decisions]
        sold_this_cycle = any(
            d.action == 'SELL' and r is not None and r.success
            for d, r in zip(exit_decisions, exit_results)
        )

        if is_live:
            _reconcile_live_positions(broker)

        # ② 진입 판단 (신규 매수) — 매도가 체결된 사이클이면 건너뛴다(위 docstring 참고).
        entry_decisions = []
        if not sold_this_cycle:
            positions = get_paper_positions(broker.broker_name, broker.mode)
            if is_live:
                account = {'cash_balance': broker.get_cash_balance()}
            else:
                account = get_or_create_paper_account(broker.broker_name, broker.mode, Config.TRADE_INITIAL_CASH_KRW)
            candidates = get_stock_screening_candidates()

            # 실거래는 승인한 종목이 하나도 없으면 절대 매수하지 않는다(opt-in 화이트리스트 필수).
            # 모의매매는 기존처럼 아무것도 안 켰으면 전체 후보를 대상으로 한다.
            approved_tickers = get_approved_candidate_tickers(broker.broker_name, broker.mode)
            if approved_tickers:
                candidates = [c for c in candidates if c['ticker'] in approved_tickers]
            elif is_live:
                candidates = []

            # 실거래 이중 안전장치: "매매 대상"(watchlist, 1단계) 없이는 "실거래 승인"(2단계)이 켜져
            # 있어도 매수하지 않는다(app/core/auto_trader.run_trade_cycle과 동일한 방어적 재확인).
            if is_live:
                watchlist_tickers = get_watchlist_tickers(broker.broker_name, broker.mode)
                candidates = [c for c in candidates if c['ticker'] in watchlist_tickers]

            condition_watch_tickers = get_condition_watch_tickers(broker.broker_name, broker.mode)
            condition_status_map = get_condition_status_map(broker.broker_name, broker.mode)

            entry_decisions = evaluate_entries(
                candidates, positions, account['cash_balance'], broker.get_current_price, strategy_cfg,
                condition_watch_tickers=condition_watch_tickers, condition_status_map=condition_status_map,
            )
            for decision in entry_decisions:
                _execute(decision, broker)

            if is_live:
                _reconcile_live_positions(broker)
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
        # 아니라 "지금 즉시 실행" 버튼(trigger_type='manual_live')으로 수동 실행했을 때도 반영되도록
        # 루프 쪽이 아니라 여기(모든 실거래 사이클의 공통 종료 지점)에서 기록한다.
        if is_live:
            set_engine_last_cycle_at(BROKER, 'live')

        if trigger_type is not None:
            end_time = datetime.now()
            try:
                save_job_run_log(
                    job_name=JOB_NAME,
                    description='토스증권 모의매매(dry-run) 사이클',
                    api_used='toss openapi',
                    start_time=start_time.strftime('%Y-%m-%d %H:%M:%S'),
                    end_time=end_time.strftime('%Y-%m-%d %H:%M:%S'),
                    success=success,
                    count=result['exit_decisions'] + result['entry_decisions'],
                    error_message=error_message,
                    trigger_type=trigger_type,
                )
            except Exception as e:
                logger.error(f"job_run_log 기록 실패: {e}")

    return result


def get_dashboard_summary() -> dict:
    """app/core/auto_trader.get_dashboard_summary()와 동일 — /toss-trade 대시보드용 읽기 전용 요약."""
    broker = TossBroker()
    account = get_or_create_paper_account(broker.broker_name, broker.mode, Config.TRADE_INITIAL_CASH_KRW)
    positions = get_paper_positions(broker.broker_name, broker.mode)

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

    strategy_cfg = _effective_strategy_config()
    preview_by_ticker = {d.ticker: d for d in evaluate_exits(positions, cached_price, strategy_cfg)}
    for pos in positions:
        preview = preview_by_ticker.get(pos['ticker'])
        pos['next_action'] = preview.action if preview else None
        pos['next_reason'] = preview.reason if preview else None
        pos['next_status'] = preview.status if preview else None

    held_tickers = {p['ticker'] for p in positions}
    approved_tickers = get_approved_candidate_tickers(broker.broker_name, broker.mode)
    condition_watch_tickers = get_condition_watch_tickers(broker.broker_name, broker.mode)
    condition_status_map = get_condition_status_map(broker.broker_name, broker.mode)
    candidates = get_stock_screening_candidates()
    for cand in candidates:
        ticker = cand['ticker']
        cand['already_held'] = ticker in held_tickers
        cand['candidate_reason'] = 'breakout_1d' if cand.get('breakout_1d') else 'near_ma200+above_cloud'
        cand['approved'] = ticker in approved_tickers
        cand['condition_watch'] = ticker in condition_watch_tickers
        status = condition_status_map.get(ticker)
        cand['condition_passed'] = status['passed'] if status else None
        cand['condition_detail'] = status['detail'] if status else None
        cand['condition_checked_at'] = status['checked_at'] if status else None

    candidates_filtered = bool(approved_tickers)

    engine_settings = get_trade_engine_settings(BROKER)
    engine_enabled = engine_settings['enabled']
    strategy_settings = get_trade_strategy_settings(BROKER)
    condition_settings = get_trade_condition_settings(BROKER)

    # 대시보드 "스크리닝 근거 조건" 안내 카드용 — app/core/toss_market_analysis.py가 실제로 쓰는 임계값.
    screening_thresholds = {
        'breakout_vol_ratio_threshold': toss_market_analysis.BREAKOUT_VOL_RATIO_THRESHOLD,
        'breakout_vol_lookback': toss_market_analysis.BREAKOUT_VOL_LOOKBACK,
        'breakout_rate_threshold': toss_market_analysis.BREAKOUT_RATE_THRESHOLD,
        'ma200_near_pct': Config.COIN_MA200_NEAR_PCT,
    }

    return {
        'account': account, 'positions': positions,
        'candidates': candidates, 'candidates_filtered': candidates_filtered,
        'engine_enabled': engine_enabled, 'last_cycle_at': engine_settings['last_cycle_at'],
        'settings': strategy_settings,
        'conditions': condition_settings, 'screening_thresholds': screening_thresholds,
    }


def get_live_dashboard_summary() -> dict:
    """app/core/auto_trader.get_live_dashboard_summary()와 동일 — /toss-trade 대시보드 "🔴 실거래"
    표용 읽기 전용 요약. 진짜 토스 계좌(TossLiveBroker)를 조회만 하고 매매 판단/주문 실행은
    절대 하지 않는다.

    두 표를 위한 데이터를 함께 반환한다:
      - all_candidates: "🎯 매매 대상 종목" 표(1단계) — 오늘 스크리닝 후보 전체에 watchlist
        (관심 등록) 체크 상태만 표시. 여기서 관심 등록해야 아래 표에 나타난다.
      - candidates: "🔴 실거래" 표(2단계) — watchlist에 등록된 종목만 담고, 보유 중이면 잔고
        (수량/평단/현재가/평가손익)를, 미보유면 다음 사이클 매수 판단 대상임을 held=False로 표시.
        approved(실거래 승인)는 이 표에서만 켤 수 있다."""
    broker = TossLiveBroker()
    cash_balance = broker.get_cash_balance()
    real_positions = {p.ticker: p for p in broker.get_positions() if p.qty > 0}

    approved_tickers = get_approved_candidate_tickers(broker.broker_name, broker.mode)
    watchlist_tickers = get_watchlist_tickers(broker.broker_name, broker.mode)
    tracked_tickers = {row['ticker'] for row in get_paper_positions(broker.broker_name, broker.mode)}
    in_scope_tickers = approved_tickers | tracked_tickers

    price_cache = {}

    def cached_price(ticker):
        if ticker not in price_cache:
            price_cache[ticker] = broker.get_current_price(ticker)
        return price_cache[ticker]

    all_candidates = get_stock_screening_candidates()
    for cand in all_candidates:
        ticker = cand['ticker']
        cand['watchlist'] = ticker in watchlist_tickers
        cand['candidate_reason'] = 'breakout_1d' if cand.get('breakout_1d') else 'near_ma200+above_cloud'
        pos = real_positions.get(ticker)
        cand['already_held'] = pos is not None
        if pos:
            price = cached_price(ticker)
            cand['pnl_pct'] = (price - pos.avg_buy_price) / pos.avg_buy_price * 100 if price and pos.avg_buy_price else None
        else:
            cand['pnl_pct'] = None

    strategy_cfg = _effective_strategy_config()
    tracking_rows = {row['ticker']: row for row in get_paper_positions(broker.broker_name, broker.mode)}

    def _held_extra_fields(ticker, pos):
        tracked = tracking_rows.get(ticker)
        return {
            'dca_enabled': bool(tracked['dca_enabled']) if tracked else False,
            'dca_count': tracked['dca_count'] if tracked else 0,
            'peak_price': tracked['peak_price'] if tracked and tracked.get('peak_price') else pos.avg_buy_price,
            'below_stop_streak': tracked['below_stop_streak'] if tracked else 0,
        }

    preview_positions = []
    for ticker, pos in real_positions.items():
        if ticker not in in_scope_tickers:
            continue
        extra = _held_extra_fields(ticker, pos)
        preview_positions.append({
            'ticker': ticker, 'qty': pos.qty, 'avg_buy_price': pos.avg_buy_price, **extra,
        })
    preview_by_ticker = {d.ticker: d for d in evaluate_exits(preview_positions, cached_price, strategy_cfg)}

    candidates = [c for c in all_candidates if c['ticker'] in watchlist_tickers]
    for cand in candidates:
        ticker = cand['ticker']
        cand['approved'] = ticker in approved_tickers
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
            preview = preview_by_ticker.get(ticker)
            cand['next_action'] = preview.action if preview else None
            cand['next_status'] = preview.status if preview else None
        else:
            cand['held'] = False
            cand['qty'] = cand['avg_buy_price'] = cand['current_price'] = cand['eval_amount'] = cand['pnl_pct'] = None
            cand['dca_enabled'] = False
            cand['dca_count'] = 0
            cand['next_action'] = cand['next_status'] = None

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
            **_held_extra_fields(ticker, pos),
        })

    engine_settings = get_trade_engine_settings(broker.broker_name, broker.mode)
    return {
        'engine_enabled': engine_settings['enabled'],
        'last_cycle_at': engine_settings['last_cycle_at'],
        'loop_interval_sec': get_trade_strategy_settings(broker.broker_name)['loop_interval_sec'],
        'cash_balance': cash_balance,
        'all_candidates': all_candidates,
        'candidates': candidates,
        'extra_positions': extra_positions,
        'dca_max_count': strategy_cfg.TRADE_DCA_MAX_COUNT,
    }


def force_buy(ticker: str, broker=None) -> dict:
    """app/core/auto_trader.force_buy()와 동일 — 대시보드 "강제 매수" 버튼."""
    broker = broker or TossBroker()
    settings = get_trade_strategy_settings(BROKER)
    result = broker.buy_market(ticker, settings['max_position_krw'], reason='강제매수(수동)')

    # 실거래는 buy_market이 실주문만 내고 DB 추적 행(paper_positions)은 안 건드린다 — 성공 시
    # 여기서 실제 잔고를 다시 조회해 즉시 추적 행을 만든다(app/core/auto_trader.force_buy와 동일 이유:
    # 추적 행이 없으면 다음 사이클의 _reconcile_live_positions()가 "관리 범위 밖"으로 보고 무시함).
    if result.success and broker.mode == 'live':
        for pos in broker.get_positions():
            if pos.ticker == ticker:
                upsert_paper_position(broker.broker_name, broker.mode, ticker, pos.qty, pos.avg_buy_price)
                break

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


def run_auto_trade_loop(interval_sec: int = None):
    fixed_interval = interval_sec
    logger.info(
        f"🤖 토스증권 모의매매(dry-run) 엔진 시작 — 실주문 없음. "
        f"초기자본 {Config.TRADE_INITIAL_CASH_KRW:,.0f}원. 매매 기준은 대시보드에서 실시간 조정 가능."
    )

    while True:
        current_interval = fixed_interval or get_trade_strategy_settings(BROKER)['loop_interval_sec']

        if not get_trade_engine_settings(BROKER)['enabled']:
            logger.info("⏸️ 토스 자동매매 엔진 일시중지 상태 — 이번 사이클 건너뜀 (대시보드에서 다시 켤 수 있음)")
            time.sleep(current_interval)
            continue

        try:
            run_trade_cycle(trigger_type='auto')
        except Exception as e:
            logger.error(f"토스 자동매매 루프 오류: {e}")
        finally:
            set_engine_last_cycle_at(BROKER)  # mode 기본값 'paper' — TossBroker.mode와 동일

        time.sleep(current_interval)


def run_live_trade_loop(interval_sec: int = None):
    """토스증권 실거래 자동매매 루프 — python main.py toss_live_trade 로 완전히 별도 프로세스로
    실행. app/core/auto_trader.run_live_trade_loop()(업비트)와 동일한 패턴이지만
    broker=TossLiveBroker()로 실제 주문을 내고, 실행 on/off는 별도 스위치
    (trade_engine_settings, broker='toss', mode='live')로 모의매매와 독립적으로 제어된다."""
    try:
        broker = TossLiveBroker()
    except RuntimeError as e:
        logger.error(f"토스 실거래 엔진을 시작할 수 없습니다: {e}")
        return

    fixed_interval = interval_sec
    logger.info(
        "🔴 토스증권 실거래 자동매매 엔진 시작 — 실주문 발생 가능. "
        "대시보드의 '실거래 실행' 스위치와 실거래 승인 체크박스로 제어."
    )

    while True:
        current_interval = fixed_interval or get_trade_strategy_settings(BROKER)['loop_interval_sec']

        if not get_trade_engine_settings(BROKER, 'live')['enabled']:
            logger.info("⏸️ 토스 실거래 엔진 일시중지 상태 — 이번 사이클 건너뜀 (대시보드에서 다시 켤 수 있음)")
            time.sleep(current_interval)
            continue

        try:
            # "마지막 실행" 하트비트는 run_trade_cycle() 자신의 finally에서 기록한다(수동 "지금 즉시
            # 실행" 버튼과 공통 경로로 합치기 위함) — 여기서 따로 또 기록하지 않는다.
            run_trade_cycle(broker=broker, trigger_type='auto_live')
        except Exception as e:
            logger.error(f"토스 실거래 루프 오류: {e}")

        time.sleep(current_interval)


if __name__ == "__main__":
    run_auto_trade_loop()
