"""토스증권 모의매매(paper) 데이터 일회성 정리.

토스도 이제 실거래 2단계(매매 대상 → 실거래 승인)만 쓰고 모의매매 자동매매 루프는 폐지됐다
(app/core/toss_auto_trader.py run_auto_trade_loop). 그동안 유령 프로세스가 쌓아둔 가상 매매 이력/
포지션/계좌를 지운다. cleanup_upbit_paper.py의 토스 버전.

    python cleanup_toss_paper.py          # 삭제 대상 건수만 출력 (dry-run)
    python cleanup_toss_paper.py --yes    # 실제 삭제

건드리는 것: broker='toss' AND mode='paper' 행만.
안 건드리는 것: 실거래(mode='live'), 업비트(broker='upbit') 데이터는 전혀 손대지 않는다.
"""
import sqlite3
import sys

from app.config import Config
from app.utils.db_manager import set_trade_engine_enabled

# (테이블, WHERE 절, 파라미터) — 전부 토스 paper 한정
TARGETS = [
    ("trade_order_log", "broker = 'toss' AND mode = 'paper'", ()),
    ("paper_positions", "broker = 'toss' AND mode = 'paper'", ()),
    ("paper_account", "broker = 'toss' AND mode = 'paper'", ()),
    ("trade_candidate_approval", "broker = 'toss' AND mode = 'paper'", ()),
    ("job_run_log", "job_name = ?", ("auto_trade_toss_paper",)),
]


def main():
    do_delete = "--yes" in sys.argv
    conn = sqlite3.connect(Config.DB_NAME)
    cur = conn.cursor()

    total = 0
    for table, where, params in TARGETS:
        try:
            n = cur.execute(f"SELECT COUNT(*) FROM {table} WHERE {where}", params).fetchone()[0]
        except sqlite3.OperationalError as e:
            print(f"  {table}: 건너뜀 ({e})")
            continue
        total += n
        print(f"  {table}: {n}건")
        if do_delete and n:
            cur.execute(f"DELETE FROM {table} WHERE {where}", params)

    if do_delete:
        conn.commit()
        conn.close()
        # 모의매매 엔진 스위치는 행을 지우면 기본값이 '실행중'으로 잡히므로(get_trade_engine_settings),
        # 지우지 말고 명시적으로 정지 상태로 남겨둔다.
        set_trade_engine_enabled(False, 'toss', 'paper')
        print(f"\n✅ 삭제 완료 — 총 {total}건 제거, 모의매매 엔진 스위치 OFF로 고정.")
    else:
        conn.close()
        print(f"\n(dry-run) 총 {total}건이 삭제 대상입니다. 실제 삭제는 --yes 를 붙이세요.")


if __name__ == "__main__":
    main()
