"""거래지원 종료 예정 코인 감지(app/core/upbit_delisting.py) 검증.

    python tests/test_upbit_delisting.py

확인하는 것:
  1. 공지 제목 파싱 — 여러 종목, 종료 시각, 연도 넘김, BTC 마켓 전용/철회 공지 제외
  2. refresh — 공지 + 투자유의를 합쳐 저장, 오래 지난 종료는 빠짐, 공지 조회 실패 시 기존 목록 유지
  3. refresh_if_stale — 12시간 안에는 다시 읽지 않음
"""
import os
import sys
import tempfile
from datetime import datetime
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.utils import db_manager
from app.core import upbit_delisting as ud

FAILED = []


def check(name, cond):
    print(('✅ ' if cond else '❌ ') + name)
    if not cond:
        FAILED.append(name)


def test_parse():
    now = datetime(2026, 9, 28, 12, 0)
    p = ud.parse_delisting_notice('플레이댑(PDA) 거래지원 종료 안내 (10/5 14:00)', '2026-09-25T16:00:00+09:00', now)
    check('단일 종목 + 종료 시각', p == (['KRW-PDA'], '2026-10-05 14:00:00'))
    p = ud.parse_delisting_notice('코박토큰(CBK), 에이피엠코인(APM) 거래지원 종료 안내 (1/13 15:00)', '2026-12-28T10:00:00+09:00', now)
    check('여러 종목 + 12월 공지의 1월 종료는 다음 해', p == (['KRW-CBK', 'KRW-APM'], '2027-01-13 15:00:00'))
    p = ud.parse_delisting_notice('위믹스(WEMIX) 거래지원 종료 안내', None, now)
    check('시각 없는 제목도 종목은 잡음', p == (['KRW-WEMIX'], None))
    check('BTC 마켓 전용 종료는 제외',
          ud.parse_delisting_notice('스텝앱(FITFI) BTC 마켓 거래지원 종료 안내 (10/5 14:00)', None, now) is None)
    check('종료 결정 철회는 제외',
          ud.parse_delisting_notice('아이콘(ICX) 거래지원 종료 결정 철회 안내', None, now) is None)
    check('종료와 무관한 공지는 제외',
          ud.parse_delisting_notice('비트코인(BTC) 신규 거래지원 안내 (KRW 마켓)', None, now) is None)


class _Res:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self.payload


NOTICES = {'success': True, 'data': {'notices': [
    {'id': 1, 'title': '플레이댑(PDA) 거래지원 종료 안내 (10/5 14:00)', 'listed_at': '2026-09-25T16:00:00+09:00'},
    {'id': 2, 'title': '옛코인(OLD) 거래지원 종료 안내 (9/1 14:00)', 'listed_at': '2026-08-20T16:00:00+09:00'},
    {'id': 3, 'title': '이벤트 안내', 'listed_at': '2026-09-25T16:00:00+09:00'},
]}}
MARKETS = [
    {'market': 'KRW-EGLD', 'market_event': {'warning': True, 'caution': {}}},
    {'market': 'KRW-PDA', 'market_event': {'warning': True, 'caution': {}}},
    {'market': 'BTC-SNX', 'market_event': {'warning': True, 'caution': {}}},
    {'market': 'KRW-BTC', 'market_event': {'warning': False, 'caution': {}}},
]


def _fake_get(url, params=None, **kw):
    if url == ud.NOTICE_URL:
        return _Res(NOTICES if params['page'] == 1 else {'data': {'notices': []}})
    return _Res(MARKETS)


def test_refresh():
    with tempfile.TemporaryDirectory() as tmp:
        db_manager.DB_PATH = os.path.join(tmp, 'test.db')
        with mock.patch.object(ud.requests, 'get', _fake_get):
            alerts = ud.refresh(now=datetime(2026, 9, 28, 12, 0))
        check('종료 예정 PDA 저장(+유의)', alerts.get('KRW-PDA', {}).get('delisting') and alerts['KRW-PDA']['warning']
              and alerts['KRW-PDA']['delisting_at'] == '2026-10-05 14:00:00')
        check('종료가 오래 지난 OLD는 빠짐', 'KRW-OLD' not in alerts)
        check('유의만인 EGLD는 delisting=False', alerts.get('KRW-EGLD') == {**alerts['KRW-EGLD'], 'delisting': False, 'warning': True})
        check('BTC 마켓 유의는 무시', 'BTC-SNX' not in alerts)
        check('매수 차단 목록 = 종료 예정만', ud.delisting_tickers() == {'KRW-PDA'})

        def boom(*a, **kw):
            raise ConnectionError('403')
        with mock.patch.object(ud.requests, 'get', boom):
            ud.refresh()
        state = db_manager.get_upbit_market_alert_state()
        check('공지 조회 실패 시 기존 목록 유지', ud.delisting_tickers() == {'KRW-PDA'})
        check('실패 상태 기록', state['success'] == 0 and '403' in state['error_message'])

        calls = []
        with mock.patch.object(ud, 'refresh', lambda: calls.append(1)):
            ud.refresh_if_stale()
        check('12시간 안에는 다시 읽지 않음', calls == [])


if __name__ == '__main__':
    test_parse()
    test_refresh()
    print('\n' + ('전부 통과' if not FAILED else f'실패 {len(FAILED)}건'))
    sys.exit(1 if FAILED else 0)
