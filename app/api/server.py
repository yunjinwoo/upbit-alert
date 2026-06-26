from flask import Flask, jsonify, render_template, request
from flask_cors import CORS
from werkzeug.middleware.proxy_fix import ProxyFix
from app.utils.db_manager import (
    get_latest_alerts, delete_alert,
    get_latest_stock_alerts, delete_stock_alert,
    get_latest_stock_raw_data, get_market_cap_history,
    get_investor_trend_history,
    get_stock_investor_combined, get_latest_market_cap_date,
    get_stock_investor_trend,
    sync_upsert_market_cap, sync_upsert_investor_daily,
    sync_upsert_investor_trend, sync_upsert_sector_index,
    get_sector_index_cached, get_investor_trend_cached,
    get_stock_investor_raw,
    get_investor_ranking,
    get_investor_distribution,
    get_investor_cross_distribution
)
from app.core.stock_monitor import fetch_market_cap_ranking, fetch_investor_trend, fetch_sector_index_daily, fetch_stock_investor_daily
from app.config import Config
import json
import os
import threading
import secrets
import subprocess
import time as _time
from datetime import datetime as _dt

app = Flask(__name__, template_folder='../../templates')
app.config['APPLICATION_ROOT'] = Config.APP_ROOT
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)
CORS(app)

@app.route('/')
def index():
    """대시보드 메인 페이지를 보여줍니다."""
    return render_template('index.html')

@app.route('/raw-data')
def raw_data_view():
    """주식 원본 데이터 확인 페이지를 보여줍니다."""
    return render_template('raw_data.html')

@app.route('/market-cap')
def market_cap_view():
    """일별 시가총액 추이 페이지를 보여줍니다."""
    return render_template('market_cap.html')

@app.route('/sector-index')
def sector_index_view():
    """업종 일자별지수 조회 페이지"""
    return render_template('sector_index.html')

@app.route('/api/sector-index', methods=['GET'])
def get_sector_index_api():
    """업종 일자별지수 API — KIS 실시간 조회"""
    try:
        iscd      = request.args.get('iscd', '0001')
        base_date = request.args.get('date')          # YYYYMMDD, 없으면 오늘

        SECTOR_NAMES = {'0001': '코스피', '1001': '코스닥', '2001': '코스피200'}
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
                'net_buy':     r.get('invt_new_psdg', ''),
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
    return render_template('investor_trend.html')

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
        # 거래소(0001) + 코스닥(1001) 순차 수집
        for iscd in ('0001', '1001'):
            fetch_market_cap_ranking(mrkt_div_code='J', input_iscd=iscd, div_cls_code=div_cls_code)
        import time; time.sleep(1)
        return jsonify({
            "status": "success",
            "message": "시가총액 데이터 수집이 성공적으로 완료되었습니다."
        })
    except Exception as e:
        return jsonify({
            "status": "error",
            "message": f"시가총액 데이터 수집 중 오류 발생: {str(e)}"
        }), 500

@app.route('/stock-investor')
def stock_investor_view():
    return render_template('stock_investor.html')

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
    'stock_market_cap_daily':  sync_upsert_market_cap,
    'stock_investor_daily':    sync_upsert_investor_daily,
    'investor_trend_daily':    sync_upsert_investor_trend,
    'sector_index_daily':      sync_upsert_sector_index,
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

# sector_index 캐시 폴백
@app.route('/api/sector-index/cached', methods=['GET'])
def get_sector_index_cached_api():
    iscd = request.args.get('iscd', '0001')
    data = get_sector_index_cached(iscd)
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
    return render_template('sync_admin.html')

@app.route('/history')
def history_view():
    """프로젝트 히스토리 페이지"""
    return render_template('history.html')

@app.route('/investor-ranking')
def investor_ranking_view():
    return render_template('investor_ranking.html')

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
    app.run(host=Config.API_HOST, port=Config.API_PORT, debug=Config.DEBUG, use_reloader=use_reloader)

if __name__ == '__main__':
    run_server(use_reloader=True)
