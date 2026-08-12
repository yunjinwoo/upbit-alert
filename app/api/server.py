from flask import Flask, jsonify, render_template, request, session, redirect, url_for
from flask_cors import CORS
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.security import generate_password_hash, check_password_hash
from app.utils.db_manager import (
    init_db,
    get_latest_alerts, delete_alert,
    get_latest_stock_alerts, delete_stock_alert,
    get_latest_stock_raw_data, get_market_cap_history,
    get_db_stats, delete_oldest_stock_raw_data,
    get_investor_trend_history,
    get_stock_investor_combined, get_latest_market_cap_date,
    get_stock_investor_trend,
    sync_upsert_market_cap, sync_upsert_investor_daily,
    sync_upsert_investor_trend, sync_upsert_sector_index, sync_upsert_sector_stocks,
    sync_upsert_hts_top_view, sync_upsert_top_interest,
    get_sector_index_cached, get_sector_stocks_cached, get_investor_trend_cached,
    get_stock_investor_raw,
    get_investor_ranking,
    get_investor_distribution,
    get_investor_cross_distribution,
    get_volume_ratio_batch,
    get_volume_collection_status,
    get_hts_top_view_history,
    get_hts_top_view_cumulative,
    get_hts_top_view_daily_scores,
    get_hts_top_view_export,
    get_top_interest_range,
    get_top_interest_export,
    get_signal_score_batch,
    get_signal_score_history,
    get_signal_score_range,
    get_job_run_log,
    get_stock_memos, add_stock_memo, delete_stock_memo_entry, search_stock_memos, get_all_stock_memos,
    get_stock_memo_grades, update_stock_memo_grade, search_stock_codes, bump_stock_memo,
    get_top_gainers_history, get_top_gainers_snapshot_dates, get_top_gainers_range,
    get_top_gainers_export, sync_upsert_top_gainers,
    get_quick_links, add_quick_link, delete_quick_link,
    get_coin_screening,
    set_trade_engine_enabled,
    set_candidate_approval,
    set_trade_strategy_settings,
    set_position_dca_enabled,
    set_candidate_condition_watch,
    set_trade_condition_setting,
    get_trade_order_log,
    count_trade_order_log,
    save_powerball_rounds, get_powerball_rounds, delete_powerball_round,
    add_powerball_favorite, get_powerball_favorites, delete_powerball_favorite,
    save_lotto645_rounds, get_lotto645_rounds, delete_lotto645_round,
    add_lotto645_favorite, get_lotto645_favorites, delete_lotto645_favorite,
    save_pension720_rounds, get_pension720_rounds, delete_pension720_round,
    add_pension720_favorite, get_pension720_favorites, delete_pension720_favorite,
    save_lottery_recommendations, get_lottery_recommendations,
    delete_lottery_recommendation, delete_lottery_recommendations_bulk,
    get_login_settings, save_login_settings,
    get_or_create_secret_key,
)
from app.core.powerball import parse_powerball_block
from app.core.lotto645 import parse_lotto645_block, parse_lotto645_excel
from app.core.pension720 import parse_pension720_block, parse_pension720_excel
from app.core.stock_monitor import (
    fetch_market_cap_ranking, fetch_investor_trend, fetch_sector_index_daily, fetch_stock_investor_daily,
    fetch_ranking_preview, fetch_sector_stocks, fetch_multi_stock_price,
    fetch_fluctuation_ranking, fetch_fluctuation_ranking_combined,
    run_job_hts_top_view, run_job_investor_trend, run_job_sector_index,
    run_job_market_cap_and_signal_score, run_job_market_cap_morning_backup, run_job_remote_sync, run_job_top_interest,
    run_job_top_gainers, run_job_top_gainers_sync,
    SECTOR_NAMES,
)
from app.core.upbit_market_analysis import run_coin_screening
from app.core.auto_trader import get_dashboard_summary, run_trade_cycle, force_buy
from app.config import Config
import json
import os
import platform
import threading
import secrets
import subprocess
import time as _time
from datetime import datetime as _dt, timedelta as _timedelta
from app.utils.slack import send_slack_msg

app = Flask(__name__, template_folder='../../templates')
app.config['APPLICATION_ROOT'] = Config.APP_ROOT
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)
CORS(app)

# API 프로세스는 다른 프로세스(run_stock_monitor 등)의 init_db() 호출 시점에 의존하지 않도록
# 여기서도 명시적으로 한 번 호출(idempotent) — 특히 아래 login_settings 조회가 모듈 임포트 시점에
# 바로 실행되므로, 테이블이 아직 없는 상태로 레이스 컨디션에 걸리는 걸 방지
init_db()

if Config.SECRET_KEY:
    app.secret_key = Config.SECRET_KEY
else:
    # .env에 SECRET_KEY가 없으면 DB에 저장된 값을 쓴다(없으면 최초 1회 자동 생성) — 재시작(배포 포함)해도
    # 계속 같은 키를 재사용하므로 로그인 세션이 풀리지 않는다. 완전히 새 키로 강제 교체하고 싶다면
    # (=모든 세션 강제 로그아웃) DB의 app_secret 테이블 행을 지우거나 .env에 SECRET_KEY를 직접 지정할 것.
    app.secret_key = get_or_create_secret_key()
    app.logger.info("[로그인] SECRET_KEY가 .env에 없어 DB에 저장된 키를 사용합니다(최초 실행 시 자동 생성·이후 재사용).")
app.config['PERMANENT_SESSION_LIFETIME'] = _timedelta(days=Config.SESSION_LIFETIME_DAYS)
# APPLICATION_ROOT(운영 서버의 리버스 프록시 하위경로, 예: /upbit)를 지정 안 하면 Flask가 세션 쿠키의
# Path를 APPLICATION_ROOT로 그대로 써버려서, 그 경로 밖에서 접근(로컬 직접 접속 등)하면 로그인 직후
# 쿠키가 안 돌아와 계속 /login으로 튕기는 문제가 있었음 — 쿠키는 항상 사이트 전체(/)에 적용되게 고정
app.config['SESSION_COOKIE_PATH'] = '/'

# ──────────────────────────────────────────────
# 로그인 (잠금 토글 — 켜질 때마다 새 비밀번호를 Slack으로 전송, 해제될 때까지 그 비밀번호 재사용)
# ──────────────────────────────────────────────
_login_state = get_login_settings()  # {'lock_enabled': bool, 'password_hash': str|None} — 시작 시 DB에서 로드, 이후 메모리 캐시
# 안전장치: 잠금은 켜져 있는데(기본값) 발급된 비밀번호가 없고 Slack 웹훅도 설정 안 돼 있으면
# 비밀번호를 받을 방법이 전혀 없어 영구적으로 잠기게 된다. 이 경우에만 잠금을 강제로 꺼서
# 접근 불가 상태를 막는다 — SLACK_TOKEN을 설정하고 /security에서 다시 켜면 정상적으로 잠글 수 있음.
if _login_state['lock_enabled'] and not _login_state['password_hash'] and not Config.SLACK_WEBHOOK_URL:
    app.logger.warning(
        "[로그인] 잠금 기본값이 켜져 있지만 비밀번호가 없고 SLACK_TOKEN도 설정돼 있지 않아 "
        "로그인할 방법이 없습니다. 잠금을 임시로 꺼둡니다 — SLACK_TOKEN을 설정한 뒤 "
        "/security에서 잠금을 다시 켜주세요."
    )
    _login_state['lock_enabled'] = False
    save_login_settings(False, None)
_login_fail_count = 0
_login_locked_until = 0
_MAX_LOGIN_FAILS = 5
_LOGIN_FAIL_LOCKOUT_SECONDS = 60

_login_request_last_ts = 0.0
_LOGIN_REQUEST_COOLDOWN_SECONDS = 120  # 로그인 페이지의 "비밀번호 받기" 재요청 최소 간격(남용 방지)

# 로그인 없이 접근 가능한 엔드포인트 — 로그인 화면, 비밀번호 검증 API, 비밀번호 요청 API, 정적 파일
# (잠금 토글/재발급 API는 일부러 여기 안 넣음 — 잠금 켜진 상태에선 로그인해야만 끄거나 재발급할 수 있어야 함)
_PUBLIC_ENDPOINTS = {'login_view', 'verify_password_api', 'request_password_api', 'static'}

@app.before_request
def require_login():
    # 서버 간 동기화(/api/sync/*)는 자체 토큰(X-Sync-Token)으로 별도 인증하므로 세션 로그인과 무관
    if request.path.startswith('/api/sync/'):
        return
    if not _login_state['lock_enabled']:
        return  # 잠금 꺼져있으면 전체 오픈
    if request.endpoint in _PUBLIC_ENDPOINTS:
        return
    if not session.get('logged_in'):
        if request.path.startswith('/api/'):
            return jsonify({'status': 'error', 'message': '로그인이 필요합니다.'}), 401
        return redirect(url_for('login_view'))

@app.route('/login')
def login_view():
    if session.get('logged_in'):
        return redirect(url_for('index'))
    return render_template('login.html')

@app.route('/api/login/status', methods=['GET'])
def login_status_api():
    """잠금 켜짐/꺼짐 여부만 반환 (비밀번호 자체는 절대 내려주지 않음)"""
    return jsonify({'status': 'success', 'lock_enabled': _login_state['lock_enabled']})

def _issue_new_password() -> str:
    """새 비밀번호를 생성해 해시로 저장하고 Slack으로 평문 전송. 반환: 평문 비밀번호(로그용)"""
    password = f"{secrets.randbelow(10_000_000):07d}"  # 숫자 7자리(0000000~9999999, 0으로 시작 가능)
    _login_state['password_hash'] = generate_password_hash(password)
    save_login_settings(_login_state['lock_enabled'], _login_state['password_hash'])
    # "로컬/서버"로 미리 라벨링하지 않고 실제 OS + 호스트명을 그대로 보냄 — local/production 같은 별도
    # 구분값을 관리·동기화할 필요 없이, 어디서 보냈는지 발급 시점에 platform 모듈로 그냥 알려줌
    # (로컬 Windows PC에서 보내면 "Windows / DESKTOP-XXXX", 리눅스 서버에서 보내면 "Linux / <서버 호스트명>")
    # 공인 IP는 넣지 않음 — 외부 API 호출이 추가로 필요하고, 서버 IP가 Slack 메시지 평문에 그대로 노출되는 게 부담스러움
    # 마크다운(*강조*)을 쓰지 않음 — 렌더링 안 되는 클라이언트에서 별표가 문자 그대로 보여 복사·붙여넣기 시 섞여 들어가는 걸 방지
    send_slack_msg(f"🔐 [{platform.system()} / {platform.node()}] 로그인 비밀번호: {password}\n해제하거나 다시 발급하기 전까지 계속 이 비밀번호를 쓰시면 됩니다.")
    return password

@app.route('/api/login/toggle', methods=['POST'])
def toggle_lock_api():
    """잠금 켜기/끄기. 켜질 때는(꺼짐→켜짐이든, 이미 켜진 상태에서 재발급이든) 항상 새 비밀번호를 발급해 Slack으로 보냄.
    끌 때는 비밀번호 그대로 두고(다음에 켤 때 재사용 안 함 — 켤 때마다 무조건 새로 발급) 잠금만 해제.
    body: {enabled: bool}
    """
    body = request.get_json(silent=True) or {}
    enabled = bool(body.get('enabled'))

    if enabled:
        if not Config.SLACK_WEBHOOK_URL:
            return jsonify({'status': 'error', 'message': 'Slack 웹훅(SLACK_TOKEN)이 설정돼 있지 않아 비밀번호를 보낼 수 없습니다.'}), 500
        _login_state['lock_enabled'] = True
        _issue_new_password()
        app.logger.info("[로그인] 잠금 켜짐 — 새 비밀번호 발급")
    else:
        _login_state['lock_enabled'] = False
        save_login_settings(False, _login_state['password_hash'])
        app.logger.info("[로그인] 잠금 꺼짐")

    return jsonify({'status': 'success', 'lock_enabled': _login_state['lock_enabled']})

@app.route('/api/login/reissue', methods=['POST'])
def reissue_password_api():
    """잠금은 켜진 채로 비밀번호만 새로 발급(로그인된 상태에서만 호출 가능 — before_request가 이미 보장)"""
    if not Config.SLACK_WEBHOOK_URL:
        return jsonify({'status': 'error', 'message': 'Slack 웹훅(SLACK_TOKEN)이 설정돼 있지 않아 비밀번호를 보낼 수 없습니다.'}), 500
    _issue_new_password()
    app.logger.info("[로그인] 비밀번호 재발급")
    return jsonify({'status': 'success'})

@app.route('/api/login/verify', methods=['POST'])
def verify_password_api():
    """비밀번호 검증 → 통과 시 세션 로그인 처리(30일 유지). 여러 번 재사용 가능한 상시 비밀번호."""
    global _login_fail_count, _login_locked_until
    now = _time.time()
    if now < _login_locked_until:
        return jsonify({'status': 'error', 'message': f'로그인 시도가 너무 많았습니다. {int(_login_locked_until - now)}초 후 다시 시도해주세요.'}), 429

    body = request.get_json(silent=True) or {}
    password = body.get('password') or ''

    if not _login_state['password_hash'] or not check_password_hash(_login_state['password_hash'], password):
        _login_fail_count += 1
        if _login_fail_count >= _MAX_LOGIN_FAILS:
            _login_locked_until = now + _LOGIN_FAIL_LOCKOUT_SECONDS
            _login_fail_count = 0
            return jsonify({'status': 'error', 'message': f'{_LOGIN_FAIL_LOCKOUT_SECONDS}초 동안 로그인이 잠겼습니다.'}), 429
        return jsonify({'status': 'error', 'message': '비밀번호가 일치하지 않습니다.'}), 401

    _login_fail_count = 0
    session.permanent = True
    session['logged_in'] = True
    app.logger.info("[로그인] 로그인 성공")
    return jsonify({'status': 'success'})

@app.route('/api/login/request', methods=['POST'])
def request_password_api():
    """로그인 페이지에서 비밀번호를 잊었을 때 Slack으로 새 비밀번호를 재발급 요청.
    로그인 없이 호출 가능한 공개 API라 남용 방지를 위해 쿨다운을 둔다."""
    global _login_request_last_ts
    if not _login_state['lock_enabled']:
        return jsonify({'status': 'error', 'message': '잠금이 꺼져있어 비밀번호가 필요 없습니다.'}), 400
    if not Config.SLACK_WEBHOOK_URL:
        return jsonify({'status': 'error', 'message': 'Slack 웹훅(SLACK_TOKEN)이 설정돼 있지 않아 비밀번호를 보낼 수 없습니다.'}), 500

    now = _time.time()
    remain = _LOGIN_REQUEST_COOLDOWN_SECONDS - (now - _login_request_last_ts)
    if remain > 0:
        return jsonify({'status': 'error', 'message': f'{int(remain)}초 후 다시 요청해주세요.'}), 429

    _login_request_last_ts = now
    _issue_new_password()
    app.logger.info("[로그인] 로그인 페이지에서 비밀번호 재발급 요청")
    return jsonify({'status': 'success', 'message': 'Slack으로 새 비밀번호를 보냈습니다.', 'cooldown': _LOGIN_REQUEST_COOLDOWN_SECONDS})

@app.route('/logout')
def logout_view():
    session.clear()
    return redirect(url_for('login_view'))

@app.route('/security')
def security_view():
    """앱 잠금 설정 페이지 (잠금 토글 + 비밀번호 재발급)"""
    return render_template('security.html', active_page='security', lock_enabled=_login_state['lock_enabled'])

@app.route('/')
def index():
    """대시보드 메인 페이지를 보여줍니다."""
    return render_template('index.html', active_page='dashboard')

@app.route('/raw-data')
def raw_data_view():
    """주식 원본 데이터 확인 페이지를 보여줍니다."""
    return render_template('raw_data.html', active_page='raw_data')

@app.route('/date-column-convert')
def date_column_convert_view():
    """웹페이지에서 복사한 표 데이터 맨 앞에 날짜 열을 붙여 구글시트 붙여넣기용으로 변환하는 페이지."""
    return render_template('date_column_convert.html', active_page='date_column_convert')

@app.route('/api/quick-links', methods=['GET'])
def get_quick_links_api():
    """바로가기 링크 목록 조회"""
    data = get_quick_links()
    return jsonify({'status': 'success', 'count': len(data), 'data': data})

@app.route('/api/quick-links', methods=['POST'])
def add_quick_link_api():
    """바로가기 링크 추가"""
    body = request.get_json(silent=True) or {}
    url = (body.get('url') or '').strip()
    label = (body.get('label') or '').strip()
    side = body.get('side') or 'left'
    if side not in ('left', 'right'):
        side = 'left'
    if not url:
        return jsonify({'status': 'error', 'message': 'url이 필요합니다.'}), 400
    if not url.startswith(('http://', 'https://')):
        url = 'https://' + url
    if not label:
        label = url
    new_id = add_quick_link(label, url, side)
    return jsonify({'status': 'success', 'id': new_id, 'label': label, 'url': url, 'side': side})

@app.route('/api/quick-links/<int:link_id>', methods=['DELETE'])
def delete_quick_link_api(link_id):
    """바로가기 링크 삭제"""
    deleted = delete_quick_link(link_id)
    if not deleted:
        return jsonify({'status': 'error', 'message': '해당 링크를 찾을 수 없습니다.'}), 404
    return jsonify({'status': 'success', 'deleted': deleted})

@app.route('/powerball')
def powerball_view():
    """동행복권 파워볼 당첨결과 저장 + 즐겨찾기 번호 관리 페이지"""
    return render_template('powerball.html', active_page='powerball')

@app.route('/api/powerball/rounds', methods=['GET'])
def get_powerball_rounds_api():
    """저장된 파워볼 당첨결과 전체 조회 (회차 최신순)"""
    data = get_powerball_rounds()
    return jsonify({'status': 'success', 'count': len(data), 'data': data})

@app.route('/api/powerball/rounds', methods=['POST'])
def add_powerball_rounds_api():
    """동행복권 사이트에서 복사한 텍스트를 붙여넣어 회차 일괄 추가. body: {text}"""
    body = request.get_json(silent=True) or {}
    text = body.get('text') or ''
    if not text.strip():
        return jsonify({'status': 'error', 'message': '붙여넣은 텍스트가 비어있습니다.'}), 400
    rounds, errors = parse_powerball_block(text)
    added, skipped = save_powerball_rounds(rounds) if rounds else (0, 0)
    return jsonify({'status': 'success', 'added': added, 'skipped': skipped, 'errors': errors})

@app.route('/api/powerball/rounds/<int:round_id>', methods=['DELETE'])
def delete_powerball_round_api(round_id):
    """파워볼 당첨결과 1건 삭제"""
    deleted = delete_powerball_round(round_id)
    if not deleted:
        return jsonify({'status': 'error', 'message': '해당 회차를 찾을 수 없습니다.'}), 404
    return jsonify({'status': 'success', 'deleted': deleted})

@app.route('/api/powerball/favorites', methods=['GET'])
def get_powerball_favorites_api():
    """즐겨찾기 번호 목록 + 저장된 당첨결과 대비 최다 일치 회차 조회"""
    data = get_powerball_favorites()
    return jsonify({'status': 'success', 'count': len(data), 'data': data})

@app.route('/api/powerball/favorites', methods=['POST'])
def add_powerball_favorite_api():
    """즐겨찾기 번호 추가. body: {name, nums: [일반볼 5개], pb: 파워볼 1개}"""
    body = request.get_json(silent=True) or {}
    name = (body.get('name') or '').strip()
    nums = body.get('nums') or []
    pb = body.get('pb')
    if len(nums) != 5 or any(not isinstance(n, int) for n in nums):
        return jsonify({'status': 'error', 'message': '일반볼 5개(정수)가 필요합니다.'}), 400
    if not isinstance(pb, int):
        return jsonify({'status': 'error', 'message': '파워볼 번호(정수)가 필요합니다.'}), 400
    if not name:
        name = f"내 번호 {_dt.now().strftime('%H%M%S')}"
    new_id = add_powerball_favorite(name, nums, pb)
    return jsonify({'status': 'success', 'id': new_id})

@app.route('/api/powerball/favorites/<int:fav_id>', methods=['DELETE'])
def delete_powerball_favorite_api(fav_id):
    """즐겨찾기 번호 1건 삭제"""
    deleted = delete_powerball_favorite(fav_id)
    if not deleted:
        return jsonify({'status': 'error', 'message': '해당 즐겨찾기를 찾을 수 없습니다.'}), 404
    return jsonify({'status': 'success', 'deleted': deleted})

@app.route('/lotto645')
def lotto645_view():
    """동행복권 로또6/45 당첨결과 저장 페이지"""
    return render_template('lotto645.html', active_page='lotto645')

@app.route('/api/lotto645/rounds', methods=['GET'])
def get_lotto645_rounds_api():
    """저장된 로또6/45 당첨결과 전체 조회 (회차 최신순)"""
    data = get_lotto645_rounds()
    return jsonify({'status': 'success', 'count': len(data), 'data': data})

@app.route('/api/lotto645/rounds', methods=['POST'])
def add_lotto645_rounds_api():
    """동행복권 사이트에서 복사한 텍스트를 붙여넣어 회차 일괄 추가. body: {text}"""
    body = request.get_json(silent=True) or {}
    text = body.get('text') or ''
    if not text.strip():
        return jsonify({'status': 'error', 'message': '붙여넣은 텍스트가 비어있습니다.'}), 400
    rounds, errors = parse_lotto645_block(text)
    added, skipped = save_lotto645_rounds(rounds) if rounds else (0, 0)
    return jsonify({'status': 'success', 'added': added, 'skipped': skipped, 'errors': errors})

@app.route('/api/lotto645/rounds/<int:round_id>', methods=['DELETE'])
def delete_lotto645_round_api(round_id):
    """로또6/45 당첨결과 1건 삭제"""
    deleted = delete_lotto645_round(round_id)
    if not deleted:
        return jsonify({'status': 'error', 'message': '해당 회차를 찾을 수 없습니다.'}), 404
    return jsonify({'status': 'success', 'deleted': deleted})

@app.route('/api/lotto645/upload', methods=['POST'])
def upload_lotto645_excel_api():
    """동행복권 "로또 회차별 당첨번호" 통계 엑셀(.xlsx) 파일을 업로드해서 일괄 추가. multipart form, 필드명: file"""
    file = request.files.get('file')
    if not file or not file.filename:
        return jsonify({'status': 'error', 'message': '업로드할 파일이 없습니다.'}), 400
    if not file.filename.lower().endswith('.xlsx'):
        return jsonify({'status': 'error', 'message': '.xlsx 파일만 지원합니다.'}), 400
    try:
        rounds, errors = parse_lotto645_excel(file.stream)
    except Exception as e:
        return jsonify({'status': 'error', 'message': f'엑셀 파일을 읽지 못했습니다: {e}'}), 400
    added, skipped = save_lotto645_rounds(rounds) if rounds else (0, 0)
    return jsonify({'status': 'success', 'added': added, 'skipped': skipped, 'errors': errors})

@app.route('/api/lotto645/favorites', methods=['GET'])
def get_lotto645_favorites_api():
    """즐겨찾기 번호 목록 + 저장된 당첨결과 대비 최고 등수 회차 조회"""
    data = get_lotto645_favorites()
    return jsonify({'status': 'success', 'count': len(data), 'data': data})

@app.route('/api/lotto645/favorites', methods=['POST'])
def add_lotto645_favorite_api():
    """즐겨찾기 번호 추가. body: {name, nums: [번호 6개]}"""
    body = request.get_json(silent=True) or {}
    name = (body.get('name') or '').strip()
    nums = body.get('nums') or []
    if len(nums) != 6 or any(not isinstance(n, int) for n in nums):
        return jsonify({'status': 'error', 'message': '번호 6개(정수)가 필요합니다.'}), 400
    if not name:
        name = f"내 번호 {_dt.now().strftime('%H%M%S')}"
    new_id = add_lotto645_favorite(name, nums)
    return jsonify({'status': 'success', 'id': new_id})

@app.route('/api/lotto645/favorites/<int:fav_id>', methods=['DELETE'])
def delete_lotto645_favorite_api(fav_id):
    """즐겨찾기 번호 1건 삭제"""
    deleted = delete_lotto645_favorite(fav_id)
    if not deleted:
        return jsonify({'status': 'error', 'message': '해당 즐겨찾기를 찾을 수 없습니다.'}), 404
    return jsonify({'status': 'success', 'deleted': deleted})

@app.route('/pension720')
def pension720_view():
    """동행복권 연금복권720+ 당첨결과 저장 + 즐겨찾기 페이지"""
    return render_template('pension720.html', active_page='pension720')

@app.route('/api/pension720/rounds', methods=['GET'])
def get_pension720_rounds_api():
    """저장된 연금복권720+ 당첨결과 전체 조회 (회차 최신순)"""
    data = get_pension720_rounds()
    return jsonify({'status': 'success', 'count': len(data), 'data': data})

@app.route('/api/pension720/rounds', methods=['POST'])
def add_pension720_rounds_api():
    """동행복권 사이트에서 복사한 텍스트를 붙여넣어 회차 일괄 추가. body: {text}"""
    body = request.get_json(silent=True) or {}
    text = body.get('text') or ''
    if not text.strip():
        return jsonify({'status': 'error', 'message': '붙여넣은 텍스트가 비어있습니다.'}), 400
    rounds, errors = parse_pension720_block(text)
    added, skipped = save_pension720_rounds(rounds) if rounds else (0, 0)
    return jsonify({'status': 'success', 'added': added, 'skipped': skipped, 'errors': errors})

@app.route('/api/pension720/rounds/<int:round_id>', methods=['DELETE'])
def delete_pension720_round_api(round_id):
    """연금복권720+ 당첨결과 1건 삭제"""
    deleted = delete_pension720_round(round_id)
    if not deleted:
        return jsonify({'status': 'error', 'message': '해당 회차를 찾을 수 없습니다.'}), 404
    return jsonify({'status': 'success', 'deleted': deleted})

@app.route('/api/pension720/upload', methods=['POST'])
def upload_pension720_excel_api():
    """동행복권 "연금복권720+ 회차별 당첨번호" 통계 엑셀(.xlsx) 파일을 업로드해서 일괄 추가. multipart form, 필드명: file"""
    file = request.files.get('file')
    if not file or not file.filename:
        return jsonify({'status': 'error', 'message': '업로드할 파일이 없습니다.'}), 400
    if not file.filename.lower().endswith('.xlsx'):
        return jsonify({'status': 'error', 'message': '.xlsx 파일만 지원합니다.'}), 400
    try:
        rounds, errors = parse_pension720_excel(file.stream)
    except Exception as e:
        return jsonify({'status': 'error', 'message': f'엑셀 파일을 읽지 못했습니다: {e}'}), 400
    added, skipped = save_pension720_rounds(rounds) if rounds else (0, 0)
    return jsonify({'status': 'success', 'added': added, 'skipped': skipped, 'errors': errors})

@app.route('/api/pension720/favorites', methods=['GET'])
def get_pension720_favorites_api():
    """즐겨찾기 번호 목록 + 저장된 당첨결과 대비 최고 등수 회차 조회"""
    data = get_pension720_favorites()
    return jsonify({'status': 'success', 'count': len(data), 'data': data})

@app.route('/api/pension720/favorites', methods=['POST'])
def add_pension720_favorite_api():
    """즐겨찾기 번호 추가. body: {name, group(조, 1~5), number(6자리 문자열/숫자)}"""
    body = request.get_json(silent=True) or {}
    name = (body.get('name') or '').strip()
    group = body.get('group')
    number_raw = body.get('number')
    if not isinstance(group, int) or not (1 <= group <= 5):
        return jsonify({'status': 'error', 'message': '조(1~5, 정수)가 필요합니다.'}), 400
    digits = ''.join(ch for ch in str(number_raw) if ch.isdigit()) if number_raw is not None else ''
    if len(digits) != 6:
        return jsonify({'status': 'error', 'message': '번호는 숫자 6자리가 필요합니다.'}), 400
    if not name:
        name = f"내 번호 {_dt.now().strftime('%H%M%S')}"
    new_id = add_pension720_favorite(name, group, digits)
    return jsonify({'status': 'success', 'id': new_id})

@app.route('/api/pension720/favorites/<int:fav_id>', methods=['DELETE'])
def delete_pension720_favorite_api(fav_id):
    """즐겨찾기 번호 1건 삭제"""
    deleted = delete_pension720_favorite(fav_id)
    if not deleted:
        return jsonify({'status': 'error', 'message': '해당 즐겨찾기를 찾을 수 없습니다.'}), 404
    return jsonify({'status': 'success', 'deleted': deleted})

@app.route('/lottery-recommend')
def lottery_recommend_view():
    """파워볼/로또6/45/연금복권720+ 재미용 번호 추천 페이지 — 당첨결과 통계는 브라우저에서 계산하고, 뽑은 결과는 DB에 저장"""
    return render_template('lottery_recommend.html', active_page='lottery_recommend')

_RECO_GAMES = {'powerball', 'lotto645', 'pension720'}
_RECO_METHODS = {'full', 'hot', 'cold', 'weighted', 'filter'}

def _validate_recommendation_row(r):
    """번호 추천 저장 요청 1건의 형태를 검증. 문제 있으면 오류 메시지, 없으면 None."""
    if not isinstance(r, dict):
        return '항목 형식이 올바르지 않습니다.'
    if r.get('game') not in _RECO_GAMES:
        return f"잘못된 game 값입니다: {r.get('game')}"
    if r.get('method') not in _RECO_METHODS:
        return f"잘못된 method 값입니다: {r.get('method')}"
    main = r.get('main')
    if not isinstance(main, list) or len(main) not in (5, 6) or not all(isinstance(n, int) and not isinstance(n, bool) for n in main):
        return 'main은 정수 5~6개짜리 배열이어야 합니다.'
    bonus = r.get('bonus')
    if bonus is not None and (not isinstance(bonus, int) or isinstance(bonus, bool)):
        return 'bonus는 정수여야 합니다.'
    return None

@app.route('/api/lottery-recommend', methods=['GET'])
def get_lottery_recommendations_api():
    """저장된 번호 추천 결과 전체 조회 (최신순) — 필터링은 프론트에서 처리"""
    data = get_lottery_recommendations()
    return jsonify({'status': 'success', 'count': len(data), 'data': data})

@app.route('/api/lottery-recommend', methods=['POST'])
def save_lottery_recommendations_api():
    """번호 추천 결과 여러 건을 한 번에 저장. body: {rows: [{game, method, main:[int,...], bonus:int|null}, ...]}"""
    body = request.get_json(silent=True) or {}
    rows = body.get('rows')
    if not isinstance(rows, list) or not rows:
        return jsonify({'status': 'error', 'message': '저장할 데이터가 없습니다.'}), 400
    for r in rows:
        err = _validate_recommendation_row(r)
        if err:
            return jsonify({'status': 'error', 'message': err}), 400
    saved = save_lottery_recommendations(rows)
    return jsonify({'status': 'success', 'saved': saved})

@app.route('/api/lottery-recommend/bulk-delete', methods=['POST'])
def delete_lottery_recommendations_bulk_api():
    """번호 추천 결과 여러 건을 한 번에 삭제. body: {ids: [int, ...]}"""
    body = request.get_json(silent=True) or {}
    ids = body.get('ids')
    if not isinstance(ids, list) or not ids or not all(isinstance(i, int) and not isinstance(i, bool) for i in ids):
        return jsonify({'status': 'error', 'message': '삭제할 id 목록이 필요합니다.'}), 400
    deleted = delete_lottery_recommendations_bulk(ids)
    return jsonify({'status': 'success', 'deleted': deleted})

@app.route('/api/lottery-recommend/<int:rec_id>', methods=['DELETE'])
def delete_lottery_recommendation_api(rec_id):
    """번호 추천 결과 1건 삭제"""
    deleted = delete_lottery_recommendation(rec_id)
    if not deleted:
        return jsonify({'status': 'error', 'message': '해당 항목을 찾을 수 없습니다.'}), 404
    return jsonify({'status': 'success', 'deleted': deleted})

@app.route('/coin-screening')
def coin_screening_view():
    """코인(업비트 KRW 마켓) 기술지표 스크리닝 페이지를 보여줍니다."""
    return render_template('coin_screening.html', active_page='coin_screening')

@app.route('/api/coin-screening', methods=['GET'])
def get_coin_screening_api():
    """코인 스크리닝 스냅샷(RSI/이동평균 등)을 JSON으로 반환합니다."""
    try:
        data = get_coin_screening()
        return jsonify({"status": "success", "count": len(data), "data": data})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/coin-screening/fetch', methods=['POST'])
def fetch_coin_screening_api():
    """코인 스크리닝 데이터를 즉시 수집하도록 요청합니다 (동기 실행 — 전 종목 조회로 다소 시간이 걸릴 수 있음)."""
    try:
        count = run_coin_screening()
        return jsonify({"status": "success", "message": f"{count}개 종목 스크리닝 완료"})
    except Exception as e:
        return jsonify({"status": "error", "message": f"코인 스크리닝 수집 중 오류 발생: {str(e)}"}), 500

@app.route('/auto-trade')
def auto_trade_view():
    """업비트 자동매매(모의) 대시보드 — 읽기 전용. 매매 판단/실행은 별도 프로세스(python main.py trade)에서만 발생."""
    return render_template('auto_trade.html', active_page='auto_trade')

@app.route('/auto-trade/logs')
def auto_trade_logs_view():
    """매매 판단/체결 이력(BUY/SELL/HOLD/SKIP 전부) 조회 페이지 — 대시보드(/auto-trade)에서 분리.
    폴링되는 대시보드 요약 API에 매번 100건씩 딸려오던 부담을 줄이려고, 실시간성이 필요 없는
    이 로그는 별도 페이지+페이지네이션으로 뺐다(get_auto_trade_logs_api 참고)."""
    return render_template('auto_trade_logs.html', active_page='auto_trade')

@app.route('/api/auto-trade/logs', methods=['GET'])
def get_auto_trade_logs_api():
    """매매 판단/체결 이력을 페이지네이션해서 조회. 쿼리파라미터: limit(기본 50, 최대 200),
    offset(기본 0), ticker(선택, 예: KRW-BTC), decision(선택, BUY/SELL/HOLD/SKIP/DCA_BUY)."""
    try:
        limit = min(int(request.args.get('limit', 50)), 200)
        offset = max(int(request.args.get('offset', 0)), 0)
        ticker = (request.args.get('ticker') or '').strip() or None
        decision = (request.args.get('decision') or '').strip() or None
        orders = get_trade_order_log('upbit', 'paper', limit=limit, offset=offset, ticker=ticker, decision=decision)
        total = count_trade_order_log('upbit', 'paper', ticker=ticker, decision=decision)
        return jsonify({'status': 'success', 'orders': orders, 'total': total, 'limit': limit, 'offset': offset})
    except (ValueError, TypeError) as e:
        return jsonify({'status': 'error', 'message': str(e)}), 400
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/api/auto-trade/summary', methods=['GET'])
def get_auto_trade_summary_api():
    """가상 계좌 잔고/보유 포지션(현재가·평가손익 포함)/진입 후보(매매 대상 코인)/엔진 실행 여부/
    매매 전략 파라미터(settings)를 JSON으로 반환합니다 (읽기 전용, 주문 실행 없음). 매매 판단 로그는
    포함하지 않음 — /auto-trade/logs 페이지의 /api/auto-trade/logs를 따로 호출할 것."""
    try:
        data = get_dashboard_summary()
        return jsonify({'status': 'success', **data})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/api/auto-trade/toggle', methods=['POST'])
def toggle_auto_trade_api():
    """자동매매 엔진 실행/일시중지 토글. 실행 중인 `python main.py trade` 프로세스가 다음 사이클(최대
    TRADE_LOOP_INTERVAL_SEC초 이내)마다 이 값을 확인해 반영하므로 재시작이 필요 없습니다."""
    body = request.get_json(silent=True) or {}
    enabled = bool(body.get('enabled'))
    try:
        set_trade_engine_enabled(enabled)
        return jsonify({'status': 'success', 'enabled': enabled})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/api/auto-trade/run-now', methods=['POST'])
def run_auto_trade_now_api():
    """매매 루프의 다음 sleep(최대 loop_interval_sec초)을 기다리지 않고, 지금 이 요청 안에서
    동기적으로 매매 판단 1사이클(청산→진입, 현재 DB에 저장된 매매 기준 그대로)을 즉시 실행합니다.
    `python main.py trade` 프로세스가 떠 있는지와 무관하게 웹 서버 프로세스에서 직접 실행하며(다른
    수동 즉시수집 버튼들과 동일한 패턴), 엔진 실행/일시중지 토글 상태와도 무관하게 항상 실행됩니다
    (수동 트리거는 토글의 자동 스케줄링과 별개). 결과는 여느 사이클처럼 trade_order_log/job_run_log에
    'manual'로 기록됩니다."""
    try:
        result = run_trade_cycle(trigger_type='manual')
        return jsonify({'status': 'success', **result})
    except Exception as e:
        return jsonify({'status': 'error', 'message': f'즉시 실행 중 오류 발생: {str(e)}'}), 500

@app.route('/api/auto-trade/candidates/approve', methods=['POST'])
def set_candidate_approval_api():
    """매매 대상 코인 체크박스 상태 저장. 체크된 종목이 하나라도 있으면 다음 사이클부터
    그 종목들만 신규 진입 대상이 되고(기존 보유/청산 로직에는 영향 없음), 전부 체크 해제하면
    다시 전체 후보를 대상으로 합니다. body: {ticker: str, approved: bool}"""
    body = request.get_json(silent=True) or {}
    ticker = (body.get('ticker') or '').strip()
    approved = bool(body.get('approved'))
    if not ticker:
        return jsonify({'status': 'error', 'message': 'ticker가 필요합니다.'}), 400
    try:
        set_candidate_approval('upbit', 'paper', ticker, approved)
        return jsonify({'status': 'success', 'ticker': ticker, 'approved': approved})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/api/auto-trade/settings', methods=['POST'])
def set_trade_strategy_settings_api():
    """매매 전략 파라미터(1종목당 매수금액/최대 동시보유/손절·익절 기준/루프 주기/손절 연속확인
    사이클수/물타기 트리거 %) 저장. 실행 중인 `python main.py trade` 프로세스가 다음 사이클부터
    새 값을 적용하므로 재시작이 필요 없습니다. body는 아래 필드 중 바꿀 것만 보내면 됩니다(부분 갱신):
    {max_position_krw, max_concurrent_positions, stop_loss_pct, take_profit_pct, loop_interval_sec,
     stop_loss_confirm_cycles, dca_trigger_pct, dca_max_count}"""
    body = request.get_json(silent=True) or {}
    try:
        kwargs = {}
        if 'max_position_krw' in body:
            v = float(body['max_position_krw'])
            if v <= 0:
                raise ValueError('1종목당 매수금액은 0보다 커야 합니다.')
            kwargs['max_position_krw'] = v
        if 'max_concurrent_positions' in body:
            v = int(body['max_concurrent_positions'])
            if v <= 0:
                raise ValueError('최대 동시보유 종목수는 1 이상이어야 합니다.')
            kwargs['max_concurrent_positions'] = v
        if 'stop_loss_pct' in body:
            v = float(body['stop_loss_pct'])
            if v <= 0:
                raise ValueError('손절 기준(%)은 0보다 커야 합니다.')
            kwargs['stop_loss_pct'] = v
        if 'take_profit_pct' in body:
            v = float(body['take_profit_pct'])
            if v <= 0:
                raise ValueError('익절 기준(%)은 0보다 커야 합니다.')
            kwargs['take_profit_pct'] = v
        if 'loop_interval_sec' in body:
            v = int(body['loop_interval_sec'])
            if v < 10:
                raise ValueError('매매 루프 주기는 최소 10초 이상이어야 합니다.')
            kwargs['loop_interval_sec'] = v
        if 'stop_loss_confirm_cycles' in body:
            v = int(body['stop_loss_confirm_cycles'])
            if v <= 0:
                raise ValueError('손절 연속확인 사이클수는 1 이상이어야 합니다.')
            kwargs['stop_loss_confirm_cycles'] = v
        if 'dca_trigger_pct' in body:
            v = float(body['dca_trigger_pct'])
            if v <= 0:
                raise ValueError('물타기 트리거(%)는 0보다 커야 합니다.')
            kwargs['dca_trigger_pct'] = v
        if 'dca_max_count' in body:
            v = int(body['dca_max_count'])
            if v <= 0:
                raise ValueError('물타기 최대 횟수는 1 이상이어야 합니다.')
            kwargs['dca_max_count'] = v
        if 'condition_check_interval_sec' in body:
            v = int(body['condition_check_interval_sec'])
            if v < 10:
                raise ValueError('정밀조건 검사 주기는 최소 10초 이상이어야 합니다.')
            kwargs['condition_check_interval_sec'] = v

        settings = set_trade_strategy_settings(**kwargs)
        return jsonify({'status': 'success', 'settings': settings})
    except (ValueError, TypeError) as e:
        return jsonify({'status': 'error', 'message': str(e)}), 400
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/api/auto-trade/candidates/condition-watch', methods=['POST'])
def set_candidate_condition_watch_api():
    """"정밀조건 검사" 체크박스 상태 저장. 켜진 종목만 entry_condition_checker.py(python main.py
    condition_check)가 별도 주기로 일봉/5분봉/1분봉을 조회해 정밀 매수조건을 검사한다.
    body: {ticker: str, enabled: bool}"""
    body = request.get_json(silent=True) or {}
    ticker = (body.get('ticker') or '').strip()
    enabled = bool(body.get('enabled'))
    if not ticker:
        return jsonify({'status': 'error', 'message': 'ticker가 필요합니다.'}), 400
    try:
        set_candidate_condition_watch('upbit', 'paper', ticker, enabled)
        return jsonify({'status': 'success', 'ticker': ticker, 'enabled': enabled})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/api/auto-trade/conditions/settings', methods=['POST'])
def set_trade_condition_settings_api():
    """정밀 매수조건(일봉 20MA 위/5분봉 지지반등/1분봉 볼밴+거래량 돌파 등) 설정 저장 — 여러 건
    한 번에 부분 갱신 가능. body: {conditions: [{condition_key, enabled?, logic_group?, params?}, ...]}
    logic_group은 'AND' 또는 'OR'만 허용. params는 조건별로 다른 키를 부분 갱신(넘긴 키만 덮어씀)."""
    body = request.get_json(silent=True) or {}
    conditions = body.get('conditions')
    if not isinstance(conditions, list) or not conditions:
        return jsonify({'status': 'error', 'message': 'conditions 배열이 필요합니다.'}), 400
    try:
        updated = []
        for c in conditions:
            condition_key = (c.get('condition_key') or '').strip()
            if not condition_key:
                raise ValueError('condition_key가 필요합니다.')
            logic_group = c.get('logic_group')
            if logic_group is not None and logic_group not in ('AND', 'OR'):
                raise ValueError(f'logic_group은 AND/OR만 가능합니다: {logic_group}')
            enabled = c.get('enabled')
            params = c.get('params')
            if params is not None and not isinstance(params, dict):
                raise ValueError('params는 객체여야 합니다.')
            updated.append(set_trade_condition_setting(
                condition_key,
                enabled=bool(enabled) if enabled is not None else None,
                logic_group=logic_group,
                params=params,
            ))
        return jsonify({'status': 'success', 'conditions': updated})
    except ValueError as e:
        return jsonify({'status': 'error', 'message': str(e)}), 400
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/api/auto-trade/force-buy', methods=['POST'])
def force_buy_api():
    """대시보드 "강제 매수" — 진입 후보/승인/정밀조건 등 모든 필터를 건너뛰고 지정한 티커를 지금
    즉시 "1종목당 매수금액"만큼 시장가 매수(모의)한다. body: {ticker: str} (예: 'KRW-BTC')."""
    body = request.get_json(silent=True) or {}
    ticker = (body.get('ticker') or '').strip().upper()
    if not ticker:
        return jsonify({'status': 'error', 'message': 'ticker가 필요합니다.'}), 400
    if not ticker.startswith('KRW-'):
        return jsonify({'status': 'error', 'message': "ticker는 'KRW-BTC'와 같은 형식이어야 합니다."}), 400
    try:
        result = force_buy(ticker)
        status = 'success' if result['success'] else 'error'
        code = 200 if result['success'] else 400
        return jsonify({'status': status, **result}), code
    except Exception as e:
        return jsonify({'status': 'error', 'message': f'강제 매수 중 오류 발생: {str(e)}'}), 500

@app.route('/api/auto-trade/positions/dca', methods=['POST'])
def set_position_dca_api():
    """보유 포지션의 물타기(추가매수) 허용 체크박스 상태 저장. 켜두면 트레일링 손절 연속확인이
    끝난 뒤 곧바로 손절하지 않고 평단 대비 -dca_trigger_pct까지 한 번 더 기다렸다가 매매기준
    (1종목당 매수금액)으로 추가매수합니다(1회 제한). body: {ticker: str, enabled: bool}"""
    body = request.get_json(silent=True) or {}
    ticker = (body.get('ticker') or '').strip()
    enabled = bool(body.get('enabled'))
    if not ticker:
        return jsonify({'status': 'error', 'message': 'ticker가 필요합니다.'}), 400
    try:
        set_position_dca_enabled('upbit', 'paper', ticker, enabled)
        return jsonify({'status': 'success', 'ticker': ticker, 'enabled': enabled})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/market-cap')
def market_cap_view():
    """일별 시가총액 추이 페이지를 보여줍니다."""
    return render_template('market_cap.html', active_page='market_cap')

@app.route('/hts-top-view')
def hts_top_view_view():
    """HTS조회상위20종목 시간별 추이 페이지를 보여줍니다."""
    return render_template('hts_top_view.html', active_page='hts_top_view')

@app.route('/sector-index')
def sector_index_view():
    """업종 일자별지수 조회 페이지"""
    return render_template('sector_index.html', active_page='sector_index')

@app.route('/api/sector-index/list', methods=['GET'])
def get_sector_index_list_api():
    """업종 선택 드롭다운용 — 지원하는 전체 업종 코드/이름 목록 반환"""
    return jsonify({'status': 'success', 'data': [{'code': c, 'name': n} for c, n in SECTOR_NAMES.items()]})

@app.route('/api/sector-index/stocks/cached', methods=['GET'])
def get_sector_stocks_cached_api():
    """업종 소속 종목 DB 캐시 조회 (가장 최근 저장일 기준)"""
    iscd = request.args.get('iscd', '0001')
    data = get_sector_stocks_cached(iscd)
    return jsonify({'status': 'success', 'count': len(data), 'data': data, 'source': 'cache'})

@app.route('/api/sector-index/stocks', methods=['GET'])
def get_sector_stocks_api():
    """업종 소속 종목 조회 — 국내주식 등락률 순위(FHPST01700000) API를 업종코드로 필터링해 실시간 반환.
    KIS API 실패/무응답 시 DB 캐시(가장 최근 저장 스냅샷)로 폴백."""
    SIGN = {'1': '상한', '2': '상승', '3': '보합', '4': '하한', '5': '하락'}
    iscd = request.args.get('iscd', '0001')
    try:
        records = fetch_sector_stocks(iscd)
        result = [{
            'rank':        r.get('data_rank', ''),
            'code':        r.get('stck_shrn_iscd', ''),
            'name':        r.get('hts_kor_isnm', ''),
            'price':       r.get('stck_prpr', ''),
            'change':      r.get('prdy_vrss', ''),
            'change_sign': SIGN.get(r.get('prdy_vrss_sign', '3'), '보합'),
            'change_rate': r.get('prdy_ctrt', ''),
            'volume':      r.get('acml_vol', ''),
        } for r in records]

        if result:
            return jsonify({'status': 'success', 'count': len(result), 'data': result})

        cached = get_sector_stocks_cached(iscd)
        if cached:
            app.logger.info(f"[sector-stocks] KIS API 결과 없음 → DB 캐시 {len(cached)}건 반환 (iscd={iscd})")
            return jsonify({'status': 'success', 'count': len(cached), 'data': cached, 'source': 'cache'})

        return jsonify({'status': 'success', 'count': 0, 'data': []})
    except Exception as e:
        try:
            cached = get_sector_stocks_cached(iscd)
            if cached:
                app.logger.warning(f"[sector-stocks] KIS API 오류 → DB 캐시 반환: {e}")
                return jsonify({'status': 'success', 'count': len(cached), 'data': cached, 'source': 'cache'})
        except Exception:
            pass
        return jsonify({'status': 'error', 'message': str(e)}), 500

_STOCK_PRICE_CACHE = {}          # {codes_key: {'ts': float, 'data': [...]}}
_STOCK_PRICE_CACHE_TTL = 3       # 초 — 이 시간 내 재요청은 KIS를 다시 부르지 않고 캐시를 반환

@app.route('/api/stock-price', methods=['GET'])
def get_stock_price_api():
    """종목코드 여러 개(콤마 구분)를 전달하면 현재가를 반환합니다.
    관심종목(멀티종목) 시세조회(FHKST11300006) 사용 — 1회 호출당 최대 30종목, 초과 시 내부적으로 나눠 호출.
    같은 종목 조합에 대해 짧은 시간(TTL) 내 재요청은 KIS를 다시 호출하지 않고 캐시된 값을 반환해
    동시 다발적 폴링이 KIS 호출량을 그대로 늘리지 않도록 함.
    예: /api/stock-price?codes=005930,000660,035420
    """
    SIGN = {'1': '상한', '2': '상승', '3': '보합', '4': '하한', '5': '하락'}
    codes_param = request.args.get('codes', '')
    codes = [c.strip() for c in codes_param.split(',') if c.strip()]
    if not codes:
        return jsonify({'status': 'error', 'message': 'codes 파라미터가 필요합니다 (예: ?codes=005930,000660)'}), 400

    cache_key = ','.join(sorted(codes))
    cached = _STOCK_PRICE_CACHE.get(cache_key)
    now = _time.time()
    if cached and (now - cached['ts']) < _STOCK_PRICE_CACHE_TTL:
        return jsonify({'status': 'success', 'count': len(cached['data']), 'data': cached['data'], 'cached': True})

    try:
        records = fetch_multi_stock_price(codes)
        result = [{
            'code':        r.get('inter_shrn_iscd', ''),
            'name':        r.get('inter_kor_isnm', ''),
            'price':       r.get('inter2_prpr', ''),
            'change':      r.get('inter2_prdy_vrss', ''),
            'change_sign': SIGN.get(r.get('prdy_vrss_sign', '3'), '보합'),
            'change_rate': r.get('prdy_ctrt', ''),
            'volume':      r.get('acml_vol', ''),
        } for r in records]
        _STOCK_PRICE_CACHE[cache_key] = {'ts': now, 'data': result}
        return jsonify({'status': 'success', 'count': len(result), 'data': result, 'cached': False})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

# ──────────────────────────────────────────────
# 종목 메모 (전 페이지 공용 — 네이버 증권 링크 옆 📝 아이콘에서 사용)
# ──────────────────────────────────────────────

@app.route('/api/stock-memo', methods=['GET'])
def get_stock_memo_api():
    """종목의 전체 메모 이력 조회 (최신순). 예: /api/stock-memo?code=005930"""
    code = request.args.get('code', '').strip()
    if not code:
        return jsonify({'status': 'error', 'message': 'code 파라미터가 필요합니다'}), 400
    data = get_stock_memos(code)
    return jsonify({'status': 'success', 'count': len(data), 'data': data})

@app.route('/api/stock-memo', methods=['POST'])
def save_stock_memo_api():
    """종목 메모 새로 추가. body: {code, name, memo, grade} — 종목당 여러 개 누적 저장됨."""
    body = request.get_json(silent=True) or {}
    code = (body.get('code') or '').strip()
    name = (body.get('name') or '').strip()
    memo = (body.get('memo') or '').strip()
    grade = (body.get('grade') or '기타').strip()
    if not code:
        return jsonify({'status': 'error', 'message': 'code가 필요합니다'}), 400
    if not memo:
        return jsonify({'status': 'error', 'message': 'memo가 비어있습니다'}), 400
    new_id = add_stock_memo(code, name, memo, grade)
    return jsonify({'status': 'success', 'id': new_id})

@app.route('/api/stock-memo/<int:memo_id>', methods=['DELETE'])
def delete_stock_memo_entry_api(memo_id):
    """메모 항목 1건 삭제 (id 기준)"""
    delete_stock_memo_entry(memo_id)
    return jsonify({'status': 'success'})

@app.route('/api/stock-memo/<int:memo_id>/grade', methods=['PATCH'])
def update_stock_memo_grade_api(memo_id):
    """메모 항목의 등급만 변경 (다른 등급 컬럼으로 옮기기). body: {grade}"""
    body = request.get_json(silent=True) or {}
    grade = (body.get('grade') or '').strip()
    if not grade:
        return jsonify({'status': 'error', 'message': 'grade가 필요합니다'}), 400
    update_stock_memo_grade(memo_id, grade)
    return jsonify({'status': 'success'})

@app.route('/api/stock-memo/<int:memo_id>/bump', methods=['PATCH'])
def bump_stock_memo_api(memo_id):
    """메모를 맨 위로 올림 (수정일을 현재 시각으로 갱신, 중요한 메모 강조용)"""
    bump_stock_memo(memo_id)
    return jsonify({'status': 'success'})

@app.route('/api/stock-search', methods=['GET'])
def search_stock_codes_api():
    """종목코드/종목명 자동완성 검색 (메모 빠른 추가 폼용). 예: /api/stock-search?q=삼성"""
    q = request.args.get('q', '').strip()
    if not q:
        return jsonify({'status': 'success', 'data': []})
    data = search_stock_codes(q, limit=15)
    return jsonify({'status': 'success', 'count': len(data), 'data': data})

@app.route('/api/stock-memo/grades', methods=['GET'])
def get_stock_memo_grades_api():
    """메모 등급(태그) 목록 조회 — 기본 등급 + DB에 실제 존재하는 등급"""
    return jsonify({'status': 'success', 'data': get_stock_memo_grades()})

@app.route('/api/stock-memo/search', methods=['GET'])
def search_stock_memo_api():
    """종목 메모 검색 (종목코드/종목명 부분일치). q 없으면 최근 수정순 전체 목록."""
    q = request.args.get('q', '').strip()
    data = search_stock_memos(query=q or None, limit=100)
    return jsonify({'status': 'success', 'count': len(data), 'data': data})

@app.route('/stock-memo')
def stock_memo_view():
    """종목 메모 전체보기 페이지"""
    return render_template('stock_memo.html', active_page='stock_memo')

@app.route('/api/stock-memo/all', methods=['GET'])
def get_all_stock_memo_api():
    """전체 메모 이력 조회 (종목당 여러 건 전부, 최신순). q로 코드/종목명/메모내용 검색 가능."""
    q = request.args.get('q', '').strip()
    data = get_all_stock_memos(query=q or None, limit=500)
    return jsonify({'status': 'success', 'count': len(data), 'data': data})

@app.route('/api/sector-index', methods=['GET'])
def get_sector_index_api():
    """업종 일자별지수 API — KIS 실시간 조회"""
    try:
        iscd      = request.args.get('iscd', '0001')
        base_date = request.args.get('date')          # YYYYMMDD, 없으면 오늘

        sector_name  = SECTOR_NAMES.get(iscd, iscd)

        records = fetch_sector_index_daily(iscd=iscd, base_date=base_date)

        SIGN = {'1': '상한', '2': '상승', '3': '보합', '4': '하한', '5': '하락'}
        result = []
        for r in records:
            d = r.get('stck_bsop_date', '')
            result.append({
                'date':        f"{d[:4]}-{d[4:6]}-{d[6:]}",
                'sector_code': iscd,
                'sector_name': sector_name,
                'close':       r.get('bstp_nmix_prpr', ''),
                'open':        r.get('bstp_nmix_oprc', ''),
                'high':        r.get('bstp_nmix_hgpr', ''),
                'low':         r.get('bstp_nmix_lwpr', ''),
                'change':      r.get('bstp_nmix_prdy_vrss', ''),
                'change_sign': SIGN.get(r.get('prdy_vrss_sign', '3'), '보합'),
                'change_rate': r.get('bstp_nmix_prdy_ctrt', ''),
                'volume':      r.get('acml_vol', ''),
                'trade_amount':r.get('acml_tr_pbmn', ''),
                'vol_ratio':   r.get('acml_vol_rlim', ''),
                'psychology_index': r.get('invt_new_psdg', ''),
                'd20_dsrt':    r.get('d20_dsrt', ''),
            })

        if result:
            return jsonify({'status': 'success', 'count': len(result), 'data': result})

        # KIS API 결과 없음 → DB 캐시 폴백
        cached = get_sector_index_cached(iscd)
        if cached:
            app.logger.info(f"[sector-index] KIS API 결과 없음 → DB 캐시 {len(cached)}건 반환 (iscd={iscd})")
            return jsonify({'status': 'success', 'count': len(cached), 'data': cached, 'source': 'cache'})

        return jsonify({'status': 'success', 'count': 0, 'data': []})
    except Exception as e:
        # KIS API 오류 → DB 캐시 폴백
        try:
            cached = get_sector_index_cached(request.args.get('iscd', '0001'))
            if cached:
                app.logger.warning(f"[sector-index] KIS API 오류 → DB 캐시 반환: {e}")
                return jsonify({'status': 'success', 'count': len(cached), 'data': cached, 'source': 'cache'})
        except Exception:
            pass
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/investor-trend')
def investor_trend_view():
    """투자자별 매매동향 페이지"""
    return render_template('investor_trend.html', active_page='investor_trend')

@app.route('/api/investor-trend', methods=['GET'])
def get_investor_trend_api():
    """투자자별 프로그램 매매동향 데이터를 JSON으로 반환. KIS API 실패 시 DB 캐시 폴백."""
    exch_div = request.args.get('exch', 'J')
    mrkt_div = request.args.get('mrkt', '1')
    try:
        data = fetch_investor_trend(exch_div=exch_div, mrkt_div=mrkt_div)
        # 정상 응답
        if data.get('rt_cd') == '0' and data.get('output1'):
            return jsonify(data)
        # KIS API 오류 코드 → 캐시 폴백
        cached = get_investor_trend_cached(exch_div, mrkt_div)
        if cached:
            app.logger.warning(f"[investor-trend] KIS API 오류 → DB 캐시 {len(cached)}건 반환")
            return jsonify({'rt_cd': '0', 'output1': cached, 'source': 'cache'})
        return jsonify(data)
    except Exception as e:
        try:
            cached = get_investor_trend_cached(exch_div, mrkt_div)
            if cached:
                app.logger.warning(f"[investor-trend] KIS API 예외 → DB 캐시 반환: {e}")
                return jsonify({'rt_cd': '0', 'output1': cached, 'source': 'cache'})
        except Exception:
            pass
        return jsonify({"error": str(e)}), 500

@app.route('/api/investor-trend/history', methods=['GET'])
def get_investor_trend_history_api():
    """투자자별 매매동향 일별 이력 조회"""
    try:
        exch_div = request.args.get('exch', 'J')
        mrkt_div = request.args.get('mrkt', '1')
        limit_days = int(request.args.get('days', 30))
        data = get_investor_trend_history(exch_div=exch_div, mrkt_div=mrkt_div, limit_days=limit_days)
        return jsonify({"status": "success", "data": data})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/market-cap', methods=['GET'])
def get_market_cap_api():
    """일별 시가총액 데이터를 JSON 형식으로 반환합니다."""
    try:
        code           = request.args.get('code')
        limit_dates    = int(request.args.get('limit', 7))
        fid_input_iscd = request.args.get('iscd', 'combined')
        date           = request.args.get('date')
        data = get_market_cap_history(code=code, limit_dates=limit_dates, fid_input_iscd=fid_input_iscd, date=date)
        
        return jsonify({
            "status": "success",
            "count": len(data),
            "data": data
        })
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/market-cap/fetch', methods=['POST'])
def fetch_market_cap_api():
    """시가총액 데이터를 즉시 수집하도록 요청합니다."""
    try:
        data = request.get_json(silent=True) or {}
        div_cls_code = data.get('div_cls_code', '0')
        # 거래소(0001) + 코스닥(1001) 순차 수집 (각각 내부적으로 재시도됨)
        results = {}
        for iscd in ('0001', '1001'):
            results[iscd] = fetch_market_cap_ranking(mrkt_div_code='J', input_iscd=iscd, div_cls_code=div_cls_code)
        import time; time.sleep(1)

        failed = [iscd for iscd, ok in results.items() if not ok]
        if failed:
            return jsonify({
                "status": "error",
                "message": f"시가총액 데이터 수집 일부 실패 (실패: {', '.join(failed)})",
                "results": results
            }), 500

        return jsonify({
            "status": "success",
            "message": "시가총액 데이터 수집이 성공적으로 완료되었습니다.",
            "results": results
        })
    except Exception as e:
        return jsonify({
            "status": "error",
            "message": f"시가총액 데이터 수집 중 오류 발생: {str(e)}"
        }), 500

@app.route('/api/hts-top-view', methods=['GET'])
def get_hts_top_view_api():
    """HTS조회상위20종목 시간별 데이터를 JSON 형식으로 반환합니다."""
    try:
        date            = request.args.get('date')
        limit_snapshots = int(request.args.get('limit', 24))
        data = get_hts_top_view_history(date=date, limit_snapshots=limit_snapshots)

        return jsonify({
            "status": "success",
            "count": len(data),
            "data": data
        })
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/hts-top-view/fetch', methods=['POST'])
def fetch_hts_top_view_api():
    """HTS조회상위20종목 데이터를 즉시 수집하도록 요청합니다. (동기화 관리 페이지의 job_run_log에도 기록됨)"""
    try:
        ok = run_job_hts_top_view(trigger_type='manual')
        if not ok:
            return jsonify({
                "status": "error",
                "message": "HTS조회상위20종목 데이터 수집 실패"
            }), 500

        return jsonify({
            "status": "success",
            "message": "HTS조회상위20종목 데이터 수집이 성공적으로 완료되었습니다."
        })
    except Exception as e:
        return jsonify({
            "status": "error",
            "message": f"HTS조회상위20종목 데이터 수집 중 오류 발생: {str(e)}"
        }), 500

@app.route('/api/hts-top-view/cumulative', methods=['GET'])
def get_hts_top_view_cumulative_api():
    """HTS조회상위20종목 구간 누적 점수(스냅샷마다 20-순위점 합산)를 반환합니다."""
    try:
        date_from = request.args.get('date_from')
        date_to   = request.args.get('date_to')
        if not date_from or not date_to:
            return jsonify({"status": "error", "message": "date_from, date_to 파라미터가 필요합니다."}), 400
        data = get_hts_top_view_cumulative(date_from=date_from, date_to=date_to)

        return jsonify({
            "status": "success",
            "count": len(data),
            "data": data
        })
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/hts-top-view/daily-scores', methods=['GET'])
def get_hts_top_view_daily_scores_api():
    """HTS조회상위20종목 구간 내 날짜별 합산 점수(스냅샷마다 20-순위점, 날짜 단위 집계)를 반환합니다."""
    try:
        date_from = request.args.get('date_from')
        date_to   = request.args.get('date_to')
        if not date_from or not date_to:
            return jsonify({"status": "error", "message": "date_from, date_to 파라미터가 필요합니다."}), 400
        data = get_hts_top_view_daily_scores(date_from=date_from, date_to=date_to)

        return jsonify({
            "status": "success",
            "count": len(data),
            "data": data
        })
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/top-interest')
def top_interest_view():
    """관심종목등록 상위 페이지를 보여줍니다. (네이버 인기검색종목 대체 — robots.txt 크롤링 제한으로 KIS API 사용)"""
    return render_template('top_interest.html', active_page='top_interest')

@app.route('/api/top-interest', methods=['GET'])
def get_top_interest_api():
    """관심종목등록 상위 구간 데이터를 JSON 형식으로 반환합니다."""
    try:
        date_from = request.args.get('date_from')
        date_to   = request.args.get('date_to')
        if not date_from or not date_to:
            return jsonify({"status": "error", "message": "date_from, date_to 파라미터가 필요합니다."}), 400
        data = get_top_interest_range(date_from=date_from, date_to=date_to)

        return jsonify({
            "status": "success",
            "count": len(data),
            "data": data
        })
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/top-interest/fetch', methods=['POST'])
def fetch_top_interest_api():
    """관심종목등록 상위 데이터를 즉시 수집하도록 요청합니다. (동기화 관리 페이지의 job_run_log에도 기록됨)"""
    try:
        ok = run_job_top_interest(trigger_type='manual')
        if not ok:
            return jsonify({
                "status": "error",
                "message": "관심종목등록 상위 데이터 수집 실패"
            }), 500

        return jsonify({
            "status": "success",
            "message": "관심종목등록 상위 데이터 수집이 성공적으로 완료되었습니다."
        })
    except Exception as e:
        return jsonify({
            "status": "error",
            "message": f"관심종목등록 상위 데이터 수집 중 오류 발생: {str(e)}"
        }), 500

@app.route('/stock-investor')
def stock_investor_view():
    return render_template('stock_investor.html', active_page='stock_investor')

@app.route('/api/stock-investor', methods=['GET'])
def get_stock_investor_api():
    """시총 순위 + 투자자 순매수 합산 데이터 반환"""
    try:
        iscd = request.args.get('iscd', 'combined')
        date = request.args.get('date') or get_latest_market_cap_date(iscd)
        data = get_stock_investor_combined(date=date, fid_input_iscd=iscd)
        return jsonify({"status": "success", "date": date, "data": data})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/stock-investor/trend', methods=['GET'])
def get_stock_investor_trend_api():
    """특정 종목의 날짜별 투자자 순매수 추이 반환"""
    try:
        code = request.args.get('code', '').strip()
        if not code:
            return jsonify({"status": "error", "message": "종목코드 필요"}), 400
        data = get_stock_investor_trend(code)
        name = data[0]['name'] if data else code
        return jsonify({"status": "success", "code": code, "name": name, "data": data})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

_fetch_status = {}  # task_id → {status, message}

@app.route('/api/stock-investor/fetch', methods=['POST'])
def fetch_stock_investor_api():
    """시총 상위 종목의 투자자 데이터 백그라운드 수집 (즉시 202 반환)"""
    from datetime import datetime as dt
    import uuid
    req = request.get_json(silent=True) or {}
    iscd = req.get('iscd', 'combined')
    raw_date = req.get('date', '').strip()
    date_str = raw_date.replace('-', '') if raw_date else dt.now().strftime('%Y%m%d')
    date_db = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:]}"

    cap_rows = get_market_cap_history(limit_dates=1, fid_input_iscd=iscd, date=date_db)
    if not cap_rows:
        cap_rows = get_market_cap_history(limit_dates=1, fid_input_iscd=iscd)
    codes = [(r['code'], r['name']) for r in cap_rows]
    if not codes:
        return jsonify({"status": "error", "message": "시총 데이터 없음. 먼저 시총 데이터를 수집하세요."}), 400

    task_id = str(uuid.uuid4())[:8]
    _fetch_status[task_id] = {"status": "running", "message": f"{date_db} 기준 {len(codes)}개 종목 수집 중..."}

    def run():
        try:
            saved, first_error = fetch_stock_investor_daily(codes, date_str=date_str)
            if saved == 0:
                err = first_error or "API 응답 없음"
                _fetch_status[task_id] = {"status": "error", "message": f"저장된 종목 없음 — {err}"}
            else:
                _fetch_status[task_id] = {"status": "done", "message": f"{date_db} 기준 {saved}개 종목 저장 완료"}
        except Exception as e:
            _fetch_status[task_id] = {"status": "error", "message": str(e)}

    threading.Thread(target=run, daemon=True).start()
    return jsonify({"status": "started", "task_id": task_id, "message": f"{date_db} 기준 {len(codes)}개 종목 수집 시작"}), 202

@app.route('/api/stock-investor/fetch/status/<task_id>', methods=['GET'])
def fetch_stock_investor_status(task_id):
    result = _fetch_status.get(task_id, {"status": "unknown", "message": "작업을 찾을 수 없습니다."})
    return jsonify(result)

@app.route('/api/stock-investor/fetch/tasks', methods=['GET'])
def fetch_stock_investor_tasks():
    tasks = [{"task_id": k, **v} for k, v in reversed(list(_fetch_status.items()))]
    return jsonify(tasks)

@app.route('/api/debug/token', methods=['GET'])
def debug_token():
    """KIS 토큰 발급 상태 진단"""
    import requests as _req
    from app.config import Config as _C
    from app.utils.db_manager import get_api_token as _get_tok
    try:
        db_token = _get_tok('KIS')
        result = {
            "app_key_set": bool(_C.KIS_APP_KEY),
            "app_secret_set": bool(_C.KIS_APP_SECRET),
            "app_key_prefix": (_C.KIS_APP_KEY or '')[:8] + '...',
            "db_token_today": bool(db_token),
        }
        # 실제 토큰 발급 시도
        url = f"{_C.KIS_URL_BASE}/oauth2/tokenP"
        body = {"grant_type": "client_credentials",
                "appkey": _C.KIS_APP_KEY, "appsecret": _C.KIS_APP_SECRET}
        res = _req.post(url, headers={"content-type": "application/json"},
                        json=body, timeout=10)
        result["token_api_status"] = res.status_code
        result["token_api_response"] = res.text[:300]
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/debug/investor-dates', methods=['GET'])
def debug_investor_dates():
    """DB 상태 진단용 — investor + market_cap 날짜/건수 확인"""
    import sqlite3 as _sq
    from app.config import Config as _C
    try:
        conn = _sq.connect(_C.DB_NAME)
        conn.row_factory = _sq.Row
        cur = conn.cursor()

        cur.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
        tables = [r[0] for r in cur.fetchall()]

        investor = {}
        if 'stock_investor_daily' in tables:
            cur.execute("SELECT COUNT(*) as cnt FROM stock_investor_daily")
            investor['total'] = cur.fetchone()[0]
            cur.execute("SELECT DISTINCT date FROM stock_investor_daily ORDER BY date DESC LIMIT 10")
            investor['dates'] = [r[0] for r in cur.fetchall()]
            cur.execute("SELECT code, name, date, frgn_ntby_qty, orgn_ntby_qty FROM stock_investor_daily ORDER BY date DESC LIMIT 5")
            investor['samples'] = [dict(r) for r in cur.fetchall()]
        else:
            investor['error'] = 'stock_investor_daily 테이블 없음'

        mktcap = {}
        if 'stock_market_cap_daily' in tables:
            cur.execute("SELECT COUNT(*) as cnt FROM stock_market_cap_daily")
            mktcap['total'] = cur.fetchone()[0]
            cur.execute("SELECT DISTINCT date FROM stock_market_cap_daily ORDER BY date DESC LIMIT 10")
            mktcap['dates'] = [r[0] for r in cur.fetchall()]

        conn.close()
        return jsonify({"status": "ok", "db_path": _C.DB_NAME, "tables": tables,
                        "stock_investor_daily": investor, "stock_market_cap_daily": mktcap})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/stock-raw-data', methods=['GET'])
def get_stock_raw_data_api():
    """최근 수집된 주식 원본 데이터를 JSON 형식으로 반환합니다."""
    try:
        raw_data = get_latest_stock_raw_data(limit=50)
        
        # JSON 문자열로 저장된 데이터를 다시 딕셔너리로 파싱
        for row in raw_data:
            if 'raw_json' in row and row['raw_json']:
                try:
                    row['raw_data'] = json.loads(row['raw_json'])
                except json.JSONDecodeError:
                    row['raw_data'] = []
                
                # 원본 텍스트는 전송하지 않음 (용량 절약)
                del row['raw_json']
                
        return jsonify({
            "status": "success",
            "count": len(raw_data),
            "data": raw_data
        })
    except Exception as e:
        return jsonify({
            "status": "error",
            "message": str(e)
        }), 500

@app.route('/api/stock-raw-data/cleanup', methods=['POST'])
def cleanup_stock_raw_data_api():
    """오래된 주식 원본 데이터(stock_raw_data)를 오래된 순으로 count건 삭제합니다."""
    try:
        body = request.get_json(silent=True) or {}
        count = body.get('count', 1000)
        try:
            count = int(count)
        except (TypeError, ValueError):
            return jsonify({"status": "error", "message": "count는 숫자여야 합니다."}), 400

        if count <= 0:
            return jsonify({"status": "error", "message": "count는 1 이상이어야 합니다."}), 400
        if count > 100000:
            return jsonify({"status": "error", "message": "한 번에 최대 100,000건까지만 삭제할 수 있습니다."}), 400

        deleted = delete_oldest_stock_raw_data(count)
        return jsonify({"status": "success", "deleted": deleted})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/db-stats', methods=['GET'])
def get_db_stats_api():
    """DB 파일 크기 및 테이블별 행 수/컬럼/추정 용량을 반환합니다."""
    try:
        stats = get_db_stats()
        return jsonify({"status": "success", **stats})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/alerts', methods=['GET'])
def get_alerts():
    """최근 발생한 코인 알림을 JSON 형식으로 반환합니다."""
    try:
        alerts = get_latest_alerts(limit=1000)
        return jsonify({
            "status": "success",
            "count": len(alerts),
            "data": alerts
        })
    except Exception as e:
        return jsonify({
            "status": "error",
            "message": str(e)
        }), 500

@app.route('/alerts/<int:alert_id>', methods=['DELETE'])
def delete_alert_api(alert_id):
    """특정 코인 알림을 삭제합니다."""
    try:
        delete_alert(alert_id)
        return jsonify({"status": "success", "message": f"Coin alert {alert_id} deleted."})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/stock-alerts', methods=['GET'])
def get_stock_alerts():
    """최근 발생한 주식 알림을 JSON 형식으로 반환합니다."""
    try:
        alerts = get_latest_stock_alerts(limit=1000)
        return jsonify({
            "status": "success",
            "count": len(alerts),
            "data": alerts
        })
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/stock-alerts/<int:alert_id>', methods=['DELETE'])
def delete_stock_alert_api(alert_id):
    """특정 주식 알림을 삭제합니다."""
    try:
        delete_stock_alert(alert_id)
        return jsonify({"status": "success", "message": f"Stock alert {alert_id} deleted."})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

# ──────────────────────────────────────────────
# 데이터 동기화 API (로컬 → 서버 push)
# ──────────────────────────────────────────────
_sync_sessions = {}   # token → session info
_sync_blocked  = False

SYNC_TABLE_MAP = {
    'stock_market_cap_daily':      sync_upsert_market_cap,
    'stock_investor_daily':        sync_upsert_investor_daily,
    'investor_trend_daily':        sync_upsert_investor_trend,
    'sector_index_daily':          sync_upsert_sector_index,
    'sector_stocks_daily':         sync_upsert_sector_stocks,
    'stock_hts_top_view_hourly':   sync_upsert_hts_top_view,
    'stock_top_interest_daily':    sync_upsert_top_interest,
    'stock_top_gainers_hourly':    sync_upsert_top_gainers,
}

def _get_client_ip():
    return request.headers.get('X-Forwarded-For', request.remote_addr).split(',')[0].strip()

def _verify_sync_token(token):
    """토큰 유효성 검사 (만료 체크)"""
    sess = _sync_sessions.get(token)
    if not sess:
        return None
    if _time.time() > sess['expires_at']:
        _sync_sessions.pop(token, None)
        return None
    return sess

@app.route('/api/sync/start', methods=['POST'])
def sync_start():
    """동기화 세션 시작 — IP 검증 후 토큰 발급"""
    global _sync_blocked
    if _sync_blocked:
        return jsonify({"error": "동기화가 차단되어 있습니다."}), 403
    ip = _get_client_ip()
    allowed = Config.SYNC_ALLOWED_IPS
    if allowed and ip not in allowed:
        app.logger.warning(f"[SYNC] 차단된 IP 접근 시도: {ip}")
        return jsonify({"error": f"허용되지 않은 IP: {ip}"}), 403
    token = secrets.token_hex(20)
    now = _dt.now()
    _sync_sessions[token] = {
        "ip": ip, "token": token,
        "started_at": now.strftime('%Y-%m-%d %H:%M:%S'),
        "expires_at": _time.time() + Config.SYNC_TOKEN_TTL,
        "tables_synced": [], "rows_synced": 0, "status": "active"
    }
    app.logger.info(f"[SYNC] 세션 시작 | IP: {ip} | token: {token[:8]}...")
    return jsonify({"token": token, "expires_in": Config.SYNC_TOKEN_TTL,
                    "message": "동기화 세션이 시작되었습니다."})

@app.route('/api/sync/push', methods=['POST'])
def sync_push():
    """데이터 수신 및 upsert"""
    token = request.headers.get('X-Sync-Token', '')
    sess = _verify_sync_token(token)
    if not sess:
        return jsonify({"error": "유효하지 않거나 만료된 토큰"}), 401
    body = request.get_json(silent=True) or {}
    table = body.get('table', '')
    rows  = body.get('rows', [])
    if table not in SYNC_TABLE_MAP:
        return jsonify({"error": f"지원하지 않는 테이블: {table}"}), 400
    if not rows:
        return jsonify({"status": "ok", "saved": 0})
    try:
        saved = SYNC_TABLE_MAP[table](rows)
        sess['tables_synced'].append(table)
        sess['rows_synced'] += saved
        app.logger.info(f"[SYNC] {table} {saved}건 저장 | IP: {sess['ip']}")
        return jsonify({"status": "ok", "table": table, "saved": saved})
    except Exception as e:
        app.logger.error(f"[SYNC] {table} 저장 오류: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/sync/end', methods=['POST'])
def sync_end():
    """동기화 세션 종료"""
    token = request.headers.get('X-Sync-Token', '')
    sess = _sync_sessions.get(token)
    if not sess:
        return jsonify({"error": "세션 없음"}), 404
    sess['status'] = 'completed'
    sess['ended_at'] = _dt.now().strftime('%Y-%m-%d %H:%M:%S')
    sess['expires_at'] = 0  # 즉시 만료
    app.logger.info(f"[SYNC] 세션 종료 | IP: {sess['ip']} | "
                    f"테이블: {sess['tables_synced']} | 총 {sess['rows_synced']}건")
    return jsonify({"status": "ok", "summary": {
        "tables": sess['tables_synced'], "rows": sess['rows_synced'],
        "started_at": sess['started_at'], "ended_at": sess['ended_at']
    }})

@app.route('/api/sync/sessions', methods=['GET'])
def sync_sessions():
    """세션 이력 조회"""
    return jsonify(list(_sync_sessions.values()))

@app.route('/api/sync/block', methods=['POST'])
def sync_block():
    global _sync_blocked
    _sync_blocked = True
    app.logger.warning("[SYNC] 동기화 차단 활성화")
    return jsonify({"status": "blocked"})

@app.route('/api/sync/unblock', methods=['POST'])
def sync_unblock():
    global _sync_blocked
    _sync_blocked = False
    app.logger.info("[SYNC] 동기화 차단 해제")
    return jsonify({"status": "unblocked"})

@app.route('/api/sync/status', methods=['GET'])
def sync_status():
    return jsonify({
        "blocked": _sync_blocked,
        "active_sessions": sum(1 for s in _sync_sessions.values() if s['status'] == 'active'),
        "allowed_ips": Config.SYNC_ALLOWED_IPS
    })

# ──────────────────────────────────────────────
# 스케줄링 작업 처리 로그 + 수동실행 ("동기화 관리" 페이지)
# ──────────────────────────────────────────────

JOB_RUNNERS = {
    'hts_top_view': lambda: run_job_hts_top_view(trigger_type='manual'),
    'investor_trend': lambda: run_job_investor_trend(trigger_type='manual'),
    'sector_index': lambda: run_job_sector_index(trigger_type='manual'),
    'market_cap_signal_score': lambda: run_job_market_cap_and_signal_score(trigger_type='manual'),
    'market_cap_morning_backup': lambda: run_job_market_cap_morning_backup(trigger_type='manual'),
    'remote_sync': lambda: run_job_remote_sync(trigger_type='manual'),
    'top_interest': lambda: run_job_top_interest(trigger_type='manual'),
    'top_gainers_sync': lambda: run_job_top_gainers_sync(trigger_type='manual'),
}

@app.route('/api/job-log', methods=['GET'])
def get_job_log_api():
    """스케줄링 작업 실행 이력을 반환합니다 (기본 최근 7일)."""
    try:
        days = int(request.args.get('days', 7))
        data = get_job_run_log(days=days)
        return jsonify({"status": "success", "count": len(data), "data": data})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/job-log/run/<job_name>', methods=['POST'])
def run_job_manual_api(job_name):
    """지정한 작업을 즉시 실행합니다 (동기화 관리 페이지의 수동실행 버튼용). 실제 API 호출/DB 저장이 일어납니다."""
    runner = JOB_RUNNERS.get(job_name)
    if not runner:
        return jsonify({"status": "error", "message": f"알 수 없는 작업: {job_name}"}), 400
    try:
        ok = runner()
        return jsonify({"status": "success" if ok else "error", "job_name": job_name, "success": ok})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

# ──────────────────────────────────────────────
# Sync export — 로컬 DB 데이터를 서버로 전송하기 위한 원시 데이터 조회
# ──────────────────────────────────────────────

@app.route('/api/sync/export/market-cap', methods=['GET'])
def sync_export_market_cap():
    limit = int(request.args.get('limit', 30))
    data = get_market_cap_history(limit_dates=limit, fid_input_iscd='combined')
    return jsonify({"status": "success", "count": len(data), "data": data})

@app.route('/api/sync/export/stock-investor', methods=['GET'])
def sync_export_stock_investor():
    limit = int(request.args.get('limit', 30))
    data = get_stock_investor_raw(limit_dates=limit)
    return jsonify({"status": "success", "count": len(data), "data": data})

@app.route('/api/sync/export/investor-trend', methods=['GET'])
def sync_export_investor_trend():
    exch = request.args.get('exch', 'J')
    mrkt = request.args.get('mrkt', '1')
    days = int(request.args.get('days', 30))
    data = get_investor_trend_history(exch_div=exch, mrkt_div=mrkt, limit_days=days)
    return jsonify({"status": "success", "count": len(data), "data": data})

@app.route('/api/sync/export/hts-top-view', methods=['GET'])
def sync_export_hts_top_view():
    limit = int(request.args.get('limit', 7))
    data = get_hts_top_view_export(limit_days=limit)
    return jsonify({"status": "success", "count": len(data), "data": data})

@app.route('/api/sync/export/top-interest', methods=['GET'])
def sync_export_top_interest():
    limit = int(request.args.get('limit', 7))
    data = get_top_interest_export(limit_days=limit)
    return jsonify({"status": "success", "count": len(data), "data": data})

@app.route('/api/sync/export/top-gainers', methods=['GET'])
def sync_export_top_gainers():
    limit = int(request.args.get('limit', 7))
    data = get_top_gainers_export(limit_days=limit)
    return jsonify({"status": "success", "count": len(data), "data": data})

# sector_index 캐시 폴백
@app.route('/api/sector-index/cached', methods=['GET'])
def get_sector_index_cached_api():
    iscd  = request.args.get('iscd', '0001')
    limit = int(request.args.get('limit', 30))
    data = get_sector_index_cached(iscd, limit=limit)
    return jsonify({'status': 'success', 'count': len(data), 'data': data, 'source': 'cache'})

# investor_trend 캐시 폴백
@app.route('/api/investor-trend/cached', methods=['GET'])
def get_investor_trend_cached_api():
    exch = request.args.get('exch', 'J')
    mrkt = request.args.get('mrkt', '1')
    data = get_investor_trend_cached(exch, mrkt)
    return jsonify({'status': 'success', 'data': data, 'source': 'cache'})

@app.route('/sync-admin')
def sync_admin_view():
    """동기화 관리 페이지"""
    return render_template('sync_admin.html', active_page='sync_admin')

@app.route('/history')
def history_view():
    """프로젝트 히스토리 페이지"""
    return render_template('history.html', active_page='history')

@app.route('/investor-ranking')
def investor_ranking_view():
    return render_template('investor_ranking.html', active_page='investor_ranking')

@app.route('/api/investor-ranking')
def investor_ranking_api():
    try:
        date_from  = request.args.get('date_from', '')
        date_to    = request.args.get('date_to', '')
        investor   = request.args.get('investor', 'orgn')   # frgn | orgn
        direction  = request.args.get('direction', 'buy')   # buy | sell
        top_n      = int(request.args.get('top_n', 20))
        if not date_from or not date_to:
            return jsonify({'status': 'error', 'message': 'date_from, date_to 필요'}), 400
        data = get_investor_ranking(date_from, date_to, investor, direction, top_n)
        return jsonify({'status': 'success', **data})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/api/investor-cross')
def investor_cross_api():
    try:
        date_from = request.args.get('date_from', '')
        date_to   = request.args.get('date_to', '')
        top_n     = int(request.args.get('top_n', 60))
        if not date_from or not date_to:
            return jsonify({'status': 'error', 'message': 'date_from, date_to 필요'}), 400
        data = get_investor_cross_distribution(date_from, date_to, top_n)
        return jsonify({'status': 'success', 'data': data})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/api/investor-distribution')
def investor_distribution_api():
    try:
        date_from = request.args.get('date_from', '')
        date_to   = request.args.get('date_to', '')
        investor  = request.args.get('investor', 'frgn')
        top_n     = int(request.args.get('top_n', 40))
        if not date_from or not date_to:
            return jsonify({'status': 'error', 'message': 'date_from, date_to 필요'}), 400
        data = get_investor_distribution(date_from, date_to, investor, top_n)
        return jsonify({'status': 'success', 'data': data})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/volume-ratio')
def volume_ratio_view():
    """종목별 거래량 배수(당일 vs 평균) 페이지"""
    return render_template('volume_ratio.html', active_page='volume_ratio')

@app.route('/api/volume-ratio')
def volume_ratio_api():
    try:
        date         = request.args.get('date')
        avg_days     = int(request.args.get('avg_days', 20))
        top_n        = int(request.args.get('top_n', 50))
        fid_input_iscd = request.args.get('market', 'combined')
        data = get_volume_ratio_batch(date=date, avg_days=avg_days, fid_input_iscd=fid_input_iscd)
        return jsonify({'status': 'success', 'date': (data[0]['date'] if data else date), 'data': data[:top_n]})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/api/volume-ratio/status')
def volume_ratio_status_api():
    try:
        status = get_volume_collection_status()
        return jsonify({'status': 'success', **status})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

# ──────────────────────────────────────────────
# 순위분석 신규 API 미리보기 (Signal Score 반영 전 데이터 확인용, DB 저장 없음)
# ──────────────────────────────────────────────
RANKING_PREVIEW_TYPES = {
    'volume_rank': {
        'name': '거래량순위',
        'path': '/uapi/domestic-stock/v1/quotations/volume-rank',
        'tr_id': 'FHPST01710000',
        'params': {
            'fid_cond_mrkt_div_code': 'J', 'fid_cond_scr_div_code': '20171',
            'fid_input_iscd': '0000', 'fid_div_cls_code': '0', 'fid_blng_cls_code': '0',
            'fid_trgt_cls_code': '111111111', 'fid_trgt_exls_cls_code': '0000000000',
            'fid_input_price_1': '', 'fid_input_price_2': '', 'fid_vol_cnt': '', 'fid_input_date_1': '',
        },
    },
    'volume_power': {
        'name': '체결강도 상위',
        'path': '/uapi/domestic-stock/v1/ranking/volume-power',
        'tr_id': 'FHPST01680000',
        'params': {
            'fid_trgt_exls_cls_code': '0', 'fid_cond_mrkt_div_code': 'J', 'fid_cond_scr_div_code': '20168',
            'fid_input_iscd': '0000', 'fid_div_cls_code': '0',
            'fid_input_price_1': '', 'fid_input_price_2': '', 'fid_vol_cnt': '', 'fid_trgt_cls_code': '0',
        },
    },
    'disparity': {
        'name': '이격도 순위',
        'path': '/uapi/domestic-stock/v1/ranking/disparity',
        'tr_id': 'FHPST01780000',
        'params': {
            'fid_input_price_2': '', 'fid_cond_mrkt_div_code': 'J', 'fid_cond_scr_div_code': '20178',
            'fid_div_cls_code': '0', 'fid_rank_sort_cls_code': '0', 'fid_hour_cls_code': '20',
            'fid_input_iscd': '0000', 'fid_trgt_cls_code': '0', 'fid_trgt_exls_cls_code': '0',
            'fid_input_price_1': '', 'fid_vol_cnt': '',
        },
    },
    'short_sale': {
        'name': '공매도 상위종목',
        'path': '/uapi/domestic-stock/v1/ranking/short-sale',
        'tr_id': 'FHPST04820000',
        'params': {
            'fid_aply_rang_vol': '0', 'fid_cond_mrkt_div_code': 'J', 'fid_cond_scr_div_code': '20482',
            'fid_input_iscd': '0000', 'fid_period_div_code': 'D', 'fid_input_cnt_1': '0',
            'fid_trgt_exls_cls_code': '', 'fid_trgt_cls_code': '',
            'fid_aply_rang_prc_1': '', 'fid_aply_rang_prc_2': '',
        },
    },
    'bulk_trans_num': {
        'name': '대량체결건수 상위',
        'path': '/uapi/domestic-stock/v1/ranking/bulk-trans-num',
        'tr_id': 'FHKST190900C0',
        'params': {
            'fid_aply_rang_prc_2': '', 'fid_cond_mrkt_div_code': 'J', 'fid_cond_scr_div_code': '11909',
            'fid_input_iscd': '0000', 'fid_rank_sort_cls_code': '0', 'fid_div_cls_code': '0',
            'fid_input_price_1': '', 'fid_aply_rang_prc_1': '', 'fid_input_iscd_2': '',
            'fid_trgt_exls_cls_code': '0', 'fid_trgt_cls_code': '0', 'fid_vol_cnt': '',
        },
    },
    'exp_trans_updown': {
        'name': '예상체결 상승/하락상위',
        'path': '/uapi/domestic-stock/v1/ranking/exp-trans-updown',
        'tr_id': 'FHPST01820000',
        'params': {
            'fid_rank_sort_cls_code': '0', 'fid_cond_mrkt_div_code': 'J', 'fid_cond_scr_div_code': '20182',
            'fid_input_iscd': '0000', 'fid_div_cls_code': '0', 'fid_aply_rang_prc_1': '',
            'fid_vol_cnt': '', 'fid_pbmn': '', 'fid_blng_cls_code': '0', 'fid_mkop_cls_code': '0',
        },
    },
    'hts_top_view': {
        'name': 'HTS조회상위20종목',
        'path': '/uapi/domestic-stock/v1/ranking/hts-top-view',
        'tr_id': 'HHMCM000100C0',
        'params': {},
    },
}

@app.route('/signal-score-preview')
def signal_score_preview_view():
    """Signal Score 등급/Slack 발송 예정 건수를 미리 확인하는 페이지 (저장·발송 없이 계산만)"""
    return render_template('signal_score_preview.html', active_page='signal_score_preview')

@app.route('/api/signal-score/preview', methods=['GET'])
def signal_score_preview_api():
    """Signal Score를 DB 저장·Slack 발송 없이 계산만 해서 등급별 건수/명단을 미리 보여줍니다."""
    try:
        scores = get_signal_score_batch(save=False)
        grade_counts = {'A': 0, 'B': 0, 'C': 0, '제외': 0}
        rows = []
        for s in scores:
            grade_counts[s['grade']] = grade_counts.get(s['grade'], 0) + 1
            rows.append({
                'code': s['code'], 'name': s['name'], 'total': s['total'], 'grade': s['grade'],
                'momentum_score': s['momentum_score'], 'supply_demand_score': s['supply_demand_score'],
                'rank_stability_score': s['rank_stability_score'],
                'market_environment_score': s['market_environment_score'],
                'risk_penalty_score': s['risk_penalty_score'],
                'hts_top_view_bonus_score': s.get('hts_top_view_bonus_score', 0),
                'top_interest_bonus_score': s.get('top_interest_bonus_score', 0),
                'detail': s.get('detail', {}),
            })

        return jsonify({
            "status": "success",
            "date": scores[0]['date'] if scores else None,
            "total": len(rows),
            "grade_counts": grade_counts,
            "data": rows
        })
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/signal-score-history')
def signal_score_history_view():
    """Signal Score 이력 페이지 (일별 스냅샷 조회 + 날짜별 추이 매트릭스)"""
    return render_template('signal_score_history.html', active_page='signal_score_history')

@app.route('/api/signal-score/history', methods=['GET'])
def signal_score_history_api():
    """특정 날짜(미지정 시 최신 저장일)의 Signal Score 스냅샷을 반환합니다."""
    try:
        date = request.args.get('date')
        grade = request.args.get('grade')
        data = get_signal_score_history(date=date, grade=grade, limit=200)
        return jsonify({
            "status": "success",
            "date": data[0]['date'] if data else date,
            "count": len(data),
            "data": data
        })
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/signal-score/range', methods=['GET'])
def signal_score_range_api():
    """구간(date_from~date_to) 내 Signal Score 저장 이력을 반환합니다 (날짜별 추이 매트릭스용)."""
    try:
        date_from = request.args.get('date_from')
        date_to   = request.args.get('date_to')
        if not date_from or not date_to:
            return jsonify({"status": "error", "message": "date_from, date_to 파라미터가 필요합니다."}), 400
        data = get_signal_score_range(date_from=date_from, date_to=date_to)
        return jsonify({"status": "success", "count": len(data), "data": data})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/ranking-preview')
def ranking_preview_view():
    """순위분석 신규 API 미리보기 페이지 (Signal Score 반영 전 검토용)"""
    types = [{'key': k, 'name': v['name']} for k, v in RANKING_PREVIEW_TYPES.items()]
    return render_template('ranking_preview.html', ranking_types=types, active_page='ranking_preview')

@app.route('/api/ranking-preview', methods=['GET'])
def ranking_preview_api():
    rtype = request.args.get('type', 'volume_rank')
    cfg = RANKING_PREVIEW_TYPES.get(rtype)
    if not cfg:
        return jsonify({'status': 'error', 'message': f'알 수 없는 타입: {rtype}'}), 400
    result = fetch_ranking_preview(cfg['path'], cfg['tr_id'], dict(cfg['params']))
    if 'error' in result:
        return jsonify({'status': 'error', 'message': result['error']}), 500
    return jsonify({
        'status': 'success', 'type': rtype, 'name': cfg['name'],
        'count': len(result['output']), 'data': result['output']
    })

# ──────────────────────────────────────────────
# 상승률(등락률) 순위 — 실시간 조회 전용, DB 저장 없음
# ──────────────────────────────────────────────
TOP_GAINERS_MARKETS = {'all': '0000', 'kospi': '0001', 'kosdaq': '1001'}

@app.route('/top-gainers')
def top_gainers_view():
    """상승률/하락률 상위 종목 페이지 (실시간 조회, DB 미저장)"""
    return render_template('top_gainers.html', active_page='top_gainers')

TOP_GAINERS_SORTS = {'0', '1', '2', '3', '4'}  # 0:상승율순 1:하락율순 2:시가대비상승율 3:시가대비하락율 4:변동율

@app.route('/api/top-gainers', methods=['GET'])
def top_gainers_api():
    market = request.args.get('market', 'all')
    sort = request.args.get('sort', '0')
    if sort not in TOP_GAINERS_SORTS:
        sort = '0'

    # "전체"는 KIS fid_input_iscd="0000"이 30건 하드캡+연속조회 미지원이라
    # 코스피+코스닥을 각각 조회해서 합치는 방식으로 최대 60건까지 확보한다.
    if market == 'all':
        result = fetch_fluctuation_ranking_combined(rank_sort_cls_code=sort)
    else:
        iscd = TOP_GAINERS_MARKETS.get(market, '0001')
        result = fetch_fluctuation_ranking(input_iscd=iscd, rank_sort_cls_code=sort, max_count=30)
    if 'error' in result:
        return jsonify({'status': 'error', 'message': result['error']}), 500

    output = result['output']
    return jsonify({
        'status': 'success', 'market': market, 'sort': sort,
        'count': len(output), 'data': output
    })

@app.route('/api/top-gainers/fetch', methods=['POST'])
def top_gainers_fetch_api():
    """상승률 순위 스냅샷 즉시 수집 (자동 스케줄: 평일 9:10/12:10/15:10/18:10)"""
    hour = _dt.now().hour
    ok = run_job_top_gainers(hour, trigger_type='manual')
    if ok:
        return jsonify({'status': 'success', 'message': f'{hour}시 스냅샷 수집 완료'})
    return jsonify({'status': 'error', 'message': '수집 실패 (로그 확인)'}), 500

@app.route('/api/top-gainers/history', methods=['GET'])
def top_gainers_history_api():
    """저장된 상승률 순위 스냅샷 조회 (date+hour 지정 시 해당 건, 미지정 시 최신 스냅샷)"""
    date = request.args.get('date')
    hour = request.args.get('hour')
    hour = int(hour) if hour not in (None, '') else None
    data = get_top_gainers_history(date=date, hour=hour)
    return jsonify({'status': 'success', 'count': len(data), 'data': data})

@app.route('/api/top-gainers/history/dates', methods=['GET'])
def top_gainers_history_dates_api():
    """상승률 순위 스냅샷이 저장된 (date, hour) 목록 (최신순)"""
    limit = int(request.args.get('limit', 60))
    data = get_top_gainers_snapshot_dates(limit=limit)
    return jsonify({'status': 'success', 'count': len(data), 'data': data})

@app.route('/api/top-gainers/history/range', methods=['GET'])
def top_gainers_history_range_api():
    """상승률 순위 구간(date_from~date_to) 추이 조회 — 날짜별 종목 매트릭스용 (그날 최고 순위 1건으로 집계)"""
    date_from = request.args.get('date_from')
    date_to = request.args.get('date_to')
    if not date_from or not date_to:
        return jsonify({'status': 'error', 'message': 'date_from, date_to 파라미터가 필요합니다.'}), 400
    data = get_top_gainers_range(date_from=date_from, date_to=date_to)
    return jsonify({'status': 'success', 'count': len(data), 'data': data})

@app.route('/api/history/git-log', methods=['GET'])
def git_log_api():
    """git 커밋 로그 반환"""
    try:
        limit = int(request.args.get('limit', 60))
        result = subprocess.run(
            ['git', 'log', f'-{limit}', '--pretty=format:%H|%h|%ad|%an|%s', '--date=format:%Y-%m-%d %H:%M'],
            capture_output=True, text=True, encoding='utf-8',
            cwd=os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        )
        commits = []
        for line in result.stdout.strip().splitlines():
            parts = line.split('|', 4)
            if len(parts) == 5:
                commits.append({
                    'hash': parts[0], 'short': parts[1],
                    'date': parts[2], 'author': parts[3], 'message': parts[4]
                })
        return jsonify({'status': 'success', 'commits': commits})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

def run_server(use_reloader=False):
    # threaded=True: 수동실행(job-log/run) 등 오래 걸리는 요청이 다른 페이지 응답을 막지 않도록.
    app.run(host=Config.API_HOST, port=Config.API_PORT, debug=Config.DEBUG, use_reloader=use_reloader, threaded=True)

if __name__ == '__main__':
    run_server(use_reloader=True)
