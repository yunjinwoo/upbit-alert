import requests
import json
import os
import time
from datetime import datetime
from dataclasses import asdict
from dotenv import load_dotenv
from db_manager import save_stock_alert_to_db, init_db, save_api_token, get_api_token
from kis_models import RequestHeader, RequestQueryParam, FluctuationRankingResponse

load_dotenv()

# 한국투자증권 설정
APP_KEY = os.getenv("KIS_APP_KEY")
APP_SECRET = os.getenv("KIS_APP_SECRET")
URL_BASE = "https://openapi.koreainvestment.com:9443"

# 토큰 전역 변수
ACCESS_TOKEN = None

def get_access_token():
    """OAuth2 토큰 발급 (DB 조회 우선)"""
    global ACCESS_TOKEN
    
    db_token = get_api_token('KIS')
    # if db_token:
    #     print("💾 DB에서 오늘 유효한 토큰을 불러왔습니다.")
    #     ACCESS_TOKEN = db_token
    #     return ACCESS_TOKEN

    print("📡 새로운 토큰 발급을 시도합니다...")
    url = f"{URL_BASE}/oauth2/tokenP"
    headers = {"content-type": "application/json"}
    body = {
        "grant_type": "client_credentials",
        "appkey": APP_KEY,
        "appsecret": APP_SECRET
    }
    
    try:
        res = requests.post(url, headers=headers, data=json.dumps(body))
        if res.status_code == 200:
            ACCESS_TOKEN = res.json().get("access_token")
            save_api_token('KIS', ACCESS_TOKEN)
            print("✅ 새로운 토큰이 발급되어 DB에 저장되었습니다.")
            return ACCESS_TOKEN
        else:
            print(f"❌ 토큰 발급 실패: {res.status_code} - {res.text}")
            return None
    except Exception as e:
        print(f"❌ 토큰 요청 중 에러: {e}")
        return None

def get_stock_ranking():
    """상승률 순위 종목 조회 (국내주식 등락률 순위)"""
    if ACCESS_TOKEN is None:
        return []

    url = f"{URL_BASE}/uapi/domestic-stock/v1/ranking/fluctuation"
    
    # 1. 헤더 객체 생성
    header_obj = RequestHeader(
        authorization=f"Bearer {ACCESS_TOKEN}",
        appkey=APP_KEY,
        appsecret=APP_SECRET,
        tr_id="FHPST01700000",
        custtype="P"
    )
    
    # 2. 쿼리 파라미터 객체 생성 (보내주신 명세 기준)
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
    print(f"📡 [{now_time}] 순위 데이터 호출 중...", end=" ")

    try:
        # header_obj.to_dict()를 통해 하이픈 처리된 헤더 전달
        res = requests.get(url, headers=header_obj.to_dict(), params=asdict(param_obj))
        raw_json = res.json()

        if res.status_code == 200:
            response_obj = FluctuationRankingResponse.from_json(raw_json)
            if response_obj.rt_cd != "0":
                print(f"❌ KIS API 에러: {response_obj.msg1} ({response_obj.msg_cd})")
                return []
            print(f"✅ 성공 ({len(response_obj.output)}건 수신)")
            return response_obj.output
        elif res.status_code == 401:
            print("🔑 토큰 만료! 재발급을 시도합니다.")
            get_access_token()
            return []
        else:
            print(f"❌ 실패! {res.status_code} - {res.text}")
            return []
    except Exception as e:
        print(f"🔥 에러: {e}")
        return []

def monitor_stocks():
    print("🚀 한국 주식 실시간 감시 시작!")
    init_db()
    
    while get_access_token() is None:
        print("⏳ 1분 후 다시 시도합니다...")
        time.sleep(61)

    last_notified = {}

    while True:
        try:
            now = datetime.now()
            if now.hour < 8 or now.hour >= 20:
                time.sleep(60)
                continue
            if now.weekday() >= 5:
                time.sleep(3600)
                continue

            stocks = get_stock_ranking()
            print(f"stocks - {stocks}")
            for stock in stocks:
                name = stock.hts_kor_isnm
                code = stock.stck_shrn_iscd
                price = stock.stck_prpr
                change_rate = stock.prdy_ctrt
                vol_rate = float(stock.prdy_vol_rvrt if stock.prdy_vol_rvrt else 0)

                if code not in last_notified or (now - last_notified[code]).seconds > 3600:
                    print(f"🔥 [포착] {name}({code}) | 등락: {change_rate}% | 거래량비: {vol_rate}%")
                    save_stock_alert_to_db(
                        code=code, name=name, price=price,
                        change_rate=change_rate, volume=stock.acml_vol,
                        volume_power="0", market_cap="-",
                        reason=f"전일비 거래량 {vol_rate}% 급증",
                        url=f"https://finance.naver.com/item/main.nhn?code={code}"
                    )
                    last_notified[code] = now

                #if vol_rate > 200 and float(change_rate) > 3:

            time.sleep(30)
        except Exception as e:
            print(f"❌ 에러: {e}")
            time.sleep(10)

if __name__ == "__main__":
    monitor_stocks()
