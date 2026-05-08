from flask import Flask, jsonify, render_template, request
from flask_cors import CORS
from werkzeug.middleware.proxy_fix import ProxyFix
from app.utils.db_manager import (
    get_latest_alerts, delete_alert, 
    get_latest_stock_alerts, delete_stock_alert,
    get_latest_stock_raw_data, get_market_cap_history
)
from app.core.stock_monitor import fetch_market_cap_ranking
from app.config import Config
import json
import os

app = Flask(__name__, template_folder='../../templates')
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

@app.route('/api/market-cap', methods=['GET'])
def get_market_cap_api():
    """일별 시가총액 데이터를 JSON 형식으로 반환합니다."""
    try:
        code = request.args.get('code')
        limit_dates = int(request.args.get('limit', 7))
        data = get_market_cap_history(code=code, limit_dates=limit_dates)
        
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
        fetch_market_cap_ranking()
        return jsonify({
            "status": "success",
            "message": "시가총액 데이터 수집이 성공적으로 완료되었습니다."
        })
    except Exception as e:
        return jsonify({
            "status": "error",
            "message": f"시가총액 데이터 수집 중 오류 발생: {str(e)}"
        }), 500

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
