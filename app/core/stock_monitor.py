import requests
import json
import time
from datetime import datetime
from dataclasses import asdict
from app.config import Config
from app.utils.db_manager import save_stock_alert_to_db, init_db, save_api_token, get_api_token, save_stock_raw_data, save_daily_market_cap
from app.core.kis_models import RequestHeader, RequestQueryParam, MarketCapQueryParam, FluctuationRankingResponse, MarketCapRankingResponse
from app.utils.logger import get_logger

logger = get_logger()

# 토큰 전역 변수
ACCESS_TOKEN = None

def get_access_token():
    """OAuth2 토큰 발급 (DB 조회 우선)"""
    global ACCESS_TOKEN
    
    db_token = get_api_token('KIS')
    if db_token:
        logger.info("💾 DB에서 오늘 유효한 토큰을 불러왔습니다.")
        ACCESS_TOKEN = db_token
        return ACCESS_TOKEN

    logger.info("📡 새로운 토큰 발급을 시도합니다...")
    url = f"{Config.KIS_URL_BASE}/oauth2/tokenP"
    headers = {"content-type": "application/json"}
    body = {
        "grant_type": "client_credentials",
        "appkey": Config.KIS_APP_KEY,
        "appsecret": Config.KIS_APP_SECRET
    }
    
    try:
        res = requests.post(url, headers=headers, data=json.dumps(body))
        if res.status_code == 200:
            ACCESS_TOKEN = res.json().get("access_token")
            save_api_token('KIS', ACCESS_TOKEN)
            logger.info("✅ 새로운 토큰이 발급되어 DB에 저장되었습니다.")
            return ACCESS_TOKEN
        else:
            logger.error(f"❌ 토큰 발급 실패: {res.status_code} - {res.text}")
            return None
    except Exception as e:
        logger.error(f"❌ 토큰 요청 중 에러: {e}")
        return None

def fetch_market_cap_ranking():
    """시가총액 순위 종목 조회 (일별 1회 수집)"""
    if ACCESS_TOKEN is None:
        logger.error("❌ KIS API 토큰이 없어 시가총액 데이터를 가져올 수 없습니다.")
        return

    url = f"{Config.KIS_URL_BASE}/uapi/domestic-stock/v1/ranking/market-cap"
    
    header_obj = RequestHeader(
        authorization=f"Bearer {ACCESS_TOKEN}",
        appkey=Config.KIS_APP_KEY,
        appsecret=Config.KIS_APP_SECRET,
        tr_id="FHPST01740000", # 시가총액 순위 거래 ID
        custtype="P"
    )
    
    param_obj = MarketCapQueryParam()
    
    logger.info("📡 [일별] 시가총액 순위 데이터 호출 중...")

    try:
        full_headers = header_obj.to_dict()
        full_params = asdict(param_obj)

        res = requests.get(url, headers=full_headers, params=full_params)
        raw_json = res.json() # 응답을 먼저 JSON으로 파싱

        # KIS 시가총액 API의 원본 응답을 상세히 로깅
        logger.info(f"--- [DEBUG] KIS 시가총액 API 원본 응답 (Status: {res.status_code}) ---")
        logger.info(json.dumps(raw_json, ensure_ascii=False, indent=4))
        logger.info("-------------------------------------------------------")

        if res.status_code == 200:
            response_obj = MarketCapRankingResponse.from_json(raw_json) # 새로 정의한 모델 사용
            
            # API 타입과 함께 원본 데이터 DB에 저장
            output_data = raw_json.get("output", [])
            save_stock_raw_data(output_data, api_type="Market Cap Ranking")

            if response_obj.rt_cd != "0":
                logger.error(f"❌ KIS API 에러 (시가총액): {response_obj.msg1} ({response_obj.msg_cd})")
                return
            
            parsed_output_data = response_obj.output
            save_daily_market_cap(parsed_output_data)
            logger.info(f"✅ 일별 시가총액 순위 데이터 저장 성공 ({len(parsed_output_data)}건)")
        elif res.status_code == 401:
            logger.info("🔑 토큰 만료! 재발급을 시도합니다.")
            get_access_token()
        else:
            logger.error(f"❌ 실패 (시가총액)! {res.status_code} - {res.text}")
    except Exception as e:
        logger.error(f"🔥 에러 (시가총액): {e}")

def get_stock_ranking():
    """상승률 순위 종목 조회 (국내주식 등락률 순위)"""
    if ACCESS_TOKEN is None:
        return []

    url = f"{Config.KIS_URL_BASE}/uapi/domestic-stock/v1/ranking/fluctuation"
    
    # 1. 헤더 객체 생성
    header_obj = RequestHeader(
        authorization=f"Bearer {ACCESS_TOKEN}",
        appkey=Config.KIS_APP_KEY,
        appsecret=Config.KIS_APP_SECRET,
        tr_id="FHPST01700000",
        custtype="P"
    )
    
    # 2. 쿼리 파라미터 객체 생성
    param_obj = RequestQueryParam(
        fid_rsfl_rate2="0",
        fid_cond_mrkt_div_code="J",
        fid_cond_scr_div_code="20170",
        fid_input_iscd="0000",
        fid_rank_sort_cls_code="0",
        fid_input_cnt_1="0",
        fid_prc_cls_code="0",
        fid_input_price_1="10000",
        fid_input_price_2="1000000",
        fid_vol_cnt="0",
        fid_trgt_cls_code="0",
        fid_trgt_exls_cls_code="0",
        fid_div_cls_code="0",
        fid_rsfl_rate1="0"
    )
    
    now_time = datetime.now().strftime('%H:%M:%S')
    logger.info(f"📡 [{now_time}] 순위 데이터 호출 중...")

    try:
        full_headers = header_obj.to_dict()
        full_params = asdict(param_obj)

        res = requests.get(url, headers=full_headers, params=full_params)

        if res.status_code != 200:
            logger.error(f"Response Status: {res.status_code}")

        raw_json = res.json()

        if res.status_code == 200:
            # 전체 응답 데이터 (최대 30개 등)를 API 타입과 함께 DB에 저장
            output_data = raw_json.get("output", [])
            save_stock_raw_data(output_data, api_type="Fluctuation Ranking")
            
            response_obj = FluctuationRankingResponse.from_json(raw_json)
            if response_obj.rt_cd != "0":
                logger.error(f"❌ KIS API 에러: {response_obj.msg1} ({response_obj.msg_cd})")
                return []
            logger.info(f"✅ 성공 ({len(response_obj.output)}건 파싱 완료)")
            return response_obj.output
        elif res.status_code == 401:
            logger.info("🔑 토큰 만료! 재발급을 시도합니다.")
            get_access_token()
            return []
        else:
            logger.error(f"❌ 실패! {res.status_code} - {res.text}")
            return []
    except Exception as e:
        logger.error(f"🔥 에러: {e}")
        return []

def run_stock_monitor():
    logger.info("🚀 한국 주식 실시간 감시 시작!")
    init_db()
    
    while get_access_token() is None:
        logger.info("⏳ 4분 후 다시 시도합니다...")
        time.sleep(244) # 61 * 4

    last_notified = {}
    last_market_cap_date = None

    while True:
        try:
            now = datetime.now()
            today_str = now.strftime('%Y-%m-%d')
            
            # 장 운영 시간 외 대기 (예시)
            if now.hour < 8 or now.hour >= 20:
                time.sleep(240) # 60 * 4
                continue
            if now.weekday() >= 5:
                time.sleep(14400) # 3600 * 4
                continue

            # 일 1회 시가총액 데이터 수집 (오후 3시 40분쯤, 장 마감 후)
            if now.hour == 15 and now.minute >= 40 and last_market_cap_date != today_str:
                fetch_market_cap_ranking()
                last_market_cap_date = today_str
                time.sleep(5) # API 과부하 방지

            stocks = get_stock_ranking()
            for stock in stocks:
                name = stock.hts_kor_isnm
                code = stock.stck_shrn_iscd
                price = stock.stck_prpr
                change_rate = stock.prdy_ctrt
                vol_rate = float(stock.prdy_vol_rvrt if stock.prdy_vol_rvrt else 0)
                
                if code not in last_notified or (now - last_notified[code]).seconds > 3600:
                    logger.info(f"🔥 [포착] {name}({code}) | 등락: {change_rate}% | 거래량비: {vol_rate}%")
                    save_stock_alert_to_db(
                        code=code, name=name, price=price, 
                        change_rate=change_rate, volume=stock.acml_vol, 
                        volume_power="0", market_cap="-", 
                        reason=f"전일비 거래량 {vol_rate}% 급증",
                        url=f"https://finance.naver.com/item/main.nhn?code={code}"
                        )
                    last_notified[code] = now
            time.sleep(120) # 30 * 4
        except Exception as e:
            logger.error(f"❌ 에러: {e}")
            time.sleep(40) # 10 * 4

if __name__ == "__main__":
    run_stock_monitor()
