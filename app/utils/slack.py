"""Slack 웹훅 발송 — 의존성이 requests뿐인 가벼운 유틸리티.
Flask API 프로세스(app/api/server.py)에서도 gspread 등 무거운 패키지를 끌어들이지 않고
쓸 수 있도록 app/core/upbit_monitor.py(google_sheets를 임포트하는 무거운 모듈)에서 분리했다.
"""
import requests
from app.config import Config
from app.utils.logger import get_logger

logger = get_logger()


def send_slack_msg(text):
    if not Config.SLACK_WEBHOOK_URL:
        logger.warning("Slack webhook URL not configured.")
        return
    try:
        payload = {"text": text}
        requests.post(Config.SLACK_WEBHOOK_URL, json=payload, timeout=5)
    except Exception as e:
        logger.error(f"슬랙 전송 실패: {e}")
