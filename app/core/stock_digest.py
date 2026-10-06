"""저녁 7시 30분 국내주식 요약 — 이미 저장된 데이터만 모아 Slack으로 한 번 보내고(새 API 호출 없음),
같은 내용을 /stock-digest 페이지에서도 보여준다.

- Signal Score(signal_score_daily, 15:40 계산) 최신 날짜의 A/B등급 상위
- 토스 스크리닝 후보(stock_screening_daily: 일봉 돌파 / 200선 근접+구름 위 / 모멘텀 컨플루언스)
- 두 목록에 모두 걸린 종목은 "관심 1순위"로 맨 위에 따로 보여준다.
- 최근 24시간 스케줄 실행 결과(job_run_log) — 다 정상이면 한 줄, 실패한 작업이 있으면 그것만 펼쳐 보여준다.
  동기화 관리 페이지 "처리 로그"를 직접 들어가 보지 않아도 되게 하려는 것.

매수 권유가 아니라 앱이 계산한 지표를 정리한 것 — 메시지 끝에 그 문구를 붙인다.
"""
from datetime import datetime, timedelta

from app.utils.db_manager import (
    get_job_run_log_since, get_signal_score_history, get_stock_screening, get_stock_screening_candidates,
)
from app.utils.logger import get_logger

logger = get_logger()

SIGNAL_TOP_N = 10
SCREENING_TOP_N = 10
JOB_WINDOW_HOURS = 24      # 어제 저녁 요약 이후 ~ 지금 (어제 20시 원격 동기화까지 들어온다)
JOB_ERROR_MAX_LEN = 80


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


def summarize_job_runs(rows: list) -> dict:
    """job_run_log 행(오래된 순) → 작업별 결과 요약. 수동 실행(manual*)은 빼고 자동 스케줄만 본다.
    작업 = (job_name, description) — 상승률 순위처럼 시각별로 description이 다른 작업은 시각마다 따로 센다.
    실패 후 다음 루프 재시도로 결국 성공했으면 recovered, 마지막 실행이 실패면 failing.
    반환: {total_runs, job_count, failing:[...], recovered:[...], jobs:[...]}"""
    jobs = {}
    for r in rows:
        trigger = r.get('trigger_type') or 'auto'
        if not trigger.startswith('auto'):
            continue
        key = (r.get('job_name'), r.get('description'))
        j = jobs.setdefault(key, {'job_name': key[0], 'description': key[1] or key[0], 'runs': 0, 'fails': 0,
                                  'last_success': None, 'last_time': None, 'last_error': None})
        j['runs'] += 1
        ok = bool(r.get('success'))
        if not ok:
            j['fails'] += 1
            j['last_error'] = r.get('error_message')
        j['last_success'] = ok
        j['last_time'] = r.get('start_time')

    job_list = list(jobs.values())
    return {
        'total_runs': sum(j['runs'] for j in job_list),
        'job_count': len(job_list),
        'failing': [j for j in job_list if not j['last_success']],
        'recovered': [j for j in job_list if j['last_success'] and j['fails']],
        'jobs': job_list,
    }


def _short_error(text) -> str:
    text = ' '.join(str(text or '오류 내용 없음').split())
    return text if len(text) <= JOB_ERROR_MAX_LEN else text[:JOB_ERROR_MAX_LEN] + '…'


def build_job_summary_text(summary: dict) -> str:
    """스케줄 요약 → Slack 텍스트. 다 정상이면 한 줄."""
    if not summary['total_runs']:
        return f"⚠️ 최근 {JOB_WINDOW_HOURS}시간 자동 스케줄 실행 기록이 없습니다 — 봇 프로세스가 멈췄는지 확인해 주세요."
    head = f"최근 {JOB_WINDOW_HOURS}시간 스케줄 {summary['job_count']}개 · 실행 {summary['total_runs']}회"
    if not summary['failing'] and not summary['recovered']:
        return f"✅ {head} 모두 정상"

    lines = [f"🗓️ {head}"]
    for j in summary['failing']:
        lines.append(f"⚠️ {j['description']} — 실패 {j['fails']}회, 마지막 실행({j['last_time']})도 실패: "
                     f"{_short_error(j['last_error'])}")
    if summary['recovered']:
        lines.append("↻ 실패 후 재시도로 정상: " + ', '.join(f"{j['description']} {j['fails']}회" for j in summary['recovered']))
    return "\n".join(lines)


def load_job_summary(now: datetime = None) -> dict:
    since = ((now or datetime.now()) - timedelta(hours=JOB_WINDOW_HOURS)).strftime('%Y-%m-%d %H:%M:%S')
    return summarize_job_runs(get_job_run_log_since(since))


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
    digest['jobs'] = load_job_summary()
    return digest


def send_stock_digest(send_fn) -> bool:
    """요약 메시지를 만들어 send_fn(text)로 보낸다. 종목이 없어도 스케줄 결과는 항상 보낸다."""
    digest = load_stock_digest()
    stock_text = build_stock_digest_text(digest)
    job_text = build_job_summary_text(digest['jobs'])
    if stock_text:
        text = f"{stock_text}\n\n{job_text}"
    else:
        text = f"🌙 [저녁 요약] 오늘은 보여줄 종목이 없습니다.\n\n{job_text}"
    send_fn(text)
    logger.info("[저녁 주식 요약] Slack 발송 완료")
    return True
