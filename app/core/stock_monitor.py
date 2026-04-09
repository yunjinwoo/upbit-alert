import requests
import json
import time
from datetime import datetime
from dataclasses import asdict
from app.config import Config
from app.utils.db_manager import save_stock_alert_to_db, init_db, save_api_token, get_api_token
from app.core.kis_models import RequestHeader, RequestQueryParam, FluctuationRankingResponse
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
            response_obj = FluctuationRankingResponse.from_json(raw_json)
            if response_obj.rt_cd != "0":
                logger.error(f"❌ KIS API 에러: {response_obj.msg1} ({response_obj.msg_cd})")
                return []
            logger.info(f"✅ 성공 ({len(response_obj.output)}건 수신)")
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

    while True:
        try:
            now = datetime.now()
            # 장 운영 시간 외 대기 (예시)
            if now.hour < 8 or now.hour >= 20:
                time.sleep(240) # 60 * 4
                continue
            if now.weekday() >= 5:
                time.sleep(14400) # 3600 * 4
                continue

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
