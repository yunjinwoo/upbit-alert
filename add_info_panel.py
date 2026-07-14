import re, sys
sys.stdout.reconfigure(encoding='utf-8')

CSS = """
        .info-btn { position: fixed; right: 0; top: 50%; transform: translateY(-50%); z-index: 1050; background: #1a1a2e; color: #fff; border: none; border-radius: 8px 0 0 8px; padding: 12px 8px; writing-mode: vertical-rl; font-size: 0.8rem; cursor: pointer; letter-spacing: 2px; }
        .info-btn:hover { background: #4da6ff; }
        .offcanvas-info .offcanvas-header { background: #1a1a2e; color: #fff; }
        .offcanvas-info .offcanvas-header .btn-close { filter: invert(1); }
        .info-section { margin-bottom: 1.2rem; }
        .info-section h6 { font-weight: bold; color: #1a1a2e; border-bottom: 2px solid #4da6ff; padding-bottom: 4px; margin-bottom: 8px; }
        .info-tag { display: inline-block; background: #e8f0fe; color: #1a1a2e; border-radius: 4px; padding: 2px 8px; font-size: 0.8rem; margin: 2px; font-family: monospace; }
        .info-tip { background: #fff8e1; border-left: 3px solid #ffc107; padding: 6px 10px; border-radius: 0 4px 4px 0; font-size: 0.85rem; margin-bottom: 4px; }
        .info-warn { background: #fce4ec; border-left: 3px solid #e91e63; padding: 6px 10px; border-radius: 0 4px 4px 0; font-size: 0.85rem; margin-bottom: 4px; }
"""

PAGES = {
    'templates/market_cap.html': {
        'title': '📊 시가총액 추이 페이지 안내',
        'body': """
        <div class="info-section">
            <h6>📌 목적</h6>
            <p class="small">코스피·코스닥 시가총액 상위 종목의 일별 순위 변동을 추적합니다. 신규 진입 종목과 순위 등락을 한눈에 파악할 수 있습니다.</p>
        </div>
        <div class="info-section">
            <h6>🔌 사용 API</h6>
            <span class="info-tag">KIS FHPST01740000</span>
            <p class="small mt-1">국내주식 시가총액 순위 조회 (한국투자증권 Open API)</p>
        </div>
        <div class="info-section">
            <h6>⏱ 수집 주기</h6>
            <p class="small">자동: 매일 장마감 후 스케줄러 수집<br>수동: 즉시 수집 버튼</p>
        </div>
        <div class="info-section">
            <h6>📈 Bump 차트</h6>
            <p class="small">종목별 순위 변동을 꺾은선으로 표시합니다. Y축이 반전되어 위쪽이 높은 순위(1위)입니다.</p>
        </div>
        <div class="info-section">
            <h6>💡 활용 팁</h6>
            <div class="info-tip">순위 범위 조절로 관심 구간(예: 15~30위) 집중 모니터링</div>
            <div class="info-tip">NEW 배지는 직전일 대비 신규 진입 종목</div>
            <div class="info-tip">▲▼ 숫자는 전일 대비 순위 변동폭</div>
        </div>
        <div class="info-section">
            <h6>⚠️ 한계</h6>
            <div class="info-warn">KIS API 1회 최대 30종목 반환 (거래소·코스닥 각각) — 연속조회(tr_cont) 미지원 확인됨</div>
            <div class="info-warn">당일 장중 데이터는 잠정치, 장마감 후 확정</div>
        </div>
        <div class="info-section">
            <h6>🔗 연관 페이지</h6>
            <p class="small">→ 시총×투자자: 순위에 투자자 매매 데이터 결합</p>
        </div>"""
    },
    'templates/stock_investor.html': {
        'title': '💹 시총×투자자 페이지 안내',
        'body': """
        <div class="info-section">
            <h6>📌 목적</h6>
            <p class="small">시가총액 순위와 투자자별(외국인·기관·개인) 순매수를 결합해 수급 강도를 분석합니다. 시총 상위 종목 중 외국인·기관이 동시 매수하는 종목을 포착합니다.</p>
        </div>
        <div class="info-section">
            <h6>🔌 사용 API</h6>
            <span class="info-tag">KIS FHPST01740000</span> 시가총액 순위<br>
            <span class="info-tag">KIS FHPTJ04160001</span>
            <p class="small mt-1">종목별 투자자매매동향 일별 (외국인·기관·개인 순매수)</p>
        </div>
        <div class="info-section">
            <h6>⏱ 수집 주기</h6>
            <p class="small">시총: 자동 스케줄러<br>투자자: 수동 수집 버튼 (과거 날짜도 가능)</p>
        </div>
        <div class="info-section">
            <h6>🎨 히트맵 색상</h6>
            <p class="small">
                <span style="background:#ffe0e0;padding:2px 6px;border-radius:3px">빨강</span> 외국인 강매수<br>
                <span style="background:#e0e8ff;padding:2px 6px;border-radius:3px">파랑</span> 기관 강매수<br>
                <span style="background:#fff3cd;padding:2px 6px;border-radius:3px">노랑</span> 외국인 강매도
            </p>
        </div>
        <div class="info-section">
            <h6>💡 활용 팁</h6>
            <div class="info-tip">외국인+기관 동시 순매수 종목을 집중 주목</div>
            <div class="info-tip">📈 추이 버튼으로 종목별 누적 순매수 흐름 확인</div>
            <div class="info-tip">금액(억원) 단위로 전환하면 실제 자금 규모 파악 가능</div>
        </div>
        <div class="info-section">
            <h6>⚠️ 한계</h6>
            <div class="info-warn">투자자 데이터는 장마감 후 15:30 이후 확정</div>
            <div class="info-warn">KIS API 서버 연결 필요 (포트 9443)</div>
        </div>"""
    },
    'templates/investor_trend.html': {
        'title': '📉 투자자별 매매동향 페이지 안내',
        'body': """
        <div class="info-section">
            <h6>📌 목적</h6>
            <p class="small">코스피·코스닥 전체 시장에서 외국인·기관·개인의 프로그램 매매동향을 일별로 추적합니다.</p>
        </div>
        <div class="info-section">
            <h6>🔌 사용 API</h6>
            <span class="info-tag">KIS HHPPG046600C1</span>
            <p class="small mt-1">투자자별 프로그램 매매동향 (한국투자증권 Open API)</p>
        </div>
        <div class="info-section">
            <h6>⏱ 수집 주기</h6>
            <p class="small">자동: 스케줄러 자동 수집<br>수동: 즉시 수집 버튼</p>
        </div>
        <div class="info-section">
            <h6>💡 활용 팁</h6>
            <div class="info-tip">외국인 순매수 지속 → 중장기 상승 신호</div>
            <div class="info-tip">기관+외국인 동반 매도 → 시장 위험 신호</div>
        </div>
        <div class="info-section">
            <h6>⚠️ 한계</h6>
            <div class="info-warn">당일 장중 데이터는 잠정치</div>
        </div>"""
    },
    'templates/sector_index.html': {
        'title': '🏭 업종 일자별지수 페이지 안내',
        'body': """
        <div class="info-section">
            <h6>📌 목적</h6>
            <p class="small">코스피·코스닥·코스피200 업종별 지수를 일별로 조회하여 섹터 흐름을 파악합니다.</p>
        </div>
        <div class="info-section">
            <h6>🔌 사용 API</h6>
            <span class="info-tag">KIS FHPUP02120000</span>
            <p class="small mt-1">국내주식 업종 일자별 지수 (한국투자증권 Open API)</p>
        </div>
        <div class="info-section">
            <h6>⏱ 수집 방식</h6>
            <p class="small">실시간 조회 — DB 저장 없이 KIS API 직접 호출</p>
        </div>
        <div class="info-section">
            <h6>💡 활용 팁</h6>
            <div class="info-tip">날짜 기준으로 최근 30일 업종 지수 조회 가능</div>
            <div class="info-tip">거래량 비율로 시장 참여도 확인</div>
        </div>
        <div class="info-section">
            <h6>⚠️ 한계</h6>
            <div class="info-warn">DB에 저장하지 않아 과거 비교 불가</div>
        </div>"""
    },
    'templates/index.html': {
        'title': '📡 대시보드 페이지 안내',
        'body': """
        <div class="info-section">
            <h6>📌 목적</h6>
            <p class="small">업비트(Upbit) 가상화폐와 국내주식 거래량 급등 알림을 실시간으로 모니터링합니다.</p>
        </div>
        <div class="info-section">
            <h6>🔌 데이터 소스</h6>
            <span class="info-tag">Upbit API</span> 가상화폐 분봉·일봉 거래량<br>
            <span class="info-tag">KIS API</span> 국내주식 거래량 급등
            <p class="small mt-1">pyupbit 라이브러리로 실시간 조회</p>
        </div>
        <div class="info-section">
            <h6>⚙️ 알림 조건</h6>
            <p class="small">4시간봉 3배 / 1시간봉 4배 / 30분봉 6배 / 15분봉 8배<br>중 2개 이상 동시 충족 시 알림 발생</p>
        </div>
        <div class="info-section">
            <h6>💡 활용 팁</h6>
            <div class="info-tip">surge_count 높을수록 여러 봉에서 동시 급등 — 신뢰도 높음</div>
            <div class="info-tip">일봉 거래량이 전일 이상일 때만 알림 발생</div>
        </div>"""
    },
    'templates/raw_data.html': {
        'title': '🗂 원본 데이터 페이지 안내',
        'body': """
        <div class="info-section">
            <h6>📌 목적</h6>
            <p class="small">KIS API에서 수집한 원본 JSON 데이터를 직접 확인합니다. 개발·디버깅 용도입니다.</p>
        </div>
        <div class="info-section">
            <h6>🔌 데이터 소스</h6>
            <span class="info-tag">stock_raw_data</span> 테이블
            <p class="small mt-1">수집 시 저장된 원본 API 응답값 그대로 표시</p>
        </div>
        <div class="info-section">
            <h6>💡 활용 팁</h6>
            <div class="info-tip">API 응답 이상 시 여기서 raw 데이터 확인</div>
            <div class="info-tip">최근 50건만 표시됨</div>
        </div>"""
    },
}

for path, info in PAGES.items():
    with open(path, encoding='utf-8') as f:
        content = f.read()

    # CSS 추가
    if '.info-btn' not in content:
        content = content.replace('    </style>', CSS + '    </style>', 1)

    # offcanvas 패널 추가
    if 'infoPanel' not in content:
        panel = f"""
<!-- 페이지 안내 슬라이드 패널 -->
<button class="info-btn" data-bs-toggle="offcanvas" data-bs-target="#infoPanel" title="페이지 안내">ℹ 안내</button>
<div class="offcanvas offcanvas-end offcanvas-info" tabindex="-1" id="infoPanel" style="width:340px">
    <div class="offcanvas-header">
        <h5 class="offcanvas-title">{info['title']}</h5>
        <button type="button" class="btn-close" data-bs-dismiss="offcanvas"></button>
    </div>
    <div class="offcanvas-body">
{info['body']}
    </div>
</div>"""
        content = content.replace('<body>\n\n<nav', '<body>\n' + panel + '\n\n<nav', 1)
        if 'infoPanel' not in content:
            content = content.replace('<body>\n<nav', '<body>\n' + panel + '\n<nav', 1)

    with open(path, 'w', encoding='utf-8') as f:
        f.write(content)
    print(f'완료: {path}')
