"""매매일지 연동(app/core/trade_journal.py, /api/journal/fills) 테스트 — 네트워크 없이 임시 DB로 검증한다.

실행: python tests/test_trade_journal.py
"""
import os
import sys
import tempfile

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app.utils import db_manager
from app.core.trade_journal import build_journal_fills, reason_label

FAILED = []


def check(name, cond, detail=''):
    print(f"{'PASS' if cond else 'FAIL'} {name}{(' — ' + str(detail)) if detail else ''}")
    if not cond:
        FAILED.append(name)


def row(i, decision, at, reason=None, price=None, qty=None, pnl_pct=None, pnl_krw=None):
    return {'id': i, 'ticker': 'KRW-EGLD', 'decision': decision, 'reason': reason, 'price': price, 'qty': qty,
            'amount_krw': price * qty if price and qty else None, 'pnl_krw': pnl_krw, 'pnl_pct': pnl_pct,
            'created_at': at}


# ── 사유 이름
check('진입 신호 이름', reason_label('breakout_4h') == '4시간봉 돌파')
check('정밀조건 접미사', reason_label('breakout_1d+정밀조건충족') == '일봉 돌파 · 정밀조건')
check('청산 사유(괄호 뒤는 버림)', reason_label('tight_stop_loss(평단대비 -2.10%, 기준 -2.00%)') == '손절')
check('고점 대비 하락', reason_label('tight_trail_exit(고점 9.00% → 현재 3.00%, 허용 고점대비 -6.00%)') == '고점 대비 하락 매도')
check('수동', reason_label('강제매도(수동)') == '수동 매도')
check('모르는 사유는 원문', reason_label('something_new(1)') == 'something_new')
check('빈 사유', reason_label(None) is None)

# ── 사이클: 매수 → HOLD들 → 물타기 → 매도(전량) → 기록 없이 매도(앱 이전 보유분)
rows = [
    row(1, 'BUY', '2026-09-20 10:00:00', 'breakout_4h', 7000, 4),
    row(2, 'HOLD', '2026-09-20 11:00:00', 'tight_watch', pnl_pct=3.0),
    row(3, 'HOLD', '2026-09-21 11:00:00', 'tight_watch', pnl_pct=-4.5),
    row(4, 'DCA_BUY', '2026-09-22 09:00:00', 'dca_buy(1/2회)', 5800, 3),
    row(5, 'HOLD', '2026-09-23 09:00:00', 'tight_trailing', pnl_pct=22.0),
    row(6, 'SELL', '2026-09-24 09:06:00', 'tight_trail_exit(고점 22% → 현재 17%)', 7505, 7, pnl_pct=17.4, pnl_krw=9000),
    row(7, 'HOLD', '2026-09-24 10:00:00', 'x', pnl_pct=99.0),   # 포지션 없을 때 HOLD는 무시
    row(8, 'SELL', '2026-09-24 16:13:00', '강제매도(수동)', 9800, 1, pnl_pct=5.0),
]
changes = [
    {'changed_at': '2026-09-21 00:00:00', 'from_regime': 'neutral', 'to_regime': 'good'},
    {'changed_at': '2026-09-24 12:00:00', 'from_regime': 'good', 'to_regime': 'bad'},
]
fills = build_journal_fills('EGLD', rows=rows, regime_changes=changes)
check('HOLD는 목록에서 빠짐', [f['id'] for f in fills] == [1, 4, 6, 8], [f['id'] for f in fills])
check('매수 사유', fills[0]['reason_label'] == '4시간봉 돌파')
check('첫 변경 이전 국면 = from_regime', fills[0]['regime'] == {'key': 'neutral', 'label': '애매'}, fills[0]['regime'])
check('물타기는 매수 쪽', fills[1]['side'] == '매수' and fills[1]['reason_label'] == '물타기')
sell = fills[2]
check('매도 시점 국면', sell['regime']['key'] == 'good')
check('진입 시점 국면', sell['entry_regime']['key'] == 'neutral')
check('보유 중 최고 수익률', sell['peak_pnl_pct'] == 22.0, sell['peak_pnl_pct'])
check('보유 중 최저 수익률', sell['trough_pnl_pct'] == -4.5, sell['trough_pnl_pct'])
check('실현 수익률', sell['pnl_pct'] == 17.4)
stray = fills[3]
check('기록 없는 매도는 이전 사이클 값이 안 섞임', stray['peak_pnl_pct'] is None and stray['trough_pnl_pct'] is None, stray)
check('기록 없는 매도의 진입 국면은 없음', stray['entry_regime'] is None)
check('두번째 변경 이후 국면', stray['regime']['key'] == 'bad')

# ── 부분 매도: 수량이 남으면 포지션 유지(최고/최저가 이어짐)
rows2 = [
    row(1, 'BUY', '2026-09-01 00:00:00', 'breakout_1d', 100, 10),
    row(2, 'HOLD', '2026-09-01 01:00:00', pnl_pct=8.0),
    row(3, 'SELL', '2026-09-01 02:00:00', 'recovery_partial', 100, 4, pnl_pct=6.0),
    row(4, 'HOLD', '2026-09-01 03:00:00', pnl_pct=-2.0),
    row(5, 'SELL', '2026-09-01 04:00:00', 'tight_stop_loss(x)', 100, 6, pnl_pct=-1.0),
]
f2 = build_journal_fills('KRW-EGLD', rows=rows2, regime_changes=[])
check('부분 매도 뒤에도 최고값 유지', f2[2]['peak_pnl_pct'] == 8.0 and f2[2]['trough_pnl_pct'] == -2.0, f2[2])
check('국면 이력이 없으면 None', f2[0]['regime'] is None)

# ── 기간 필터
f3 = build_journal_fills('EGLD', rows=rows, regime_changes=changes, date_from='2026-09-22', date_to='2026-09-24')
check('기간 필터(체결일)', [f['id'] for f in f3] == [4, 6, 8], [f['id'] for f in f3])
f4 = build_journal_fills('EGLD', rows=rows, regime_changes=changes, date_from='2026-09-24')
check('기간 밖 매수가 있어도 최고/최저 유지', f4[0]['peak_pnl_pct'] == 22.0)

# ── DB + API
with tempfile.TemporaryDirectory() as tmp:
    db_manager.DB_PATH = os.path.join(tmp, 'test.db')
    db_manager.init_db()
    db_manager.save_trade_order_log('upbit', 'live', 'KRW-EGLD', 'BUY', 'breakout_4h', 7000, 4, 28000)
    db_manager.save_trade_order_log('upbit', 'live', 'KRW-EGLD', 'HOLD', 'w', 7300, 4, pnl_pct=4.2)
    db_manager.save_trade_order_log('upbit', 'live', 'KRW-EGLD', 'SELL', 'take_profit(3.00%)', 7210, 4, pnl_pct=3.0)
    db_manager.save_trade_order_log('upbit', 'paper', 'KRW-EGLD', 'BUY', 'breakout_1d', 7000, 4, 28000)
    db_manager.save_trade_order_log('upbit', 'live', 'KRW-XRP', 'BUY', 'breakout_1d', 700, 4, 2800)

    from app.api.server import app
    client = app.test_client()
    res = client.get('/api/journal/fills?symbol=egld', environ_base={'REMOTE_ADDR': '127.0.0.1'})
    body = res.get_json()
    check('API 200', res.status_code == 200, res.status_code)
    check('API: 종목·모드로 걸러짐', [f['decision'] for f in body['fills']] == ['BUY', 'SELL'], body)
    check('API: 최고 수익률', body['fills'][1]['peak_pnl_pct'] == 4.2)
    res = client.get('/api/journal/fills?symbol=EGLD', environ_base={'REMOTE_ADDR': '8.8.8.8'})
    check('외부 IP는 403', res.status_code == 403, res.status_code)
    res = client.get('/api/journal/fills?symbol=EGLD', environ_base={'REMOTE_ADDR': '127.0.0.1'},
                     headers={'X-Forwarded-For': '127.0.0.1'})
    check('프록시를 거친 요청은 403', res.status_code == 403, res.status_code)
    res = client.get('/api/journal/fills', environ_base={'REMOTE_ADDR': '127.0.0.1'})
    check('symbol 없으면 400', res.status_code == 400)

print()
print('ALL PASS' if not FAILED else f'FAILED: {FAILED}')
sys.exit(1 if FAILED else 0)
