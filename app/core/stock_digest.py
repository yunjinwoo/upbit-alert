"""저녁 7시 30분 국내주식 요약 — 이미 저장된 데이터만 모아 Slack으로 한 번 보내고(새 API 호출 없음),
같은 내용을 /stock-digest 페이지에서도 보여준다.

- Signal Score(signal_score_daily, 15:40 계산) 최신 날짜의 A/B등급 상위
- 토스 스크리닝 후보(stock_screening_daily: 일봉 돌파 / 200선 근접+구름 위 / 모멘텀 컨플루언스)
- 두 목록에 모두 걸린 종목은 "관심 1순위"로 맨 위에 따로 보여준다.

매수 권유가 아니라 앱이 계산한 지표를 정리한 것 — 메시지 끝에 그 문구를 붙인다.
"""
from app.utils.db_manager import get_signal_score_history, get_stock_screening, get_stock_screening_candidates
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


def collect_stock_digest(signal_rows: list, screening_rows: list) -> dict:
    """저장된 Signal Score/스크리닝 후보 → 요약 구조(Slack 메시지와 페이지가 같이 쓴다).
    반환: {score_date, both, graded, candidates} — both/candidates 항목엔 reasons(사유 문자열)가 붙는다."""
    graded = [r for r in signal_rows if r.get('grade') in ('A', 'B')][:SIGNAL_TOP_N]
    screening_by_code = {r.get('ticker'): r for r in screening_rows}
    both = [dict(r, reasons=_screening_reasons(screening_by_code[r['code']]))
            for r in graded if r.get('code') in screening_by_code]
    candidates = [dict(s, reasons=_screening_reasons(s)) for s in screening_rows[:SCREENING_TOP_N]]
    return {
        'score_date': signal_rows[0].get('date') if signal_rows else None,
        'both': both,
        'graded': graded,
        'candidates': candidates,
    }


def build_stock_digest_text(digest: dict) -> str:
    """요약 구조 → Slack 메시지 텍스트. 보여줄 종목이 없으면 빈 문자열."""
    graded, candidates, both = digest['graded'], digest['candidates'], digest['both']
    if not graded and not candidates:
        return ''

    lines = [f"🌙 [저녁 주식 요약] Signal Score 기준일 {digest['score_date'] or '-'}"]

    if both:
        lines.append("")
        lines.append("⭐ 관심 1순위 (Signal Score A/B + 토스 후보 동시)")
        for r in both:
            lines.append(f"• {r['name']}({r['code']}) {r['grade']}등급 {r['total_score']}점 · {r['reasons']}")

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
            lines.append(f"• {s.get('name')}({s.get('ticker')}){rate_text} · {s['reasons']}")

    lines.append("")
    lines.append("※ 앱이 계산한 지표 요약이며 매수 권유가 아닙니다.")
    return "\n".join(lines)


def load_stock_digest() -> dict:
    """DB에 저장된 최신 Signal Score/스크리닝 후보로 요약 구조를 만든다.
    페이지가 "왜 비었는지"를 보여줄 수 있게 원본 건수(stats)도 같이 담는다."""
    signal_rows = get_signal_score_history(limit=1000)
    screening_all = get_stock_screening()
    digest = collect_stock_digest(signal_rows, get_stock_screening_candidates())
    grade_counts = {}
    for r in signal_rows:
        grade_counts[r.get('grade') or '-'] = grade_counts.get(r.get('grade') or '-', 0) + 1
    digest['stats'] = {
        'signal_total': len(signal_rows),
        'grade_counts': grade_counts,
        'screening_total': len(screening_all),
        'screening_updated_at': max((r.get('updated_at') or '' for r in screening_all), default='') or None,
    }
    return digest


def send_stock_digest(send_fn) -> bool:
    """요약 메시지를 만들어 send_fn(text)로 보낸다. 보낼 내용이 없으면 False."""
    text = build_stock_digest_text(load_stock_digest())
    if not text:
        logger.info("[저녁 주식 요약] 보낼 데이터 없음 — 생략")
        return False
    send_fn(text)
    logger.info("[저녁 주식 요약] Slack 발송 완료")
    return True
