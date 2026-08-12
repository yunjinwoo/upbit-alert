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
from app.core.trade_strategy import evaluate_entries, evaluate_exits
from app.core import toss_market_analysis
from app.utils.db_manager import (
    get_or_create_paper_account,
    get_paper_positions,
    get_stock_screening_candidates,
    save_trade_order_log,
    save_job_run_log,
    get_trade_engine_settings,
    get_approved_candidate_tickers,
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
            send_slack_msg(
                f"[토스 모의매매] {side_label} {decision.ticker} {result.qty:.4f}주 @ {result.price:,.0f}원 "
                f"(총 {result.amount_krw:,.0f}원, {decision.reason})"
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


def run_trade_cycle(broker=None, trigger_type: str = None) -> dict:
    """app/core/auto_trader.run_trade_cycle()과 동일 — 1사이클: 청산 판단 → 진입 판단 → 실행/기록."""
    start_time = datetime.now()
    success = True
    error_message = None
    result = {'exit_decisions': 0, 'entry_decisions': 0}
    try:
        broker = broker or TossBroker()
        strategy_cfg = _effective_strategy_config()

        # ① 청산 판단 (손절/익절)
        positions = get_paper_positions(broker.broker_name, broker.mode)
        exit_decisions = evaluate_exits(positions, broker.get_current_price, strategy_cfg)
        for decision in exit_decisions:
            _execute(decision, broker)

        # ② 진입 판단 (신규 매수)
        positions = get_paper_positions(broker.broker_name, broker.mode)
        account = get_or_create_paper_account(broker.broker_name, broker.mode, Config.TRADE_INITIAL_CASH_KRW)
        candidates = get_stock_screening_candidates()

        approved_tickers = get_approved_candidate_tickers(broker.broker_name, broker.mode)
        if approved_tickers:
            candidates = [c for c in candidates if c['ticker'] in approved_tickers]

        condition_watch_tickers = get_condition_watch_tickers(broker.broker_name, broker.mode)
        condition_status_map = get_condition_status_map(broker.broker_name, broker.mode)

        entry_decisions = evaluate_entries(
            candidates, positions, account['cash_balance'], broker.get_current_price, strategy_cfg,
            condition_watch_tickers=condition_watch_tickers, condition_status_map=condition_status_map,
        )
        for decision in entry_decisions:
            _execute(decision, broker)

        result = {
            'exit_decisions': len(exit_decisions),
            'entry_decisions': len(entry_decisions),
        }
    except Exception as e:
        success = False
        error_message = str(e)
        raise
    finally:
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

    engine_enabled = get_trade_engine_settings(BROKER)['enabled']
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
        'engine_enabled': engine_enabled, 'settings': strategy_settings,
        'conditions': condition_settings, 'screening_thresholds': screening_thresholds,
    }


def force_buy(ticker: str, broker=None) -> dict:
    """app/core/auto_trader.force_buy()와 동일 — 대시보드 "강제 매수" 버튼."""
    broker = broker or TossBroker()
    settings = get_trade_strategy_settings(BROKER)
    result = broker.buy_market(ticker, settings['max_position_krw'], reason='강제매수(수동)')

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

        time.sleep(current_interval)


if __name__ == "__main__":
    run_auto_trade_loop()
