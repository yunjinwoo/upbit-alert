from flask import Flask, jsonify
from flask_cors import CORS
from db_manager import get_latest_alerts

app = Flask(__name__)
# 모든 도메인에서의 접근을 허용합니다 (CORS 해결)
CORS(app)

@app.route('/alerts', methods=['GET'])
def get_alerts():
    """최근 발생한 알림을 JSON 형식으로 반환합니다."""
    try:
        alerts = get_latest_alerts(limit=50)
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

@app.route('/', methods=['GET'])
def index():
    return jsonify({
        "message": "Upbit Alert API Server is running.",
        "endpoints": {
            "/alerts": "Get latest 50 alerts in JSON format"
        }
    })

if __name__ == '__main__':
    # 외부에서도 접속 가능하게 하려면 host='0.0.0.0'으로 설정하세요.
    app.run(host='0.0.0.0', port=5000, debug=True)
