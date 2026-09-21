"""백테스트 — 과거 캔들로 "그때 이 규칙대로 샀으면 어땠을까"를 되돌려 보는 모듈.

매매 판단 로직은 여기서 새로 만들지 않는다. app/core/trade_strategy.py의 evaluate_entries()/
evaluate_exits()가 시세를 get_price_fn 콜백으로 받는 순수 함수라, 그 콜백만 과거 캔들로 바꿔 끼우면
실거래에서 도는 판단 로직 그대로 과거를 돌려볼 수 있다. 성과 집계도 app/core/trade_performance.py를
그대로 재사용하므로, 백테스트 결과는 매매 성과 화면과 같은 지표/같은 정의로 읽힌다.

구성:
  candle_store.py — 과거 캔들 로컬 캐시(SQLite). 수집과 시뮬레이션을 분리하기 위한 중간 저장소
  collector.py    — 업비트 REST에서 캔들을 받아 캐시에 채운다(서버에서만 동작 — 아래 참고)
  market_data.py  — 캐시를 메모리에 올려 "특정 시각 기준" 시세와 순위를 돌려준다
  engine.py       — 시뮬레이션 루프(기존 판단 로직 재사용)
  report.py       — 결과를 사람이 읽는 표로

수집은 서버에서만 된다: 개발용 원격 세션에서는 업비트 API가 막혀 있어(CONNECT 403) 캔들을 받을 수
없다. 그래서 collector(네트워크)와 engine(순수 계산)을 파일부터 갈라 뒀다 — 엔진은 캐시만 있으면
어디서든 돌고, 테스트도 목 데이터로 네트워크 없이 돌아간다(tests/test_backtest_engine.py).
"""
