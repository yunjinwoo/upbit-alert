from flask import Flask, jsonify, render_template, request
from flask_cors import CORS
from werkzeug.middleware.proxy_fix import ProxyFix
from app.utils.db_manager import (
    get_latest_alerts, delete_alert,
    get_latest_stock_alerts, delete_stock_alert,
    get_latest_stock_raw_data, get_market_cap_history,
    get_investor_trend_history,
    get_stock_investor_combined, get_latest_market_cap_date,
    get_stock_investor_trend
)
from app.core.stock_monitor import fetch_market_cap_ranking, fetch_investor_trend, fetch_sector_index_daily, fetch_stock_investor_daily
from app.config import Config
import json
import os

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

        return jsonify({'status': 'success', 'count': len(result), 'data': result})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/investor-trend')
def investor_trend_view():
    """투자자별 매매동향 페이지"""
    return render_template('investor_trend.html')

@app.route('/api/investor-trend', methods=['GET'])
def get_investor_trend_api():
    """투자자별 프로그램 매매동향 데이터를 JSON으로 반환"""
    try:
        exch_div = request.args.get('exch', 'J')
        mrkt_div = request.args.get('mrkt', '1')
        data = fetch_investor_trend(exch_div=exch_div, mrkt_div=mrkt_div)
        return jsonify(data)
    except Exception as e:
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

@app.route('/api/stock-investor/fetch', methods=['POST'])
def fetch_stock_investor_api():
    """시총 상위 종목의 투자자 데이터 즉시 수집"""
    try:
        from datetime import datetime as dt
        req = request.get_json(silent=True) or {}
        iscd = req.get('iscd', 'combined')
        # 날짜: YYYY-MM-DD → YYYYMMDD 변환, 없으면 오늘
        raw_date = req.get('date', '').strip()
        date_str = raw_date.replace('-', '') if raw_date else dt.now().strftime('%Y%m%d')
        date_db = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:]}"  # DB 조회용 YYYY-MM-DD

        # 해당 날짜의 시총 코드 조회, 없으면 최신 날짜 코드 사용
        cap_rows = get_market_cap_history(limit_dates=1, fid_input_iscd=iscd, date=date_db)
        if not cap_rows:
            cap_rows = get_market_cap_history(limit_dates=1, fid_input_iscd=iscd)
        codes = [(r['code'], r['name']) for r in cap_rows]
        if not codes:
            return jsonify({"status": "error", "message": "시총 데이터 없음. 먼저 시총 데이터를 수집하세요."}), 400
        fetch_stock_investor_daily(codes, date_str=date_str)
        return jsonify({"status": "success", "message": f"{date_db} 기준 {len(codes)}개 종목 투자자 데이터 수집 완료"})
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

def run_server(use_reloader=False):
    app.run(host=Config.API_HOST, port=Config.API_PORT, debug=Config.DEBUG, use_reloader=use_reloader)

if __name__ == '__main__':
    run_server(use_reloader=True)
