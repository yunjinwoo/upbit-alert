"""업비트 자동매매 1단계 — 모의매매(dry-run) 오케스트레이션.

app/core/upbit_market_analysis.run_coin_screening_loop()와 동일한 패턴(1회 실행 함수 +
while-True 루프 래퍼)을 따른다. 실주문은 절대 발생하지 않는다 — 실제 체결은 전부
PaperBroker(app/core/brokers/paper_broker.py)가 DB 가상 원장에만 반영한다.

python main.py trade 로 독립 프로세스 실행. main.py의 start_all()에는 포함하지 않는다
(알림/모니터링 프로세스와 장애를 격리하기 위함).
"""
import time
from datetime import datetime
from types import SimpleNamespace

from app.config import Config
from app.utils.logger import get_logger
from app.utils.slack import send_slack_msg
from app.core.brokers.paper_broker import PaperBroker
from app.core.trade_strategy import evaluate_entries, evaluate_exits
from app.utils.db_manager import (
    get_or_create_paper_account,
    get_paper_positions,
    get_coin_screening_candidates,
    save_trade_order_log,
    get_trade_order_log,
    save_job_run_log,
    get_trade_engine_settings,
    get_approved_candidate_tickers,
    get_trade_strategy_settings,
)

logger = get_logger()

JOB_NAME = "auto_trade_upbit_paper"


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
    )


def _execute(decision, broker):
    """판단 1건을 실행하고 결과를 trade_order_log에 기록한다. HOLD/SKIP은 주문 없이 기록만 남긴다."""
    result = None
    if decision.action == 'BUY':
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

        if result.success and Config.TRADE_SLACK_ALERT:
            side_label = '매수' if decision.action == 'BUY' else '매도'
            send_slack_msg(
                f"[모의매매] {side_label} {decision.ticker} {result.qty:.6f}개 @ {result.price:,.0f}원 "
                f"(총 {result.amount_krw:,.0f}원, {decision.reason})"
            )
    else:
        # HOLD / SKIP — 실주문 없이 판단 근거만 감사로그로 남김
        save_trade_order_log(
            broker=broker.broker_name, mode=broker.mode, ticker=decision.ticker,
            decision=decision.action, reason=decision.reason,
            price=decision.price, qty=decision.qty, amount_krw=decision.amount_krw,
            cash_balance_after=None, pnl_krw=decision.pnl_krw, pnl_pct=decision.pnl_pct,
        )


def run_trade_cycle(broker=None) -> dict:
    """1사이클: 보유 포지션 청산 판단 → 진입 후보 매수 판단 → 전부 실행/기록."""
    broker = broker or PaperBroker()
    strategy_cfg = _effective_strategy_config()  # 매 사이클마다 새로 읽어 대시보드 설정 변경을 즉시 반영

    # ① 청산 판단 (손절/익절) — 먼저 처리해 현금을 회수한 뒤 진입 판단에 반영
    positions = get_paper_positions(broker.broker_name, broker.mode)
    exit_decisions = evaluate_exits(positions, broker.get_current_price, strategy_cfg)
    for decision in exit_decisions:
        _execute(decision, broker)

    # ② 진입 판단 (신규 매수) — 청산 반영된 최신 잔고/포지션으로 재조회
    positions = get_paper_positions(broker.broker_name, broker.mode)
    account = get_or_create_paper_account(broker.broker_name, broker.mode, Config.TRADE_INITIAL_CASH_KRW)
    candidates = get_coin_screening_candidates()

    # 대시보드에서 특정 종목만 체크(수동 승인)했다면 그 종목만 진입 대상으로 좁힌다.
    # 아무것도 체크 안 했으면(빈 집합) 기존처럼 전체 후보를 대상으로 함 — 즉 화이트리스트가
    # "비어있으면 무제한", "하나라도 있으면 그것만"인 opt-in 필터.
    approved_tickers = get_approved_candidate_tickers(broker.broker_name, broker.mode)
    if approved_tickers:
        candidates = [c for c in candidates if c['ticker'] in approved_tickers]

    entry_decisions = evaluate_entries(candidates, positions, account['cash_balance'], broker.get_current_price, strategy_cfg)
    for decision in entry_decisions:
        _execute(decision, broker)

    return {
        'exit_decisions': len(exit_decisions),
        'entry_decisions': len(entry_decisions),
    }


def get_dashboard_summary() -> dict:
    """/auto-trade 대시보드용 읽기 전용 요약 — 계좌/포지션(현재가·평가손익 포함)/최근 로그/진입 후보.
    현재가 조회는 여기서만(웹 요청 처리 중) 수행하며, 매매 판단/주문 실행은 절대 하지 않는다."""
    broker = PaperBroker()
    account = get_or_create_paper_account(broker.broker_name, broker.mode, Config.TRADE_INITIAL_CASH_KRW)
    positions = get_paper_positions(broker.broker_name, broker.mode)

    for pos in positions:
        price = broker.get_current_price(pos['ticker'])
        pos['current_price'] = price
        if price:
            pos['eval_amount'] = price * pos['qty']
            pos['pnl_krw'] = (price - pos['avg_buy_price']) * pos['qty']
            pos['pnl_pct'] = (price - pos['avg_buy_price']) / pos['avg_buy_price'] * 100 if pos['avg_buy_price'] else None
        else:
            pos['eval_amount'] = pos['pnl_krw'] = pos['pnl_pct'] = None

    # 매매 대상(진입 후보) — coin_screening_daily 스냅샷 기준, 다음 사이클에 evaluate_entries()가
    # 실제로 보게 될 후보와 동일한 목록. 가격은 스크리닝 시점 값(최대 30분 전)이라 실시간 시세는 아님 —
    # 여기서 후보마다 현재가를 새로 조회하면 종목 수만큼 네트워크 호출이 늘어 페이지가 느려지므로 생략.
    held_tickers = {p['ticker'] for p in positions}
    approved_tickers = get_approved_candidate_tickers(broker.broker_name, broker.mode)
    candidates = get_coin_screening_candidates()
    for cand in candidates:
        cand['already_held'] = cand['ticker'] in held_tickers
        cand['candidate_reason'] = 'breakout_4h' if cand.get('breakout_4h') else 'near_ma200+above_cloud'
        cand['approved'] = cand['ticker'] in approved_tickers

    # 하나라도 체크돼 있으면 "선택 매매 모드"임을 대시보드에 알려주기 위한 플래그
    candidates_filtered = bool(approved_tickers)

    orders = get_trade_order_log(broker.broker_name, broker.mode, limit=100)
    engine_enabled = get_trade_engine_settings()['enabled']
    strategy_settings = get_trade_strategy_settings()
    return {
        'account': account, 'positions': positions, 'orders': orders,
        'candidates': candidates, 'candidates_filtered': candidates_filtered,
        'engine_enabled': engine_enabled, 'settings': strategy_settings,
    }


def run_auto_trade_loop(interval_sec: int = None):
    # interval_sec을 명시적으로 넘기면(테스트 등 특수 목적) 그 값으로 고정, 안 넘기면 매 사이클
    # DB(trade_strategy_settings.loop_interval_sec)를 다시 읽어 대시보드에서 바꾼 주기를 그때그때 반영한다.
    fixed_interval = interval_sec
    logger.info(
        f"🤖 업비트 모의매매(dry-run) 엔진 시작 — 실주문 없음. "
        f"초기자본 {Config.TRADE_INITIAL_CASH_KRW:,.0f}원. 매매 기준은 대시보드에서 실시간 조정 가능."
    )

    while True:
        current_interval = fixed_interval or get_trade_strategy_settings()['loop_interval_sec']

        # 대시보드(/auto-trade)의 실행/일시중지 토글을 매 사이클마다 확인 — 껐다 켜도 프로세스
        # 재시작 없이(최대 current_interval 지연으로) 반영된다. 꺼진 동안은 판단/체결/로그 기록 전부 건너뜀.
        if not get_trade_engine_settings()['enabled']:
            logger.info("⏸️ 자동매매 엔진 일시중지 상태 — 이번 사이클 건너뜀 (대시보드에서 다시 켤 수 있음)")
            time.sleep(current_interval)
            continue

        start_time = datetime.now()
        success = True
        error_message = None
        count = 0
        try:
            result = run_trade_cycle()
            count = result['exit_decisions'] + result['entry_decisions']
        except Exception as e:
            success = False
            error_message = str(e)
            logger.error(f"자동매매 루프 오류: {e}")

        end_time = datetime.now()
        try:
            save_job_run_log(
                job_name=JOB_NAME,
                description='업비트 모의매매(dry-run) 사이클',
                api_used='pyupbit(public)',
                start_time=start_time.strftime('%Y-%m-%d %H:%M:%S'),
                end_time=end_time.strftime('%Y-%m-%d %H:%M:%S'),
                success=success, count=count, error_message=error_message,
                trigger_type='auto',
            )
        except Exception as e:
            logger.error(f"job_run_log 기록 실패: {e}")

        time.sleep(current_interval)


if __name__ == "__main__":
    run_auto_trade_loop()
