"""밤 11시 국내주식 요약 알림 — 그날 이미 저장된 데이터만 모아 Slack으로 한 번 보낸다(새 API 호출 없음).

- Signal Score(signal_score_daily, 15:40 계산) 최신 날짜의 A/B등급 상위
- 토스 스크리닝 후보(stock_screening_daily: 일봉 돌파 / 200선 근접+구름 위 / 모멘텀 컨플루언스)
- 두 목록에 모두 걸린 종목은 "관심 1순위"로 맨 위에 따로 보여준다.

매수 권유가 아니라 앱이 계산한 지표를 정리한 것 — 메시지 끝에 그 문구를 붙인다.
"""
from app.utils.db_manager import get_signal_score_history, get_stock_screening_candidates
from app.utils.logger import get_logger

logger = get_logger()

SIGNAL_TOP_N = 10
SCREENING_TOP_N = 10


def _screening_reasons(row: dict) -> str:
    reasons = []
    if row.get('breakout_1d'):
        reasons.append('일봉 돌파')
    if row.get('near_ma200') and row.get('above_cloud'):
        reasons.append('200선 근접+구름 위')
    if row.get('momentum_confluence'):
        reasons.append('모멘텀')
    return '/'.join(reasons) or '-'


def build_nightly_stock_digest(signal_rows: list, screening_rows: list) -> str:
    """저장된 Signal Score/스크리닝 후보 → Slack 메시지 텍스트. 둘 다 비면 빈 문자열."""
    graded = [r for r in signal_rows if r.get('grade') in ('A', 'B')][:SIGNAL_TOP_N]
    candidates = screening_rows[:SCREENING_TOP_N]
    if not graded and not candidates:
        return ''

    score_date = signal_rows[0].get('date') if signal_rows else None
    screening_by_code = {r.get('ticker'): r for r in screening_rows}

    lines = [f"🌙 [밤 11시 주식 요약] Signal Score 기준일 {score_date or '-'}"]

    both = [r for r in graded if r.get('code') in screening_by_code]
    if both:
        lines.append("")
        lines.append("⭐ 관심 1순위 (Signal Score A/B + 토스 후보 동시)")
        for r in both:
            s = screening_by_code[r['code']]
            lines.append(f"• {r['name']}({r['code']}) {r['grade']}등급 {r['total_score']}점 · {_screening_reasons(s)}")

    if graded:
        lines.append("")
        lines.append("🅰️ Signal Score A/B 상위")
        for r in graded:
            lines.append(
                f"• {r['name']}({r['code']}) {r['grade']} {r['total_score']}점 "
                f"(모멘텀 {r.get('momentum_score')} · 수급 {r.get('supply_demand_score')} · 리스크 {r.get('risk_penalty_score')})"
            )

    if candidates:
        lines.append("")
        lines.append("📈 토스 스크리닝 후보 (거래대금순)")
        for s in candidates:
            rate = s.get('change_rate')
            rate_text = f" {rate:+.2f}%" if isinstance(rate, (int, float)) else ''
            lines.append(f"• {s.get('name')}({s.get('ticker')}){rate_text} · {_screening_reasons(s)}")

    lines.append("")
    lines.append("※ 앱이 계산한 지표 요약이며 매수 권유가 아닙니다.")
    return "\n".join(lines)


def send_nightly_stock_digest(send_fn) -> bool:
    """DB에서 읽어 메시지를 만들고 send_fn(text)로 보낸다. 보낼 내용이 없으면 False."""
    signal_rows = get_signal_score_history(limit=200)
    screening_rows = get_stock_screening_candidates()
    text = build_nightly_stock_digest(signal_rows, screening_rows)
    if not text:
        logger.info("[밤 11시 주식 요약] 보낼 데이터 없음 — 생략")
        return False
    send_fn(text)
    logger.info("[밤 11시 주식 요약] Slack 발송 완료")
    return True
