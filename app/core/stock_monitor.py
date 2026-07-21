import requests
import json
import time
from datetime import datetime
from dataclasses import asdict
from app.config import Config
from app.utils.db_manager import save_stock_alert_to_db, init_db, save_api_token, get_api_token, save_stock_raw_data, save_daily_market_cap, save_daily_investor_trend, save_stock_investor_daily, save_sector_index_daily, get_signal_score_batch, save_hts_top_view, save_job_run_log, get_market_cap_history, save_top_interest_daily
from app.core.kis_models import RequestHeader, RequestQueryParam, MarketCapQueryParam, FluctuationRankingResponse, MarketCapRankingResponse, StockInvestorDailyItem
from app.core.upbit_monitor import send_slack_msg
from app.utils.google_sheets import save_signal_score_to_sheet, save_signal_score_readme, save_investor_ranking_to_sheet
from app.utils.sync_client import push_all_tables_to_server
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

# 코스피/코스닥 지수 + 세부 업종 코드 (KIS 국내업종 구분별전체시세 FHPUP02140000 조회로 확인한 실제 코드)
SECTOR_NAMES = {
    '0001': '코스피', '1001': '코스닥', '2001': '코스피200',
    # 코스피 세부업종
    '0002': '코스피 대형주', '0003': '코스피 중형주', '0004': '코스피 소형주',
    '0005': '코스피 음식료·담배', '0006': '코스피 섬유·의류', '0007': '코스피 종이·목재',
    '0008': '코스피 화학', '0009': '코스피 제약', '0010': '코스피 비금속',
    '0011': '코스피 금속', '0012': '코스피 기계·장비', '0013': '코스피 전기·전자',
    '0014': '코스피 의료·정밀기기', '0015': '코스피 운송장비·부품', '0016': '코스피 유통',
    '0017': '코스피 전기·가스', '0018': '코스피 건설', '0019': '코스피 운송·창고',
    '0020': '코스피 통신', '0021': '코스피 금융', '0024': '코스피 증권',
    '0025': '코스피 보험', '0026': '코스피 일반서비스', '0027': '코스피 제조',
    '0028': '코스피 부동산', '0029': '코스피 IT서비스', '0030': '코스피 오락·문화',
    # 코스닥 세부업종
    '1006': '코스닥 일반서비스', '1009': '코스닥 제조', '1010': '코스닥 건설',
    '1011': '코스닥 유통', '1013': '코스닥 운송·창고', '1014': '코스닥 금융',
    '1015': '코스닥 오락·문화', '1019': '코스닥 음식료·담배', '1020': '코스닥 섬유·의류',
    '1021': '코스닥 종이·목재', '1023': '코스닥 화학', '1024': '코스닥 제약',
    '1025': '코스닥 비금속', '1026': '코스닥 금속', '1027': '코스닥 기계·장비',
    '1028': '코스닥 전기·전자', '1029': '코스닥 의료·정밀기기', '1030': '코스닥 운송장비·부품',
    '1031': '코스닥 기타제조',
}

def fetch_sector_index_daily(iscd="0001", base_date=None):
    """업종 일자별지수 조회 (FHPUP02120000) — output2 배열 반환"""
    if ACCESS_TOKEN is None:
        get_access_token()
    if ACCESS_TOKEN is None:
        logger.error("❌ KIS API 토큰이 없어 업종지수 데이터를 가져올 수 없습니다.")
        return []

    if base_date is None:
        base_date = datetime.now().strftime("%Y%m%d")

    url = f"{Config.KIS_URL_BASE}/uapi/domestic-stock/v1/quotations/inquire-index-daily-price"
    headers = {
        "content-type": "application/json",
        "authorization": f"Bearer {ACCESS_TOKEN}",
        "appkey": Config.KIS_APP_KEY,
        "appsecret": Config.KIS_APP_SECRET,
        "tr_id": "FHPUP02120000",
        "custtype": "P",
    }
    params = {
        "FID_COND_MRKT_DIV_CODE": "U",
        "FID_INPUT_ISCD": iscd,
        "FID_INPUT_DATE_1": base_date,
        "FID_PERIOD_DIV_CODE": "D",
    }

    sector_name = SECTOR_NAMES.get(iscd, iscd)

    try:
        res = requests.get(url, headers=headers, params=params)
        data = res.json()
        if data.get("rt_cd") != "0":
            logger.error(f"❌ 업종지수 API 오류: {data.get('msg1')}")
            return []
        records = data.get("output2", [])
        if records:
            saved = save_sector_index_daily(records, iscd, sector_name)
            logger.info(f"[업종지수] {sector_name}({iscd}) {saved}건 DB 저장")
        return records
    except Exception as e:
        logger.error(f"❌ 업종지수 조회 에러: {e}")
        return []


def fetch_sector_stocks(iscd):
    """업종 소속 종목 조회 (국내주식 등락률 순위 FHPST01700000 — 업종코드로 필터링, 실시간 조회 전용, DB 미저장)"""
    global ACCESS_TOKEN
    if ACCESS_TOKEN is None:
        get_access_token()
    if ACCESS_TOKEN is None:
        logger.error("❌ KIS API 토큰이 없어 업종 소속 종목을 가져올 수 없습니다.")
        return []

    url = f"{Config.KIS_URL_BASE}/uapi/domestic-stock/v1/ranking/fluctuation"
    headers = {
        "content-type": "application/json",
        "authorization": f"Bearer {ACCESS_TOKEN}",
        "appkey": Config.KIS_APP_KEY,
        "appsecret": Config.KIS_APP_SECRET,
        "tr_id": "FHPST01700000",
        "custtype": "P",
    }
    params = {
        "fid_rsfl_rate2": "",
        "fid_cond_mrkt_div_code": "J",
        "fid_cond_scr_div_code": "20170",
        "fid_input_iscd": iscd,
        "fid_rank_sort_cls_code": "0",
        "fid_input_cnt_1": "0",
        "fid_prc_cls_code": "0",
        "fid_input_price_1": "",
        "fid_input_price_2": "",
        "fid_vol_cnt": "",
        "fid_trgt_cls_code": "0",
        "fid_trgt_exls_cls_code": "0",
        "fid_div_cls_code": "0",
        "fid_rsfl_rate1": "",
    }

    try:
        res = requests.get(url, headers=headers, params=params)
        data = res.json()
        if data.get("rt_cd") != "0":
            logger.error(f"❌ 업종 소속 종목 API 오류: {data.get('msg1')}")
            return []
        return data.get("output", [])
    except Exception as e:
        logger.error(f"❌ 업종 소속 종목 조회 에러: {e}")
        return []


def fetch_market_cap_ranking(mrkt_div_code="J", input_iscd="0000", div_cls_code="0", max_retries=2, max_pages=3):
    """시가총액 순위 종목 조회 (일별 1회 수집).
    KIS API는 1회 호출당 최대 30건만 반환하므로, 연속조회(tr_cont)로 최대 max_pages회
    이어붙여 최대 max_pages*30건까지 수집한다.
    401(토큰 만료)/일시적 오류 시 토큰을 재발급하고 페이지별 최대 max_retries회까지 재시도한다.
    (2026-07-09 코스피만 수집 누락된 사고 — 재시도 없이 그냥 넘어가던 게 원인)
    반환: 저장 성공 여부(bool)
    """
    global ACCESS_TOKEN
    if ACCESS_TOKEN is None:
        get_access_token()
    if ACCESS_TOKEN is None:
        logger.error("❌ KIS API 토큰이 없어 시가총액 데이터를 가져올 수 없습니다.")
        return False

    url = f"{Config.KIS_URL_BASE}/uapi/domestic-stock/v1/ranking/market-cap"
    all_items = []
    request_tr_cont = None  # 첫 페이지는 미지정, 이후 페이지는 "N"(연속조회)

    for page in range(1, max_pages + 1):
        page_ok = False
        has_more = False

        for attempt in range(1, max_retries + 1):
            header_obj = RequestHeader(
                authorization=f"Bearer {ACCESS_TOKEN}",
                appkey=Config.KIS_APP_KEY,
                appsecret=Config.KIS_APP_SECRET,
                tr_id="FHPST01740000",
                custtype="P",
                tr_cont=request_tr_cont,
            )

            param_obj = MarketCapQueryParam(
                fid_cond_mrkt_div_code=mrkt_div_code,
                fid_input_iscd=input_iscd,
                fid_div_cls_code=div_cls_code,
            )

            logger.info(f"📡 [일별] 시가총액 순위 데이터 호출 중... (iscd={input_iscd}, page={page}/{max_pages}, 시도 {attempt}/{max_retries})")

            try:
                full_headers = header_obj.to_dict()
                full_params = asdict(param_obj)

                res = requests.get(url, headers=full_headers, params=full_params, timeout=10)
                raw_json = res.json() # 응답을 먼저 JSON으로 파싱

                # KIS 시가총액 API의 원본 응답을 상세히 로깅
                logger.info(f"--- [DEBUG] KIS 시가총액 API 원본 응답 (Status: {res.status_code}, page={page}) ---")
                logger.info(json.dumps(raw_json, ensure_ascii=False, indent=4))
                logger.info("-------------------------------------------------------")

                if res.status_code == 200:
                    response_obj = MarketCapRankingResponse.from_json(raw_json) # 새로 정의한 모델 사용

                    # API 타입과 함께 원본 데이터 DB에 저장
                    output_data = raw_json.get("output", [])
                    save_stock_raw_data(output_data, api_type="Market Cap Ranking")

                    if response_obj.rt_cd != "0":
                        logger.error(f"❌ KIS API 에러 (시가총액): {response_obj.msg1} ({response_obj.msg_cd})")
                    else:
                        all_items.extend(response_obj.output)
                        has_more = res.headers.get("tr_cont", "") == "M"
                        page_ok = True
                        break
                elif res.status_code == 401:
                    logger.info("🔑 토큰 만료! 재발급을 시도합니다.")
                    get_access_token()
                else:
                    logger.error(f"❌ 실패 (시가총액)! {res.status_code} - {res.text}")
            except Exception as e:
                logger.error(f"🔥 에러 (시가총액): {e}")

            if attempt < max_retries:
                time.sleep(2)

        if not page_ok:
            logger.error(f"❌ 시가총액 수집 최종 실패 (iscd={input_iscd}, page={page}, {max_retries}회 시도 모두 실패)")
            return False

        if not has_more:
            break
        request_tr_cont = "N"
        time.sleep(1)

    if not all_items:
        logger.error(f"❌ 시가총액 수집 실패 (iscd={input_iscd}, 수신 데이터 없음)")
        return False

    save_daily_market_cap(all_items, fid_input_iscd=input_iscd)
    logger.info(f"✅ 일별 시가총액 순위 데이터 저장 성공 ({len(all_items)}건)")
    return True

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

def fetch_ranking_preview(api_path: str, tr_id: str, params: dict, max_retries=2):
    """순위분석 신규 API 미리보기 조회 (DB 저장 없음 — 실제 반영 전 데이터 확인용).
    api_path: KIS_URL_BASE 이후 경로 (예: /uapi/domestic-stock/v1/ranking/volume-power)
    """
    global ACCESS_TOKEN
    if ACCESS_TOKEN is None:
        get_access_token()
    if ACCESS_TOKEN is None:
        return {"error": "KIS API 토큰이 없습니다."}

    url = f"{Config.KIS_URL_BASE}{api_path}"

    for attempt in range(1, max_retries + 1):
        header_obj = RequestHeader(
            authorization=f"Bearer {ACCESS_TOKEN}",
            appkey=Config.KIS_APP_KEY,
            appsecret=Config.KIS_APP_SECRET,
            tr_id=tr_id,
            custtype="P"
        )
        try:
            res = requests.get(url, headers=header_obj.to_dict(), params=params, timeout=10)
            raw_json = res.json()

            if res.status_code == 200:
                if raw_json.get("rt_cd") != "0":
                    logger.error(f"❌ 순위분석 미리보기 API 에러 ({tr_id}): {raw_json.get('msg1')}")
                    return {"error": raw_json.get("msg1", "알 수 없는 오류")}
                # 응답 배열 키는 API마다 다름 (output / output1 / output2)
                output = raw_json.get("output") or raw_json.get("output1") or raw_json.get("output2") or []
                return {"output": output}
            elif res.status_code == 401:
                logger.info("🔑 토큰 만료! 재발급을 시도합니다.")
                get_access_token()
            else:
                logger.error(f"❌ 순위분석 미리보기 실패 ({tr_id})! {res.status_code} - {res.text}")
                return {"error": f"{res.status_code} - {res.text[:200]}"}
        except Exception as e:
            logger.error(f"🔥 순위분석 미리보기 에러 ({tr_id}): {e}")
            return {"error": str(e)}

        if attempt < max_retries:
            time.sleep(1)

    return {"error": "최종 실패 (재시도 초과)"}


def fetch_hts_top_view(max_retries=2):
    """HTS조회상위20종목 수집 후 종목별 현재가 조회(이름/가격/등락률)로 보강해서 저장 (시간별 1회).
    hts-top-view 응답엔 종목코드만 있어서, 종목당 1회씩 현재가 API를 추가 호출한다(최대 20회).
    반환: 저장 성공 여부(bool)
    """
    global ACCESS_TOKEN
    if ACCESS_TOKEN is None:
        get_access_token()
    if ACCESS_TOKEN is None:
        logger.error("❌ KIS API 토큰이 없어 HTS조회상위 데이터를 가져올 수 없습니다.")
        return False

    url = f"{Config.KIS_URL_BASE}/uapi/domestic-stock/v1/ranking/hts-top-view"
    raw_items = None

    for attempt in range(1, max_retries + 1):
        header_obj = RequestHeader(
            authorization=f"Bearer {ACCESS_TOKEN}",
            appkey=Config.KIS_APP_KEY,
            appsecret=Config.KIS_APP_SECRET,
            tr_id="HHMCM000100C0",
            custtype="P"
        )
        try:
            res = requests.get(url, headers=header_obj.to_dict(), params={}, timeout=10)
            raw_json = res.json()

            if res.status_code == 200:
                if raw_json.get("rt_cd") != "0":
                    logger.error(f"❌ HTS조회상위 API 에러: {raw_json.get('msg1')}")
                else:
                    raw_items = raw_json.get("output1", [])
                    break
            elif res.status_code == 401:
                logger.info("🔑 토큰 만료! 재발급을 시도합니다.")
                get_access_token()
            else:
                logger.error(f"❌ HTS조회상위 실패! {res.status_code} - {res.text}")
        except Exception as e:
            logger.error(f"🔥 HTS조회상위 에러: {e}")

        if attempt < max_retries:
            time.sleep(2)

    if not raw_items:
        logger.error("❌ HTS조회상위 수집 최종 실패 (재시도 초과)")
        return False

    # 종목별 현재가(가격/등락률) + 기본조회(이름) 조합 — 코드당 2회, 실패해도 코드/순위는 유지
    # 주의: mrkt_div_cls_code가 'Q'인 항목도 코드 앞에 'Q'를 붙이면 조회 실패함(실측 확인) — 코드는 항상 그대로 사용
    price_url = f"{Config.KIS_URL_BASE}/uapi/domestic-stock/v1/quotations/inquire-price"
    info_url = f"{Config.KIS_URL_BASE}/uapi/domestic-stock/v1/quotations/search-stock-info"
    items = []
    for idx, raw in enumerate(raw_items, start=1):
        code = raw.get("mksc_shrn_iscd", "")
        market_div = raw.get("mrkt_div_cls_code", "")

        name, price, change_rate, prdy_vrss = None, None, None, None
        try:
            price_headers = RequestHeader(
                authorization=f"Bearer {ACCESS_TOKEN}",
                appkey=Config.KIS_APP_KEY,
                appsecret=Config.KIS_APP_SECRET,
                tr_id="FHKST01010100",
                custtype="P"
            ).to_dict()
            price_params = {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": code}
            pres = requests.get(price_url, headers=price_headers, params=price_params, timeout=10)
            pdata = pres.json()
            if pres.status_code == 200 and pdata.get("rt_cd") == "0":
                out = pdata.get("output", {})
                price = out.get("stck_prpr")
                change_rate = out.get("prdy_ctrt")
                prdy_vrss = out.get("prdy_vrss")  # 전일대비(부호 포함) — 전일종가 = price - prdy_vrss
            else:
                logger.warning(f"[HTS조회상위] {code} 현재가 조회 실패: {pdata.get('msg1', pres.status_code)}")
        except Exception as e:
            logger.warning(f"[HTS조회상위] {code} 현재가 조회 에러: {e}")
        time.sleep(0.15)  # API 호출 간격 (초당 제한 여유있게 회피)

        try:
            info_headers = RequestHeader(
                authorization=f"Bearer {ACCESS_TOKEN}",
                appkey=Config.KIS_APP_KEY,
                appsecret=Config.KIS_APP_SECRET,
                tr_id="CTPF1002R",
                custtype="P"
            ).to_dict()
            info_params = {"PRDT_TYPE_CD": "300", "PDNO": code}
            ires = requests.get(info_url, headers=info_headers, params=info_params, timeout=10)
            idata = ires.json()
            if ires.status_code == 200 and idata.get("rt_cd") == "0":
                name = idata.get("output", {}).get("prdt_abrv_name")
            else:
                logger.warning(f"[HTS조회상위] {code} 종목명 조회 실패: {idata.get('msg1', ires.status_code)}")
        except Exception as e:
            logger.warning(f"[HTS조회상위] {code} 종목명 조회 에러: {e}")

        items.append({
            "rank": idx, "code": code, "market_div": market_div,
            "name": name, "price": price, "change_rate": change_rate, "prdy_vrss": prdy_vrss,
        })
        time.sleep(0.15)  # API 호출 간격 (초당 제한 여유있게 회피)

    now = datetime.now()
    save_hts_top_view(items, date=now.strftime('%Y-%m-%d'), hour=now.hour)
    logger.info(f"✅ HTS조회상위20종목 저장 성공 ({len(items)}건, {now.hour}시)")
    return True


def fetch_top_interest_stock(max_retries=2):
    """관심종목등록 상위 수집 (일별 1회). 네이버 인기검색종목(lastsearch2.naver)의 대체 지표 —
    해당 페이지는 robots.txt(Disallow: / for User-agent: *)로 크롤링이 막혀 있어, 비슷한 성격의
    KIS 공식 API(관심종목등록 건수 기준 순위)를 대신 사용한다. 종목명/가격/등락률/등록건수가
    한 번의 호출로 전부 내려와서 HTS조회상위와 달리 종목별 추가 호출이 필요 없다.
    반환: 저장 성공 여부(bool)
    """
    global ACCESS_TOKEN
    if ACCESS_TOKEN is None:
        get_access_token()
    if ACCESS_TOKEN is None:
        logger.error("❌ KIS API 토큰이 없어 관심종목등록 상위 데이터를 가져올 수 없습니다.")
        return False

    url = f"{Config.KIS_URL_BASE}/uapi/domestic-stock/v1/ranking/top-interest-stock"
    raw_items = None

    for attempt in range(1, max_retries + 1):
        header_obj = RequestHeader(
            authorization=f"Bearer {ACCESS_TOKEN}",
            appkey=Config.KIS_APP_KEY,
            appsecret=Config.KIS_APP_SECRET,
            tr_id="FHPST01800000",
            custtype="P"
        )
        params = {
            "fid_input_iscd_2": "000000",
            "fid_cond_mrkt_div_code": "J",
            "fid_cond_scr_div_code": "20180",
            "fid_input_iscd": "0000",
            "fid_trgt_cls_code": "0",
            "fid_trgt_exls_cls_code": "0",
            "fid_input_price_1": "",
            "fid_input_price_2": "",
            "fid_vol_cnt": "",
            "fid_div_cls_code": "0",
            "fid_input_cnt_1": "1",
        }
        try:
            res = requests.get(url, headers=header_obj.to_dict(), params=params, timeout=10)
            raw_json = res.json()

            if res.status_code == 200:
                if raw_json.get("rt_cd") != "0":
                    logger.error(f"❌ 관심종목등록 상위 API 에러: {raw_json.get('msg1')}")
                else:
                    raw_items = raw_json.get("output", [])
                    break
            elif res.status_code == 401:
                logger.info("🔑 토큰 만료! 재발급을 시도합니다.")
                get_access_token()
            else:
                logger.error(f"❌ 관심종목등록 상위 실패! {res.status_code} - {res.text}")
        except Exception as e:
            logger.error(f"🔥 관심종목등록 상위 에러: {e}")

        if attempt < max_retries:
            time.sleep(2)

    if not raw_items:
        logger.error("❌ 관심종목등록 상위 수집 최종 실패 (재시도 초과)")
        return False

    items = [{
        "rank": int(r.get("data_rank", idx)),
        "code": r.get("mksc_shrn_iscd", ""),
        "name": r.get("hts_kor_isnm"),
        "market_div": r.get("mrkt_div_cls_name"),
        "price": r.get("stck_prpr"),
        "change_rate": r.get("prdy_ctrt"),
        "reg_count": r.get("inter_issu_reg_csnu"),
    } for idx, r in enumerate(raw_items, start=1)]

    now = datetime.now()
    save_top_interest_daily(items, date=now.strftime('%Y-%m-%d'))
    logger.info(f"✅ 관심종목등록 상위 저장 성공 ({len(items)}건)")
    return True


def fetch_investor_trend(exch_div="J", mrkt_div="1"):
    """투자자별 프로그램 매매동향 조회"""
    if ACCESS_TOKEN is None:
        get_access_token()
    if ACCESS_TOKEN is None:
        return {"error": "토큰 없음"}

    url = f"{Config.KIS_URL_BASE}/uapi/domestic-stock/v1/quotations/investor-program-trade-today"

    header_obj = RequestHeader(
        authorization=f"Bearer {ACCESS_TOKEN}",
        appkey=Config.KIS_APP_KEY,
        appsecret=Config.KIS_APP_SECRET,
        tr_id="HHPPG046600C1",
        custtype="P"
    )

    params = {
        "EXCH_DIV_CLS_CODE": exch_div,   # J: KRX, NX: NXT, UN: 통합
        "MRKT_DIV_CLS_CODE": mrkt_div,   # 1: 코스피, 4: 코스닥
    }

    try:
        res = requests.get(url, headers=header_obj.to_dict(), params=params)
        raw_json = res.json()
        logger.info(f"[투자자별 매매동향] Status: {res.status_code}, Response: {json.dumps(raw_json, ensure_ascii=False)[:500]}")

        if res.status_code == 200 and raw_json.get('rt_cd') == '0':
            output1 = raw_json.get('output1', [])
            if output1:
                save_daily_investor_trend(output1, exch_div, mrkt_div)
                logger.info(f"[투자자별 매매동향] DB 저장 완료 ({len(output1)}건)")

        return raw_json
    except Exception as e:
        logger.error(f"[투자자별 매매동향] 오류: {e}")
        return {"error": str(e)}


def fetch_stock_investor_daily(codes: list, date_str: str = None):
    """시총 상위 종목들의 투자자매매동향(일별) 수집 및 저장.
    codes: [(code, name), ...] 형태의 리스트
    date_str: 'YYYYMMDD' 형식, 없으면 오늘
    반환: (saved_count, first_error_msg)
    """
    if ACCESS_TOKEN is None:
        get_access_token()
    if ACCESS_TOKEN is None:
        msg = "KIS 토큰 없음 — 수집 중단"
        logger.error(msg)
        return 0, msg

    if not date_str:
        date_str = datetime.now().strftime('%Y%m%d')

    url = f"{Config.KIS_URL_BASE}/uapi/domestic-stock/v1/quotations/investor-trade-by-stock-daily"

    saved = 0
    first_error = None

    for code, name in codes:
        try:
            headers = RequestHeader(
                authorization=f"Bearer {ACCESS_TOKEN}",
                appkey=Config.KIS_APP_KEY,
                appsecret=Config.KIS_APP_SECRET,
                tr_id="FHPTJ04160001",
                custtype="P"
            ).to_dict()

            params = {
                "FID_COND_MRKT_DIV_CODE": "J",
                "FID_INPUT_ISCD": code,
                "FID_INPUT_DATE_1": date_str,
                "FID_ORG_ADJ_PRC": "",
                "FID_ETC_CLS_CODE": "1",
            }

            res = requests.get(url, headers=headers, params=params, timeout=8)
            data = res.json()

            if data.get("rt_cd") != "0":
                err_msg = data.get('msg1', '알 수 없는 오류')
                logger.warning(f"[투자자일별] {code} {name} — {err_msg}")
                if first_error is None:
                    first_error = f"{code} {name}: {err_msg}"
                time.sleep(0.3)
                continue

            items = [StockInvestorDailyItem.from_json(d) for d in data.get("output2", [])]
            if items:
                save_stock_investor_daily(code, name, items)
                saved += 1
                logger.info(f"[투자자일별] {code} {name} — {len(items)}건 저장")
            time.sleep(0.35)  # API 호출 간격
        except Exception as e:
            logger.error(f"[투자자일별] {code} 에러: {e}")
            if first_error is None:
                first_error = f"{code}: {e}"
            time.sleep(0.5)

    return saved, first_error


def send_signal_score_alerts(scores: list):
    """Signal Score 계산 결과 중 A등급 종목만 Slack으로 즉시 알림 발송.
    B등급(대시보드 노출)/C등급(저장만)은 여기서 알림을 보내지 않고
    signal_score_daily 테이블 저장만으로 처리(대시보드에서 등급 필터로 조회).
    """
    a_grade = [s for s in scores if s.get('grade') == 'A']
    if not a_grade:
        logger.info("[Signal Score] A등급 종목 없음 — Slack 알림 생략")
        return

    for s in a_grade:
        hts_bonus = s.get('hts_top_view_bonus_score', 0)
        top_interest_bonus = s.get('top_interest_bonus_score', 0)
        interest_bonus = hts_bonus + top_interest_bonus
        base_score = s['total'] - interest_bonus
        text = (
            f"🅰️ [Signal Score A등급] {s['name']}({s['code']}) 기본점수 {base_score}점"
            f"{f' + 관심도 보너스 {interest_bonus}점(HTS {hts_bonus}+관심등록 {top_interest_bonus})' if interest_bonus else ''} = 총점 {s['total']}점\n"
            f"모멘텀 {s['momentum_score']} | 수급 {s['supply_demand_score']} | "
            f"랭킹안정성 {s['rank_stability_score']} | 시장환경 {s['market_environment_score']} | "
            f"리스크 {s['risk_penalty_score']}\n"
            f"https://finance.naver.com/item/main.nhn?code={s['code']}"
        )
        send_slack_msg(text)
        logger.info(f"[Signal Score] A등급 알림 발송: {s['name']}({s['code']}) {s['total']}점")


def _log_job_run(job_name: str, description: str, api_used: str, start_time: datetime,
                  success: bool, count: int = None, error_message: str = None, trigger_type: str = 'auto'):
    """작업 실행 결과를 job_run_log 테이블에 기록 (동기화 관리 페이지 "처리 로그"용)."""
    end_time = datetime.now()
    save_job_run_log(
        job_name, description, api_used,
        start_time.strftime('%Y-%m-%d %H:%M:%S'),
        end_time.strftime('%Y-%m-%d %H:%M:%S'),
        success, count=count, error_message=error_message, trigger_type=trigger_type,
    )


# ── 스케줄 작업 — 자동(run_stock_monitor 루프)/수동(동기화 관리 페이지 버튼) 공용 ──────────

def run_job_hts_top_view(trigger_type: str = 'auto') -> bool:
    """HTS조회상위20종목 수집 실행 + 실행이력 기록."""
    start = datetime.now()
    error_message = None
    try:
        ok = fetch_hts_top_view()
    except Exception as e:
        ok = False
        error_message = str(e)
    _log_job_run('hts_top_view', 'HTS조회상위20종목 수집',
                 '/uapi/domestic-stock/v1/ranking/hts-top-view', start, ok,
                 error_message=error_message, trigger_type=trigger_type)
    return ok


def run_job_top_interest(trigger_type: str = 'auto') -> bool:
    """관심종목등록 상위 수집 실행 + 실행이력 기록."""
    start = datetime.now()
    error_message = None
    try:
        ok = fetch_top_interest_stock()
    except Exception as e:
        ok = False
        error_message = str(e)
    _log_job_run('top_interest', '관심종목등록 상위 수집',
                 '/uapi/domestic-stock/v1/ranking/top-interest-stock', start, ok,
                 error_message=error_message, trigger_type=trigger_type)
    return ok


def run_job_investor_trend(trigger_type: str = 'auto') -> bool:
    """투자자별 프로그램 매매동향 수집(코스피/코스닥) 실행 + 실행이력 기록."""
    start = datetime.now()
    error_message = None
    count = 0
    try:
        for mrkt in ("1", "4"):
            fetch_investor_trend(exch_div="J", mrkt_div=mrkt)
            count += 1
            time.sleep(2)
        ok = True
    except Exception as e:
        ok = False
        error_message = str(e)
    _log_job_run('investor_trend', '투자자별 프로그램 매매동향 수집 (코스피/코스닥)',
                 '/uapi/domestic-stock/v1/quotations/investor-program-trade-today', start, ok,
                 count=count, error_message=error_message, trigger_type=trigger_type)
    return ok


def run_job_sector_index(trigger_type: str = 'auto') -> bool:
    """업종 일자별지수 수집(코스피/코스닥/코스피200 + 세부 업종 전체) 실행 + 실행이력 기록."""
    start = datetime.now()
    error_message = None
    count = 0
    try:
        for iscd in SECTOR_NAMES:
            fetch_sector_index_daily(iscd=iscd)
            count += 1
            time.sleep(1)
        ok = True
    except Exception as e:
        ok = False
        error_message = str(e)
    _log_job_run('sector_index', '업종 일자별지수 수집 (코스피/코스닥/코스피200 + 세부 업종)',
                 '/uapi/domestic-stock/v1/quotations/inquire-index-daily-price', start, ok,
                 count=count, error_message=error_message, trigger_type=trigger_type)
    return ok


def run_job_market_cap_and_signal_score(trigger_type: str = 'auto') -> bool:
    """시가총액 순위 + 종목별 투자자 수집 → Signal Score 계산/저장/Slack알림/시트동기화까지 한 번에 실행.
    반환: 시가총액 수집(코스피+코스닥)이 모두 성공했는지 여부.
    """
    start = datetime.now()
    error_message = None
    count = None
    all_ok = True
    try:
        for iscd in ('0001', '1001'):
            ok = fetch_market_cap_ranking(mrkt_div_code='J', input_iscd=iscd)
            if not ok:
                all_ok = False
            time.sleep(2)

        if all_ok:
            cap_rows = get_market_cap_history(limit_dates=1, fid_input_iscd='combined')
            codes = [(r['code'], r['name']) for r in cap_rows]
            count = len(codes)
            if codes:
                fetch_stock_investor_daily(codes, date_str=datetime.now().strftime('%Y%m%d'))
            time.sleep(3)

            scores = get_signal_score_batch(fid_input_iscd='combined', save=True)
            logger.info(f"[Signal Score] {len(scores)}건 계산/저장 완료")
            send_signal_score_alerts(scores)
            save_signal_score_to_sheet(scores)
            save_investor_ranking_to_sheet(days=10, top_n=40)
        else:
            error_message = "시가총액 수집 일부 실패 (코스피/코스닥 중 하나 이상)"
    except Exception as e:
        all_ok = False
        error_message = str(e)

    _log_job_run('market_cap_signal_score', '시가총액 수집 + 종목별 투자자 + Signal Score 계산/알림',
                 '/uapi/domestic-stock/v1/ranking/market-cap', start, all_ok,
                 count=count, error_message=error_message, trigger_type=trigger_type)
    return all_ok


def run_job_remote_sync(trigger_type: str = 'auto') -> bool:
    """원격 서버로 전체 데이터 동기화 실행 + 실행이력 기록."""
    start = datetime.now()
    error_message = None
    ok = True
    try:
        result = push_all_tables_to_server()
        logger.info(f"[동기화] 전송 결과: {result}")
    except Exception as e:
        ok = False
        error_message = str(e)
    _log_job_run('remote_sync', '원격 서버 전체 데이터 동기화', '내부 API (자체 서버 /api/sync/push)',
                 start, ok, error_message=error_message, trigger_type=trigger_type)
    return ok


def run_stock_monitor():
    logger.info("🚀 한국 주식 실시간 감시 시작!")
    init_db()
    save_signal_score_readme()

    while get_access_token() is None:
        logger.info("⏳ 4분 후 다시 시도합니다...")
        time.sleep(244) # 61 * 4

    last_notified = {}
    last_market_cap_date = None
    last_close_data_hour = None  # 14시 or 19시 수집 여부 (시간 단위로 추적)
    last_sync_date = None  # 20시 원격 서버 동기화 여부 (하루 1회)
    last_hts_top_view_hour = None  # HTS조회상위20종목 매시간 수집 여부 (시간 단위로 추적)
    last_top_interest_date = None  # 관심종목등록 상위 일 1회 수집 여부

    while True:
        try:
            now = datetime.now()
            today_str = now.strftime('%Y-%m-%d')

            # 평일 장중(09~15시) 매시 10분 이후 첫 루프 — HTS조회상위20종목 수집 (시간당 1회)
            if now.weekday() < 5 and 9 <= now.hour <= 15 and now.minute >= 10:
                run_key = f"{today_str}-{now.hour}"
                if last_hts_top_view_hour != run_key:
                    logger.info(f"⏰ [스케줄] {now.hour}시 HTS조회상위20종목 수집 시작")
                    if run_job_hts_top_view():
                        last_hts_top_view_hour = run_key
                    else:
                        logger.warning("⚠️ HTS조회상위 수집 실패 — 다음 루프에서 재시도합니다.")

            # 평일 16시 — 관심종목등록 상위 수집 (하루 1회, 장마감 직후)
            if now.weekday() < 5 and now.hour == 16 and last_top_interest_date != today_str:
                logger.info("⏰ [스케줄] 16시 관심종목등록 상위 수집 시작")
                if run_job_top_interest():
                    last_top_interest_date = today_str
                else:
                    logger.warning("⚠️ 관심종목등록 상위 수집 실패 — 다음 루프에서 재시도합니다.")

            # 평일 14시 또는 19시 — 투자자별 매매동향 + 업종지수 수집 (시간당 1회)
            # 14시는 장중이라 업종지수는 잠정치로 저장되고, 19시(장마감 후)에 확정치로 덮어써진다.
            if now.weekday() < 5 and now.hour in (14, 19):
                run_key = f"{today_str}-{now.hour}"
                if last_close_data_hour != run_key:
                    logger.info(f"⏰ [스케줄] {now.hour}시 데이터 수집 시작")
                    run_job_investor_trend()
                    logger.info("✅ [스케줄] 투자자별 매매동향 수집 완료 (코스피/코스닥)")
                    run_job_sector_index()
                    logger.info("✅ [스케줄] 업종 일자별지수 수집 완료 (코스피/코스닥/코스피200)")
                    last_close_data_hour = run_key

            # 평일 20시 — 원격 서버로 전체 데이터 자동 전송 ("동기화 관리" 페이지의 수동 전송과 동일 로직)
            # 14/19시 투자자별 매매동향·업종지수 수집까지 끝난 뒤에 보내기 위해 20시로 분리함.
            if now.weekday() < 5 and now.hour == 20 and last_sync_date != today_str:
                logger.info("⏰ [스케줄] 20시 원격 서버 동기화 시작")
                run_job_remote_sync()
                last_sync_date = today_str

            # 장 운영 시간 외 대기
            if now.hour < 8 or now.hour >= 20:
                time.sleep(240) # 60 * 4
                continue
            if now.weekday() >= 5:
                time.sleep(14400) # 3600 * 4
                continue

            # 일 1회 시가총액 데이터 수집 + 종목별 투자자 수집 + Signal Score 계산 (오후 3시 40분쯤, 장 마감 후)
            # 코스피/코스닥 중 하나라도 수집 실패하면 last_market_cap_date를 갱신하지 않아
            # 다음 루프(약 2분 후)에 전체를 재시도한다 (2026-07-09 코스피 누락 사고 재발 방지).
            if now.hour == 15 and now.minute >= 40 and last_market_cap_date != today_str:
                if run_job_market_cap_and_signal_score():
                    last_market_cap_date = today_str
                else:
                    logger.warning("⚠️ 시가총액 수집 일부 실패 — last_market_cap_date 갱신 보류, 다음 루프에서 재시도합니다.")

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
