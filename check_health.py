"""서버 상태 점검 — /api/health 를 불러 결과를 출력한다(읽기 전용, 서버 상태를 바꾸지 않음).

    python check_health.py            # 운영 서버(Config.SYNC_SERVER_URL)
    python check_health.py --local    # 로컬(http://localhost:5000)
    python check_health.py --json     # 원본 JSON 그대로

토큰은 .env 의 HEALTH_API_TOKEN 에서 읽어 헤더로만 보내고 화면에 찍지 않는다.
종료 코드: 0 = 정상(ok), 1 = 확인 필요(warn), 2 = 서버에 못 닿음/인증 실패.
"""
import argparse
import json
import sys

import requests

from app.config import Config

MARK = {'ok': '✅', 'stale': '⚠️', 'empty': '⚠️', 'missing': '❌', 'unreadable': '❌'}


def fetch_health(base_url: str) -> dict:
    headers = {'X-Health-Token': Config.HEALTH_API_TOKEN} if Config.HEALTH_API_TOKEN else {}
    res = requests.get(f"{base_url.rstrip('/')}/api/health", headers=headers, timeout=20, allow_redirects=False)
    if res.status_code in (301, 302, 401, 403):
        raise RuntimeError(f"인증 실패(HTTP {res.status_code}) — 서버와 이쪽 .env 의 HEALTH_API_TOKEN 이 같은지 확인하세요.")
    res.raise_for_status()
    return res.json()


def format_health(data: dict) -> str:
    lines = [f"[{data.get('checked_at')}] 전체 판정: {data.get('overall')}", '', '■ 수집 데이터']
    for f in data.get('freshness', []):
        detail = f"마지막 {f.get('latest') or '-'}"
        if f.get('expected'):
            detail += f" / 기대 {f['expected']}"
        if f.get('note'):
            detail += f" — {f['note']}"
        lines.append(f"{MARK.get(f.get('status'), '?')} {f.get('label')}: {detail}")

    lines += ['', '■ 스케줄(최근 24시간)', data.get('jobs', {}).get('text') or '-']

    log = data.get('log', {})
    lines += ['', '■ 로그(최근 24시간)']
    if log.get('status') == 'missing':
        lines.append(f"❌ {log.get('note')}")
    else:
        lines.append(f"ERROR {log.get('error_count')}건 · WARNING {log.get('warning_count')}건 · 마지막 기록 {log.get('last_line_at') or '-'}")
        if log.get('order_failure_count'):
            lines.append(f"❌ 실거래 주문 실패 {log['order_failure_count']}건 — 아래 오류 줄과 서버 pm2 로그를 확인하세요")
        lines += [f"  {line}" for line in log.get('recent_errors', [])]
    return '\n'.join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description='서버 상태 점검(읽기 전용)')
    parser.add_argument('--local', action='store_true', help='로컬 서버(http://localhost:5000)를 점검')
    parser.add_argument('--json', action='store_true', help='원본 JSON 출력')
    args = parser.parse_args()

    base_url = 'http://localhost:5000' if args.local else Config.SYNC_SERVER_URL
    try:
        data = fetch_health(base_url)
    except Exception as e:
        print(f"❌ 상태를 가져오지 못했습니다: {e}")
        return 2
    if data.get('status') != 'success':
        print(f"❌ 서버가 오류를 돌려줬습니다: {data.get('message')}")
        return 2

    print(json.dumps(data, ensure_ascii=False, indent=2) if args.json else format_health(data))
    return 0 if data.get('overall') == 'ok' else 1


if __name__ == '__main__':
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    sys.exit(main())
