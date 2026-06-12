"""
국내업종 일자별지수 수집 스크립트 (v1_국내주식-065)
한국투자증권 Open API → CSV 저장
"""
import os
import csv
import json
import time
import requests
from datetime import date
from dotenv import load_dotenv

load_dotenv()

APP_KEY    = os.environ["KIS_APP_KEY"]
APP_SECRET = os.environ["KIS_APP_SECRET"]
BASE_URL   = "https://openapi.koreainvestment.com:9443"

SECTORS = {
    "0001": "코스피",
    "1001": "코스닥",
    "2001": "코스피200",
}

OUTPUT_FILE = "sector_index_2026_06.csv"

FIELDNAMES = [
    "업종코드", "업종명", "날짜",
    "종가", "시가", "고가", "저가",
    "전일대비", "전일대비부호", "등락률",
    "거래량", "거래대금", "거래량비율",
    "투자자순매수도", "20일이격도",
]


def get_access_token() -> str:
    resp = requests.post(
        f"{BASE_URL}/oauth2/tokenP",
        headers={"content-type": "application/json"},
        json={
            "grant_type": "client_credentials",
            "appkey": APP_KEY,
            "appsecret": APP_SECRET,
        },
    )
    resp.raise_for_status()
    token = resp.json()["access_token"]
    print("✅ 액세스 토큰 발급 완료")
    return token


def fetch_index_daily(token: str, iscd: str, base_date: str) -> list[dict]:
    """base_date 기준 최대 100건 일별 데이터 반환"""
    resp = requests.get(
        f"{BASE_URL}/uapi/domestic-stock/v1/quotations/inquire-index-daily-price",
        headers={
            "content-type": "application/json",
            "authorization": f"Bearer {token}",
            "appkey": APP_KEY,
            "appsecret": APP_SECRET,
            "tr_id": "FHPUP02120000",
            "custtype": "P",
        },
        params={
            "FID_COND_MRKT_DIV_CODE": "U",
            "FID_INPUT_ISCD": iscd,
            "FID_INPUT_DATE_1": base_date,
            "FID_PERIOD_DIV_CODE": "D",
        },
    )
    resp.raise_for_status()
    data = resp.json()
    if data.get("rt_cd") != "0":
        print(f"  ⚠️  API 오류: {data.get('msg1')}")
        return []
    return data.get("output2", [])


def sign_label(sign: str) -> str:
    return {"1": "상한", "2": "상승", "3": "보합", "4": "하한", "5": "하락"}.get(sign, sign)


def main():
    token = get_access_token()

    # 6월 마지막 영업일 기준으로 조회 (오늘 날짜 사용, API가 이후 데이터 포함해 반환)
    today = date.today().strftime("%Y%m%d")

    rows = []
    for iscd, name in SECTORS.items():
        print(f"📡 [{name}] 수집 중...")
        records = fetch_index_daily(token, iscd, today)

        for r in records:
            d = r["stck_bsop_date"]  # YYYYMMDD
            if not (d.startswith("202606")):
                continue  # 6월 데이터만 필터
            rows.append({
                "업종코드": iscd,
                "업종명": name,
                "날짜": f"{d[:4]}-{d[4:6]}-{d[6:]}",
                "종가": r["bstp_nmix_prpr"],
                "시가": r["bstp_nmix_oprc"],
                "고가": r["bstp_nmix_hgpr"],
                "저가": r["bstp_nmix_lwpr"],
                "전일대비": r["bstp_nmix_prdy_vrss"],
                "전일대비부호": sign_label(r["prdy_vrss_sign"]),
                "등락률": r["bstp_nmix_prdy_ctrt"],
                "거래량": r["acml_vol"],
                "거래대금": r["acml_tr_pbmn"],
                "거래량비율": r["acml_vol_rlim"],
                "투자자순매수도": r["invt_new_psdg"],
                "20일이격도": r["d20_dsrt"],
            })

        print(f"   → {sum(1 for rr in rows if rr['업종코드'] == iscd)}건 저장")
        time.sleep(0.5)  # API 호출 간격

    if not rows:
        print("❌ 수집된 6월 데이터가 없습니다. 날짜 범위를 확인하세요.")
        return

    rows.sort(key=lambda x: (x["날짜"], x["업종코드"]))

    with open(OUTPUT_FILE, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)

    print(f"\n✅ 완료! {len(rows)}건 → {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
