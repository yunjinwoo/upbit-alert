import sqlite3
from datetime import datetime, timedelta
import os
import json
import secrets
from typing import List
from app.config import Config
from app.core.kis_models import MarketCapRankingItem, StockInvestorDailyItem

DB_PATH = Config.DB_NAME

def init_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    # 코인 알림 테이블
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT,
            ticker TEXT,
            surge_count TEXT,
            m240 TEXT,
            m60 TEXT,
            m30 TEXT,
            m15 TEXT,
            daily_info TEXT,
            url TEXT
        )
    ''')
    # 주식 알림 테이블 (신규)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS stock_alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT,
            code TEXT,
            name TEXT,
            price TEXT,
            change_rate TEXT,
            volume TEXT,
            volume_power TEXT,
            market_cap TEXT,
            reason TEXT,
            url TEXT
        )
    ''')
    # API 토큰 저장 테이블 (신규)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS api_tokens (
            provider TEXT PRIMARY KEY,
            token TEXT,
            issued_date TEXT
        )
    ''')


    # 골든 데이터셋 저장소 만들기 - 하네스 테스트용
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS gold_dataset (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            scenario_name TEXT,
            input_data TEXT,       -- 업비트 Mock 데이터 (JSON)
            expected_output TEXT,  -- 기대하는 슬랙 메시지
            category TEXT          -- '폭등', '횡보', '에러처리' 등
        )
    ''')

    # 주식 원본 데이터 저장 테이블
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS stock_raw_data (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT,
            api_type TEXT,
            raw_json TEXT
        )
    ''')

    # 일별 시가총액 데이터 저장 테이블
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS stock_market_cap_daily (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT,
            code TEXT,
            name TEXT,
            market_cap_amount TEXT,
            rank INTEGER,
            price TEXT,
            change_rate TEXT,
            market_weight TEXT,
            fid_input_iscd TEXT,
            volume TEXT,
            timestamp TEXT
        )
    ''')

    # 일별 투자자별 프로그램 매매동향 저장 테이블
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS investor_trend_daily (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT,
            exch_div TEXT,
            mrkt_div TEXT,
            invr_cls_code TEXT,
            invr_cls_name TEXT,
            all_shnu_amt TEXT,
            all_seln_amt TEXT,
            all_ntby_amt TEXT,
            all_shnu_qty TEXT,
            all_seln_qty TEXT,
            all_ntby_qty TEXT,
            arbt_shnu_amt TEXT,
            arbt_seln_amt TEXT,
            arbt_ntby_amt TEXT,
            arbt_shnu_qty TEXT,
            arbt_seln_qty TEXT,
            arbt_ntby_qty TEXT,
            nabt_shnu_amt TEXT,
            nabt_seln_amt TEXT,
            nabt_ntby_amt TEXT,
            nabt_shnu_qty TEXT,
            nabt_seln_qty TEXT,
            nabt_ntby_qty TEXT,
            timestamp TEXT
        )
    ''')

    # 종목별 투자자매매동향(일별) 저장 테이블
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS stock_investor_daily (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT,
            code TEXT,
            name TEXT,
            frgn_ntby_qty TEXT,
            frgn_ntby_tr_pbmn TEXT,
            prsn_ntby_qty TEXT,
            prsn_ntby_tr_pbmn TEXT,
            orgn_ntby_qty TEXT,
            orgn_ntby_tr_pbmn TEXT,
            timestamp TEXT,
            UNIQUE(date, code)
        )
    ''')

    # 업종 일자별지수 캐시 테이블
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS sector_index_daily (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT,
            sector_code TEXT,
            sector_name TEXT,
            close TEXT, open TEXT, high TEXT, low TEXT,
            change TEXT, change_sign TEXT, change_rate TEXT,
            volume TEXT, trade_amount TEXT, vol_ratio TEXT,
            psychology_index TEXT, d20_dsrt TEXT,
            timestamp TEXT,
            UNIQUE(date, sector_code)
        )
    ''')

    # 업종 소속 종목 (일별 스냅샷)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS sector_stocks_daily (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT,
            sector_code TEXT,
            sector_name TEXT,
            rank TEXT,
            code TEXT,
            name TEXT,
            price TEXT, change TEXT, change_sign TEXT, change_rate TEXT,
            volume TEXT,
            timestamp TEXT,
            UNIQUE(date, sector_code, code)
        )
    ''')

    # 종목 메모 (전 페이지 공용 — 종목코드당 여러 개 입력 가능한 로그형)
    # 구버전(종목당 1개, code가 PRIMARY KEY)이 남아있으면 새 스키마로 전환하고 기존 메모는
    # 첫 로그 항목으로 그대로 이전한다.
    cursor.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='stock_memo'")
    row = cursor.fetchone()
    if row and 'code TEXT PRIMARY KEY' in row[0]:
        cursor.execute("ALTER TABLE stock_memo RENAME TO stock_memo_old")
        cursor.execute('''
            CREATE TABLE stock_memo (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                code TEXT,
                name TEXT,
                memo TEXT,
                created_at TEXT
            )
        ''')
        cursor.execute('''
            INSERT INTO stock_memo (code, name, memo, created_at)
            SELECT code, name, memo, updated_at FROM stock_memo_old WHERE memo IS NOT NULL AND memo != ''
        ''')
        cursor.execute("DROP TABLE stock_memo_old")
    else:
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS stock_memo (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                code TEXT,
                name TEXT,
                memo TEXT,
                created_at TEXT
            )
        ''')

    # Signal Score 일별 결과 저장 테이블
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS signal_score_daily (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT,
            code TEXT,
            name TEXT,
            momentum_score INTEGER,
            supply_demand_score INTEGER,
            rank_stability_score INTEGER,
            market_environment_score INTEGER,
            risk_penalty_score INTEGER,
            hts_top_view_bonus_score INTEGER,
            top_interest_bonus_score INTEGER,
            total_score INTEGER,
            grade TEXT,
            timestamp TEXT,
            UNIQUE(date, code)
        )
    ''')

    # HTS조회상위20종목 시간별 저장 테이블
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS stock_hts_top_view_hourly (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT,
            hour INTEGER,
            rank INTEGER,
            code TEXT,
            market_div TEXT,
            name TEXT,
            price TEXT,
            change_rate TEXT,
            prdy_vrss TEXT,
            timestamp TEXT,
            UNIQUE(date, hour, code)
        )
    ''')

    # 관심종목등록 상위 일별 저장 테이블 (네이버 인기검색종목 대체 — robots.txt로 크롤링 불가해서 KIS 공식 API 사용)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS stock_top_interest_daily (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT,
            rank INTEGER,
            code TEXT,
            name TEXT,
            market_div TEXT,
            price TEXT,
            change_rate TEXT,
            reg_count TEXT,
            timestamp TEXT,
            UNIQUE(date, code)
        )
    ''')

    # 상승률 순위 시간대별 스냅샷 저장 테이블 (하루 4회: 9/12/15/18시)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS stock_top_gainers_hourly (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT,
            hour INTEGER,
            rank INTEGER,
            code TEXT,
            name TEXT,
            price TEXT,
            change_rate TEXT,
            prdy_vrss TEXT,
            volume TEXT,
            timestamp TEXT,
            UNIQUE(date, hour, code)
        )
    ''')

    # 바로가기 링크 (날짜열 변환 페이지 — 데이터 복사해오는 원본 웹페이지 즐겨찾기용)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS quick_links (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            label TEXT,
            url TEXT,
            timestamp TEXT
        )
    ''')

    # 동행복권 파워볼 당첨결과 — 회차별 1건, round은 붙여넣기 중복 방지용 UNIQUE
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS powerball_rounds (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            round TEXT UNIQUE,
            date TEXT,
            nums TEXT,
            pb INTEGER,
            sum INTEGER,
            oe TEXT,
            size TEXT,
            sum_band TEXT,
            pb_band TEXT,
            created_at TEXT
        )
    ''')

    # 동행복권 로또6/45 당첨결과 — 회차별 1건, round은 붙여넣기 중복 방지용 UNIQUE
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS lotto645_rounds (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            round TEXT UNIQUE,
            nums TEXT,
            bonus INTEGER,
            winners INTEGER,
            prize INTEGER,
            created_at TEXT
        )
    ''')

    # 파워볼 즐겨찾기 번호 (개인이 골라둔 조합 — 당첨결과와 비교용)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS powerball_favorites (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            nums TEXT,
            pb INTEGER,
            created_at TEXT
        )
    ''')

    # 동행복권 연금복권720+ 당첨결과 — 회차별 1건, round은 붙여넣기 중복 방지용 UNIQUE
    # number는 6자리 문자열(앞자리 0 보존을 위해 TEXT로 저장)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS pension720_rounds (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            round TEXT UNIQUE,
            group_no INTEGER,
            number TEXT,
            created_at TEXT
        )
    ''')

    # 연금복권720+ 즐겨찾기 번호 (개인이 골라둔 조합 — 당첨결과와 비교용)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS pension720_favorites (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            group_no INTEGER,
            number TEXT,
            created_at TEXT
        )
    ''')

    # 로또6/45 즐겨찾기 번호 (개인이 골라둔 조합 — 당첨결과와 비교용). 보너스번호는 뽑는 대상이 아니라 결과에서만 나오므로 저장 안 함
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS lotto645_favorites (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            nums TEXT,
            created_at TEXT
        )
    ''')

    # 재미용 "번호 추천" 결과 저장 — 파워볼/로또6/45/연금복권720+ 공통 테이블.
    # num1~num6: 번호 자리별로 각각 컬럼 저장(파워볼은 5개만 써서 num6은 NULL, 순서는 그대로 보존).
    # bonus: 게임별 부가값(파워볼=파워볼번호, 연금복권=조, 로또는 NULL)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS lottery_recommendations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            game TEXT,
            method TEXT,
            num1 INTEGER,
            num2 INTEGER,
            num3 INTEGER,
            num4 INTEGER,
            num5 INTEGER,
            num6 INTEGER,
            bonus INTEGER,
            created_at TEXT
        )
    ''')

    # 코인(업비트 KRW 마켓) 매매 후보 필터 스냅샷 — 티커당 최신 1건만 유지 (전부 4시간봉 기준)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS coin_screening_daily (
            ticker TEXT PRIMARY KEY,
            name TEXT,
            price REAL,
            change_rate REAL,
            trade_value REAL,
            ma200 REAL,
            ma200_dist_pct REAL,
            near_ma200 INTEGER,
            above_cloud INTEGER,
            breakout_4h INTEGER,
            breakout_vol_ratio REAL,
            breakout_candle_rate REAL,
            updated_at TEXT
        )
    ''')

    # 국내주식(토스증권 Open API) 매매 후보 필터 스냅샷 — coin_screening_daily와 동일한 지표를
    # 일봉(1d) 기준으로 계산해 저장한다(app/core/toss_market_analysis.py). ticker=6자리 종목코드.
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS stock_screening_daily (
            ticker TEXT PRIMARY KEY,
            name TEXT,
            price REAL,
            change_rate REAL,
            trade_value REAL,
            ma200 REAL,
            ma200_dist_pct REAL,
            near_ma200 INTEGER,
            above_cloud INTEGER,
            breakout_1d INTEGER,
            breakout_vol_ratio REAL,
            breakout_candle_rate REAL,
            updated_at TEXT
        )
    ''')

    # 앱 잠금 설정 — 싱글톤 1행(id=1). 잠금 켜질 때마다 비밀번호를 새로 발급해 Slack으로 전송하고,
    # 해제될 때까지 그 비밀번호를 계속 재사용한다(매번 새 코드를 받는 방식이 아님).
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS login_settings (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            lock_enabled INTEGER DEFAULT 1,
            password_hash TEXT,
            updated_at TEXT
        )
    ''')

    # Flask 세션 서명 키(SECRET_KEY) — 싱글톤 1행(id=1). .env에 SECRET_KEY를 수동으로 안 넣어도
    # 최초 실행 시 자동 생성해 여기에 저장하고, 이후엔 재시작(배포 포함)마다 이 값을 재사용한다.
    # (.env에 SECRET_KEY가 있으면 그쪽이 우선 — app/config.py 참고)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS app_secret (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            secret_key TEXT NOT NULL
        )
    ''')

    # 스케줄링 작업 실행 이력 (동기화 관리 페이지 "처리 로그"용)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS job_run_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_name TEXT,
            description TEXT,
            api_used TEXT,
            start_time TEXT,
            end_time TEXT,
            duration_sec REAL,
            success INTEGER,
            count INTEGER,
            error_message TEXT,
            trigger_type TEXT
        )
    ''')

    # 자동매매(1단계: 업비트 모의매매 전용) — 가상 계좌 1행(broker+mode 조합당)
    # broker/mode 컬럼을 처음부터 둬서, 향후 KIS/토스 및 실거래(live) 데이터도 같은 구조로 얹을 수 있게 함
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS paper_account (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            broker TEXT NOT NULL DEFAULT 'upbit',
            mode TEXT NOT NULL DEFAULT 'paper',
            cash_balance REAL NOT NULL,
            initial_balance REAL NOT NULL,
            updated_at TEXT,
            UNIQUE(broker, mode)
        )
    ''')

    # 자동매매 가상 보유 포지션 — 종목당 1행(완전 청산 시 행 삭제, 분할매도/보유기간 제한은 범위 밖).
    # peak_price/below_stop_streak: 트레일링 손절(진입가가 아닌 "보유 중 최고가" 대비 하락률로 손절
    # 판단) + 연속 확인(노이즈로 바로 손절되지 않게 N사이클 연속 조건 유지 확인)에 사용.
    # dca_enabled: 대시보드 체크박스로 켠 "물타기(추가매수)" 허용 여부.
    # dca_used: (구) 1회 제한 시절의 플래그 — 이제 dca_count로 대체됐지만 기존 배포와의 호환을 위해
    # 컬럼은 남겨둠(더 이상 읽지 않음). dca_count: 이 포지션에서 실제로 물탄 횟수, dca_max_count(전략
    # 설정)에 도달할 때까지 반복 허용.
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS paper_positions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            broker TEXT NOT NULL DEFAULT 'upbit',
            mode TEXT NOT NULL DEFAULT 'paper',
            ticker TEXT NOT NULL,
            qty REAL NOT NULL,
            avg_buy_price REAL NOT NULL,
            entry_at TEXT,
            updated_at TEXT,
            peak_price REAL,
            below_stop_streak INTEGER NOT NULL DEFAULT 0,
            dca_enabled INTEGER NOT NULL DEFAULT 0,
            dca_used INTEGER NOT NULL DEFAULT 0,
            dca_count INTEGER NOT NULL DEFAULT 0,
            UNIQUE(broker, mode, ticker)
        )
    ''')

    # 자동매매 판단/체결 감사로그 — BUY/SELL/HOLD/SKIP 전부 기록(실주문 없음, 가상 체결만)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS trade_order_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            broker TEXT NOT NULL DEFAULT 'upbit',
            mode TEXT NOT NULL DEFAULT 'paper',
            ticker TEXT NOT NULL,
            decision TEXT NOT NULL,
            reason TEXT,
            price REAL,
            qty REAL,
            amount_krw REAL,
            cash_balance_after REAL,
            pnl_krw REAL,
            pnl_pct REAL,
            created_at TEXT
        )
    ''')

    # 자동매매 엔진 실행 여부 토글 — 브로커('upbit'/'toss')별 1행(UNIQUE(broker)). python main.py trade
    # (또는 toss_trade) 프로세스는 재시작 없이 매 사이클(TRADE_LOOP_INTERVAL_SEC)마다 자기 브로커의
    # 이 값을 확인해 실행/일시중지를 반영한다. (예전엔 id=1 싱글톤이라 브로커 구분이 없었음 — 기존
    # 배포 DB는 아래 migrations의 재생성 마이그레이션으로 이 스키마로 옮겨진다.)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS trade_engine_settings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            broker TEXT NOT NULL DEFAULT 'upbit',
            enabled INTEGER NOT NULL DEFAULT 1,
            updated_at TEXT,
            UNIQUE(broker)
        )
    ''')

    # 매매 전략 파라미터(포지션당 매수금액/최대 동시보유/손절·익절 기준/루프 주기) — 브로커별 1행
    # (UNIQUE(broker), 예전엔 id=1 싱글톤). app/config.py의 TRADE_* 상수는 이 테이블에 그 브로커의
    # 행이 없을 때만 쓰이는 기본값이고, 대시보드에서 저장하면 이 테이블 값이 우선한다.
    # python main.py trade 프로세스는 재시작 없이 매 사이클마다 이 값을 다시 읽는다(app/core/auto_trader.py 참고).
    # stop_loss_confirm_cycles: 트레일링 손절 조건이 몇 사이클 연속으로 유지돼야 실제로 매도할지(기본 1=즉시,
    # 기존 동작과 동일). dca_trigger_pct: 물타기(추가매수) 체크된 포지션이 몇 % 하락(평단 대비)했을 때
    # 추가매수를 실행할지(기본 10%). dca_max_count: 포지션당 물타기 최대 허용 횟수(기본 2회, 무제한 방지).
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS trade_strategy_settings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            broker TEXT NOT NULL DEFAULT 'upbit',
            max_position_krw REAL NOT NULL,
            max_concurrent_positions INTEGER NOT NULL,
            stop_loss_pct REAL NOT NULL,
            take_profit_pct REAL NOT NULL,
            loop_interval_sec INTEGER NOT NULL,
            stop_loss_confirm_cycles INTEGER NOT NULL DEFAULT 1,
            dca_trigger_pct REAL NOT NULL DEFAULT 10.0,
            dca_max_count INTEGER NOT NULL DEFAULT 2,
            condition_check_interval_sec INTEGER NOT NULL DEFAULT 60,
            updated_at TEXT,
            UNIQUE(broker)
        )
    ''')

    # 매매 대상 코인 수동 승인(체크박스) — 사용자가 후보 중 일부만 체크하면 그 종목만 진입 대상으로
    # 좁힌다(대시보드/auto_trader.py 참고). 체크된 행이 하나도 없으면 기존처럼 전체 후보를 대상으로 함.
    # 체크 안 한 종목은 행 자체가 없어도 되므로(기본 미승인), 실제로 한 번이라도 토글된 종목만 저장된다.
    # condition_watch: "정밀 매수조건 검사" 대상 여부(별도 체크박스) — approved와 독립적인 opt-in.
    # 켜진 종목만 entry_condition_checker.py가 주기적으로 일봉/5분봉/1분봉을 조회해 조건을 검사한다
    # (전체 후보를 매번 다중 시간대로 조회하면 API 호출이 너무 많아지므로, 사용자가 지정한 종목만 대상).
    # watchlist: 실거래(live) 전용 1단계 필터 — "🎯 매매 대상 코인" 표에서 관심 등록한 종목만
    # "🔴 실거래" 표(2단계, approved로 실제 매수 승인)에 나타난다. approved와 별개 opt-in이라
    # watchlist 없이 approved만 켜는 건 UI상 불가능(이중 안전장치, app/core/auto_trader.py 참고).
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS trade_candidate_approval (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            broker TEXT NOT NULL DEFAULT 'upbit',
            mode TEXT NOT NULL DEFAULT 'paper',
            ticker TEXT NOT NULL,
            approved INTEGER NOT NULL DEFAULT 0,
            condition_watch INTEGER NOT NULL DEFAULT 0,
            watchlist INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT,
            UNIQUE(broker, mode, ticker)
        )
    ''')

    # 정밀 매수조건(일봉/5분봉/1분봉 등 다중 시간대) 정의 — app/core/entry_conditions.py가 이 설정으로
    # 조건을 계산한다. condition_key로 코드가 어떤 판단 로직을 쓸지 매칭하고, logic_group('AND'/'OR')은
    # 여러 켜진 조건을 어떻게 결합할지: AND 그룹은 전부 충족해야 하고, OR 그룹은 하나만 충족해도 됨
    # (AND 그룹 전부 충족 AND (OR 그룹이 비었거나 그중 하나 이상 충족)). params는 조건별 파라미터(JSON 문자열).
    # condition_key는 브로커마다 같은 키를 재사용하므로(예: 'daily_above_ma') UNIQUE(broker, condition_key)
    # 조합으로 구분한다(예전엔 condition_key 단독 UNIQUE라 브로커 구분이 없었음).
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS trade_condition_settings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            broker TEXT NOT NULL DEFAULT 'upbit',
            condition_key TEXT NOT NULL,
            label TEXT,
            enabled INTEGER NOT NULL DEFAULT 0,
            logic_group TEXT NOT NULL DEFAULT 'AND',
            params TEXT,
            updated_at TEXT,
            UNIQUE(broker, condition_key)
        )
    ''')

    # 정밀 매수조건 검사 결과 캐시 — entry_condition_checker.py가 별도 루프(조건 검사 주기)로 갱신하고,
    # auto_trader.py의 evaluate_entries()는 이 캐시된 결과만 읽는다(매매 사이클마다 재조회하지 않음).
    # detail: 조건별 개별 판단 결과 JSON({condition_key: {passed, message}, ...}) — 대시보드 툴팁용.
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS trade_condition_status (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            broker TEXT NOT NULL DEFAULT 'upbit',
            mode TEXT NOT NULL DEFAULT 'paper',
            ticker TEXT NOT NULL,
            passed INTEGER NOT NULL DEFAULT 0,
            detail TEXT,
            checked_at TEXT,
            UNIQUE(broker, mode, ticker)
        )
    ''')

    # 기존 테이블 컬럼 마이그레이션
    migrations = [
        'ALTER TABLE stock_market_cap_daily ADD COLUMN market_weight TEXT DEFAULT "0"',
        'ALTER TABLE stock_market_cap_daily ADD COLUMN fid_input_iscd TEXT DEFAULT "0000"',
        'ALTER TABLE stock_market_cap_daily ADD COLUMN volume TEXT DEFAULT "0"',
        'ALTER TABLE stock_raw_data ADD COLUMN api_type TEXT DEFAULT "UNKNOWN"',
        'CREATE UNIQUE INDEX IF NOT EXISTS idx_mktcap_unique ON stock_market_cap_daily(date, code, fid_input_iscd)',
        'CREATE UNIQUE INDEX IF NOT EXISTS idx_invtrend_unique ON investor_trend_daily(date, exch_div, mrkt_div, invr_cls_code)',
        'ALTER TABLE stock_hts_top_view_hourly ADD COLUMN prdy_vrss TEXT',
        'ALTER TABLE signal_score_daily ADD COLUMN hts_top_view_bonus_score INTEGER DEFAULT 0',
        'ALTER TABLE signal_score_daily ADD COLUMN top_interest_bonus_score INTEGER DEFAULT 0',
        # net_buy는 이름과 달리 실제로는 KIS API의 invt_new_psdg(투자 신 심리도) 필드였음 — 이름 정정
        'ALTER TABLE sector_index_daily RENAME COLUMN net_buy TO psychology_index',
        # 종목 메모 등급(태그)별로 컬럼을 나눠보기 위한 컬럼 추가
        'ALTER TABLE stock_memo ADD COLUMN grade TEXT DEFAULT "기타"',
        # 메모 정렬 기준을 작성일이 아닌 "마지막으로 손댄 시각"으로 바꾸기 위한 컬럼
        # (중요한 메모를 위로 올리는 용도) — 기존 행은 created_at 값으로 채워 넣음
        'ALTER TABLE stock_memo ADD COLUMN updated_at TEXT',
        # 바로가기 링크를 좌/우 영역으로 나눠 보여주기 위한 컬럼 (기존 행은 기본값 left)
        'ALTER TABLE quick_links ADD COLUMN side TEXT DEFAULT "left"',
        # 코인 스크리닝을 일봉 RSI/이평선 나열에서 4시간봉 기준 매매 후보 필터(돌파/구름/200선)로 재설계
        'ALTER TABLE coin_screening_daily ADD COLUMN ma200 REAL',
        'ALTER TABLE coin_screening_daily ADD COLUMN ma200_dist_pct REAL',
        'ALTER TABLE coin_screening_daily ADD COLUMN near_ma200 INTEGER',
        'ALTER TABLE coin_screening_daily ADD COLUMN above_cloud INTEGER',
        'ALTER TABLE coin_screening_daily ADD COLUMN breakout_4h INTEGER',
        'ALTER TABLE coin_screening_daily ADD COLUMN breakout_vol_ratio REAL',
        'ALTER TABLE coin_screening_daily ADD COLUMN breakout_candle_rate REAL',
        # 실시간 감시에 주봉 타임프레임 추가
        'ALTER TABLE alerts ADD COLUMN mweek TEXT',
        # 실시간 감시에 일봉 타임프레임 추가 (분봉 3종 제거, 주/일/4시간봉 3개로 재편)
        'ALTER TABLE alerts ADD COLUMN mday TEXT',
        # 번호 추천: 콤마문자열 한 컬럼(main)/extra였던 걸 번호 자리별 컬럼(num1~num6)+bonus로 분리
        'ALTER TABLE lottery_recommendations ADD COLUMN num1 INTEGER',
        'ALTER TABLE lottery_recommendations ADD COLUMN num2 INTEGER',
        'ALTER TABLE lottery_recommendations ADD COLUMN num3 INTEGER',
        'ALTER TABLE lottery_recommendations ADD COLUMN num4 INTEGER',
        'ALTER TABLE lottery_recommendations ADD COLUMN num5 INTEGER',
        'ALTER TABLE lottery_recommendations ADD COLUMN num6 INTEGER',
        'ALTER TABLE lottery_recommendations ADD COLUMN bonus INTEGER',
        # 자동매매: 트레일링 손절(최고가 대비 하락률) + 연속 확인 + 물타기(추가매수) 지원을 위한 컬럼 추가
        # (trade_strategy_settings/paper_positions는 이미 배포된 테이블이라 CREATE TABLE IF NOT EXISTS만으론
        # 기존 행에 반영이 안 돼 마이그레이션으로 추가)
        'ALTER TABLE paper_positions ADD COLUMN peak_price REAL',
        'ALTER TABLE paper_positions ADD COLUMN below_stop_streak INTEGER NOT NULL DEFAULT 0',
        'ALTER TABLE paper_positions ADD COLUMN dca_enabled INTEGER NOT NULL DEFAULT 0',
        'ALTER TABLE paper_positions ADD COLUMN dca_used INTEGER NOT NULL DEFAULT 0',
        'ALTER TABLE trade_strategy_settings ADD COLUMN stop_loss_confirm_cycles INTEGER NOT NULL DEFAULT 1',
        'ALTER TABLE trade_strategy_settings ADD COLUMN dca_trigger_pct REAL NOT NULL DEFAULT 10.0',
        # 물타기 1회 제한(dca_used) → N회 제한(dca_count/dca_max_count)으로 완화.
        # 기존에 이미 1회 물탄 행(dca_used=1)은 dca_count=1로 채워 넣어 남은 허용 횟수를 정확히 유지한다.
        'ALTER TABLE paper_positions ADD COLUMN dca_count INTEGER NOT NULL DEFAULT 0',
        'UPDATE paper_positions SET dca_count = 1 WHERE dca_used = 1 AND dca_count = 0',
        'ALTER TABLE trade_strategy_settings ADD COLUMN dca_max_count INTEGER NOT NULL DEFAULT 2',
        # 정밀 매수조건(일봉/5분봉/1분봉) 검사 기능 — 기존 배포 테이블에 컬럼 추가
        'ALTER TABLE trade_candidate_approval ADD COLUMN condition_watch INTEGER NOT NULL DEFAULT 0',
        'ALTER TABLE trade_strategy_settings ADD COLUMN condition_check_interval_sec INTEGER NOT NULL DEFAULT 60',

        # 토스증권 자동매매 추가: trade_engine_settings/trade_strategy_settings(예전 id=1 싱글톤)와
        # trade_condition_settings(예전 condition_key 단독 UNIQUE)는 브로커 구분이 없어 업비트/토스가
        # 설정을 공유하게 되므로, 브로커별로 분리한 새 스키마로 재생성한다. 이미 새 스키마인 DB에서는
        # RENAME 대상 테이블이 없어 첫 문장이 실패(무시)하고 나머지는 조용히 스킵됨 — 여러 번 실행해도 안전.
        'ALTER TABLE trade_engine_settings RENAME TO trade_engine_settings_v1_upbit_only',
        '''CREATE TABLE IF NOT EXISTS trade_engine_settings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                broker TEXT NOT NULL DEFAULT 'upbit',
                enabled INTEGER NOT NULL DEFAULT 1,
                updated_at TEXT,
                UNIQUE(broker)
            )''',
        '''INSERT INTO trade_engine_settings (broker, enabled, updated_at)
            SELECT 'upbit', enabled, updated_at FROM trade_engine_settings_v1_upbit_only
            WHERE NOT EXISTS (SELECT 1 FROM trade_engine_settings WHERE broker = 'upbit')''',
        'DROP TABLE IF EXISTS trade_engine_settings_v1_upbit_only',

        'ALTER TABLE trade_strategy_settings RENAME TO trade_strategy_settings_v1_upbit_only',
        '''CREATE TABLE IF NOT EXISTS trade_strategy_settings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                broker TEXT NOT NULL DEFAULT 'upbit',
                max_position_krw REAL NOT NULL,
                max_concurrent_positions INTEGER NOT NULL,
                stop_loss_pct REAL NOT NULL,
                take_profit_pct REAL NOT NULL,
                loop_interval_sec INTEGER NOT NULL,
                stop_loss_confirm_cycles INTEGER NOT NULL DEFAULT 1,
                dca_trigger_pct REAL NOT NULL DEFAULT 10.0,
                dca_max_count INTEGER NOT NULL DEFAULT 2,
                condition_check_interval_sec INTEGER NOT NULL DEFAULT 60,
                updated_at TEXT,
                UNIQUE(broker)
            )''',
        '''INSERT INTO trade_strategy_settings
                (broker, max_position_krw, max_concurrent_positions, stop_loss_pct, take_profit_pct,
                 loop_interval_sec, stop_loss_confirm_cycles, dca_trigger_pct, dca_max_count,
                 condition_check_interval_sec, updated_at)
            SELECT 'upbit', max_position_krw, max_concurrent_positions, stop_loss_pct, take_profit_pct,
                 loop_interval_sec, stop_loss_confirm_cycles, dca_trigger_pct, dca_max_count,
                 condition_check_interval_sec, updated_at
            FROM trade_strategy_settings_v1_upbit_only
            WHERE NOT EXISTS (SELECT 1 FROM trade_strategy_settings WHERE broker = 'upbit')''',
        'DROP TABLE IF EXISTS trade_strategy_settings_v1_upbit_only',

        # 업비트 실거래(live) 자동매매 추가: trade_engine_settings에 mode 컬럼을 넣어 같은 브로커라도
        # 모의(paper)/실거래(live) 실행 스위치를 독립적으로 켜고 끌 수 있게 한다(UNIQUE를 (broker, mode)로
        # 재구성). 기존 행은 전부 mode='paper'로 이관 — 이미 새 스키마인 DB에서는 RENAME 대상이 없어
        # 첫 문장이 실패(무시)하고 나머지는 조용히 스킵됨.
        'ALTER TABLE trade_engine_settings RENAME TO trade_engine_settings_v2_no_mode',
        '''CREATE TABLE IF NOT EXISTS trade_engine_settings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                broker TEXT NOT NULL DEFAULT 'upbit',
                mode TEXT NOT NULL DEFAULT 'paper',
                enabled INTEGER NOT NULL DEFAULT 1,
                updated_at TEXT,
                UNIQUE(broker, mode)
            )''',
        '''INSERT INTO trade_engine_settings (broker, mode, enabled, updated_at)
            SELECT broker, 'paper', enabled, updated_at FROM trade_engine_settings_v2_no_mode
            WHERE NOT EXISTS (
                SELECT 1 FROM trade_engine_settings t2
                WHERE t2.broker = trade_engine_settings_v2_no_mode.broker AND t2.mode = 'paper'
            )''',
        'DROP TABLE IF EXISTS trade_engine_settings_v2_no_mode',

        'ALTER TABLE trade_condition_settings RENAME TO trade_condition_settings_v1_upbit_only',
        '''CREATE TABLE IF NOT EXISTS trade_condition_settings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                broker TEXT NOT NULL DEFAULT 'upbit',
                condition_key TEXT NOT NULL,
                label TEXT,
                enabled INTEGER NOT NULL DEFAULT 0,
                logic_group TEXT NOT NULL DEFAULT 'AND',
                params TEXT,
                updated_at TEXT,
                UNIQUE(broker, condition_key)
            )''',
        '''INSERT INTO trade_condition_settings (broker, condition_key, label, enabled, logic_group, params, updated_at)
            SELECT 'upbit', condition_key, label, enabled, logic_group, params, updated_at
            FROM trade_condition_settings_v1_upbit_only
            WHERE NOT EXISTS (SELECT 1 FROM trade_condition_settings WHERE broker = 'upbit')''',
        'DROP TABLE IF EXISTS trade_condition_settings_v1_upbit_only',

        # 실거래 "매매 대상" 1단계 필터(watchlist) 추가 — 기존 배포 테이블에 컬럼만 얹는다.
        'ALTER TABLE trade_candidate_approval ADD COLUMN watchlist INTEGER NOT NULL DEFAULT 0',

        # 대시보드에 "마지막 실행/다음 실행 예정" 시각을 보여주기 위해 루프가 사이클을 처리할 때마다
        # 찍는 하트비트 컬럼 추가 — set_engine_last_cycle_at() 참고.
        'ALTER TABLE trade_engine_settings ADD COLUMN last_cycle_at TEXT',
    ]
    for sql in migrations:
        try:
            cursor.execute(sql)
        except Exception:
            pass

    # 정밀 매수조건 3종 기본 행 시딩(브로커별로 최초 1회, 이미 있으면 건드리지 않음) — 전부 기본
    # 비활성화(enabled=0)로 시작해서, 사용자가 대시보드에서 켜기 전까진 기존 동작(스크리닝 필터만)이
    # 그대로 유지된다. m5_ma_support는 토스는 5분봉 API가 없어 1분봉을 리샘플링해 계산한다
    # (app/core/toss_client.py의 get_candles_resampled 참고).
    default_conditions = [
        ('daily_above_ma', '일봉 종가가 N일 이동평균 이상', '{"ma_period": 20}'),
        ('m5_ma_support', '5분봉이 N선에 지지받고 반등(저가 근접 후 종가 위 마감)', '{"ma_period": 20, "touch_tolerance_pct": 0.3}'),
        ('m1_bb_breakout_volume', '1분봉이 볼린저밴드 상단을 거래량 동반 돌파', '{"bb_period": 20, "bb_mult": 2.0, "vol_lookback": 20, "vol_ratio_threshold": 2.0}'),
    ]
    for broker in ('upbit', 'toss'):
        for condition_key, label, params in default_conditions:
            try:
                cursor.execute('''
                    INSERT OR IGNORE INTO trade_condition_settings (broker, condition_key, label, enabled, logic_group, params)
                    VALUES (?, ?, ?, 0, 'AND', ?)
                ''', (broker, condition_key, label, params))
            except Exception:
                pass

    try:
        cursor.execute("UPDATE stock_memo SET updated_at = created_at WHERE updated_at IS NULL")
    except Exception:
        pass

    # 번호 추천 기존 행(main 콤마문자열/extra) → num1~num6/bonus로 1회성 백필
    try:
        cols = [c[1] for c in cursor.execute('PRAGMA table_info("lottery_recommendations")').fetchall()]
        if 'main' in cols and 'num1' in cols:
            old_rows = cursor.execute(
                "SELECT id, main, extra FROM lottery_recommendations WHERE num1 IS NULL AND main IS NOT NULL AND main != ''"
            ).fetchall()
            for rec_id, main_str, extra_val in old_rows:
                nums = [int(n) for n in main_str.split(',')] if main_str else []
                nums = (nums + [None] * 6)[:6]
                cursor.execute(
                    'UPDATE lottery_recommendations SET num1=?, num2=?, num3=?, num4=?, num5=?, num6=?, bonus=? WHERE id=?',
                    (*nums, extra_val, rec_id)
                )
    except Exception:
        pass

    conn.commit()
    conn.close()

def get_db_stats():
    """DB 파일 전체 크기 + 테이블별 행 수/컬럼/추정 용량 조회 (원본 데이터 페이지용)"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    page_count = cursor.execute("PRAGMA page_count").fetchone()[0]
    page_size = cursor.execute("PRAGMA page_size").fetchone()[0]
    db_size_bytes = page_count * page_size
    try:
        file_size_bytes = os.path.getsize(DB_PATH)
    except OSError:
        file_size_bytes = db_size_bytes

    table_names = [
        row[0] for row in cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        ).fetchall()
    ]

    tables = []
    for name in table_names:
        # 컬럼 정보
        col_rows = cursor.execute(f'PRAGMA table_info("{name}")').fetchall()
        columns = [
            {
                "name": c[1],
                "type": c[2] or "",
                "notnull": bool(c[3]),
                "pk": bool(c[5]),
            }
            for c in col_rows
        ]

        row_count = cursor.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]

        # 실제 페이지 단위 크기는 dbstat 가상 테이블이 없으면 알 수 없어,
        # 각 컬럼 값의 바이트 길이 합으로 근사치를 계산 (인덱스/페이지 오버헤드 제외)
        estimated_bytes = 0
        if row_count > 0 and columns:
            length_expr = " + ".join(
                f'IFNULL(LENGTH("{c["name"]}"), 0)' for c in columns
            )
            estimated_bytes = cursor.execute(
                f'SELECT IFNULL(SUM({length_expr}), 0) FROM "{name}"'
            ).fetchone()[0]

        tables.append({
            "name": name,
            "row_count": row_count,
            "estimated_bytes": estimated_bytes,
            "columns": columns,
        })

    conn.close()

    tables.sort(key=lambda t: t["estimated_bytes"], reverse=True)

    return {
        "file_size_bytes": file_size_bytes,
        "db_size_bytes": db_size_bytes,
        "page_count": page_count,
        "page_size": page_size,
        "tables": tables,
    }

def save_api_token(provider, token):
    """API 토큰 저장 (날짜 포함)"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    today = datetime.now().strftime('%Y-%m-%d')
    cursor.execute('''
        INSERT OR REPLACE INTO api_tokens (provider, token, issued_date)
        VALUES (?, ?, ?)
    ''', (provider, token, today))
    conn.commit()
    conn.close()

def get_api_token(provider):
    """오늘 날짜의 유효한 토큰 조회"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    today = datetime.now().strftime('%Y-%m-%d')
    cursor.execute('''
        SELECT token FROM api_tokens
        WHERE provider = ? AND issued_date = ?
    ''', (provider, today))
    row = cursor.fetchone()
    conn.close()
    return row[0] if row else None

def save_alert_to_db(ticker, surge_count, m240, m60, m30, m15, daily_info, url, mweek="-", mday="-"):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    cursor.execute('''
        INSERT INTO alerts (timestamp, ticker, surge_count, m240, m60, m30, m15, daily_info, url, mweek, mday)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (timestamp, ticker, surge_count, m240, m60, m30, m15, daily_info, url, mweek, mday))
    conn.commit()
    conn.close()

def save_stock_alert_to_db(code, name, price, change_rate, volume, volume_power, market_cap, reason, url):
    """주식 알림 저장"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    cursor.execute('''
        INSERT INTO stock_alerts (timestamp, code, name, price, change_rate, volume, volume_power, market_cap, reason, url)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (timestamp, code, name, price, change_rate, volume, volume_power, market_cap, reason, url))
    conn.commit()
    conn.close()

def get_latest_alerts(limit=100):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM alerts ORDER BY id DESC LIMIT ?', (limit,))
    rows = cursor.fetchall()
    conn.close()

    return [dict(row) for row in rows]

def get_latest_stock_alerts(limit=100):
    """최신 주식 알림 조회"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM stock_alerts ORDER BY id DESC LIMIT ?', (limit,))
    rows = cursor.fetchall()
    conn.close()

    return [dict(row) for row in rows]

def get_today_alert_count(ticker):
    """오늘 해당 티커의 알림 횟수 조회"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    today = datetime.now().strftime('%Y-%m-%d')
    cursor.execute('''
        SELECT COUNT(*) FROM alerts
        WHERE ticker = ? AND timestamp LIKE ?
    ''', (ticker, f"{today}%"))
    count = cursor.fetchone()[0]
    conn.close()
    return count

def delete_alert(alert_id):
    """특정 ID의 코인 알림을 삭제합니다."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('DELETE FROM alerts WHERE id = ?', (alert_id,))
    conn.commit()
    conn.close()

def delete_stock_alert(alert_id):
    """특정 ID의 주식 알림을 삭제합니다."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('DELETE FROM stock_alerts WHERE id = ?', (alert_id,))
    conn.commit()
    conn.close()

def save_stock_raw_data(data, api_type="UNKNOWN"):
    """주식 원본 데이터 저장"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    cursor.execute('''
        INSERT INTO stock_raw_data (timestamp, api_type, raw_json)
        VALUES (?, ?, ?)
    ''', (timestamp, api_type, json.dumps(data, ensure_ascii=False)))
    conn.commit()
    conn.close()

def get_latest_stock_raw_data(limit=10):
    """최신 주식 원본 데이터 조회"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM stock_raw_data ORDER BY id DESC LIMIT ?', (limit,))
    rows = cursor.fetchall()
    conn.close()

    return [dict(row) for row in rows]

def delete_oldest_stock_raw_data(count):
    """오래된 주식 원본 데이터를 id 오름차순(가장 오래된 것부터) count건 삭제.
    반환값: 실제 삭제된 행 수"""
    count = int(count)
    if count <= 0:
        return 0
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''
        DELETE FROM stock_raw_data
        WHERE id IN (
            SELECT id FROM stock_raw_data ORDER BY id ASC LIMIT ?
        )
    ''', (count,))
    deleted = cursor.rowcount
    conn.commit()
    conn.close()
    return deleted

def save_daily_market_cap(data_list: List[MarketCapRankingItem], fid_input_iscd: str = "0000"):
    """일별 시가총액 순위 데이터 일괄 저장 (당일 + 동일 시장구분 데이터 덮어쓰기)"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    today_date = datetime.now().strftime('%Y-%m-%d')
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    cursor.execute('DELETE FROM stock_market_cap_daily WHERE date = ? AND fid_input_iscd = ?', (today_date, fid_input_iscd))

    insert_data = []
    for item in data_list:
        insert_data.append((
            today_date,
            item.mksc_shrn_iscd,
            item.hts_kor_isnm,
            item.stck_avls,
            int(item.data_rank),
            item.stck_prpr,
            item.prdy_ctrt,
            item.mrkt_whol_avls_rlim,
            fid_input_iscd,
            item.acml_vol,
            timestamp
        ))

    cursor.executemany('''
        INSERT INTO stock_market_cap_daily (date, code, name, market_cap_amount, rank, price, change_rate, market_weight, fid_input_iscd, volume, timestamp)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', insert_data)

    conn.commit()
    conn.close()

def get_market_cap_history(code=None, limit_dates=7, fid_input_iscd="combined", date=None):
    """시총 순위 이력 조회. fid_input_iscd='combined' 이면 거래소+코스닥 합산."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    if fid_input_iscd == "combined":
        iscd_list = ("0001", "1001")
    else:
        iscd_list = (fid_input_iscd,)

    placeholders = ",".join("?" * len(iscd_list))

    if code:
        cursor.execute(f'''
            SELECT * FROM stock_market_cap_daily
            WHERE code = ? AND fid_input_iscd IN ({placeholders})
            ORDER BY date ASC
        ''', (code, *iscd_list))
    elif date:
        cursor.execute(f'''
            SELECT * FROM stock_market_cap_daily
            WHERE date = ? AND fid_input_iscd IN ({placeholders})
            ORDER BY CAST(market_cap_amount AS INTEGER) DESC
        ''', (date, *iscd_list))
    else:
        cursor.execute(f'''
            SELECT * FROM stock_market_cap_daily
            WHERE fid_input_iscd IN ({placeholders})
              AND date IN (
                  SELECT DISTINCT date FROM stock_market_cap_daily
                  WHERE fid_input_iscd IN ({placeholders})
                  ORDER BY date DESC LIMIT ?
              )
            ORDER BY date DESC, CAST(market_cap_amount AS INTEGER) DESC
        ''', (*iscd_list, *iscd_list, limit_dates))

    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]


def save_daily_investor_trend(output1: list, exch_div: str, mrkt_div: str):
    """투자자별 프로그램 매매동향 일별 저장 (당일 데이터 덮어쓰기)"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    today = datetime.now().strftime('%Y-%m-%d')
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    cursor.execute(
        'DELETE FROM investor_trend_daily WHERE date = ? AND exch_div = ? AND mrkt_div = ?',
        (today, exch_div, mrkt_div)
    )

    rows = []
    for item in output1:
        rows.append((
            today, exch_div, mrkt_div,
            item.get('invr_cls_code', ''),
            item.get('invr_cls_name', ''),
            item.get('all_shnu_amt', '0'),
            item.get('all_seln_amt', '0'),
            item.get('all_ntby_amt', '0'),
            item.get('all_shnu_qty', '0'),
            item.get('all_seln_qty', '0'),
            item.get('all_ntby_qty', '0'),
            item.get('arbt_shnu_amt', '0'),
            item.get('arbt_seln_amt', '0'),
            item.get('arbt_ntby_amt', '0'),
            item.get('arbt_shnu_qty', '0'),
            item.get('arbt_seln_qty', '0'),
            item.get('arbt_ntby_qty', '0'),
            item.get('nabt_shnu_amt', '0'),
            item.get('nabt_seln_amt', '0'),
            item.get('nabt_ntby_amt', '0'),
            item.get('nabt_shnu_qty', '0'),
            item.get('nabt_seln_qty', '0'),
            item.get('nabt_ntby_qty', '0'),
            timestamp,
        ))

    cursor.executemany('''
        INSERT INTO investor_trend_daily (
            date, exch_div, mrkt_div,
            invr_cls_code, invr_cls_name,
            all_shnu_amt, all_seln_amt, all_ntby_amt,
            all_shnu_qty, all_seln_qty, all_ntby_qty,
            arbt_shnu_amt, arbt_seln_amt, arbt_ntby_amt,
            arbt_shnu_qty, arbt_seln_qty, arbt_ntby_qty,
            nabt_shnu_amt, nabt_seln_amt, nabt_ntby_amt,
            nabt_shnu_qty, nabt_seln_qty, nabt_ntby_qty,
            timestamp
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    ''', rows)

    conn.commit()
    conn.close()


def get_investor_trend_history(exch_div: str, mrkt_div: str, limit_days: int = 30):
    """투자자별 프로그램 매매동향 이력 조회"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute('''
        SELECT * FROM investor_trend_daily
        WHERE exch_div = ? AND mrkt_div = ?
          AND date IN (
              SELECT DISTINCT date FROM investor_trend_daily
              WHERE exch_div = ? AND mrkt_div = ?
              ORDER BY date DESC LIMIT ?
          )
        ORDER BY date DESC, invr_cls_code ASC
    ''', (exch_div, mrkt_div, exch_div, mrkt_div, limit_days))
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]


def save_stock_investor_daily(code: str, name: str, items: list):
    """종목별 투자자매매동향 일별 저장 (날짜별 upsert)"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    for item in items:
        date = item.stck_bsop_date[:4] + '-' + item.stck_bsop_date[4:6] + '-' + item.stck_bsop_date[6:]
        cursor.execute('''
            INSERT INTO stock_investor_daily
                (date, code, name, frgn_ntby_qty, frgn_ntby_tr_pbmn,
                 prsn_ntby_qty, prsn_ntby_tr_pbmn, orgn_ntby_qty, orgn_ntby_tr_pbmn, timestamp)
            VALUES (?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(date, code) DO UPDATE SET
                frgn_ntby_qty=excluded.frgn_ntby_qty,
                frgn_ntby_tr_pbmn=excluded.frgn_ntby_tr_pbmn,
                prsn_ntby_qty=excluded.prsn_ntby_qty,
                prsn_ntby_tr_pbmn=excluded.prsn_ntby_tr_pbmn,
                orgn_ntby_qty=excluded.orgn_ntby_qty,
                orgn_ntby_tr_pbmn=excluded.orgn_ntby_tr_pbmn,
                timestamp=excluded.timestamp
        ''', (date, code, name,
              item.frgn_ntby_qty, item.frgn_ntby_tr_pbmn,
              item.prsn_ntby_qty, item.prsn_ntby_tr_pbmn,
              item.orgn_ntby_qty, item.orgn_ntby_tr_pbmn,
              timestamp))
    conn.commit()
    conn.close()


def get_stock_investor_combined(date: str, fid_input_iscd: str = "combined"):
    """시총 순위 + 투자자 순매수 합산 조회 (특정 날짜)"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    if fid_input_iscd == "combined":
        iscd_list = ("0001", "1001")
    else:
        iscd_list = (fid_input_iscd,)

    placeholders = ",".join("?" * len(iscd_list))

    cursor.execute(f'''
        SELECT
            m.date, m.code, m.name, m.rank, m.price, m.change_rate,
            m.market_cap_amount, m.market_weight, m.fid_input_iscd,
            COALESCE(i.frgn_ntby_qty, '0')       AS frgn_ntby_qty,
            COALESCE(i.frgn_ntby_tr_pbmn, '0')   AS frgn_ntby_tr_pbmn,
            COALESCE(i.prsn_ntby_qty, '0')        AS prsn_ntby_qty,
            COALESCE(i.prsn_ntby_tr_pbmn, '0')   AS prsn_ntby_tr_pbmn,
            COALESCE(i.orgn_ntby_qty, '0')        AS orgn_ntby_qty,
            COALESCE(i.orgn_ntby_tr_pbmn, '0')   AS orgn_ntby_tr_pbmn
        FROM stock_market_cap_daily m
        LEFT JOIN stock_investor_daily i ON m.date = i.date AND m.code = i.code
        WHERE m.date = ? AND m.fid_input_iscd IN ({placeholders})
        ORDER BY m.rank ASC
    ''', (date, *iscd_list))

    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]


def get_stock_investor_trend(code: str):
    """특정 종목의 날짜별 투자자 순매수 + 시총 이력 조회"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute('''
        SELECT
            i.date, i.code, i.name,
            i.frgn_ntby_qty, i.frgn_ntby_tr_pbmn,
            i.prsn_ntby_qty, i.prsn_ntby_tr_pbmn,
            i.orgn_ntby_qty, i.orgn_ntby_tr_pbmn,
            COALESCE(m.rank, 0)               AS rank,
            COALESCE(m.market_cap_amount, '0') AS market_cap_amount,
            COALESCE(m.price, '0')            AS price,
            COALESCE(m.change_rate, '0')      AS change_rate
        FROM stock_investor_daily i
        LEFT JOIN (
            SELECT date, code, rank, market_cap_amount, price, change_rate
            FROM stock_market_cap_daily
            GROUP BY date, code
        ) m ON i.date = m.date AND i.code = m.code
        WHERE i.code = ?
        ORDER BY i.date ASC
    ''', (code,))
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]


def get_latest_market_cap_date(fid_input_iscd: str = "combined") -> str:
    """가장 최근 시총 데이터 날짜 반환"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    if fid_input_iscd == "combined":
        iscd_list = ("0001", "1001")
    else:
        iscd_list = (fid_input_iscd,)
    placeholders = ",".join("?" * len(iscd_list))
    cursor.execute(f'''
        SELECT MAX(date) FROM stock_market_cap_daily
        WHERE fid_input_iscd IN ({placeholders})
    ''', iscd_list)
    row = cursor.fetchone()
    conn.close()
    return row[0] if row and row[0] else ""


def save_hts_top_view(items: list, date: str, hour: int):
    """HTS조회상위20종목 시간별 스냅샷 저장 (같은 date+hour 데이터는 덮어쓰기).
    items: [{rank, code, market_div, name, price, change_rate, prdy_vrss}, ...]
    prdy_vrss(전일대비, 부호 포함)는 전일종가 = price - prdy_vrss 로 프론트에서 역산해 참고용으로 보여주는 데 쓰인다.
    """
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    cursor.execute('DELETE FROM stock_hts_top_view_hourly WHERE date = ? AND hour = ?', (date, hour))

    insert_data = [
        (date, hour, it['rank'], it['code'], it['market_div'], it.get('name'), it.get('price'), it.get('change_rate'),
         it.get('prdy_vrss'), timestamp)
        for it in items
    ]
    cursor.executemany('''
        INSERT INTO stock_hts_top_view_hourly (date, hour, rank, code, market_div, name, price, change_rate, prdy_vrss, timestamp)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', insert_data)

    conn.commit()
    conn.close()


def save_top_gainers_snapshot(items: list, date: str, hour: int):
    """상승률 순위 시간대별 스냅샷 저장 (같은 date+hour 데이터는 덮어쓰기).
    items: KIS 등락률 순위 API 원시 output 레코드 리스트
    (data_rank, stck_shrn_iscd, hts_kor_isnm, stck_prpr, prdy_ctrt, prdy_vrss, acml_vol)
    """
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    cursor.execute('DELETE FROM stock_top_gainers_hourly WHERE date = ? AND hour = ?', (date, hour))

    insert_data = [
        (date, hour, it.get('data_rank'), it.get('stck_shrn_iscd'), it.get('hts_kor_isnm'),
         it.get('stck_prpr'), it.get('prdy_ctrt'), it.get('prdy_vrss'), it.get('acml_vol'), timestamp)
        for it in items
    ]
    cursor.executemany('''
        INSERT INTO stock_top_gainers_hourly (date, hour, rank, code, name, price, change_rate, prdy_vrss, volume, timestamp)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', insert_data)

    conn.commit()
    conn.close()


def get_top_gainers_snapshot_dates(limit: int = 60):
    """상승률 순위 스냅샷이 저장된 (date, hour) 목록 (최신순)"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute('''
        SELECT DISTINCT date, hour FROM stock_top_gainers_hourly
        ORDER BY date DESC, hour DESC LIMIT ?
    ''', (limit,))
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]


def get_top_gainers_history(date: str = None, hour: int = None):
    """상승률 순위 스냅샷 조회. date+hour 지정 시 해당 스냅샷, date만 지정 시 그날 전체 시간대,
    미지정 시 가장 최근 저장된 스냅샷 1개."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    if date and hour is not None:
        cursor.execute('''
            SELECT * FROM stock_top_gainers_hourly
            WHERE date = ? AND hour = ?
            ORDER BY rank ASC
        ''', (date, hour))
    elif date:
        cursor.execute('''
            SELECT * FROM stock_top_gainers_hourly
            WHERE date = ?
            ORDER BY hour DESC, rank ASC
        ''', (date,))
    else:
        cursor.execute('''
            SELECT * FROM stock_top_gainers_hourly
            WHERE (date, hour) = (
                SELECT date, hour FROM stock_top_gainers_hourly
                ORDER BY date DESC, hour DESC LIMIT 1
            )
            ORDER BY rank ASC
        ''')

    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]


def get_top_gainers_range(date_from: str, date_to: str):
    """상승률 순위 구간(date_from~date_to) 추이 조회 — 날짜별 종목 매트릭스용.
    하루 4번(9:10/12:10/15:10/18:10) 스냅샷 중 그날 가장 높은 순위(가장 작은 rank) 1건으로 집계한다.
    같은 날짜에 HTS조회상위20종목/관심종목등록 상위에도 있었는지(hts/top_interest 불린)를 함께 표시해
    "여러 순위에서 동시에 포착됐는지" 관심도를 볼 수 있게 한다.
    반환: [{date, code, name, rank, price, change_rate, volume, hts, top_interest}, ...] (날짜 오름차순, 날짜 내 순위 오름차순)
    """
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute('''
        SELECT * FROM stock_top_gainers_hourly
        WHERE date BETWEEN ? AND ?
        ORDER BY date ASC, rank ASC
    ''', (date_from, date_to))
    rows = cursor.fetchall()

    cursor.execute('''
        SELECT DISTINCT date, code FROM stock_hts_top_view_hourly
        WHERE date BETWEEN ? AND ?
    ''', (date_from, date_to))
    hts_set = {(r['date'], r['code']) for r in cursor.fetchall()}

    cursor.execute('''
        SELECT DISTINCT date, code FROM stock_top_interest_daily
        WHERE date BETWEEN ? AND ?
    ''', (date_from, date_to))
    interest_set = {(r['date'], r['code']) for r in cursor.fetchall()}

    conn.close()

    # 날짜별로 이미 rank 오름차순이므로, (date, code) 조합에서 첫 등장이 그날의 최고 순위
    best = {}
    for r in rows:
        key = (r['date'], r['code'])
        if key not in best:
            item = dict(r)
            item['hts'] = key in hts_set
            item['top_interest'] = key in interest_set
            best[key] = item

    result = list(best.values())
    result.sort(key=lambda r: (r['date'], r['rank']))
    return result


def get_top_gainers_export(limit_days: int = 7) -> list:
    """동기화 전송용 — 최근 N일치 상승률 순위 원본 스냅샷 전체 반환 (해당 기간 내 모든 시간대 포함)."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cutoff = (datetime.now() - timedelta(days=limit_days)).strftime('%Y-%m-%d')
    cursor.execute('''
        SELECT date, hour, rank, code, name, price, change_rate, prdy_vrss, volume, timestamp
        FROM stock_top_gainers_hourly
        WHERE date >= ?
        ORDER BY date DESC, hour DESC, rank ASC
    ''', (cutoff,))
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]


def sync_upsert_top_gainers(rows: list) -> int:
    """stock_top_gainers_hourly upsert (date+hour+code 기준) — 원격 동기화 수신용"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    saved = 0
    for r in rows:
        cursor.execute('''
            INSERT INTO stock_top_gainers_hourly
                (date, hour, rank, code, name, price, change_rate, prdy_vrss, volume, timestamp)
            VALUES (?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(date, hour, code) DO UPDATE SET
                rank=excluded.rank, name=excluded.name, price=excluded.price,
                change_rate=excluded.change_rate, prdy_vrss=excluded.prdy_vrss,
                volume=excluded.volume, timestamp=excluded.timestamp
        ''', (r.get('date'), r.get('hour'), r.get('rank'), r.get('code'), r.get('name'),
              r.get('price'), r.get('change_rate'), r.get('prdy_vrss'), r.get('volume'),
              r.get('timestamp', datetime.now().strftime('%Y-%m-%d %H:%M:%S'))))
        saved += 1
    conn.commit()
    conn.close()
    return saved


def get_hts_top_view_history(date: str = None, limit_snapshots: int = 24):
    """HTS조회상위20종목 이력 조회. date 지정 시 해당 날짜 전체, 미지정 시 최근 limit_snapshots개 (date,hour) 스냅샷."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    if date:
        cursor.execute('''
            SELECT * FROM stock_hts_top_view_hourly
            WHERE date = ?
            ORDER BY hour DESC, rank ASC
        ''', (date,))
    else:
        cursor.execute('''
            SELECT * FROM stock_hts_top_view_hourly
            WHERE (date, hour) IN (
                SELECT DISTINCT date, hour FROM stock_hts_top_view_hourly
                ORDER BY date DESC, hour DESC LIMIT ?
            )
            ORDER BY date DESC, hour DESC, rank ASC
        ''', (limit_snapshots,))

    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]


def get_hts_top_view_export(limit_days: int = 7) -> list:
    """동기화 전송용 — 최근 N일치 HTS조회상위 원본 스냅샷 전체 반환 (해당 기간 내 모든 시간대 포함)."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cutoff = (datetime.now() - timedelta(days=limit_days)).strftime('%Y-%m-%d')
    cursor.execute('''
        SELECT date, hour, rank, code, market_div, name, price, change_rate, prdy_vrss, timestamp
        FROM stock_hts_top_view_hourly
        WHERE date >= ?
        ORDER BY date DESC, hour DESC, rank ASC
    ''', (cutoff,))
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]


def sync_upsert_hts_top_view(rows: list) -> int:
    """stock_hts_top_view_hourly upsert (date+hour+code 기준) — 원격 동기화 수신용"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    saved = 0
    for r in rows:
        cursor.execute('''
            INSERT INTO stock_hts_top_view_hourly
                (date, hour, rank, code, market_div, name, price, change_rate, prdy_vrss, timestamp)
            VALUES (?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(date, hour, code) DO UPDATE SET
                rank=excluded.rank, market_div=excluded.market_div, name=excluded.name,
                price=excluded.price, change_rate=excluded.change_rate, prdy_vrss=excluded.prdy_vrss,
                timestamp=excluded.timestamp
        ''', (r.get('date'), r.get('hour'), r.get('rank'), r.get('code'), r.get('market_div'),
              r.get('name'), r.get('price'), r.get('change_rate'), r.get('prdy_vrss'),
              r.get('timestamp', datetime.now().strftime('%Y-%m-%d %H:%M:%S'))))
        saved += 1
    conn.commit()
    conn.close()
    return saved


def save_top_interest_daily(items: list, date: str):
    """관심종목등록 상위 일별 스냅샷 저장 (같은 date 데이터는 덮어쓰기).
    items: [{rank, code, name, market_div, price, change_rate, reg_count}, ...]
    """
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    cursor.execute('DELETE FROM stock_top_interest_daily WHERE date = ?', (date,))

    insert_data = [
        (date, it['rank'], it['code'], it.get('name'), it.get('market_div'),
         it.get('price'), it.get('change_rate'), it.get('reg_count'), timestamp)
        for it in items
    ]
    cursor.executemany('''
        INSERT INTO stock_top_interest_daily (date, rank, code, name, market_div, price, change_rate, reg_count, timestamp)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', insert_data)

    conn.commit()
    conn.close()


def get_top_interest_range(date_from: str, date_to: str) -> list:
    """관심종목등록 상위 구간 조회 (date_from~date_to 포함, 날짜별 1스냅샷씩).
    반환: [{date, rank, code, name, market_div, price, change_rate, reg_count, timestamp}, ...]
    """
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute('''
        SELECT * FROM stock_top_interest_daily
        WHERE date BETWEEN ? AND ?
        ORDER BY date DESC, rank ASC
    ''', (date_from, date_to))
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]


def get_top_interest_export(limit_days: int = 7) -> list:
    """동기화 전송용 — 최근 N일치 관심종목등록 상위 원본 스냅샷 전체 반환."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cutoff = (datetime.now() - timedelta(days=limit_days)).strftime('%Y-%m-%d')
    cursor.execute('''
        SELECT date, rank, code, name, market_div, price, change_rate, reg_count, timestamp
        FROM stock_top_interest_daily
        WHERE date >= ?
        ORDER BY date DESC, rank ASC
    ''', (cutoff,))
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]


def sync_upsert_top_interest(rows: list) -> int:
    """stock_top_interest_daily upsert (date+code 기준) — 원격 동기화 수신용"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    saved = 0
    for r in rows:
        cursor.execute('''
            INSERT INTO stock_top_interest_daily
                (date, rank, code, name, market_div, price, change_rate, reg_count, timestamp)
            VALUES (?,?,?,?,?,?,?,?,?)
            ON CONFLICT(date, code) DO UPDATE SET
                rank=excluded.rank, name=excluded.name, market_div=excluded.market_div,
                price=excluded.price, change_rate=excluded.change_rate, reg_count=excluded.reg_count,
                timestamp=excluded.timestamp
        ''', (r.get('date'), r.get('rank'), r.get('code'), r.get('name'), r.get('market_div'),
              r.get('price'), r.get('change_rate'), r.get('reg_count'),
              r.get('timestamp', datetime.now().strftime('%Y-%m-%d %H:%M:%S'))))
        saved += 1
    conn.commit()
    conn.close()
    return saved


def get_hts_top_view_cumulative(date_from: str, date_to: str):
    """HTS조회상위20종목 구간 누적 점수 조회 (date_from~date_to 포함).
    스냅샷마다 순위 기준 (20 - rank)점을 부여해 종목별로 합산 — 여러 시간대에 걸쳐
    꾸준히 상위권에 머문 종목일수록 높은 점수. 점수 내림차순 정렬.
    반환: [{code, market_div, name, score, appearances, best_rank}, ...]
    """
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute('''
        SELECT code,
               MAX(market_div) AS market_div,
               MAX(name) AS name,
               SUM(20 - rank) AS score,
               COUNT(*) AS appearances,
               MIN(rank) AS best_rank
        FROM stock_hts_top_view_hourly
        WHERE date BETWEEN ? AND ?
        GROUP BY code
        ORDER BY score DESC
    ''', (date_from, date_to))
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]


def get_hts_top_view_daily_scores(date_from: str, date_to: str):
    """HTS조회상위20종목 구간 내 날짜별 합산 점수 조회 (date_from~date_to 포함).
    get_hts_top_view_cumulative와 같은 (20-rank) 점수 방식이지만, 구간 전체를 하나로 합치지 않고
    날짜 단위로 따로 집계한다 — 날짜별 순위 매트릭스를 만들 때 사용.
    반환: [{code, market_div, name, date, score, appearances}, ...] (날짜 오름차순, 날짜 내 점수 내림차순)
    """
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute('''
        SELECT code,
               date,
               MAX(market_div) AS market_div,
               MAX(name) AS name,
               SUM(20 - rank) AS score,
               COUNT(*) AS appearances
        FROM stock_hts_top_view_hourly
        WHERE date BETWEEN ? AND ?
        GROUP BY code, date
        ORDER BY date ASC, score DESC
    ''', (date_from, date_to))
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]


def save_job_run_log(job_name: str, description: str, api_used: str, start_time: str, end_time: str,
                      success: bool, count: int = None, error_message: str = None, trigger_type: str = 'auto'):
    """스케줄링 작업(자동/수동) 실행 결과 1건 기록.
    start_time/end_time: '%Y-%m-%d %H:%M:%S' 형식 문자열.
    """
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    duration_sec = None
    try:
        fmt = '%Y-%m-%d %H:%M:%S'
        duration_sec = (datetime.strptime(end_time, fmt) - datetime.strptime(start_time, fmt)).total_seconds()
    except (ValueError, TypeError):
        pass

    cursor.execute('''
        INSERT INTO job_run_log
            (job_name, description, api_used, start_time, end_time, duration_sec, success, count, error_message, trigger_type)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (job_name, description, api_used, start_time, end_time, duration_sec,
          1 if success else 0, count, error_message, trigger_type))

    conn.commit()
    conn.close()


def get_job_run_log(days: int = 7, job_name: str = None, limit: int = 500) -> list:
    """최근 N일치 작업 실행 이력 조회 (최신순)."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    since = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d %H:%M:%S')
    if job_name:
        cursor.execute('''
            SELECT * FROM job_run_log
            WHERE start_time >= ? AND job_name = ?
            ORDER BY start_time DESC LIMIT ?
        ''', (since, job_name, limit))
    else:
        cursor.execute('''
            SELECT * FROM job_run_log
            WHERE start_time >= ?
            ORDER BY start_time DESC LIMIT ?
        ''', (since, limit))

    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]


def get_recent_avg_volume(code: str, avg_days: int = 20) -> dict:
    """종목의 최근 avg_days "완결된 이전 거래일" 평균 거래량 조회 (오늘 날짜는 항상 제외).
    실시간 감시(stock_monitor.run_stock_monitor)에서 장중 누적거래량(acml_vol)과 비교할 분모로 쓴다.
    get_volume_ratio()와 달리 "오늘" 행이 있어도(오전 8:30 백업 수집 등, 장중이라 거래량이 미미/0) 제외하고
    순수 과거 N거래일치만으로 평균을 낸다.
    반환: {code, avg_volume, days_used, latest_date}
    """
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    today_str = datetime.now().strftime('%Y-%m-%d')
    cursor.execute('''
        SELECT date, MAX(CAST(volume AS INTEGER)) AS volume
        FROM stock_market_cap_daily
        WHERE code = ? AND date < ? AND volume IS NOT NULL AND volume != '' AND volume != '0'
        GROUP BY date
        ORDER BY date DESC
        LIMIT ?
    ''', (code, today_str, avg_days))
    rows = cursor.fetchall()
    conn.close()

    if not rows:
        return {'code': code, 'avg_volume': 0, 'days_used': 0, 'latest_date': None}

    days_used = len(rows)
    avg_volume = sum(r['volume'] for r in rows) / days_used
    return {
        'code': code,
        'avg_volume': round(avg_volume, 2),
        'days_used': days_used,
        'latest_date': rows[0]['date'],
    }


def get_volume_ratio(code: str, avg_days: int = 20) -> dict:
    """종목의 최근 N일 평균 거래량 대비 당일 거래량 배수 계산.
    반환: {code, date, today_volume, avg_volume, ratio, days_used}
    days_used < avg_days 이면 아직 데이터가 avg_days만큼 쌓이지 않았다는 뜻(참고용으로만 사용).
    """
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute('''
        SELECT date, MAX(CAST(volume AS INTEGER)) AS volume
        FROM stock_market_cap_daily
        WHERE code = ? AND volume IS NOT NULL AND volume != '' AND volume != '0'
        GROUP BY date
        ORDER BY date DESC
        LIMIT ?
    ''', (code, avg_days + 1))
    rows = cursor.fetchall()
    conn.close()

    if not rows:
        return {'code': code, 'date': None, 'today_volume': 0, 'avg_volume': 0, 'ratio': 0.0, 'days_used': 0}

    today_row = rows[0]
    past_rows = rows[1:]

    today_volume = today_row['volume']
    days_used = len(past_rows)
    avg_volume = (sum(r['volume'] for r in past_rows) / days_used) if days_used > 0 else 0
    ratio = (today_volume / avg_volume) if avg_volume > 0 else 0.0

    return {
        'code': code,
        'date': today_row['date'],
        'today_volume': today_volume,
        'avg_volume': round(avg_volume, 2),
        'ratio': round(ratio, 3),
        'days_used': days_used,
    }


def get_volume_ratio_batch(date: str = None, avg_days: int = 20, fid_input_iscd: str = "combined") -> list:
    """특정 날짜(기본: 최신일) 기준, 해당 날짜에 데이터가 있는 전 종목의 거래량 배수를 일괄 계산.
    반환: [{code, name, date, today_volume, avg_volume, ratio, days_used}, ...] ratio 내림차순 정렬
    """
    if not date:
        date = get_latest_market_cap_date(fid_input_iscd)
    if not date:
        return []

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute('''
        SELECT DISTINCT code, name FROM stock_market_cap_daily WHERE date = ?
    ''', (date,))
    stocks = [(r['code'], r['name']) for r in cursor.fetchall()]
    conn.close()

    results = []
    for code, name in stocks:
        ratio_info = get_volume_ratio(code, avg_days=avg_days)
        if ratio_info['date'] != date:
            continue
        ratio_info['name'] = name
        results.append(ratio_info)

    results.sort(key=lambda x: x['ratio'], reverse=True)
    return results


def get_volume_collection_status() -> dict:
    """실제 거래량(volume != 0)이 저장된 날짜가 며칠치 쌓였는지 확인.
    20일 평균 계산이 얼마나 신뢰할 만한지 가늠하는 용도.
    반환: {days_collected, latest_date, dates}
    """
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''
        SELECT DISTINCT date FROM stock_market_cap_daily
        WHERE volume IS NOT NULL AND volume != '' AND volume != '0'
        ORDER BY date DESC
    ''')
    dates = [r[0] for r in cursor.fetchall()]
    conn.close()
    return {
        'days_collected': len(dates),
        'latest_date': dates[0] if dates else None,
        'dates': dates,
    }


def _score_momentum(ratio: float, change_rate: float) -> int:
    """거래량 배수 + 등락률 조합 → 모멘텀 점수(0~30점).
    거래량 급증(배수↑) + 가격 상승(등락률↑)이 동시에 나타날수록 고득점.
    거래량은 늘었는데 가격이 빠지는 경우(분산/이탈 신호)는 모멘텀이 아니라 리스크패널티에서
    감점으로 전담 처리한다(get_risk_penalty의 "거래량 급증+당일 하락" 항목) — 중복 반영 방지.
    """
    if ratio >= 3 and change_rate > 3:
        return 30
    if ratio >= 3 and change_rate > 0:
        return 25
    if ratio >= 2 and change_rate > 1:
        return 20
    if ratio >= 2 and change_rate > 0:
        return 15
    if ratio >= 1.5 and change_rate > 0:
        return 10
    return 0


def get_momentum_score(code: str, avg_days: int = 20) -> dict:
    """종목 하나의 모멘텀 점수(거래량 배수 + 당일 등락률 조합, 0~30점) 산출.
    반환: {code, date, ratio, change_rate, score, days_used}
    """
    ratio_info = get_volume_ratio(code, avg_days=avg_days)
    if not ratio_info['date']:
        return {**ratio_info, 'change_rate': None, 'score': 0}

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute('''
        SELECT change_rate FROM stock_market_cap_daily
        WHERE code = ? AND date = ? LIMIT 1
    ''', (code, ratio_info['date']))
    row = cursor.fetchone()
    conn.close()

    change_rate = float(row['change_rate']) if row and row['change_rate'] not in (None, '') else 0.0
    score = _score_momentum(ratio_info['ratio'], change_rate)

    return {
        'code': code,
        'date': ratio_info['date'],
        'ratio': ratio_info['ratio'],
        'change_rate': change_rate,
        'score': score,
        'days_used': ratio_info['days_used'],
    }


def get_momentum_score_batch(date: str = None, avg_days: int = 20, fid_input_iscd: str = "combined") -> list:
    """특정 날짜(기본: 최신일) 기준, 전 종목의 모멘텀 점수를 일괄 계산.
    반환: [{code, name, date, ratio, change_rate, score, days_used}, ...] 점수 내림차순 정렬
    """
    if not date:
        date = get_latest_market_cap_date(fid_input_iscd)
    if not date:
        return []

    ratio_rows = get_volume_ratio_batch(date=date, avg_days=avg_days, fid_input_iscd=fid_input_iscd)

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute('SELECT code, change_rate FROM stock_market_cap_daily WHERE date = ?', (date,))
    change_map = {r['code']: r['change_rate'] for r in cursor.fetchall()}
    conn.close()

    results = []
    for r in ratio_rows:
        cr = change_map.get(r['code'])
        change_rate = float(cr) if cr not in (None, '') else 0.0
        score = _score_momentum(r['ratio'], change_rate)
        results.append({**r, 'change_rate': change_rate, 'score': score})

    results.sort(key=lambda x: x['score'], reverse=True)
    return results


def get_rank_stability_score(code: str, trend_days: int = 5, fid_input_iscd: str = "combined") -> dict:
    """시가총액 랭킹 안정성 점수(0~15점) 산출.
    - 최신 랭킹이 상위 100위 이내면 +10 (소형주 리스크 대비 신뢰도)
    - trend_days 영업일 전 대비 랭킹이 상승(숫자가 작아짐)했으면 +5
    반환: {code, date, rank, rank_before, rank_change, score}
    """
    if fid_input_iscd == "combined":
        iscd_list = ("0001", "1001")
    else:
        iscd_list = (fid_input_iscd,)
    placeholders = ",".join("?" * len(iscd_list))

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute(f'''
        SELECT date, rank FROM stock_market_cap_daily
        WHERE code = ? AND fid_input_iscd IN ({placeholders})
        ORDER BY date DESC
        LIMIT ?
    ''', (code, *iscd_list, trend_days + 1))
    rows = cursor.fetchall()
    conn.close()

    if not rows:
        return {'code': code, 'date': None, 'rank': None, 'rank_before': None, 'rank_change': None, 'score': 0}

    rank = rows[0]['rank']
    score = 10 if rank <= 100 else 0

    rank_before = None
    rank_change = None
    if len(rows) > 1:
        before_row = rows[min(trend_days, len(rows) - 1)]
        rank_before = before_row['rank']
        rank_change = rank_before - rank  # 양수 = 랭킹 상승(숫자 감소)
        if rank_change > 0:
            score += 5

    return {
        'code': code,
        'date': rows[0]['date'],
        'rank': rank,
        'rank_before': rank_before,
        'rank_change': rank_change,
        'score': score,
    }


def get_rank_stability_score_batch(date: str = None, trend_days: int = 5, fid_input_iscd: str = "combined") -> list:
    """특정 날짜(기본: 최신일) 기준, 전 종목의 랭킹 안정성 점수를 일괄 계산.
    반환: [{code, name, date, rank, rank_before, rank_change, score}, ...] 점수 내림차순, 동점이면 랭킹 오름차순
    """
    if not date:
        date = get_latest_market_cap_date(fid_input_iscd)
    if not date:
        return []

    if fid_input_iscd == "combined":
        iscd_list = ("0001", "1001")
    else:
        iscd_list = (fid_input_iscd,)
    placeholders = ",".join("?" * len(iscd_list))

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute(f'''
        SELECT DISTINCT code, name FROM stock_market_cap_daily
        WHERE date = ? AND fid_input_iscd IN ({placeholders})
    ''', (date, *iscd_list))
    stocks = [(r['code'], r['name']) for r in cursor.fetchall()]
    conn.close()

    results = []
    for code, name in stocks:
        info = get_rank_stability_score(code, trend_days=trend_days, fid_input_iscd=fid_input_iscd)
        if info['date'] != date:
            continue
        info['name'] = name
        results.append(info)

    results.sort(key=lambda x: (-x['score'], x['rank']))
    return results


def _score_supply_demand(frgn_total: int, orgn_total: int) -> int:
    """외국인/기관 N일 누적 순매수 조합 → 수급 점수(-15~30점).
    둘 다 순매수면 고득점, 하나만 순매수면 중간 점수, 둘 다 순매도(동반 이탈)면 감점.
    """
    if frgn_total > 0 and orgn_total > 0:
        return 30
    if frgn_total > 0 or orgn_total > 0:
        return 15
    if frgn_total < 0 and orgn_total < 0:
        return -15
    return 0


def get_supply_demand_score(code: str, days: int = 3) -> dict:
    """종목 하나의 외국인/기관 N일(기본 3일) 누적 순매수 기반 수급 점수(-15~30점) 산출.
    반환: {code, date_from, date_to, frgn_total, orgn_total, days_used, score}
    """
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute('''
        SELECT date,
               CAST(frgn_ntby_tr_pbmn AS INTEGER) AS frgn,
               CAST(orgn_ntby_tr_pbmn AS INTEGER) AS orgn
        FROM stock_investor_daily
        WHERE code = ?
        ORDER BY date DESC
        LIMIT ?
    ''', (code, days))
    rows = cursor.fetchall()
    conn.close()

    if not rows:
        return {'code': code, 'date_from': None, 'date_to': None,
                'frgn_total': 0, 'orgn_total': 0, 'days_used': 0, 'score': 0}

    frgn_total = sum(r['frgn'] for r in rows)
    orgn_total = sum(r['orgn'] for r in rows)

    return {
        'code': code,
        'date_from': rows[-1]['date'],
        'date_to': rows[0]['date'],
        'frgn_total': frgn_total,
        'orgn_total': orgn_total,
        'days_used': len(rows),
        'score': _score_supply_demand(frgn_total, orgn_total),
    }


def get_supply_demand_score_batch(days: int = 3) -> list:
    """stock_investor_daily에 데이터가 있는 전 종목의 수급 점수를 일괄 계산.
    반환: [{code, name, date_from, date_to, frgn_total, orgn_total, days_used, score}, ...] 점수 내림차순
    (참고: 투자자매매동향은 시총 상위 종목만 수집되므로 전체 상장 종목이 아님)
    """
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute('SELECT DISTINCT code, name FROM stock_investor_daily')
    stocks = [(r['code'], r['name']) for r in cursor.fetchall()]
    conn.close()

    results = []
    for code, name in stocks:
        info = get_supply_demand_score(code, days=days)
        if info['days_used'] == 0:
            continue
        info['name'] = name
        results.append(info)

    results.sort(key=lambda x: x['score'], reverse=True)
    return results


def _get_index_score(sector_code: str, date: str) -> dict:
    """시장 지수(코스피=0001/코스닥=1001) 당일 등락률 + 5영업일 추세로 점수(0~15점) 산출.
    - 당일 등락률 양수 → +10
    - 5영업일 전 종가 대비 오늘 종가가 높으면(상승 추세) → +5
    """
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute('''
        SELECT date, close, change_rate FROM sector_index_daily
        WHERE sector_code = ? AND date <= ?
        ORDER BY date DESC LIMIT 6
    ''', (sector_code, date))
    rows = cursor.fetchall()
    conn.close()

    if not rows:
        return {'index_change_rate': None, 'index_trend_up': None, 'score': 0}

    today = rows[0]
    change_rate = float(today['change_rate']) if today['change_rate'] not in (None, '') else 0.0
    score = 10 if change_rate > 0 else 0

    trend_up = None
    if len(rows) > 1:
        oldest = rows[-1]
        try:
            trend_up = float(today['close']) > float(oldest['close'])
        except (TypeError, ValueError):
            trend_up = None
        if trend_up:
            score += 5

    return {'index_change_rate': change_rate, 'index_trend_up': trend_up, 'score': score}


def get_market_environment_score(code: str, date: str = None) -> dict:
    """종목이 속한 시장(코스피/코스닥) 지수의 환경 점수(0~15점) 산출.
    반환: {code, market, date, index_change_rate, index_trend_up, score}
    """
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    if date:
        cursor.execute('''
            SELECT date, fid_input_iscd FROM stock_market_cap_daily
            WHERE code = ? AND date = ? AND fid_input_iscd IN ('0001', '1001')
            LIMIT 1
        ''', (code, date))
    else:
        cursor.execute('''
            SELECT date, fid_input_iscd FROM stock_market_cap_daily
            WHERE code = ? AND fid_input_iscd IN ('0001', '1001')
            ORDER BY date DESC LIMIT 1
        ''', (code,))
    row = cursor.fetchone()
    conn.close()

    if not row:
        return {'code': code, 'market': None, 'date': None,
                'index_change_rate': None, 'index_trend_up': None, 'score': 0}

    idx = _get_index_score(row['fid_input_iscd'], row['date'])
    return {'code': code, 'market': row['fid_input_iscd'], 'date': row['date'], **idx}


def get_market_environment_score_batch(date: str = None, fid_input_iscd: str = "combined") -> list:
    """특정 날짜(기본: 최신일) 기준, 전 종목의 시장 환경 점수를 일괄 계산.
    같은 시장(코스피/코스닥) 종목은 지수 점수를 공유하므로 시장별로 한 번만 계산 후 매핑.
    반환: [{code, name, market, date, index_change_rate, index_trend_up, score}, ...]
    """
    if not date:
        date = get_latest_market_cap_date(fid_input_iscd)
    if not date:
        return []

    if fid_input_iscd == "combined":
        iscd_list = ("0001", "1001")
    else:
        iscd_list = (fid_input_iscd,)

    market_scores = {iscd: _get_index_score(iscd, date) for iscd in iscd_list}

    placeholders = ",".join("?" * len(iscd_list))
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute(f'''
        SELECT DISTINCT code, name, fid_input_iscd FROM stock_market_cap_daily
        WHERE date = ? AND fid_input_iscd IN ({placeholders})
    ''', (date, *iscd_list))
    rows = cursor.fetchall()
    conn.close()

    results = []
    for r in rows:
        idx = market_scores.get(r['fid_input_iscd'], {'index_change_rate': None, 'index_trend_up': None, 'score': 0})
        results.append({
            'code': r['code'], 'name': r['name'], 'market': r['fid_input_iscd'], 'date': date,
            **idx,
        })
    return results


def _get_cumulative_return(code: str, days: int = 5, fid_input_iscd: str = "combined") -> dict:
    """최근 N영업일 누적 상승률(%) 계산 (오늘 종가 vs N영업일 전 종가).
    반환: {date, price_now, price_before, cum_return, days_used}
    """
    if fid_input_iscd == "combined":
        iscd_list = ("0001", "1001")
    else:
        iscd_list = (fid_input_iscd,)
    placeholders = ",".join("?" * len(iscd_list))

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute(f'''
        SELECT date, price FROM stock_market_cap_daily
        WHERE code = ? AND fid_input_iscd IN ({placeholders})
        ORDER BY date DESC LIMIT ?
    ''', (code, *iscd_list, days + 1))
    rows = cursor.fetchall()
    conn.close()

    if len(rows) < 2:
        return {'date': rows[0]['date'] if rows else None, 'price_now': None,
                'price_before': None, 'cum_return': 0.0, 'days_used': len(rows)}

    price_now = float(rows[0]['price'])
    price_before = float(rows[min(days, len(rows) - 1)]['price'])
    cum_return = ((price_now - price_before) / price_before * 100) if price_before else 0.0

    return {
        'date': rows[0]['date'],
        'price_now': price_now,
        'price_before': price_before,
        'cum_return': round(cum_return, 2),
        'days_used': len(rows) - 1,
    }


def get_risk_penalty(code: str, fid_input_iscd: str = "combined") -> dict:
    """과열·이탈·수급 신호를 감지해 리스크 조정 점수(-25~15점) 산출.
    반영 조건 (근거 데이터가 있는 것만 — 고가/저가/시가가 없어 윗꼬리·갭상승은 제외):
    - 최근 7영업일 누적 상승률 30% 이상(추세 지속 강세로 판단) → +15
    - 당일 거래량 배수 2배 이상인데 등락률이 음수(거래량은 터졌는데 하락, 이탈 신호) → -10
    - 외국인·기관 3일 동반 순매도 → -15
    (합산 후 -20점 하한, 상한은 두지 않음)
    반환: {code, date, cum_return_7d, ratio, change_rate, supply_demand_score, penalties, score}
    """
    momentum = get_momentum_score(code)
    ret_info = _get_cumulative_return(code, days=7, fid_input_iscd=fid_input_iscd)
    supply_info = get_supply_demand_score(code, days=3)

    penalties = []
    if ret_info['cum_return'] >= 30:
        penalties.append({'reason': '최근 7일 강한 상승 추세(+30% 이상)', 'value': 15})
    if momentum['ratio'] >= 2 and momentum.get('change_rate') is not None and momentum['change_rate'] < 0:
        penalties.append({'reason': '거래량 급증+당일 하락(이탈 신호)', 'value': -10})
    if supply_info['score'] == -15:
        penalties.append({'reason': '외국인·기관 동반 순매도', 'value': -15})

    score = max(sum(p['value'] for p in penalties), -20)

    return {
        'code': code,
        'date': momentum.get('date') or ret_info.get('date'),
        'cum_return_7d': ret_info['cum_return'],
        'ratio': momentum['ratio'],
        'change_rate': momentum.get('change_rate'),
        'supply_demand_score': supply_info['score'],
        'penalties': penalties,
        'score': score,
    }


def get_risk_penalty_batch(date: str = None, fid_input_iscd: str = "combined") -> list:
    """특정 날짜(기본: 최신일) 기준, 전 종목의 리스크 패널티를 일괄 계산.
    반환: [{code, name, ...get_risk_penalty 결과...}, ...] 점수 오름차순(가장 위험한 종목이 먼저)
    """
    if not date:
        date = get_latest_market_cap_date(fid_input_iscd)
    if not date:
        return []

    if fid_input_iscd == "combined":
        iscd_list = ("0001", "1001")
    else:
        iscd_list = (fid_input_iscd,)
    placeholders = ",".join("?" * len(iscd_list))

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute(f'''
        SELECT DISTINCT code, name FROM stock_market_cap_daily
        WHERE date = ? AND fid_input_iscd IN ({placeholders})
    ''', (date, *iscd_list))
    stocks = [(r['code'], r['name']) for r in cursor.fetchall()]
    conn.close()

    results = []
    for code, name in stocks:
        info = get_risk_penalty(code, fid_input_iscd=fid_input_iscd)
        if info['date'] != date:
            continue
        info['name'] = name
        results.append(info)

    results.sort(key=lambda x: x['score'])
    return results


def get_hts_top_view_bonus(code: str, date: str = None) -> dict:
    """HTS조회상위20종목 당일 등장 여부에 따른 가점(0~20점) 산출.
    date 기준(미지정 시 최신 시가총액 날짜)으로 그날 HTS조회상위에 등장했다면 최고 순위 구간별 가점.
    - 1~3위 등장 → +20
    - 4~10위 등장 → +12
    - 11위 이하 등장 → +6
    - 그날 미등장 → 0
    같은 날 여러 시간대에 걸쳐 등장했으면 그중 최고 순위(가장 작은 rank) 기준.
    반환: {code, date, best_rank, appearances, score}
    """
    if not date:
        date = get_latest_market_cap_date()
    if not date:
        return {'code': code, 'date': None, 'best_rank': None, 'appearances': 0, 'score': 0}

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute('''
        SELECT MIN(rank) AS best_rank, COUNT(*) AS appearances
        FROM stock_hts_top_view_hourly
        WHERE code = ? AND date = ?
    ''', (code, date))
    row = cursor.fetchone()
    conn.close()

    best_rank = row['best_rank'] if row and row['best_rank'] is not None else None
    appearances = row['appearances'] if row else 0

    if best_rank is None:
        score = 0
    elif best_rank <= 3:
        score = 20
    elif best_rank <= 10:
        score = 12
    else:
        score = 6

    return {'code': code, 'date': date, 'best_rank': best_rank, 'appearances': appearances, 'score': score}


def get_top_interest_bonus(code: str, date: str = None) -> dict:
    """관심종목등록 상위 당일 등장 여부에 따른 가점(0~10점) 산출.
    HTS조회상위 가점(0~20점)과 합쳐 "관심도 보너스" 최대 30점을 구성하는 두 번째 축.
    date 기준(미지정 시 최신 시가총액 날짜)으로 그날 관심종목등록 상위에 등장했다면 순위 구간별 가점.
    - 1~3위 등장 → +10
    - 4~10위 등장 → +6
    - 11위 이하 등장 → +3
    - 그날 미등장 → 0
    반환: {code, date, rank, reg_count, score}
    """
    if not date:
        date = get_latest_market_cap_date()
    if not date:
        return {'code': code, 'date': None, 'rank': None, 'reg_count': None, 'score': 0}

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute('''
        SELECT rank, reg_count FROM stock_top_interest_daily
        WHERE code = ? AND date = ?
        LIMIT 1
    ''', (code, date))
    row = cursor.fetchone()
    conn.close()

    rank = row['rank'] if row else None
    reg_count = row['reg_count'] if row else None

    if rank is None:
        score = 0
    elif rank <= 3:
        score = 10
    elif rank <= 10:
        score = 6
    else:
        score = 3

    return {'code': code, 'date': date, 'rank': rank, 'reg_count': reg_count, 'score': score}


def _grade_for_score(total: int) -> str:
    """종합 점수 → 알림 등급(A/B/C/제외)."""
    if total >= 80:
        return 'A'
    if total >= 65:
        return 'B'
    if total >= 50:
        return 'C'
    return '제외'


def get_signal_score(code: str, fid_input_iscd: str = "combined") -> dict:
    """1~7번 점수(모멘텀·수급·랭킹안정성·시장환경·리스크패널티·HTS조회상위가점·관심종목등록가점)를
    합산한 종합 Signal Score 산출.
    HTS조회상위가점(0~20)+관심종목등록가점(0~10) = "관심도 보너스" 최대 30점(합산 후 30점 상한).
    등급: A(80점↑) / B(65~79) / C(50~64) / 제외(50 미만)
    반환: {code, date, momentum_score, supply_demand_score, rank_stability_score,
           market_environment_score, risk_penalty_score, hts_top_view_bonus_score,
           top_interest_bonus_score, total, grade, detail}
    """
    momentum = get_momentum_score(code)
    supply = get_supply_demand_score(code, days=3)
    rank = get_rank_stability_score(code, fid_input_iscd=fid_input_iscd)
    market = get_market_environment_score(code)
    risk = get_risk_penalty(code, fid_input_iscd=fid_input_iscd)
    date = momentum.get('date') or rank.get('date') or market.get('date') or risk.get('date')
    hts_bonus = get_hts_top_view_bonus(code, date=date)
    top_interest_bonus = get_top_interest_bonus(code, date=date)
    interest_bonus_total = min(hts_bonus['score'] + top_interest_bonus['score'], 30)

    total = momentum['score'] + supply['score'] + rank['score'] + market['score'] + risk['score'] + interest_bonus_total

    return {
        'code': code,
        'date': date,
        'momentum_score': momentum['score'],
        'supply_demand_score': supply['score'],
        'rank_stability_score': rank['score'],
        'market_environment_score': market['score'],
        'risk_penalty_score': risk['score'],
        'hts_top_view_bonus_score': hts_bonus['score'],
        'top_interest_bonus_score': top_interest_bonus['score'],
        'total': total,
        'grade': _grade_for_score(total),
        'detail': {
            'momentum': momentum,
            'supply_demand': supply,
            'rank_stability': rank,
            'market_environment': market,
            'risk_penalty': risk,
            'hts_top_view_bonus': hts_bonus,
            'top_interest_bonus': top_interest_bonus,
        },
    }


def get_signal_score_batch(date: str = None, fid_input_iscd: str = "combined", save: bool = False) -> list:
    """특정 날짜(기본: 최신일) 기준, 전 종목의 종합 Signal Score를 일괄 계산.
    save=True면 signal_score_daily 테이블에 upsert 저장.
    반환: [{code, name, ...get_signal_score 결과(detail 제외)...}, ...] 총점 내림차순
    """
    if not date:
        date = get_latest_market_cap_date(fid_input_iscd)
    if not date:
        return []

    if fid_input_iscd == "combined":
        iscd_list = ("0001", "1001")
    else:
        iscd_list = (fid_input_iscd,)
    placeholders = ",".join("?" * len(iscd_list))

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute(f'''
        SELECT DISTINCT code, name FROM stock_market_cap_daily
        WHERE date = ? AND fid_input_iscd IN ({placeholders})
    ''', (date, *iscd_list))
    stocks = [(r['code'], r['name']) for r in cursor.fetchall()]
    conn.close()

    results = []
    for code, name in stocks:
        info = get_signal_score(code, fid_input_iscd=fid_input_iscd)
        if info['date'] != date:
            continue
        info['name'] = name
        results.append(info)

    results.sort(key=lambda x: x['total'], reverse=True)

    if save:
        save_signal_score_daily(results)

    return results


def save_signal_score_daily(rows: list) -> int:
    """get_signal_score_batch() 결과를 signal_score_daily 테이블에 upsert 저장."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    saved = 0
    for r in rows:
        cursor.execute('''
            INSERT INTO signal_score_daily
                (date, code, name, momentum_score, supply_demand_score, rank_stability_score,
                 market_environment_score, risk_penalty_score, hts_top_view_bonus_score,
                 top_interest_bonus_score, total_score, grade, timestamp)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(date, code) DO UPDATE SET
                name=excluded.name, momentum_score=excluded.momentum_score,
                supply_demand_score=excluded.supply_demand_score,
                rank_stability_score=excluded.rank_stability_score,
                market_environment_score=excluded.market_environment_score,
                risk_penalty_score=excluded.risk_penalty_score,
                hts_top_view_bonus_score=excluded.hts_top_view_bonus_score,
                top_interest_bonus_score=excluded.top_interest_bonus_score,
                total_score=excluded.total_score, grade=excluded.grade, timestamp=excluded.timestamp
        ''', (r['date'], r['code'], r['name'], r['momentum_score'], r['supply_demand_score'],
              r['rank_stability_score'], r['market_environment_score'], r['risk_penalty_score'],
              r.get('hts_top_view_bonus_score', 0), r.get('top_interest_bonus_score', 0),
              r['total'], r['grade'], timestamp))
        saved += 1
    conn.commit()
    conn.close()
    return saved


def get_signal_score_history(date: str = None, grade: str = None, limit: int = 100) -> list:
    """signal_score_daily 저장된 결과 조회. date 미지정 시 최신 저장 날짜 기준."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    if not date:
        cursor.execute('SELECT MAX(date) FROM signal_score_daily')
        row = cursor.fetchone()
        date = row[0] if row and row[0] else None
    if not date:
        conn.close()
        return []

    if grade:
        cursor.execute('''
            SELECT * FROM signal_score_daily WHERE date = ? AND grade = ?
            ORDER BY total_score DESC LIMIT ?
        ''', (date, grade, limit))
    else:
        cursor.execute('''
            SELECT * FROM signal_score_daily WHERE date = ?
            ORDER BY total_score DESC LIMIT ?
        ''', (date, limit))
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_signal_score_range(date_from: str, date_to: str) -> list:
    """signal_score_daily 저장된 결과를 구간(date_from~date_to)으로 조회 — 날짜별 추이 매트릭스용.
    반환: [{date, code, name, ...점수 필드들..., total_score, grade}, ...] (날짜 오름차순, 날짜 내 총점 내림차순)
    """
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute('''
        SELECT * FROM signal_score_daily
        WHERE date BETWEEN ? AND ?
        ORDER BY date ASC, total_score DESC
    ''', (date_from, date_to))
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]


# Initialize DB on load
init_db()


# ──────────────────────────────────────────────
# 데이터 동기화 (로컬 → 서버 push) 함수들
# ──────────────────────────────────────────────

def sync_upsert_market_cap(rows: list) -> int:
    """stock_market_cap_daily upsert (date+code+fid_input_iscd 기준)"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    saved = 0
    for r in rows:
        cursor.execute('''
            INSERT INTO stock_market_cap_daily
                (date, code, name, market_cap_amount, rank, price, change_rate, market_weight, fid_input_iscd, volume, timestamp)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(date, code, fid_input_iscd) DO UPDATE SET
                name=excluded.name, market_cap_amount=excluded.market_cap_amount,
                rank=excluded.rank, price=excluded.price, change_rate=excluded.change_rate,
                market_weight=excluded.market_weight, volume=excluded.volume, timestamp=excluded.timestamp
        ''', (r.get('date'), r.get('code'), r.get('name'), r.get('market_cap_amount'),
              r.get('rank'), r.get('price'), r.get('change_rate'),
              r.get('market_weight', '0'), r.get('fid_input_iscd', '0001'), r.get('volume', '0'),
              r.get('timestamp', datetime.now().strftime('%Y-%m-%d %H:%M:%S'))))
        saved += 1
    conn.commit()
    conn.close()
    return saved


def sync_upsert_investor_daily(rows: list) -> int:
    """stock_investor_daily upsert (date+code 기준)"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    saved = 0
    for r in rows:
        cursor.execute('''
            INSERT INTO stock_investor_daily
                (date, code, name, frgn_ntby_qty, frgn_ntby_tr_pbmn,
                 prsn_ntby_qty, prsn_ntby_tr_pbmn, orgn_ntby_qty, orgn_ntby_tr_pbmn, timestamp)
            VALUES (?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(date, code) DO UPDATE SET
                name=excluded.name, frgn_ntby_qty=excluded.frgn_ntby_qty,
                frgn_ntby_tr_pbmn=excluded.frgn_ntby_tr_pbmn,
                prsn_ntby_qty=excluded.prsn_ntby_qty, prsn_ntby_tr_pbmn=excluded.prsn_ntby_tr_pbmn,
                orgn_ntby_qty=excluded.orgn_ntby_qty, orgn_ntby_tr_pbmn=excluded.orgn_ntby_tr_pbmn,
                timestamp=excluded.timestamp
        ''', (r.get('date'), r.get('code'), r.get('name'),
              r.get('frgn_ntby_qty', '0'), r.get('frgn_ntby_tr_pbmn', '0'),
              r.get('prsn_ntby_qty', '0'), r.get('prsn_ntby_tr_pbmn', '0'),
              r.get('orgn_ntby_qty', '0'), r.get('orgn_ntby_tr_pbmn', '0'),
              r.get('timestamp', datetime.now().strftime('%Y-%m-%d %H:%M:%S'))))
        saved += 1
    conn.commit()
    conn.close()
    return saved


def sync_upsert_investor_trend(rows: list) -> int:
    """investor_trend_daily upsert (date+exch_div+mrkt_div+invr_cls_code 기준)"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    saved = 0
    for r in rows:
        cursor.execute('''
            INSERT INTO investor_trend_daily
                (date, exch_div, mrkt_div, invr_cls_code, invr_cls_name,
                 all_shnu_amt, all_seln_amt, all_ntby_amt, all_shnu_qty, all_seln_qty, all_ntby_qty,
                 arbt_shnu_amt, arbt_seln_amt, arbt_ntby_amt, arbt_shnu_qty, arbt_seln_qty, arbt_ntby_qty,
                 nabt_shnu_amt, nabt_seln_amt, nabt_ntby_amt, nabt_shnu_qty, nabt_seln_qty, nabt_ntby_qty,
                 timestamp)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(date, exch_div, mrkt_div, invr_cls_code) DO UPDATE SET
                invr_cls_name=excluded.invr_cls_name,
                all_shnu_amt=excluded.all_shnu_amt, all_seln_amt=excluded.all_seln_amt, all_ntby_amt=excluded.all_ntby_amt,
                all_shnu_qty=excluded.all_shnu_qty, all_seln_qty=excluded.all_seln_qty, all_ntby_qty=excluded.all_ntby_qty,
                timestamp=excluded.timestamp
        ''', (r.get('date'), r.get('exch_div'), r.get('mrkt_div'), r.get('invr_cls_code'), r.get('invr_cls_name'),
              r.get('all_shnu_amt','0'), r.get('all_seln_amt','0'), r.get('all_ntby_amt','0'),
              r.get('all_shnu_qty','0'), r.get('all_seln_qty','0'), r.get('all_ntby_qty','0'),
              r.get('arbt_shnu_amt','0'), r.get('arbt_seln_amt','0'), r.get('arbt_ntby_amt','0'),
              r.get('arbt_shnu_qty','0'), r.get('arbt_seln_qty','0'), r.get('arbt_ntby_qty','0'),
              r.get('nabt_shnu_amt','0'), r.get('nabt_seln_amt','0'), r.get('nabt_ntby_amt','0'),
              r.get('nabt_shnu_qty','0'), r.get('nabt_seln_qty','0'), r.get('nabt_ntby_qty','0'),
              r.get('timestamp', datetime.now().strftime('%Y-%m-%d %H:%M:%S'))))
        saved += 1
    conn.commit()
    conn.close()
    return saved


def sync_upsert_sector_index(rows: list) -> int:
    """sector_index_daily upsert (date+sector_code 기준)"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    saved = 0
    for r in rows:
        cursor.execute('''
            INSERT INTO sector_index_daily
                (date, sector_code, sector_name, close, open, high, low,
                 change, change_sign, change_rate, volume, trade_amount, vol_ratio, psychology_index, d20_dsrt, timestamp)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(date, sector_code) DO UPDATE SET
                sector_name=excluded.sector_name, close=excluded.close, open=excluded.open,
                high=excluded.high, low=excluded.low, change=excluded.change,
                change_sign=excluded.change_sign, change_rate=excluded.change_rate,
                volume=excluded.volume, trade_amount=excluded.trade_amount,
                vol_ratio=excluded.vol_ratio, psychology_index=excluded.psychology_index,
                d20_dsrt=excluded.d20_dsrt, timestamp=excluded.timestamp
        ''', (r.get('date'), r.get('sector_code'), r.get('sector_name'),
              r.get('close','0'), r.get('open','0'), r.get('high','0'), r.get('low','0'),
              r.get('change','0'), r.get('change_sign','3'), r.get('change_rate','0'),
              r.get('volume','0'), r.get('trade_amount','0'), r.get('vol_ratio','0'),
              r.get('psychology_index', r.get('net_buy', '0')), r.get('d20_dsrt','0'),
              r.get('timestamp', datetime.now().strftime('%Y-%m-%d %H:%M:%S'))))
        saved += 1
    conn.commit()
    conn.close()
    return saved


def get_sector_index_cached(sector_code: str, limit: int = 30) -> list:
    """sector_index_daily 캐시 조회"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute('''
        SELECT * FROM sector_index_daily
        WHERE sector_code = ?
        ORDER BY date DESC LIMIT ?
    ''', (sector_code, limit))
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_investor_trend_cached(exch_div: str = 'J', mrkt_div: str = '1') -> list:
    """investor_trend_daily 최신 날짜 데이터 조회"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute('''
        SELECT * FROM investor_trend_daily
        WHERE exch_div = ? AND mrkt_div = ?
        ORDER BY date DESC, invr_cls_code ASC
        LIMIT 20
    ''', (exch_div, mrkt_div))
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def save_sector_index_daily(records: list, iscd: str, sector_name: str):
    """업종 일자별지수 저장 (KIS API output2 원시 레코드 → sector_index_daily upsert)"""
    SIGN = {'1': '상한', '2': '상승', '3': '보합', '4': '하한', '5': '하락'}
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    saved = 0
    for r in records:
        d = r.get('stck_bsop_date', '')
        if len(d) != 8:
            continue
        date = f"{d[:4]}-{d[4:6]}-{d[6:]}"
        cursor.execute('''
            INSERT INTO sector_index_daily
                (date, sector_code, sector_name, close, open, high, low,
                 change, change_sign, change_rate, volume, trade_amount, vol_ratio, psychology_index, d20_dsrt, timestamp)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(date, sector_code) DO UPDATE SET
                sector_name=excluded.sector_name, close=excluded.close, open=excluded.open,
                high=excluded.high, low=excluded.low, change=excluded.change,
                change_sign=excluded.change_sign, change_rate=excluded.change_rate,
                volume=excluded.volume, trade_amount=excluded.trade_amount,
                vol_ratio=excluded.vol_ratio, psychology_index=excluded.psychology_index,
                d20_dsrt=excluded.d20_dsrt, timestamp=excluded.timestamp
        ''', (
            date, iscd, sector_name,
            r.get('bstp_nmix_prpr', '0'), r.get('bstp_nmix_oprc', '0'),
            r.get('bstp_nmix_hgpr', '0'), r.get('bstp_nmix_lwpr', '0'),
            r.get('bstp_nmix_prdy_vrss', '0'),
            SIGN.get(r.get('prdy_vrss_sign', '3'), '보합'),
            r.get('bstp_nmix_prdy_ctrt', '0'),
            r.get('acml_vol', '0'), r.get('acml_tr_pbmn', '0'),
            r.get('acml_vol_rlim', '0'), r.get('invt_new_psdg', '0'),
            r.get('d20_dsrt', '0'), timestamp
        ))
        saved += 1
    conn.commit()
    conn.close()
    return saved


def save_sector_stocks_daily(records: list, iscd: str, sector_name: str):
    """업종 소속 종목 저장 (KIS 등락률 순위 API 원시 레코드 → sector_stocks_daily upsert, 당일 스냅샷)"""
    SIGN = {'1': '상한', '2': '상승', '3': '보합', '4': '하한', '5': '하락'}
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    date = datetime.now().strftime('%Y-%m-%d')
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    saved = 0
    for r in records:
        code = r.get('stck_shrn_iscd', '')
        if not code:
            continue
        cursor.execute('''
            INSERT INTO sector_stocks_daily
                (date, sector_code, sector_name, rank, code, name, price, change, change_sign, change_rate, volume, timestamp)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(date, sector_code, code) DO UPDATE SET
                sector_name=excluded.sector_name, rank=excluded.rank, name=excluded.name,
                price=excluded.price, change=excluded.change, change_sign=excluded.change_sign,
                change_rate=excluded.change_rate, volume=excluded.volume, timestamp=excluded.timestamp
        ''', (
            date, iscd, sector_name,
            r.get('data_rank', '0'), code, r.get('hts_kor_isnm', ''),
            r.get('stck_prpr', '0'), r.get('prdy_vrss', '0'),
            SIGN.get(r.get('prdy_vrss_sign', '3'), '보합'),
            r.get('prdy_ctrt', '0'), r.get('acml_vol', '0'), timestamp
        ))
        saved += 1
    conn.commit()
    conn.close()
    return saved


def get_sector_stocks_cached(sector_code: str, limit: int = 30) -> list:
    """sector_stocks_daily 최신 날짜 캐시 조회"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute('''
        SELECT * FROM sector_stocks_daily
        WHERE sector_code = ? AND date = (
            SELECT MAX(date) FROM sector_stocks_daily WHERE sector_code = ?
        )
        ORDER BY CAST(rank AS INTEGER) ASC LIMIT ?
    ''', (sector_code, sector_code, limit))
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def sync_upsert_sector_stocks(rows: list) -> int:
    """sector_stocks_daily upsert (date+sector_code+code 기준) — 원격 동기화 수신용"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    saved = 0
    for r in rows:
        cursor.execute('''
            INSERT INTO sector_stocks_daily
                (date, sector_code, sector_name, rank, code, name, price, change, change_sign, change_rate, volume, timestamp)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(date, sector_code, code) DO UPDATE SET
                sector_name=excluded.sector_name, rank=excluded.rank, name=excluded.name,
                price=excluded.price, change=excluded.change, change_sign=excluded.change_sign,
                change_rate=excluded.change_rate, volume=excluded.volume, timestamp=excluded.timestamp
        ''', (r.get('date'), r.get('sector_code'), r.get('sector_name'),
              r.get('rank', '0'), r.get('code'), r.get('name', ''),
              r.get('price', '0'), r.get('change', '0'), r.get('change_sign', '3'),
              r.get('change_rate', '0'), r.get('volume', '0'),
              r.get('timestamp', datetime.now().strftime('%Y-%m-%d %H:%M:%S'))))
        saved += 1
    conn.commit()
    conn.close()
    return saved


def get_stock_memos(code: str, limit: int = 100) -> list:
    """종목의 전체 메모 이력 조회 (최신순). 종목당 여러 개 저장 가능."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute('''
        SELECT * FROM stock_memo WHERE code = ? ORDER BY updated_at DESC, id DESC LIMIT ?
    ''', (code, limit))
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def add_stock_memo(code: str, name: str, memo: str, grade: str = '기타') -> int:
    """종목 메모 새로 추가 (항상 새 로그 항목으로 INSERT). 반환: 생성된 row id."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    cursor.execute('''
        INSERT INTO stock_memo (code, name, memo, grade, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)
    ''', (code, name, memo, grade or '기타', timestamp, timestamp))
    new_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return new_id


def bump_stock_memo(memo_id: int) -> None:
    """메모를 '중요' 표시하듯 맨 위로 올림 — updated_at을 현재 시각으로 갱신."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    cursor.execute('UPDATE stock_memo SET updated_at = ? WHERE id = ?', (timestamp, memo_id))
    conn.commit()
    conn.close()


def get_stock_memo_grades() -> list:
    """현재 사용 중인 메모 등급 목록 (전체보기 페이지의 컬럼 구성용). 기본 등급 5개 + DB에 실제 존재하는 등급을 합쳐 중복 제거."""
    DEFAULT_GRADES = ['관심', '매수검토', '보유중', '매도검토', '기타']
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT DISTINCT grade FROM stock_memo WHERE grade IS NOT NULL AND grade != ''")
    existing = [r[0] for r in cursor.fetchall()]
    conn.close()
    grades = list(DEFAULT_GRADES)
    for g in existing:
        if g not in grades:
            grades.append(g)
    return grades


def delete_stock_memo_entry(memo_id: int) -> None:
    """메모 항목 1건 삭제 (id 기준)"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('DELETE FROM stock_memo WHERE id = ?', (memo_id,))
    conn.commit()
    conn.close()


def update_stock_memo_grade(memo_id: int, grade: str) -> None:
    """메모 항목의 등급만 변경 (다른 등급 컬럼으로 옮기기)"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('UPDATE stock_memo SET grade = ? WHERE id = ?', (grade, memo_id))
    conn.commit()
    conn.close()


def search_stock_memos(query: str = None, limit: int = 50) -> list:
    """종목별 가장 최근 메모 1건씩 검색 (종목코드/종목명 부분일치) — query 없으면 전체 종목 최신순.
    "다른 종목 메모 검색" 목록용 — 종목당 여러 메모가 있어도 최신 1건만 대표로 보여줌.
    """
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    like = f'%{query}%' if query else '%'
    cursor.execute('''
        SELECT s1.* FROM stock_memo s1
        INNER JOIN (
            SELECT code, MAX(id) AS max_id FROM stock_memo GROUP BY code
        ) s2 ON s1.code = s2.code AND s1.id = s2.max_id
        WHERE s1.code LIKE ? OR s1.name LIKE ?
        ORDER BY s1.created_at DESC LIMIT ?
    ''', (like, like, limit))
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_all_stock_memos(query: str = None, limit: int = 500) -> list:
    """전체 메모 이력 조회 (종목당 여러 건이어도 전부 반환, 최신순) — 메모 전체보기 페이지용."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    like = f'%{query}%' if query else '%'
    cursor.execute('''
        SELECT * FROM stock_memo
        WHERE code LIKE ? OR name LIKE ? OR memo LIKE ?
        ORDER BY updated_at DESC, id DESC LIMIT ?
    ''', (like, like, like, limit))
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def search_stock_codes(query: str, limit: int = 15) -> list:
    """종목코드/종목명 자동완성용 검색 — stock_market_cap_daily에 수집된 종목 중 부분일치.
    같은 종목이 날짜별로 여러 행 있으므로 code 기준으로 중복 제거하고 최신 종목명만 반환.
    """
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    like = f'%{query}%'
    cursor.execute('''
        SELECT code, name, MAX(date) AS latest_date FROM stock_market_cap_daily
        WHERE code LIKE ? OR name LIKE ?
        GROUP BY code
        ORDER BY latest_date DESC
        LIMIT ?
    ''', (like, like, limit))
    rows = cursor.fetchall()
    conn.close()
    return [{'code': r['code'], 'name': r['name']} for r in rows]


def get_recent_investor_dates(limit: int = 10) -> list:
    """stock_investor_daily에 존재하는 최근 N영업일 날짜 목록(내림차순).
    주말/공휴일은 애초에 데이터가 없으므로 달력일이 아닌 실제 거래일 기준으로 안전하게 계산됨.
    """
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('SELECT DISTINCT date FROM stock_investor_daily ORDER BY date DESC LIMIT ?', (limit,))
    dates = [r[0] for r in cursor.fetchall()]
    conn.close()
    return dates


def get_investor_cross_distribution(date_from: str, date_to: str, top_n: int = 60) -> list:
    """외국인+기관 합산금액을 종목별로 반환 (십자 분포도용).
    반환: [{code, name, frgn_total(signed), orgn_total(signed), frgn_days, orgn_days}]
    |frgn_total| + |orgn_total| 기준 상위 top_n개
    """
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute('''
        SELECT code, name,
               SUM(CAST(frgn_ntby_tr_pbmn AS INTEGER)) AS frgn_total,
               SUM(CAST(orgn_ntby_tr_pbmn AS INTEGER)) AS orgn_total,
               COUNT(CASE WHEN CAST(frgn_ntby_tr_pbmn AS INTEGER) != 0 THEN 1 END) AS frgn_days,
               COUNT(CASE WHEN CAST(orgn_ntby_tr_pbmn AS INTEGER) != 0 THEN 1 END) AS orgn_days
        FROM stock_investor_daily
        WHERE date BETWEEN ? AND ?
        GROUP BY code, name
        HAVING frgn_days > 0 OR orgn_days > 0
        ORDER BY (ABS(SUM(CAST(frgn_ntby_tr_pbmn AS INTEGER))) +
                  ABS(SUM(CAST(orgn_ntby_tr_pbmn AS INTEGER)))) DESC
        LIMIT ?
    ''', (date_from, date_to, top_n))
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_investor_distribution(date_from: str, date_to: str, investor: str, top_n: int = 40) -> list:
    """날짜 범위 내 종목별 순매수/순매도 분리 집계 (분포도용).
    investor: 'frgn' | 'orgn'
    반환: [{code, name,
            buy_amount(양수합산), buy_days,
            sell_amount(음수합산 절대값), sell_days}, ...]
          |buy_amount| + |sell_amount| 기준 상위 top_n개
    """
    col_amount = 'frgn_ntby_tr_pbmn' if investor == 'frgn' else 'orgn_ntby_tr_pbmn'

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute(f'''
        SELECT code, name,
               COALESCE(SUM(CASE WHEN CAST({col_amount} AS INTEGER) > 0
                            THEN CAST({col_amount} AS INTEGER) END), 0)   AS buy_amount,
               COUNT(CASE WHEN CAST({col_amount} AS INTEGER) > 0 THEN 1 END) AS buy_days,
               COALESCE(ABS(SUM(CASE WHEN CAST({col_amount} AS INTEGER) < 0
                                THEN CAST({col_amount} AS INTEGER) END)), 0) AS sell_amount,
               COUNT(CASE WHEN CAST({col_amount} AS INTEGER) < 0 THEN 1 END) AS sell_days
        FROM stock_investor_daily
        WHERE date BETWEEN ? AND ?
          AND CAST({col_amount} AS INTEGER) != 0
        GROUP BY code, name
        HAVING buy_days > 0 OR sell_days > 0
        ORDER BY (COALESCE(SUM(CASE WHEN CAST({col_amount} AS INTEGER) > 0
                               THEN CAST({col_amount} AS INTEGER) END), 0) +
                  COALESCE(ABS(SUM(CASE WHEN CAST({col_amount} AS INTEGER) < 0
                                   THEN CAST({col_amount} AS INTEGER) END)), 0)) DESC
        LIMIT ?
    ''', (date_from, date_to, top_n))
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_investor_ranking(date_from: str, date_to: str, investor: str, direction: str, top_n: int = 20) -> dict:
    """날짜 범위별 투자자 순매수/순매도 랭킹.
    investor: 'frgn' | 'orgn'
    direction: 'buy' (순매수, 금액 > 0) | 'sell' (순매도, 금액 < 0)
    반환: {date: [{code, name, qty, amount}, ...], dates: [...]}
    """
    col_qty    = 'frgn_ntby_qty'    if investor == 'frgn' else 'orgn_ntby_qty'
    col_amount = 'frgn_ntby_tr_pbmn' if investor == 'frgn' else 'orgn_ntby_tr_pbmn'
    sign = '>' if direction == 'buy' else '<'
    order = 'DESC' if direction == 'buy' else 'ASC'

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    cursor.execute(f'''
        SELECT DISTINCT date FROM stock_investor_daily
        WHERE date BETWEEN ? AND ?
        ORDER BY date DESC
    ''', (date_from, date_to))
    dates = [r['date'] for r in cursor.fetchall()]

    result = {}
    for d in dates:
        cursor.execute(f'''
            SELECT code, name,
                   CAST({col_qty} AS INTEGER)    AS qty,
                   CAST({col_amount} AS INTEGER)  AS amount
            FROM stock_investor_daily
            WHERE date = ? AND CAST({col_amount} AS INTEGER) {sign} 0
            ORDER BY CAST({col_amount} AS INTEGER) {order}
            LIMIT ?
        ''', (d, top_n))
        result[d] = [dict(r) for r in cursor.fetchall()]

    conn.close()
    return {'dates': dates, 'data': result}


def get_stock_investor_raw(limit_dates: int = 30) -> list:
    """stock_investor_daily 원시 데이터 조회 (sync export용)"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute('''
        SELECT * FROM stock_investor_daily
        WHERE date IN (
            SELECT DISTINCT date FROM stock_investor_daily
            ORDER BY date DESC LIMIT ?
        )
        ORDER BY date DESC, code ASC
    ''', (limit_dates,))
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ──────────────────────────────────────────────
# 바로가기 링크 (날짜열 변환 페이지 — 데이터 복사해오는 원본 웹페이지 즐겨찾기)
# ──────────────────────────────────────────────

def get_quick_links() -> list:
    """저장된 바로가기 링크 전체 조회 (등록순)"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM quick_links ORDER BY id ASC')
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def add_quick_link(label: str, url: str, side: str = 'left') -> int:
    """바로가기 링크 추가. side: 'left' 또는 'right' (화면에서 좌/우 영역 구분용). 반환: 새로 생성된 id"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    cursor.execute('INSERT INTO quick_links (label, url, side, timestamp) VALUES (?, ?, ?, ?)', (label, url, side, timestamp))
    conn.commit()
    new_id = cursor.lastrowid
    conn.close()
    return new_id


def delete_quick_link(link_id: int) -> int:
    """바로가기 링크 삭제. 반환: 삭제된 행 수(0 또는 1)"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('DELETE FROM quick_links WHERE id = ?', (link_id,))
    conn.commit()
    deleted = cursor.rowcount
    conn.close()
    return deleted


# ──────────────────────────────────────────────
# 앱 잠금 설정 (Slack 비밀번호 로그인)
# ──────────────────────────────────────────────

def get_login_settings() -> dict:
    """잠금 설정 조회. 반환: {lock_enabled: bool, password_hash: str|None}
    행이 없으면(최초 실행) 잠금 켜짐 상태로 취급 — 기본값은 잠금이며, 비밀번호는 /security에서
    직접 켜거나 로그인 페이지의 "비밀번호 받기"로 최초 발급해야 한다.
    """
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM login_settings WHERE id = 1')
    row = cursor.fetchone()
    conn.close()
    if not row:
        return {'lock_enabled': True, 'password_hash': None}
    return {'lock_enabled': bool(row['lock_enabled']), 'password_hash': row['password_hash']}


def save_login_settings(lock_enabled: bool, password_hash: str = None) -> None:
    """잠금 설정 저장(upsert, 싱글톤 1행). password_hash를 None으로 넘기면 기존 값 유지."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    if password_hash is None:
        cursor.execute('SELECT password_hash FROM login_settings WHERE id = 1')
        row = cursor.fetchone()
        password_hash = row[0] if row else None
    cursor.execute('''
        INSERT INTO login_settings (id, lock_enabled, password_hash, updated_at)
        VALUES (1, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET lock_enabled = excluded.lock_enabled,
                                       password_hash = excluded.password_hash,
                                       updated_at = excluded.updated_at
    ''', (1 if lock_enabled else 0, password_hash, timestamp))
    conn.commit()
    conn.close()


def get_or_create_secret_key() -> str:
    """Flask 세션 서명 키(SECRET_KEY) 조회. DB에 이미 있으면 그 값을 반환하고,
    없으면(최초 실행) 새로 생성해 저장한 뒤 반환한다 — 이후엔 계속 이 값을 재사용하므로
    서버 재시작(배포 포함)마다 로그인 세션이 풀리지 않는다.
    (.env에 SECRET_KEY가 설정돼 있으면 이 함수는 아예 호출되지 않음 — app/api/server.py 참고)
    """
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('SELECT secret_key FROM app_secret WHERE id = 1')
    row = cursor.fetchone()
    if row:
        conn.close()
        return row[0]

    # INSERT OR IGNORE: 여러 프로세스가 동시에 최초 실행되더라도(레이스 컨디션) 하나만 실제 저장되고,
    # 나머지는 무시된 뒤 아래 SELECT로 동일한 값을 읽어가게 됨
    new_key = secrets.token_hex(32)
    cursor.execute('INSERT OR IGNORE INTO app_secret (id, secret_key) VALUES (1, ?)', (new_key,))
    conn.commit()
    cursor.execute('SELECT secret_key FROM app_secret WHERE id = 1')
    row = cursor.fetchone()
    conn.close()
    return row[0]


# ──────────────────────────────────────────────
# 동행복권 파워볼 — 당첨결과 저장/조회 + 즐겨찾기 번호 관리
# ──────────────────────────────────────────────

def save_powerball_rounds(rounds: list) -> tuple:
    """파워볼 당첨결과 여러 회차를 한 번에 저장. round(회차)가 이미 있으면 건너뜀.
    rounds: [{round, date, nums(list[int]), pb, sum, oe, size, sum_band, pb_band}, ...]
    반환: (added, skipped)
    """
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    added = 0
    skipped = 0
    for r in rounds:
        nums_str = ','.join(str(n) for n in r['nums'])
        try:
            cursor.execute('''
                INSERT INTO powerball_rounds (round, date, nums, pb, sum, oe, size, sum_band, pb_band, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (r['round'], r['date'], nums_str, r['pb'], r['sum'], r['oe'], r['size'], r['sum_band'], r['pb_band'], timestamp))
            added += 1
        except sqlite3.IntegrityError:
            skipped += 1  # round UNIQUE 제약 위반 — 이미 저장된 회차
    conn.commit()
    conn.close()
    return added, skipped


def get_powerball_rounds(limit: int = 300) -> list:
    """파워볼 당첨결과 목록 조회 (회차 최신순). nums는 리스트[int]로 파싱해서 반환."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM powerball_rounds ORDER BY round DESC LIMIT ?', (limit,))
    rows = cursor.fetchall()
    conn.close()
    result = []
    for row in rows:
        d = dict(row)
        d['nums'] = [int(n) for n in d['nums'].split(',')] if d['nums'] else []
        result.append(d)
    return result


def delete_powerball_round(round_id: int) -> int:
    """파워볼 당첨결과 1건 삭제 (id 기준). 반환: 삭제된 행 수(0 또는 1)"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('DELETE FROM powerball_rounds WHERE id = ?', (round_id,))
    conn.commit()
    deleted = cursor.rowcount
    conn.close()
    return deleted


def add_powerball_favorite(name: str, nums: list, pb: int) -> int:
    """즐겨찾기 번호 추가. nums: 일반볼 5개(list[int]), pb: 파워볼 1개. 반환: 새로 생성된 id"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    nums_str = ','.join(str(n) for n in sorted(nums))
    cursor.execute('INSERT INTO powerball_favorites (name, nums, pb, created_at) VALUES (?, ?, ?, ?)',
                   (name, nums_str, pb, timestamp))
    conn.commit()
    new_id = cursor.lastrowid
    conn.close()
    return new_id


def get_powerball_favorites() -> list:
    """즐겨찾기 번호 목록 조회 (최신순) — 저장된 당첨결과 전체와 비교해 가장 많이 맞은 회차도 함께 계산해서 반환.
    각 항목에 best_round/best_date/best_hit_count/best_pb_hit 필드가 추가됨(비교할 결과가 없으면 None).
    """
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM powerball_favorites ORDER BY id DESC')
    fav_rows = [dict(r) for r in cursor.fetchall()]
    conn.close()

    rounds = get_powerball_rounds(limit=1000)

    result = []
    for fav in fav_rows:
        fav_nums = set(int(n) for n in fav['nums'].split(',')) if fav['nums'] else set()
        best = None
        for r in rounds:
            hit_count = len(fav_nums & set(r['nums']))
            pb_hit = (fav['pb'] == r['pb'])
            if best is None or hit_count > best['best_hit_count'] or (hit_count == best['best_hit_count'] and pb_hit and not best['best_pb_hit']):
                best = {'best_round': r['round'], 'best_date': r['date'], 'best_hit_count': hit_count, 'best_pb_hit': pb_hit}
        fav['nums'] = sorted(fav_nums)
        if best:
            fav.update(best)
        else:
            fav.update({'best_round': None, 'best_date': None, 'best_hit_count': None, 'best_pb_hit': None})
        result.append(fav)
    return result


def delete_powerball_favorite(fav_id: int) -> int:
    """즐겨찾기 번호 1건 삭제 (id 기준). 반환: 삭제된 행 수(0 또는 1)"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('DELETE FROM powerball_favorites WHERE id = ?', (fav_id,))
    conn.commit()
    deleted = cursor.rowcount
    conn.close()
    return deleted


# ──────────────────────────────────────────────
# 동행복권 로또6/45 — 당첨결과 저장/조회
# ──────────────────────────────────────────────

def save_lotto645_rounds(rounds: list) -> tuple:
    """로또6/45 당첨결과 여러 회차를 한 번에 저장. round(회차)가 이미 있으면 건너뜀.
    rounds: [{round, nums(list[int] 6개), bonus, winners, prize}, ...]
    반환: (added, skipped)
    """
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    added = 0
    skipped = 0
    for r in rounds:
        nums_str = ','.join(str(n) for n in r['nums'])
        try:
            cursor.execute('''
                INSERT INTO lotto645_rounds (round, nums, bonus, winners, prize, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (r['round'], nums_str, r['bonus'], r['winners'], r['prize'], timestamp))
            added += 1
        except sqlite3.IntegrityError:
            skipped += 1  # round UNIQUE 제약 위반 — 이미 저장된 회차
    conn.commit()
    conn.close()
    return added, skipped


def get_lotto645_rounds(limit: int = 300) -> list:
    """로또6/45 당첨결과 목록 조회 (회차 최신순). nums는 리스트[int]로 파싱해서 반환."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM lotto645_rounds ORDER BY round DESC LIMIT ?', (limit,))
    rows = cursor.fetchall()
    conn.close()
    result = []
    for row in rows:
        d = dict(row)
        d['nums'] = [int(n) for n in d['nums'].split(',')] if d['nums'] else []
        result.append(d)
    return result


def delete_lotto645_round(round_id: int) -> int:
    """로또6/45 당첨결과 1건 삭제 (id 기준). 반환: 삭제된 행 수(0 또는 1)"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('DELETE FROM lotto645_rounds WHERE id = ?', (round_id,))
    conn.commit()
    deleted = cursor.rowcount
    conn.close()
    return deleted


def _lotto645_grade(fav_nums: set, r_nums: list, r_bonus: int):
    """로또6/45 실제 등수 규칙으로 즐겨찾기 번호 1개 vs 당첨결과 1건을 비교.
    1등(6개 일치) / 2등(5개+보너스 일치) / 3등(5개 일치) / 4등(4개 일치) / 5등(3개 일치).
    반환: (등수 순위(1이 최고) 또는 None, 등수 라벨 또는 None)
    """
    main_match = len(fav_nums & set(r_nums))
    if main_match == 6:
        return 1, '1등'
    if main_match == 5 and r_bonus in fav_nums:
        return 2, '2등'
    if main_match == 5:
        return 3, '3등'
    if main_match == 4:
        return 4, '4등'
    if main_match == 3:
        return 5, '5등'
    return None, None


def add_lotto645_favorite(name: str, nums: list) -> int:
    """즐겨찾기 번호 추가. nums: 번호 6개(list[int]). 반환: 새로 생성된 id"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    nums_str = ','.join(str(n) for n in sorted(nums))
    cursor.execute('INSERT INTO lotto645_favorites (name, nums, created_at) VALUES (?, ?, ?)',
                   (name, nums_str, timestamp))
    conn.commit()
    new_id = cursor.lastrowid
    conn.close()
    return new_id


def get_lotto645_favorites() -> list:
    """즐겨찾기 번호 목록 조회 (최신순) — 저장된 당첨결과 전체와 비교해 가장 좋은 등수의 회차도 함께 계산해서 반환.
    각 항목에 best_round/best_grade_rank/best_grade_label/best_prize/best_winners 필드가 추가됨.
    당첨금(best_prize)은 1등으로 정확히 일치한 회차에 한해서만 채워짐 — 2~5등은 회차마다 금액이 달라
    (4·5등은 법으로 고정, 2·3등은 그때그때 상금 규모에 따라 달라짐) 실제 데이터를 저장해두지 않아 None으로 둠.
    """
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM lotto645_favorites ORDER BY id DESC')
    fav_rows = [dict(r) for r in cursor.fetchall()]
    conn.close()

    rounds = get_lotto645_rounds(limit=1000)

    result = []
    for fav in fav_rows:
        fav_nums = set(int(n) for n in fav['nums'].split(',')) if fav['nums'] else set()
        best = None
        for r in rounds:
            rank, label = _lotto645_grade(fav_nums, r['nums'], r['bonus'])
            if rank is None:
                continue
            if best is None or rank < best['best_grade_rank']:
                best = {
                    'best_round': r['round'], 'best_grade_rank': rank, 'best_grade_label': label,
                    'best_prize': r['prize'] if rank == 1 else None,
                    'best_winners': r['winners'] if rank == 1 else None,
                }
        fav['nums'] = sorted(fav_nums)
        if best:
            fav.update(best)
        else:
            fav.update({'best_round': None, 'best_grade_rank': None, 'best_grade_label': None,
                        'best_prize': None, 'best_winners': None})
        result.append(fav)
    return result


def delete_lotto645_favorite(fav_id: int) -> int:
    """즐겨찾기 번호 1건 삭제 (id 기준). 반환: 삭제된 행 수(0 또는 1)"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('DELETE FROM lotto645_favorites WHERE id = ?', (fav_id,))
    conn.commit()
    deleted = cursor.rowcount
    conn.close()
    return deleted


# ──────────────────────────────────────────────
# 동행복권 연금복권720+ — 당첨결과 저장/조회 + 즐겨찾기 번호 관리
# ──────────────────────────────────────────────

def save_pension720_rounds(rounds: list) -> tuple:
    """연금복권720+ 당첨결과 여러 회차를 한 번에 저장. round(회차)가 이미 있으면 건너뜀.
    rounds: [{round, group(1~5), number(6자리 문자열)}, ...]
    반환: (added, skipped)
    """
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    added = 0
    skipped = 0
    for r in rounds:
        try:
            cursor.execute('''
                INSERT INTO pension720_rounds (round, group_no, number, created_at)
                VALUES (?, ?, ?, ?)
            ''', (r['round'], r['group'], r['number'], timestamp))
            added += 1
        except sqlite3.IntegrityError:
            skipped += 1  # round UNIQUE 제약 위반 — 이미 저장된 회차
    conn.commit()
    conn.close()
    return added, skipped


def get_pension720_rounds(limit: int = 400) -> list:
    """연금복권720+ 당첨결과 목록 조회 (회차 최신순)."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM pension720_rounds ORDER BY CAST(round AS INTEGER) DESC LIMIT ?', (limit,))
    rows = cursor.fetchall()
    conn.close()
    result = []
    for row in rows:
        d = dict(row)
        d['group'] = d.pop('group_no')
        result.append(d)
    return result


def delete_pension720_round(round_id: int) -> int:
    """연금복권720+ 당첨결과 1건 삭제 (id 기준). 반환: 삭제된 행 수(0 또는 1)"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('DELETE FROM pension720_rounds WHERE id = ?', (round_id,))
    conn.commit()
    deleted = cursor.rowcount
    conn.close()
    return deleted


def _pension720_grade(fav_group: int, fav_number: str, r_group: int, r_number: str):
    """연금복권720+ 실제 등수 규칙으로 즐겨찾기 번호 1개 vs 당첨결과 1건을 비교.
    번호 끝자리부터 몇 자리가 연속으로 일치하는지로 등수를 매김:
    1등(조+6자리 일치) / 2등(6자리 일치, 조 무관) / 3등(뒤5자리) / 4등(뒤4자리) / 5등(뒤3자리) / 6등(뒤2자리) / 7등(뒤1자리).
    반환: (등수 순위(1이 최고, 낮을수록 좋음) 또는 None, 등수 라벨 문자열 또는 None)
    """
    trailing = 0
    for a, b in zip(reversed(fav_number), reversed(r_number)):
        if a != b:
            break
        trailing += 1

    if trailing == 6:
        return (1, '1등') if fav_group == r_group else (2, '2등')
    if trailing == 5:
        return 3, '3등'
    if trailing == 4:
        return 4, '4등'
    if trailing == 3:
        return 5, '5등'
    if trailing == 2:
        return 6, '6등'
    if trailing == 1:
        return 7, '7등'
    return None, None


def add_pension720_favorite(name: str, group: int, number: str) -> int:
    """즐겨찾기 번호 추가. group: 조(1~5), number: 6자리 문자열. 반환: 새로 생성된 id"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    cursor.execute('INSERT INTO pension720_favorites (name, group_no, number, created_at) VALUES (?, ?, ?, ?)',
                   (name, group, number, timestamp))
    conn.commit()
    new_id = cursor.lastrowid
    conn.close()
    return new_id


def get_pension720_favorites() -> list:
    """즐겨찾기 번호 목록 조회 (최신순) — 저장된 당첨결과 전체와 비교해 가장 좋은 등수의 회차도 함께 계산해서 반환.
    각 항목에 best_round/best_group/best_number/best_grade_rank/best_grade_label 필드가 추가됨(당첨된 적 없으면 None).
    """
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM pension720_favorites ORDER BY id DESC')
    fav_rows = [dict(r) for r in cursor.fetchall()]
    conn.close()

    rounds = get_pension720_rounds(limit=1000)

    result = []
    for fav in fav_rows:
        fav['group'] = fav.pop('group_no')
        best = None
        for r in rounds:
            rank, label = _pension720_grade(fav['group'], fav['number'], r['group'], r['number'])
            if rank is None:
                continue
            if best is None or rank < best['best_grade_rank']:
                best = {
                    'best_round': r['round'], 'best_group': r['group'], 'best_number': r['number'],
                    'best_grade_rank': rank, 'best_grade_label': label,
                }
        if best:
            fav.update(best)
        else:
            fav.update({'best_round': None, 'best_group': None, 'best_number': None,
                        'best_grade_rank': None, 'best_grade_label': None})
        result.append(fav)
    return result


def delete_pension720_favorite(fav_id: int) -> int:
    """즐겨찾기 번호 1건 삭제 (id 기준). 반환: 삭제된 행 수(0 또는 1)"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('DELETE FROM pension720_favorites WHERE id = ?', (fav_id,))
    conn.commit()
    deleted = cursor.rowcount
    conn.close()
    return deleted


# ──────────────────────────────────────────────
# 재미용 "번호 추천" 결과 저장/조회/삭제 (파워볼/로또6/45/연금복권720+ 공통)
# ──────────────────────────────────────────────

def save_lottery_recommendations(rows: list) -> int:
    """번호 추천 결과 여러 건을 한 번에 저장 — 번호 1~6자리를 각각 num1~num6 컬럼에, 보너스(파워볼/조)는 bonus 컬럼에 저장.
    rows: [{game, method, main(list[int], 길이 5~6, 자릿수 순서 그대로), bonus(int 또는 None)}, ...]
    반환: 저장된 건수
    """
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    for r in rows:
        nums = (list(r['main']) + [None] * 6)[:6]  # 5자리 게임(파워볼)은 num6이 NULL로 남음
        cursor.execute(
            '''INSERT INTO lottery_recommendations
               (game, method, num1, num2, num3, num4, num5, num6, bonus, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
            (r['game'], r['method'], *nums, r.get('bonus'), timestamp)
        )
    conn.commit()
    conn.close()
    return len(rows)


def get_lottery_recommendations(limit: int = 1000) -> list:
    """저장된 번호 추천 결과 목록 조회 (최신 저장순). num1~num6은 main 리스트로 합쳐서 반환(비어있는 자리는 제외)."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM lottery_recommendations ORDER BY id DESC LIMIT ?', (limit,))
    rows = cursor.fetchall()
    conn.close()
    result = []
    for row in rows:
        d = dict(row)
        d.pop('main', None)   # 예전 스키마의 콤마문자열 컬럼(더 이상 안 씀) — 남아있어도 응답에선 제거
        d.pop('extra', None)  # 예전 스키마의 부가값 컬럼(bonus로 대체됨)
        nums = [d.pop(f'num{i}') for i in range(1, 7)]
        d['main'] = [n for n in nums if n is not None]
        result.append(d)
    return result


def delete_lottery_recommendation(rec_id: int) -> int:
    """번호 추천 결과 1건 삭제 (id 기준). 반환: 삭제된 행 수(0 또는 1)"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('DELETE FROM lottery_recommendations WHERE id = ?', (rec_id,))
    conn.commit()
    deleted = cursor.rowcount
    conn.close()
    return deleted


def delete_lottery_recommendations_bulk(ids: list) -> int:
    """번호 추천 결과 여러 건을 한 번에 삭제. 반환: 삭제된 행 수"""
    if not ids:
        return 0
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    placeholders = ','.join('?' for _ in ids)
    cursor.execute(f'DELETE FROM lottery_recommendations WHERE id IN ({placeholders})', ids)
    conn.commit()
    deleted = cursor.rowcount
    conn.close()
    return deleted


# ──────────────────────────────────────────────
# 코인(업비트) 기술지표 스크리닝
# ──────────────────────────────────────────────

def save_coin_screening(rows: list):
    """코인 스크리닝(매매 후보 필터) 스냅샷 저장 (티커 기준 upsert — 최신 값으로 갱신)"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    for r in rows:
        cursor.execute('''
            INSERT INTO coin_screening_daily
                (ticker, name, price, change_rate, trade_value,
                 ma200, ma200_dist_pct, near_ma200, above_cloud,
                 breakout_4h, breakout_vol_ratio, breakout_candle_rate, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(ticker) DO UPDATE SET
                name=excluded.name, price=excluded.price, change_rate=excluded.change_rate,
                trade_value=excluded.trade_value,
                ma200=excluded.ma200, ma200_dist_pct=excluded.ma200_dist_pct,
                near_ma200=excluded.near_ma200, above_cloud=excluded.above_cloud,
                breakout_4h=excluded.breakout_4h, breakout_vol_ratio=excluded.breakout_vol_ratio,
                breakout_candle_rate=excluded.breakout_candle_rate,
                updated_at=excluded.updated_at
        ''', (r['ticker'], r.get('name'), r.get('price'), r.get('change_rate'), r.get('trade_value'),
              r.get('ma200'), r.get('ma200_dist_pct'), int(bool(r.get('near_ma200'))), int(bool(r.get('above_cloud'))),
              int(bool(r.get('breakout_4h'))), r.get('breakout_vol_ratio'), r.get('breakout_candle_rate'), timestamp))
    conn.commit()
    conn.close()


def get_coin_screening() -> list:
    """코인 스크리닝 스냅샷 전체 조회 (거래대금 내림차순)"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM coin_screening_daily ORDER BY trade_value DESC')
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_coin_screening_candidates() -> list:
    """자동매매 진입 후보만 필터링 조회 (4시간봉 돌파, 또는 200선 근접이면서 구름 위)"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute('''
        SELECT * FROM coin_screening_daily
        WHERE breakout_4h = 1 OR (near_ma200 = 1 AND above_cloud = 1)
        ORDER BY trade_value DESC
    ''')
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ──────────────────────────────────────────────
# 국내주식(토스증권) 기술지표 스크리닝 — coin_screening_daily의 3개 함수와 동일 패턴, 일봉(1d) 기준
# ──────────────────────────────────────────────

def save_stock_screening(rows: list):
    """국내주식 스크리닝(매매 후보 필터) 스냅샷 저장 (종목코드 기준 upsert — 최신 값으로 갱신)"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    for r in rows:
        cursor.execute('''
            INSERT INTO stock_screening_daily
                (ticker, name, price, change_rate, trade_value,
                 ma200, ma200_dist_pct, near_ma200, above_cloud,
                 breakout_1d, breakout_vol_ratio, breakout_candle_rate, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(ticker) DO UPDATE SET
                name=excluded.name, price=excluded.price, change_rate=excluded.change_rate,
                trade_value=excluded.trade_value,
                ma200=excluded.ma200, ma200_dist_pct=excluded.ma200_dist_pct,
                near_ma200=excluded.near_ma200, above_cloud=excluded.above_cloud,
                breakout_1d=excluded.breakout_1d, breakout_vol_ratio=excluded.breakout_vol_ratio,
                breakout_candle_rate=excluded.breakout_candle_rate,
                updated_at=excluded.updated_at
        ''', (r['ticker'], r.get('name'), r.get('price'), r.get('change_rate'), r.get('trade_value'),
              r.get('ma200'), r.get('ma200_dist_pct'), int(bool(r.get('near_ma200'))), int(bool(r.get('above_cloud'))),
              int(bool(r.get('breakout_1d'))), r.get('breakout_vol_ratio'), r.get('breakout_candle_rate'), timestamp))
    conn.commit()
    conn.close()


def get_stock_screening() -> list:
    """국내주식 스크리닝 스냅샷 전체 조회 (거래대금 내림차순)"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM stock_screening_daily ORDER BY trade_value DESC')
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_stock_screening_candidates() -> list:
    """자동매매 진입 후보만 필터링 조회 (일봉 돌파, 또는 200선 근접이면서 구름 위)"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute('''
        SELECT * FROM stock_screening_daily
        WHERE breakout_1d = 1 OR (near_ma200 = 1 AND above_cloud = 1)
        ORDER BY trade_value DESC
    ''')
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ──────────────────────────────────────────────
# 자동매매(1단계: 업비트 모의매매) — 가상 계좌/포지션/주문 로그
# ──────────────────────────────────────────────

def get_or_create_paper_account(broker: str, mode: str, initial_cash: float) -> dict:
    """가상 계좌 조회 (없으면 initial_cash로 최초 생성)."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM paper_account WHERE broker = ? AND mode = ?', (broker, mode))
    row = cursor.fetchone()
    if row is None:
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        cursor.execute('''
            INSERT INTO paper_account (broker, mode, cash_balance, initial_balance, updated_at)
            VALUES (?, ?, ?, ?, ?)
        ''', (broker, mode, initial_cash, initial_cash, timestamp))
        conn.commit()
        cursor.execute('SELECT * FROM paper_account WHERE broker = ? AND mode = ?', (broker, mode))
        row = cursor.fetchone()
    conn.close()
    return dict(row)


def update_paper_account_cash(broker: str, mode: str, new_cash: float):
    """가상 계좌 현금 잔고 갱신."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    cursor.execute('''
        UPDATE paper_account SET cash_balance = ?, updated_at = ?
        WHERE broker = ? AND mode = ?
    ''', (new_cash, timestamp, broker, mode))
    conn.commit()
    conn.close()


def get_paper_positions(broker: str, mode: str) -> list:
    """가상 보유 포지션 전체 조회."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM paper_positions WHERE broker = ? AND mode = ? ORDER BY ticker', (broker, mode))
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_paper_position(broker: str, mode: str, ticker: str):
    """가상 보유 포지션 1건 조회 (없으면 None)."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM paper_positions WHERE broker = ? AND mode = ? AND ticker = ?', (broker, mode, ticker))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None


def upsert_paper_position(broker: str, mode: str, ticker: str, qty: float, avg_buy_price: float,
                           entry_at: str = None, peak_price: float = None):
    """가상 포지션 upsert. 신규 진입이면 entry_at을 기록하고, 기존 보유분에 얹는 경우(추가매수)는
    호출부(PaperBroker)에서 이미 합산한 qty/avg_buy_price를 넘겨받아 그대로 덮어쓴다.
    peak_price를 명시적으로 안 넘기면(None) 기존 행이 있을 때 그 값을 그대로 유지하고(트레일링
    손절 기준점은 매수/매도로 안 건드림 — update_position_tracking()이 사이클마다 별도 갱신),
    신규 행이면 avg_buy_price로 시작한다. dca_enabled/dca_used/below_stop_streak은 이 함수가
    건드리지 않음(set_position_dca_enabled/mark_position_dca_used/update_position_tracking 참고)."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    entry_at = entry_at or timestamp

    if peak_price is None:
        cursor.execute(
            'SELECT peak_price FROM paper_positions WHERE broker = ? AND mode = ? AND ticker = ?',
            (broker, mode, ticker)
        )
        existing = cursor.fetchone()
        peak_price = existing[0] if existing and existing[0] is not None else avg_buy_price

    cursor.execute('''
        INSERT INTO paper_positions (broker, mode, ticker, qty, avg_buy_price, entry_at, peak_price, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(broker, mode, ticker) DO UPDATE SET
            qty=excluded.qty, avg_buy_price=excluded.avg_buy_price,
            peak_price=excluded.peak_price, updated_at=excluded.updated_at
    ''', (broker, mode, ticker, qty, avg_buy_price, entry_at, peak_price, timestamp))
    conn.commit()
    conn.close()


def update_position_tracking(broker: str, mode: str, ticker: str, peak_price: float, below_stop_streak: int):
    """트레일링 손절 추적값(보유 중 최고가/손절 조건 연속 확인 횟수) 갱신. 매수/매도가 없는(HOLD)
    사이클에도 매번 호출해서 다음 사이클 판단에 쓰일 상태를 최신화한다."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    cursor.execute('''
        UPDATE paper_positions SET peak_price = ?, below_stop_streak = ?, updated_at = ?
        WHERE broker = ? AND mode = ? AND ticker = ?
    ''', (peak_price, below_stop_streak, timestamp, broker, mode, ticker))
    conn.commit()
    conn.close()


def set_position_dca_enabled(broker: str, mode: str, ticker: str, enabled: bool):
    """대시보드 체크박스: 이 포지션에 물타기(추가매수) 허용 여부 저장."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    cursor.execute('''
        UPDATE paper_positions SET dca_enabled = ?, updated_at = ?
        WHERE broker = ? AND mode = ? AND ticker = ?
    ''', (1 if enabled else 0, timestamp, broker, mode, ticker))
    conn.commit()
    conn.close()


def mark_position_dca_used(broker: str, mode: str, ticker: str, new_peak_price: float):
    """물타기(추가매수) 실행 직후 호출 — dca_count를 1 증가시켜(trade_strategy.py가 dca_max_count와
    비교해 남은 허용 횟수를 판단) 트레일링 손절의 기준점(peak_price)과 연속 확인 카운트
    (below_stop_streak)를 새 평단 시점 기준으로 리셋한다. dca_used는 더 이상 갱신하지 않음(구버전
    호환용으로 컬럼만 남아있음)."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    cursor.execute('''
        UPDATE paper_positions SET dca_count = dca_count + 1, peak_price = ?, below_stop_streak = 0, updated_at = ?
        WHERE broker = ? AND mode = ? AND ticker = ?
    ''', (new_peak_price, timestamp, broker, mode, ticker))
    conn.commit()
    conn.close()


def delete_paper_position(broker: str, mode: str, ticker: str):
    """가상 포지션 완전 청산 시 행 삭제."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('DELETE FROM paper_positions WHERE broker = ? AND mode = ? AND ticker = ?', (broker, mode, ticker))
    conn.commit()
    conn.close()


def save_trade_order_log(broker: str, mode: str, ticker: str, decision: str, reason: str = None,
                          price: float = None, qty: float = None, amount_krw: float = None,
                          cash_balance_after: float = None, pnl_krw: float = None, pnl_pct: float = None):
    """매매 판단(BUY/SELL/HOLD/SKIP) + 가상 체결 결과를 감사로그로 기록 (실주문 없음, 전부 시뮬레이션)."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    cursor.execute('''
        INSERT INTO trade_order_log
            (broker, mode, ticker, decision, reason, price, qty, amount_krw,
             cash_balance_after, pnl_krw, pnl_pct, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (broker, mode, ticker, decision, reason, price, qty, amount_krw,
          cash_balance_after, pnl_krw, pnl_pct, timestamp))
    conn.commit()
    conn.close()


def get_trade_engine_settings(broker: str = 'upbit', mode: str = 'paper') -> dict:
    """자동매매 엔진 실행 여부 조회(브로커+모드별). 행이 없으면(최초 실행) 모의(paper)는 기존처럼
    기본값 실행중(enabled=True)으로 취급하지만, 실거래(live)는 반드시 기본값 정지(enabled=False)다 —
    사용자가 화면에서 명시적으로 한 번 켜기 전까지는 실주문 루프가 저절로 도는 일이 없어야 한다."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM trade_engine_settings WHERE broker = ? AND mode = ?', (broker, mode))
    row = cursor.fetchone()
    conn.close()
    if not row:
        return {'enabled': mode != 'live', 'last_cycle_at': None}
    return {'enabled': bool(row['enabled']), 'last_cycle_at': row['last_cycle_at']}


def set_trade_engine_enabled(enabled: bool, broker: str = 'upbit', mode: str = 'paper') -> None:
    """자동매매 엔진 실행 여부 저장(upsert, 브로커+모드당 1행)."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    cursor.execute('''
        INSERT INTO trade_engine_settings (broker, mode, enabled, updated_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(broker, mode) DO UPDATE SET enabled = excluded.enabled, updated_at = excluded.updated_at
    ''', (broker, mode, 1 if enabled else 0, timestamp))
    conn.commit()
    conn.close()


def set_engine_last_cycle_at(broker: str = 'upbit', mode: str = 'paper') -> str:
    """루프가 사이클 1건을 실제로 처리했을 때마다 호출 — 대시보드의 "마지막 실행/다음 실행 예정"
    표시용 하트비트만 남기고 enabled 값은 건드리지 않는다. 저장한 타임스탬프를 그대로 반환."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    cursor.execute('''
        INSERT INTO trade_engine_settings (broker, mode, enabled, last_cycle_at, updated_at)
        VALUES (?, ?, 1, ?, ?)
        ON CONFLICT(broker, mode) DO UPDATE SET last_cycle_at = excluded.last_cycle_at
    ''', (broker, mode, timestamp, timestamp))
    conn.commit()
    conn.close()
    return timestamp


def get_trade_strategy_settings(broker: str = 'upbit') -> dict:
    """매매 전략 파라미터(포지션당 매수금액/최대 동시보유/손절·익절 기준/루프 주기) 조회(브로커별).
    행이 없으면(최초 실행, 대시보드에서 아직 저장한 적 없음) app/config.py의 TRADE_* 기본값을 그대로 반환."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM trade_strategy_settings WHERE broker = ?', (broker,))
    row = cursor.fetchone()
    conn.close()
    if not row:
        return {
            'max_position_krw': Config.TRADE_MAX_POSITION_KRW,
            'max_concurrent_positions': Config.TRADE_MAX_CONCURRENT_POSITIONS,
            'stop_loss_pct': Config.TRADE_STOP_LOSS_PCT,
            'take_profit_pct': Config.TRADE_TAKE_PROFIT_PCT,
            'loop_interval_sec': Config.TRADE_LOOP_INTERVAL_SEC,
            'stop_loss_confirm_cycles': Config.TRADE_STOP_LOSS_CONFIRM_CYCLES,
            'dca_trigger_pct': Config.TRADE_DCA_TRIGGER_PCT,
            'dca_max_count': Config.TRADE_DCA_MAX_COUNT,
            'condition_check_interval_sec': Config.TRADE_CONDITION_CHECK_INTERVAL_SEC,
            'updated_at': None,
        }
    return {
        'max_position_krw': row['max_position_krw'],
        'max_concurrent_positions': row['max_concurrent_positions'],
        'stop_loss_pct': row['stop_loss_pct'],
        'take_profit_pct': row['take_profit_pct'],
        'loop_interval_sec': row['loop_interval_sec'],
        'stop_loss_confirm_cycles': row['stop_loss_confirm_cycles'],
        'dca_trigger_pct': row['dca_trigger_pct'],
        'dca_max_count': row['dca_max_count'],
        'condition_check_interval_sec': row['condition_check_interval_sec'],
        'updated_at': row['updated_at'],
    }


def set_trade_strategy_settings(max_position_krw: float = None, max_concurrent_positions: int = None,
                                 stop_loss_pct: float = None, take_profit_pct: float = None,
                                 loop_interval_sec: int = None, stop_loss_confirm_cycles: int = None,
                                 dca_trigger_pct: float = None, dca_max_count: int = None,
                                 condition_check_interval_sec: int = None, broker: str = 'upbit') -> dict:
    """매매 전략 파라미터 저장(upsert, 브로커별 1행, 부분 갱신 — None인 필드는 기존값 유지). 저장된 값을 반환."""
    current = get_trade_strategy_settings(broker)
    merged = {
        'max_position_krw': max_position_krw if max_position_krw is not None else current['max_position_krw'],
        'max_concurrent_positions': max_concurrent_positions if max_concurrent_positions is not None else current['max_concurrent_positions'],
        'stop_loss_pct': stop_loss_pct if stop_loss_pct is not None else current['stop_loss_pct'],
        'take_profit_pct': take_profit_pct if take_profit_pct is not None else current['take_profit_pct'],
        'loop_interval_sec': loop_interval_sec if loop_interval_sec is not None else current['loop_interval_sec'],
        'stop_loss_confirm_cycles': stop_loss_confirm_cycles if stop_loss_confirm_cycles is not None else current['stop_loss_confirm_cycles'],
        'dca_trigger_pct': dca_trigger_pct if dca_trigger_pct is not None else current['dca_trigger_pct'],
        'dca_max_count': dca_max_count if dca_max_count is not None else current['dca_max_count'],
        'condition_check_interval_sec': condition_check_interval_sec if condition_check_interval_sec is not None else current['condition_check_interval_sec'],
    }
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    cursor.execute('''
        INSERT INTO trade_strategy_settings
            (broker, max_position_krw, max_concurrent_positions, stop_loss_pct, take_profit_pct,
             loop_interval_sec, stop_loss_confirm_cycles, dca_trigger_pct, dca_max_count,
             condition_check_interval_sec, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(broker) DO UPDATE SET
            max_position_krw=excluded.max_position_krw,
            max_concurrent_positions=excluded.max_concurrent_positions,
            stop_loss_pct=excluded.stop_loss_pct,
            take_profit_pct=excluded.take_profit_pct,
            loop_interval_sec=excluded.loop_interval_sec,
            stop_loss_confirm_cycles=excluded.stop_loss_confirm_cycles,
            dca_trigger_pct=excluded.dca_trigger_pct,
            dca_max_count=excluded.dca_max_count,
            condition_check_interval_sec=excluded.condition_check_interval_sec,
            updated_at=excluded.updated_at
    ''', (broker, merged['max_position_krw'], merged['max_concurrent_positions'], merged['stop_loss_pct'],
          merged['take_profit_pct'], merged['loop_interval_sec'], merged['stop_loss_confirm_cycles'],
          merged['dca_trigger_pct'], merged['dca_max_count'], merged['condition_check_interval_sec'], timestamp))
    conn.commit()
    conn.close()
    merged['updated_at'] = timestamp
    return merged


def get_approved_candidate_tickers(broker: str, mode: str) -> set:
    """수동으로 매매 승인 체크된 티커 집합 조회. 빈 집합이면 '전체 후보 대상'을 의미(호출부에서 처리)."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        'SELECT ticker FROM trade_candidate_approval WHERE broker = ? AND mode = ? AND approved = 1',
        (broker, mode)
    )
    rows = cursor.fetchall()
    conn.close()
    return {r[0] for r in rows}


def set_candidate_approval(broker: str, mode: str, ticker: str, approved: bool) -> None:
    """매매 대상 코인 체크박스 상태 저장(upsert)."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    cursor.execute('''
        INSERT INTO trade_candidate_approval (broker, mode, ticker, approved, updated_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(broker, mode, ticker) DO UPDATE SET
            approved=excluded.approved, updated_at=excluded.updated_at
    ''', (broker, mode, ticker, 1 if approved else 0, timestamp))
    conn.commit()
    conn.close()


def get_condition_watch_tickers(broker: str, mode: str) -> set:
    """"정밀 매수조건 검사" 체크박스가 켜진 티커 집합 조회 (entry_condition_checker.py가 이 종목만
    주기적으로 다중 시간대 조회한다)."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        'SELECT ticker FROM trade_candidate_approval WHERE broker = ? AND mode = ? AND condition_watch = 1',
        (broker, mode)
    )
    rows = cursor.fetchall()
    conn.close()
    return {r[0] for r in rows}


def set_candidate_condition_watch(broker: str, mode: str, ticker: str, enabled: bool) -> None:
    """"정밀 매수조건 검사" 체크박스 상태 저장(upsert) — approved 체크박스와 독립적인 컬럼."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    cursor.execute('''
        INSERT INTO trade_candidate_approval (broker, mode, ticker, condition_watch, updated_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(broker, mode, ticker) DO UPDATE SET
            condition_watch=excluded.condition_watch, updated_at=excluded.updated_at
    ''', (broker, mode, ticker, 1 if enabled else 0, timestamp))
    conn.commit()
    conn.close()


def get_watchlist_tickers(broker: str, mode: str) -> set:
    """"매매 대상" 1단계 체크박스(관심 등록)가 켜진 티커 집합 조회 — 실거래(live)에서 "🔴 실거래"
    표(2단계 승인)에 나타날 후보를 이 집합으로 먼저 좁힌다(app/core/auto_trader.py 참고)."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        'SELECT ticker FROM trade_candidate_approval WHERE broker = ? AND mode = ? AND watchlist = 1',
        (broker, mode)
    )
    rows = cursor.fetchall()
    conn.close()
    return {r[0] for r in rows}


def set_candidate_watchlist(broker: str, mode: str, ticker: str, watchlisted: bool) -> None:
    """"매매 대상"(관심 등록) 체크박스 상태 저장(upsert) — approved와 독립적인 컬럼."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    cursor.execute('''
        INSERT INTO trade_candidate_approval (broker, mode, ticker, watchlist, updated_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(broker, mode, ticker) DO UPDATE SET
            watchlist=excluded.watchlist, updated_at=excluded.updated_at
    ''', (broker, mode, ticker, 1 if watchlisted else 0, timestamp))
    conn.commit()
    conn.close()


def get_trade_condition_settings(broker: str = 'upbit') -> list:
    """정밀 매수조건(일봉/5분봉/1분봉 등) 설정 전체 조회(브로커별). params는 JSON 문자열을 dict로 파싱해 반환."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM trade_condition_settings WHERE broker = ? ORDER BY id', (broker,))
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()
    for r in rows:
        try:
            r['params'] = json.loads(r['params']) if r['params'] else {}
        except (TypeError, ValueError):
            r['params'] = {}
    return rows


def set_trade_condition_setting(condition_key: str, enabled: bool = None, logic_group: str = None,
                                 params: dict = None, broker: str = 'upbit') -> dict:
    """정밀 매수조건 1건 부분 갱신(브로커별, None인 필드는 기존값 유지). condition_key가 없으면 예외 발생."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM trade_condition_settings WHERE broker = ? AND condition_key = ?', (broker, condition_key))
    current = cursor.fetchone()
    if not current:
        conn.close()
        raise ValueError(f'알 수 없는 조건 키: {condition_key}')

    next_enabled = (1 if enabled else 0) if enabled is not None else current['enabled']
    next_logic_group = logic_group if logic_group is not None else current['logic_group']
    if params is not None:
        try:
            current_params = json.loads(current['params']) if current['params'] else {}
        except (TypeError, ValueError):
            current_params = {}
        current_params.update(params)
        next_params = json.dumps(current_params, ensure_ascii=False)
    else:
        next_params = current['params']

    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    cursor.execute('''
        UPDATE trade_condition_settings SET enabled = ?, logic_group = ?, params = ?, updated_at = ?
        WHERE broker = ? AND condition_key = ?
    ''', (next_enabled, next_logic_group, next_params, timestamp, broker, condition_key))
    conn.commit()
    conn.close()
    result = dict(current)
    result.update({'enabled': next_enabled, 'logic_group': next_logic_group, 'updated_at': timestamp})
    try:
        result['params'] = json.loads(next_params) if next_params else {}
    except (TypeError, ValueError):
        result['params'] = {}
    return result


def save_condition_status(broker: str, mode: str, ticker: str, passed: bool, detail: dict) -> None:
    """정밀 매수조건 검사 결과 캐시 저장(upsert) — entry_condition_checker.py 전용 쓰기 지점."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    cursor.execute('''
        INSERT INTO trade_condition_status (broker, mode, ticker, passed, detail, checked_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(broker, mode, ticker) DO UPDATE SET
            passed=excluded.passed, detail=excluded.detail, checked_at=excluded.checked_at
    ''', (broker, mode, ticker, 1 if passed else 0, json.dumps(detail, ensure_ascii=False), timestamp))
    conn.commit()
    conn.close()


def get_condition_status_map(broker: str, mode: str) -> dict:
    """정밀 매수조건 검사 결과 캐시를 {ticker: {passed, detail, checked_at}} 형태로 조회.
    evaluate_entries()가 이 값을 읽기만 하고(DB 접근 없음), 여기서 미리 dict로 준비해 넘긴다."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM trade_condition_status WHERE broker = ? AND mode = ?', (broker, mode))
    rows = cursor.fetchall()
    conn.close()
    result = {}
    for r in rows:
        try:
            detail = json.loads(r['detail']) if r['detail'] else {}
        except (TypeError, ValueError):
            detail = {}
        result[r['ticker']] = {'passed': bool(r['passed']), 'detail': detail, 'checked_at': r['checked_at']}
    return result


def get_trade_order_log(broker: str = None, mode: str = None, limit: int = 100, offset: int = 0,
                         ticker: str = None, decision: str = None) -> list:
    """매매 판단/체결 로그 최신순 조회. offset/ticker/decision은 별도 이력 페이지(/auto-trade/logs)의
    페이지네이션·필터용 — 기본값(offset=0, 필터 없음)이면 기존 대시보드 요약 호출과 동일하게 동작."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    where = []
    params = []
    if broker and mode:
        where.append('broker = ? AND mode = ?')
        params.extend([broker, mode])
    if ticker:
        where.append('ticker = ?')
        params.append(ticker)
    if decision:
        where.append('decision = ?')
        params.append(decision)
    where_sql = f"WHERE {' AND '.join(where)}" if where else ''
    cursor.execute(f'''
        SELECT * FROM trade_order_log {where_sql}
        ORDER BY id DESC LIMIT ? OFFSET ?
    ''', (*params, limit, offset))
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def count_trade_order_log(broker: str = None, mode: str = None, ticker: str = None, decision: str = None) -> int:
    """매매 판단/체결 로그 전체 건수(필터 적용) — 이력 페이지 페이지네이션용."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    where = []
    params = []
    if broker and mode:
        where.append('broker = ? AND mode = ?')
        params.extend([broker, mode])
    if ticker:
        where.append('ticker = ?')
        params.append(ticker)
    if decision:
        where.append('decision = ?')
        params.append(decision)
    where_sql = f"WHERE {' AND '.join(where)}" if where else ''
    cursor.execute(f'SELECT COUNT(*) FROM trade_order_log {where_sql}', params)
    count = cursor.fetchone()[0]
    conn.close()
    return count
