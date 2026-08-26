# 📓 매매일지 서비스 고도화 기능 제안서

이 문서는 기존 매매일지 서비스를 고도화하여 트레이더의 복기 경험을 극대화하고 뇌동매매를 방지할 수 있는 4가지 핵심 기능과 구체적인 구현 방안을 정리한 개발 제안서입니다.

---

## 1. 🕒 매매 순간의 시장 상황(Market Context) 자동 박제

### 💡 개요
매매 복기 시 가장 중요한 요소 중 하나는 '진입 당시 시장 전체의 분위기'입니다. 개별 종목 차트만 보고 복기하면 당시 하락장이었는지, 특정 테마가 지배적이던 날이었는지 파악하기 어렵습니다. 매수/매도 시점의 거시 지표 및 시장 분위기를 자동으로 기록하여 일지에 박제합니다.

### 🛠️ 구현 방안

#### A. 데이터베이스 설계 (`market_snapshots`)
10분 또는 30분 단위로 시장 지표 데이터를 수집하여 보관합니다.
```sql
CREATE TABLE market_snapshots (
    snapshot_time TIMESTAMP PRIMARY KEY,  -- 스냅샷 기록 시간
    kospi_price REAL,                      -- 코스피 지수
    kospi_change_rate REAL,               -- 코스피 당일 등락률 (%)
    btc_price REAL,                       -- 비트코인 가격
    usd_krw REAL,                         -- 원/달러 환율
    top_sector TEXT                       -- 당일 거래대금 상위 섹터/테마
);
```

#### B. 시스템 아키텍처 및 로직
1. **스케줄러 (Backend)**: Python의 `APScheduler` 등을 활용해 10~30분 주기마다 외부 API(한국투자증권 API, 야후 파이낸스, 업비트 등)를 호출하여 현재 지표를 저장합니다.
2. **매칭 로직**: 유저가 매매일지를 불러올 때, 매매 기록의 `created_at`(체결 시간)과 가장 가까운 시간대의 `snapshot_time`을 조회하여 데이터를 매핑합니다.
3. **화면 표시**:
   > 📌 **매수 당시 시장 상황**: 코스피 하락세 (-1.2%) | 비트코인 횡보 중 | 환율 상승 중 ⚠️ (시장 위험도 높음)

---

## 2. 🤖 AI 트레이딩 파트너의 '매매 피드백' (AI Auditor)

### 💡 개요
혼자 작성하는 매매일지의 단조로움을 깨고, LLM(AI)을 활용하여 객관적인 제3자의 관점에서 내 매매 습관에 대해 피드백 및 코칭을 제공받습니다.

### 🛠️ 구현 방안

#### A. 백엔드 피드백 생성 로직 (Python)
매매일지 저장이 완료되었을 때 AI API(Google Gemini 혹은 OpenAI)로 프롬프트를 전송합니다.

```python
import google.generativeai as genai

def generate_trade_feedback(trade_log: dict) -> str:
    prompt = f"""
    당신은 15년 차 프로 트레이더이자 냉정하고 객관적인 투자 코치입니다.
    아래의 매매 일지를 분석하고 3가지 관점(1. 진입 타점, 2. 리스크 관리, 3. 개선점)으로 피드백을 주세요.
    말투는 차분하고 전문적인 어조(반말 금지)로 작성해 주세요.

    [매매 정보]
    - 종목명: {trade_log['ticker']}
    - 매수 평단가: {trade_log['buy_price']}
    - 매도 평단가: {trade_log['sell_price']}
    - 수익률: {trade_log['profit_rate']}%
    - 매수 사유: {trade_log['reason']}
    - 손절 원칙 준수 여부: {trade_log['rule_followed']}
    """
    
    # AI 모델 호출 (예: Gemini 1.5 Pro/Flash)
    model = genai.GenerativeModel("gemini-1.5-flash")
    response = model.generate_content(prompt)
    return response.text
```

#### B. 프론트엔드 UI/UX
* 매매일지 뷰어 하단에 **"AI 코치의 피드백 💬"** 컴포넌트를 배치합니다.
* 사용자가 일지를 저장할 때 백그라운드에서 비동기로 요청을 보낸 후 결과를 DB에 저장해 둠으로써, 지연 시간 없이 조회가 가능하도록 구현합니다.

---

## 3. 🎯 실패한 타점을 재훈련하는 '오답노트 Replay 게임'

### 💡 개요
자신의 실패한 매매 패턴을 체득하고 극복하기 위한 게임형 복기 시스템입니다. 손실이 컸던 종목의 매매 시점 이전 차트로 되돌아가 가상으로 다시 대응해 봅니다.

### 🛠️ 구현 방안

#### A. 작동 프로세스
1. **오답 선정**: 유저가 손실률 -5% 이하인 종목 중 원하는 복기 대상을 선택합니다.
2. **차트 영역 차단**: 해당 종목의 매수 시점 `N일 전`부터 `매수 완료` 시점까지만 차트를 로드하고, 미래 캔들은 렌더링하지 않습니다. (예: `TradingView Lightweight Charts` 사용)
3. **가상 대응 시뮬레이션**:
   * 유저가 `다음 봉(Next)` 버튼을 클릭할 때마다 미래 캔들이 하나씩 생성됩니다.
   * 실제 내가 샀던 위치에 도달했을 때 **"당신이 실제로 진입한 타점입니다. 어떻게 대응하시겠습니까? [홀딩 / 손절 / 불타기]"** 팝업을 표시합니다.
4. **결과 피드백**: 실제 손실금액과 가상 훈련을 통해 대응했을 때의 가상 손실금액을 비교하여 개선도를 수치화합니다.

---

## 4. 🧮 뇌동방지용 '비중 조절 & 물타기 시뮬레이터'

### 💡 개요
현재 물려 있는 종목에 감정적으로 추가 매수를 하기 전에, 수학적 계산을 통해 물타기 이후의 새로운 평단가, 필요한 반등률, 전체 계좌 내 비중 변화를 미리 확인하게 도와줍니다.

### 🛠️ 구현 방안

#### A. 계산 로직 (JavaScript)
프론트엔드에서 즉시 연산하여 지연 없이 사용자 반응을 보여줍니다.
```javascript
/**
 * @param {number} currentPrice - 현재 보유 평단가
 * @param {number} currentQty - 현재 보유 수량
 * @param {number} targetBuyPrice - 물타기할 진입가 (현재가 등)
 * @param {number} addAmount - 추가 매수할 예수금 총액
 */
function calculateWatering(currentPrice, currentQty, targetBuyPrice, addAmount) {
    const currentTotalValue = currentPrice * currentQty; // 현재 매수 총액
    const addQty = Math.floor(addAmount / targetBuyPrice); // 추가 매수 수량
    const newTotalQty = currentQty + addQty; // 물탄 후 총 수량
    const newTotalCost = currentTotalValue + (addQty * targetBuyPrice); // 총 투자 비용
    const newAveragePrice = newTotalCost / newTotalQty; // 물탄 후 평단가
    
    // 평단까지 오기 위해 현재가 대비 필요한 반등률
    const requiredRecoveryRate = ((newAveragePrice - targetBuyPrice) / targetBuyPrice) * 100;

    return {
        newAveragePrice: newAveragePrice.toFixed(0),
        requiredRecoveryRate: requiredRecoveryRate.toFixed(2),
        addQty: addQty
    };
}
```

#### B. UI/UX 구현
* 매매일지 작성 및 편집 화면 우측 사이드바에 위치시킵니다.
* 입력 폼을 슬라이더(예수금의 10%, 25%, 50% 등)와 매핑하여 유저가 터치나 클릭 몇 번만으로 결과를 체감할 수 있게 만듭니다.

---

## 🚀 향후 로드맵 추천

1. **Phase 1 (기초 유틸리티)**: `4. 물타기 시뮬레이터` 구현 (UI 위젯 제작으로 가장 빠르고 실용적)
2. **Phase 2 (데이터 축적)**: `1. 시장 상황 자동 박제` (배경 스케줄러 개발 및 DB 적재 시작)
3. **Phase 3 (AI 연동)**: `2. AI 매매 피드백` (유저가 일지를 적는 동기부여 요소 추가)
4. **Phase 4 (심화 콘텐츠)**: `3. 오답노트 Replay 게임` (차트 라이브러리 연동 및 인터랙티브 인터페이스 개발)
