import requests
import json
import os
import time
from datetime import datetime
from dotenv import load_dotenv
from db_manager import save_stock_alert_to_db, init_db

load_dotenv()

# 한국투자증권 설정
APP_KEY = os.getenv("KIS_APP_KEY")
APP_SECRET = os.getenv("KIS_APP_SECRET")
URL_BASE = "https://openapi.koreainvestment.com:9443" # 실전 투자용

def get_access_token():
    """OAuth2 토큰 발급"""
    url = f"{URL_BASE}/oauth2/tokenP"
    headers = {"content-type": "application/json"}
    body = {
        "grant_type": "client_credentials",
        "appkey": APP_KEY,
        "appsecret": APP_SECRET
    }
    res = requests.post(url, headers=headers, data=json.dumps(body))
    return res.json().get("access_token")

def get_volume_ranking(token):
    """거래량 순위 상위 종목 조회"""
    url = f"{URL_BASE}/uapi/domestic-stock/v1/ranking/volume"
    headers = {
        "Content-Type": "application/json",
        "authorization": f"Bearer {token}",
        "appkey": APP_KEY,
        "appsecret": APP_SECRET,
        "tr_id": "FHPST01710000", # 거래량 순위 tr_id
        "custtype": "P"
    }
    params = {
        "fid_cond_mrkt_div_code": "J", # 전체
        "fid_cond_scr_div_code": "20171",
        "fid_input_iscd": "0000", # 전체
        "fid_div_cls_code": "0", # 전체
        "fid_blank": "",
        "fid_rank_sort_cls_code": "0" # 거래량 순
    }
    res = requests.get(url, headers=headers, params=params)
    return res.json().get("output", [])

def monitor_stocks():
    print("🚀 한국 주식 거래량 감시 시작!")
    init_db()
    token = get_access_token()
    
    last_notified = {} # 중복 알림 방지

    while True:
        try:
            # 장 운영 시간 체크 (9:00 ~ 15:30)
            now = datetime.now()
            if now.hour < 9 or (now.hour == 15 and now.minute > 30) or now.hour > 15:
                # print("💤 장외 시간 (대기 중...)")
                time.sleep(60)
                continue

            # 주말 체크
            if now.weekday() >= 5:
                time.sleep(3600)
                continue

            stocks = get_volume_ranking(token)
            
            for stock in stocks:
                name = stock['hts_kor_isnm']
                code = stock['mksc_shrn_iscd']
                price = stock['stck_prpr']
                change_rate = stock['prdy_ctrt'] # 전일 대비 등락률
                volume = stock['acml_vol'] # 누적 거래량
                vol_rate = float(stock['prdy_vol_rvrt']) # 전일 대비 거래량 비율
                vol_power = stock['bstp_nmpt_be_tnrt'] # 체결강도 (라이브러리에 따라 필드명 다를 수 있음)

                # 조건: 전일 대비 거래량 300% 이상 & 등락률 3% 이상
                if vol_rate > 300 and float(change_rate) > 3:
                    
                    # 1시간 내 중복 알림 방지
                    if code not in last_notified or (now - last_notified[code]).seconds > 3600:
                        print(f"🔥 [급등 포착] {name}({code}) - 거래량 비율: {vol_rate}%")
                        
                        save_stock_alert_to_db(
                            code=code,
                            name=name,
                            price=price,
                            change_rate=change_rate,
                            volume=volume,
                            volume_power=vol_power,
                            market_cap="-", # 순위 API에서는 별도 조회 필요
                            reason=f"전일비 거래량 {vol_rate}% 폭발",
                            url=f"https://finance.naver.com/item/main.nhn?code={code}"
                        )
                        last_notified[code] = now

            time.sleep(30) # 30초 간격으로 체크

        except Exception as e:
            print(f"❌ 에러 발생: {e}")
            token = get_access_token() # 토큰 재발급
            time.sleep(10)

if __name__ == "__main__":
    monitor_stocks()
