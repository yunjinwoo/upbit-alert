"""백테스트 CLI — 과거 캔들을 모으고(collect), 그 위에서 매매 규칙을 돌려본다(run).

    python backtest.py collect --days 90 --interval minutes60     # 서버에서만 (업비트 API 필요)
    python backtest.py status                                     # 캐시에 뭐가 들어있는지
    python backtest.py run --selection gainers                    # 상승률 상위 10 + 현재 청산 로직
    python backtest.py run --compare                              # 선정 2종 × (현재 로직 / 기준선) 비교

collect는 업비트 API에 닿아야 하므로 서버(49.247.202.50)에서 돌린다. run은 캐시만 읽으므로 어디서든
돈다 — 한 번 모아두면 파라미터를 바꿔가며 몇 번이고 다시 돌릴 수 있다.

왜 "기준선"을 같이 돌리나: 상승률 상위 10을 샀더니 수익이 났다고 해도, 그게 종목 선정이 좋아서인지
청산 로직이 좋아서인지는 하나만 봐서는 모른다. 아무 규칙 없이 정해진 시간만 들고 있었을 때와 나란히
놓고 봐야 "현재 로직이 보탬이 되는지"가 답해진다.
"""
import argparse
import json
import sys
import time
from datetime import datetime

from app.backtest import candle_store as store
from app.backtest.engine import BacktestParams, run_backtest, EXIT_MODE_HOLD, EXIT_MODE_STRATEGY
from app.backtest.market_data import MarketData, SELECT_GAINERS, SELECT_TRADE_VALUE
from app.backtest.report import build_report, format_report, format_comparison
from app.backtest.strategy_config import strategy_config, describe
from app.config import Config


def cmd_collect(args) -> int:
    from app.backtest.collector import collect   # requests를 쓰므로 필요할 때만 들여온다

    def progress(i, total, market, rows):
        if i % 20 == 0 or i == total:
            print(f"  [{i}/{total}] {market} (+{rows}행)", flush=True)

    started = time.time()
    stats = collect(days=args.days, interval=args.interval, force=args.force, progress_fn=progress)
    print(f"\n수집 완료 — {stats['fetched']}종목 갱신, {stats['skipped']}종목 건너뜀, "
          f"{stats['rows']:,}행, {time.time() - started:.0f}초")
    if stats['failed']:
        print(f"실패: {', '.join(stats['failed'])}")
    print(f"캐시 파일 {store.DB_PATH} — {store.db_size_mb():.1f}MB")
    return 0


def cmd_status(args) -> int:
    store.init_db()
    for interval in ('days', args.interval):
        cov = store.coverage(interval)
        if not cov['row_count']:
            print(f"{interval:<12} 비어 있음")
            continue
        print(f"{interval:<12} {cov['market_count']}종목 {cov['row_count']:,}행  "
              f"{store.kst_str(cov['first_ts'])} ~ {store.kst_str(cov['last_ts'])}")
    print(f"캐시 파일 {store.DB_PATH} — {store.db_size_mb():.1f}MB")
    return 0


def _resolve_window(args) -> tuple:
    """--days 또는 --from/--to를 (start_ts, end_ts)로. 기준은 KST."""
    if args.date_from or args.date_to:
        start = datetime.strptime(args.date_from, '%Y-%m-%d').replace(tzinfo=store.KST) if args.date_from else None
        end = datetime.strptime(args.date_to, '%Y-%m-%d').replace(tzinfo=store.KST) if args.date_to else None
        cov = store.coverage(args.interval)
        start_ts = int(start.timestamp()) if start else cov['first_ts']
        end_ts = int(end.timestamp()) + 86400 if end else cov['last_ts']
        return start_ts, end_ts
    now = int(time.time())
    return now - args.days * 86400, now


def _overrides(args) -> dict:
    """CLI로 덮어쓴 전략 파라미터만 모아준다(안 준 건 None이라 무시된다)."""
    return {
        'TRADE_TAKE_PROFIT_PCT': args.take_profit,
        'TRADE_STOP_LOSS_PCT': args.stop_loss,
        'TRADE_DCA_MAX_COUNT': args.dca_max,
        'TRADE_MAX_POSITION_KRW': args.position_krw,
        'TRADE_MAX_CONCURRENT_POSITIONS': args.max_positions,
        'TRADE_RSI_EXIT_ENABLED': True if args.rsi_exit else None,
        'TRADE_TRAILING_TP_ENABLED': True if args.trailing_tp else None,
        'TRADE_RECOVERY_DCA_ENABLED': True if args.recovery else None,
    }


def cmd_run(args) -> int:
    store.init_db()
    cov = store.coverage(args.interval)
    if not cov['row_count']:
        print(f"캐시가 비어 있다. 먼저 서버에서 `python backtest.py collect --interval {args.interval}`를 돌려야 한다.")
        return 1

    start_ts, end_ts = _resolve_window(args)
    print(f"캔들 로딩 중 — {args.interval}, {store.kst_str(start_ts)} ~ {store.kst_str(end_ts)}", flush=True)
    md = MarketData(args.interval, start_ts, end_ts)
    if not md.timestamps:
        print("해당 구간에 캔들이 없다. status로 캐시 구간을 확인할 것.")
        return 1

    cfg = strategy_config(from_db=args.from_db, overrides=_overrides(args))
    print(f"매매 설정 — {describe(cfg)}\n")

    # --compare: 선정 기준 2종 × (현재 청산 로직 / 기준선) — 무엇이 성과를 만들었는지 가르기 위한 조합
    if args.compare:
        combos = [
            (SELECT_GAINERS, EXIT_MODE_STRATEGY), (SELECT_GAINERS, EXIT_MODE_HOLD),
            (SELECT_TRADE_VALUE, EXIT_MODE_STRATEGY), (SELECT_TRADE_VALUE, EXIT_MODE_HOLD),
        ]
    else:
        combos = [(args.selection, args.exit_mode)]

    reports = []
    for selection, exit_mode in combos:
        params = BacktestParams(
            selection=selection, top_n=args.top, exit_mode=exit_mode,
            hold_hours=args.hold_hours, initial_cash=args.cash,
            entry_hour=args.entry_hour, min_trade_value_krw=args.min_trade_value,
            buy_fee_rate=Config.TRADE_FEE_RATE_UPBIT_BUY,
            sell_fee_rate=Config.TRADE_FEE_RATE_UPBIT_SELL,
            rsi_period=int(cfg.TRADE_RSI_EXIT_PERIOD),
        )
        result = run_backtest(md, cfg, params)
        report = build_report(result)
        reports.append(report)
        print(format_report(report))
        print()

    if len(reports) > 1:
        print(format_comparison(reports))

    if args.json:
        with open(args.json, 'w', encoding='utf-8') as f:
            json.dump(reports if len(reports) > 1 else reports[0], f, ensure_ascii=False, indent=2)
        print(f"\n상세 결과 저장 — {args.json}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description='업비트 백테스트')
    sub = parser.add_subparsers(dest='command', required=True)

    p_collect = sub.add_parser('collect', help='업비트에서 과거 캔들을 받아 캐시에 채운다(서버 전용)')
    p_collect.add_argument('--days', type=int, default=90, help='최근 며칠치 (기본 90)')
    p_collect.add_argument('--interval', default='minutes60',
                           choices=list(store.INTERVAL_URLS.keys()), help='봉 종류 (기본 minutes60)')
    p_collect.add_argument('--force', action='store_true', help='이미 받아둔 구간도 다시 받는다')
    p_collect.set_defaults(func=cmd_collect)

    p_status = sub.add_parser('status', help='캐시에 들어있는 데이터 구간')
    p_status.add_argument('--interval', default='minutes60', choices=list(store.INTERVAL_URLS.keys()))
    p_status.set_defaults(func=cmd_status)

    p_run = sub.add_parser('run', help='캐시된 캔들로 백테스트를 돌린다')
    p_run.add_argument('--interval', default='minutes60', choices=list(store.INTERVAL_URLS.keys()))
    p_run.add_argument('--days', type=int, default=90, help='최근 며칠 구간 (기본 90)')
    p_run.add_argument('--from', dest='date_from', help='시작일 YYYY-MM-DD (KST)')
    p_run.add_argument('--to', dest='date_to', help='종료일 YYYY-MM-DD (KST)')
    p_run.add_argument('--selection', default=SELECT_GAINERS,
                       choices=[SELECT_GAINERS, SELECT_TRADE_VALUE], help='종목 선정 기준')
    p_run.add_argument('--top', type=int, default=10, help='상위 몇 개를 후보로 볼지 (기본 10)')
    p_run.add_argument('--exit-mode', dest='exit_mode', default=EXIT_MODE_STRATEGY,
                       choices=[EXIT_MODE_STRATEGY, EXIT_MODE_HOLD], help='청산 방식')
    p_run.add_argument('--hold-hours', dest='hold_hours', type=float, default=24.0,
                       help='기준선 청산까지 보유 시간 (기본 24)')
    p_run.add_argument('--compare', action='store_true', help='선정 2종 × 청산 2종을 한 번에 비교')
    p_run.add_argument('--entry-hour', dest='entry_hour', type=int,
                       help='이 시각(KST)에만 신규 진입. 안 주면 매 봉마다 본다')
    p_run.add_argument('--min-trade-value', dest='min_trade_value', type=float, default=0,
                       help='후보 최소 당일 거래대금(원) — 호가가 빈 종목 걸러내기')
    p_run.add_argument('--cash', type=float, default=Config.TRADE_INITIAL_CASH_KRW, help='초기 자본금')
    p_run.add_argument('--from-db', dest='from_db', action='store_true',
                       help='app/config.py 기본값 대신 대시보드에 저장된 매매 설정으로 돌린다')
    p_run.add_argument('--take-profit', dest='take_profit', type=float, help='익절 기준(%%) 덮어쓰기')
    p_run.add_argument('--stop-loss', dest='stop_loss', type=float, help='트레일링 손절 기준(%%) 덮어쓰기')
    p_run.add_argument('--dca-max', dest='dca_max', type=int, help='물타기 최대 횟수 덮어쓰기')
    p_run.add_argument('--position-krw', dest='position_krw', type=float, help='1종목 매수금액 덮어쓰기')
    p_run.add_argument('--max-positions', dest='max_positions', type=int, help='동시 보유 종목 수 덮어쓰기')
    p_run.add_argument('--rsi-exit', dest='rsi_exit', action='store_true', help='RSI 과매수 매도 켜기')
    p_run.add_argument('--trailing-tp', dest='trailing_tp', action='store_true', help='되돌림 익절 켜기')
    p_run.add_argument('--recovery', action='store_true', help='회복형 분할 물타기 모드로 돌리기')
    p_run.add_argument('--json', help='상세 결과(주문 내역/자산 곡선)를 이 경로에 JSON으로 저장')
    p_run.set_defaults(func=cmd_run)

    return parser


if __name__ == '__main__':
    args = build_parser().parse_args()
    sys.exit(args.func(args))
