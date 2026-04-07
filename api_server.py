from flask import Flask, jsonify, render_template
from flask_cors import CORS
from db_manager import (
    get_latest_alerts, delete_alert, 
    get_latest_stock_alerts, delete_stock_alert
)

app = Flask(__name__)
# 모든 도메인에서의 접근을 허용합니다 (CORS 해결)
CORS(app)

@app.route('/')
def index():
    """대시보드 메인 페이지를 보여줍니다."""
    return render_template('index.html')

@app.route('/alerts', methods=['GET'])
def get_alerts():
    """최근 발생한 코인 알림을 JSON 형식으로 반환합니다."""
    try:
        alerts = get_latest_alerts(limit=300)

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
        alerts = get_latest_stock_alerts(limit=300)
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

if __name__ == '__main__':
    # 외부에서도 접속 가능하게 하려면 host='0.0.0.0'으로 설정하세요.
    app.run(host='0.0.0.0', port=5000, debug=True)
