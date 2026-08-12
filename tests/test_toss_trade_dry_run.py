import os
import sys

# Add project root to sys.path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app.utils.db_manager import (
    init_db,
    get_or_create_paper_account,
    get_paper_positions,
    get_trade_order_log,
)
from app.core.toss_auto_trader import run_trade_cycle
from app.config import Config

# 1. DB 초기화 (파일이 없으면 생성)
init_db()

print("--- [테스트] 토스증권 자동매매 dry-run 1사이클 실행 ---")
print("⚠️  실주문 없음 — 시세만 토스 Open API로 조회하고, 체결은 전부 DB 가상 원장에만 반영됩니다.")
print("⚠️  TOSS_CLIENT_ID/TOSS_CLIENT_SECRET이 .env에 설정돼 있어야 시세 조회가 됩니다.\n")

# 2. 1사이클 실행 (진입 후보는 stock_screening_daily가 미리 채워져 있어야 함 —
#    비어 있으면 `python main.py toss_analysis`를 먼저 한 번 돌려두거나, entry_decisions=0이 정상)
result = run_trade_cycle()
print(f"이번 사이클 판단 건수: 청산 {result['exit_decisions']}건, 진입 {result['entry_decisions']}건\n")

# 3. 결과 확인
account = get_or_create_paper_account('toss', 'paper', Config.TRADE_INITIAL_CASH_KRW)
print(f"[가상 계좌] 현금 잔고: {account['cash_balance']:,.0f}원 (초기자본 {account['initial_balance']:,.0f}원)")

positions = get_paper_positions('toss', 'paper')
print(f"\n[보유 포지션] {len(positions)}건")
for p in positions:
    print(f"  - {p['ticker']}: {p['qty']:.4f}주 @ 평단 {p['avg_buy_price']:,.0f}원 (진입: {p['entry_at']})")

orders = get_trade_order_log('toss', 'paper', limit=20)
print(f"\n[최근 매매 판단 로그] 최신 {len(orders)}건")
for o in orders:
    print(f"  - {o['created_at']} {o['ticker']} {o['decision']} ({o['reason']})")

print("\n✅ dry-run 1사이클 완료. 대시보드에서도 확인해보세요: http://localhost:5000/toss-trade")
