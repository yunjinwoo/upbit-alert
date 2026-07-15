# 업종 일자별지수 — `net_buy` 필드 오라벨링 발견 및 수정

- 상태: 완료
- 날짜: 2026-07-15
- 관련 파일: `app/utils/db_manager.py`, `app/api/server.py`, `templates/sector_index.html`

## 질문

"국내업종 일자별지수 페이지 그래프에 20일 이격도가 있는데, 다른 기간(5일/10일/60일 등) 이격도도 API에서 내려오는가?"

## 조사

1. KIS `국내업종 일자별지수`(`FHPUP02120000`, `/uapi/domestic-stock/v1/quotations/inquire-index-daily-price`) API를 실제로 직접 호출해서 `output2` 원본 응답 필드를 전부 확인.

```
stck_bsop_date, bstp_nmix_prpr, prdy_vrss_sign, bstp_nmix_prdy_vrss,
bstp_nmix_prdy_ctrt, bstp_nmix_oprc, bstp_nmix_hgpr, bstp_nmix_lwpr,
acml_vol_rlim, acml_vol, acml_tr_pbmn, invt_new_psdg, d20_dsrt
```

총 13개 필드 중 이격도 관련은 `d20_dsrt` 하나뿐 — 5일/10일/60일 등 다른 기간 이격도는 이 API 자체에 없음(KIS가 아예 제공하지 않음).

2. `invt_new_psdg` 필드가 뭔지 확인하기 위해 KIS 공식 GitHub(`koreainvestment/open-trading-api`)의 예제 소스(`chk_inquire_index_daily_price.py`)에 있는 컬럼 매핑 딕셔너리를 조회:

```python
COLUMN_MAPPING = {
    ...
    'invt_new_psdg': '투자 신 심리도',
    'd20_dsrt': '20일 이격도',
}
```

## 발견

기존 코드(`sector_index_daily` 테이블, `/api/sector-index` 응답)에서 `invt_new_psdg` 필드를 **`net_buy`(순매수)** 컬럼에 매핑해서 저장/표시하고 있었음. 그런데 이 필드는 KIS 공식 문서 기준 **"투자 신 심리도"**(심리도/Psychological Line 계열 지표)로, 순매수 금액과는 전혀 다른 지표.

- 실제 저장된 값도 `-49.54`, `5.31`, `64.17` 같은 소규모 범위 — 원 단위 순매수 금액이라기엔 스케일이 맞지 않고, 심리도 지표(대략 -100~+150 범위) 스케일과 일치.
- 화면에는 "투자자순매수도"라는 라벨로 노출되고 있어 실제 의미와 다른 정보로 오인될 수 있는 상태였음.

## 조치

`net_buy` → `psychology_index`로 필드/컬럼명 정정.

- `app/utils/db_manager.py`
  - `sector_index_daily` 테이블 스키마: `net_buy` → `psychology_index`
  - 마이그레이션 추가: `ALTER TABLE sector_index_daily RENAME COLUMN net_buy TO psychology_index` (기존 데이터 보존)
  - `save_sector_index_daily()`, `sync_upsert_sector_index()` 컬럼명 갱신
  - `sync_upsert_sector_index()`는 원격 서버 간 배포 시차를 대비해 `r.get('psychology_index', r.get('net_buy', '0'))`로 구 필드명도 폴백 인식
- `app/api/server.py`: 실시간 `/api/sector-index` 응답의 키를 `net_buy` → `psychology_index`로 변경
- `templates/sector_index.html`: 테이블 헤더 "투자자순매수도" → "투자심리도", JS의 `d.net_buy` 참조를 `d.psychology_index`로 변경

## 검증

- 라이브 DB(`alerts.db`)에 마이그레이션을 실제로 적용 — 마이그레이션 전/후 데이터 값이 정확히 보존됨을 확인(`-36.48`, `-35.69`, `-29.18` 등 기존 값 그대로).
- `/sector-index` 페이지에서 DB 조회 + 실시간 API 조회(`API 조회` 버튼) 둘 다 실제로 실행 — 테이블 헤더 "투자심리도"와 값이 정상 렌더링되는 것 확인.
- 실시간 API 응답(JSON)에도 `psychology_index` 키로 정상 반환되는 것 확인 (예: 2026-07-15 코스피 `psychology_index: -13.13`).

## 참고

- 다른 기간(5/10/60/120일) 이격도를 보고 싶다면 KIS API가 제공하지 않으므로, 종가 데이터로 직접 계산해서 추가해야 함(예: N일 이격도 = 당일종가 / N일 이동평균 × 100). 이번 작업 범위에는 포함하지 않음.
