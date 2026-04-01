import pyupbit
import time
import requests
from save_alert import init_sheet, save_to_sheet, get_daily_volume_info
from datetime import datetime, timedelta
from dotenv import load_dotenv
import os

load_dotenv()
slack_token = os.getenv("SLACK_TOKEN")

# 스크립트를 실행하려면 여백의 녹색 버튼을 누릅니다.
if __name__ != '__main__':
    print(f" not main ")
    exit

# 1. 슬랙 설정
SLACK_WEBHOOK_URL = os.getenv("SLACK_TOKEN")

def send_slack_msg(text):
    try:
        payload = {"text": text}
        requests.post(SLACK_WEBHOOK_URL, json=payload, timeout=5)
    except Exception as e:
        print(f"슬랙 전송 실패: {e}")

# 2. 거래량 체크 함수
def check_volume_surge(ticker, interval):
    try:
        df = pyupbit.get_ohlcv(ticker, interval=interval, count=2)

        if df is None or len(df) < 2:
            return False, 0

        # 거래량 데이터를 리스트로 변환
        vol_list = df['volume'].tolist()
        # [수정 완료!] 인덱스 번호를 정확히 붙였습니다.
        # vol_list는 [이전봉거래량, 현재봉거래량] 형태입니다.
        prev_vol = float(vol_list[0])  # <--이 반드시 있어야 함!
        curr_vol = float(vol_list[1])  # <--이 반드시 있어야 함!

        if prev_vol == 0:
            return False, 0

        ratio = curr_vol / prev_vol

        print(f"{interval} :: ratio = {ratio} === vol_list:{vol_list}" )
        # 3배 이상 급증 시 True
        if ratio >= 3:
            return True, ratio
        return False, ratio

    except Exception as e:
        print(f"[{interval}] 상세 에러: {e}")
        return False, 0

# 3. 메인 루프
# 1. 감시하고 싶은 코인들을 리스트로 만듭니다.
# target_tickers = ["KRW-BTC", "KRW-ETH", "KRW-XRP", "KRW-DOGE"]
#
# 업비트의 모든 KRW 코인을 자동으로 싹 긁어옵니다.
# target_tickers = pyupbit.get_tickers(fiat="KRW")

# https://upbit.com/exchange?code=CRIX.UPBIT.KRW-ONT

# 거래량 비율만 순수하게 계산해서 돌려주는 함수
def get_volume_ratio(ticker, interval):
    try:
        df = pyupbit.get_ohlcv(ticker, interval=interval, count=2)
        if df is None or len(df) < 2:
            return 0

        vol_list = df['volume'].tolist()

        # 사용자님이 알려주신 완벽한 인덱스 접근!
        prev_vol = float(vol_list[0])  # <--이 반드시 있어야 함!
        curr_vol = float(vol_list[1])  # <--이 반드시 있어야 함!

        if prev_vol == 0:
            return 0

        print(f" {interval} = {curr_vol / prev_vol}")
        return curr_vol / prev_vol
    except Exception as e:
        print(f"[{ticker}-{interval}] 에러: {e}")
        return 0


# 감시 설정
# target_tickers = ["KRW-BTC", "KRW-ETH", "KRW-XRP"]  # 감시할 코인들
# 업비트의 모든 KRW 코인을 자동으로 싹 긁어옵니다.
target_tickers = pyupbit.get_tickers(fiat="KRW")

# 각 분봉별로 원하는 기준 배수를 다르게 세팅합니다.
thresholds = {
    "minutes5": 10.0,    # 5분봉은 최소 10배는 터져야 알림!
    "minutes15": 8.0,    # 5분봉은 최소 10배는 터져야 알림!
    "minutes30": 6.0,    # 5분봉은 최소 10배는 터져야 알림!
    "minutes60": 4.0,    # 1시간봉은 5배 이상일 때 알림!
    "minutes240": 3.0    # 4시간봉은 3배 이상일 때 알림!
}

intervals = {
    "minutes240": "4시간봉",
    "minutes60": "1시간봉",
    "minutes15": "15분봉",
    "minutes30": "30분봉"
}

# 알림 중복 방지 (코인별로 마지막 알림 보낸 시간 기록)
# last_notified_time = {ticker: None for ticker in target_tickers}
last_notified_time = {ticker: {intv: None for intv in intervals} for ticker in target_tickers}


# 스킵된 코인 캐시 {ticker: 다시_체크할_시간}
skip_cache = {}
#SKIP_DURATION = timedelta(minutes=10)  # 10분
#SKIP_DURATION = timedelta(hours=1)     # 1시간
SKIP_DURATION = timedelta(hours=4)     # 4시간봉 기준으로 맞추고 싶으면
SKIP_DURATION_ALERT = timedelta(hours=1)     # 4시간봉 기준으로 맞추고 싶으면

print(f"🚀 전 타임프레임 동시 폭발 감시 시작! (노이즈 최소화 버전)")
print("-" * 40)

init_sheet()  # 헤더 자동 생성
while True:
    active_count = len(target_tickers) - len(skip_cache)
    print(f"\n{'=' * 40}")
    print(f"감시 대상: {active_count}/{len(target_tickers)}개 (스킵: {len(skip_cache)}개)")
    print(f"{'=' * 40}")

    for i, ticker in enumerate(target_tickers, 1):  # 1부터 시작
        ratios = {}

        if ticker in skip_cache and datetime.now() < skip_cache[ticker]:
            continue  # 출력도 없이 조용히 스킵

        print("-" * 10)
        print(f"{time.strftime('%H:%M:%S')} :: [{i}/{len(target_tickers)}] ticker = {ticker}")

        # ① 일봉 먼저 체크 → 전일 이하면 바로 스킵
        daily = get_daily_volume_info(ticker)
        if daily is None:
            print(f"[{ticker}] 일봉 전일 이하 → 스킵")
            time.sleep(0.5)  # API 과부하 방지
            continue  # 분봉 조회 자체를 안 함

        # ③ 스킵 캐시에서 제거 (일봉 통과했으니 정상 감시 대상)
        skip_cache.pop(ticker, None)

        # ④ 분봉 조회
        for interval in intervals.keys():
            ratios[interval] = get_volume_ratio(ticker, interval)
            time.sleep(0.1)  # API 과부하 방지

        surge_count = 0
        active_intervals = []

        for interval, name in intervals.items():
            threshold = thresholds.get(interval, 3.0)  # 없으면 기본값 3.0
            if ratios[interval] >= threshold:
                surge_count += 1
                active_intervals.append(f"{name}(*{ratios[interval]:.1f}배* / 기준:{threshold}배)")

        daily_str = (
            f"▲{daily['increase_rate']}%  "
            f"거래량: {daily['today_vol']:,}  "
            f"거래대금: {daily['trade_value']:,}원"
        )
        if surge_count >= 2:
            save_to_sheet(ticker, active_intervals, surge_count, daily_str)
            print(f" active_intervals = {active_intervals}")
            print(f" https://upbit.com/exchange?code=CRIX.UPBIT.{ticker}")

        if surge_count >= 2 and ratios["minutes240"] >= 1 :
            df_now = pyupbit.get_ohlcv(ticker, interval="minutes5", count=1)
            print(f" df_now :: {df_now} ")

            if df_now is not None and not df_now.empty:
                # [★여기 수정 완료!★] .index 뒤에을 붙여 진짜 날짜 값만 쏙 빼옵니다.
                current_candle_time = df_now.index[0]
                print(f" df_now.index :: {df_now.index[0]} ")

                print(f" {current_candle_time}")
                if last_notified_time[ticker] != current_candle_time:

                    surge_text = ", ".join(active_intervals)
                    message = (
                        f"🚨 *[{ticker}] 거래량 조건 만족 ({surge_count}/4)!* 🚨\n"
                        f"폭발한 봉: {surge_text} \n"
                        f" https://upbit.com/exchange?code=CRIX.UPBIT.{ticker}"
                    )

                    save_to_sheet(ticker, active_intervals, surge_count, daily_str)
                    send_slack_msg(message)
                    skip_cache[ticker] = datetime.now() + SKIP_DURATION_ALERT  # ← 1시간 스킵

                    print(f"[{time.strftime('%H:%M:%S')}] {ticker} 조건 만족 알림 발송!")
                    last_notified_time[ticker] = current_candle_time
        time.sleep(1)  # API 과부하 방지
    time.sleep(10)  # 10초마다 체크




# https://www.jetbrains.com/help/pycharm/에서 PyCharm 도움말 참조
