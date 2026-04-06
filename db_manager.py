import sqlite3
from datetime import datetime
import os

DB_NAME = "alerts.db"

def init_db():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    # 코인 알림 테이블
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT,
            ticker TEXT,
            surge_count TEXT,
            m240 TEXT,
            m60 TEXT,
            m30 TEXT,
            m15 TEXT,
            daily_info TEXT,
            url TEXT
        )
    ''')
    # 주식 알림 테이블 (신규)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS stock_alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT,
            code TEXT,
            name TEXT,
            price TEXT,
            change_rate TEXT,
            volume TEXT,
            volume_power TEXT,
            market_cap TEXT,
            reason TEXT,
            url TEXT
        )
    ''')
    conn.commit()
    conn.close()

def save_alert_to_db(ticker, surge_count, m240, m60, m30, m15, daily_info, url):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    cursor.execute('''
        INSERT INTO alerts (timestamp, ticker, surge_count, m240, m60, m30, m15, daily_info, url)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (timestamp, ticker, surge_count, m240, m60, m30, m15, daily_info, url))
    conn.commit()
    conn.close()

def save_stock_alert_to_db(code, name, price, change_rate, volume, volume_power, market_cap, reason, url):
    """주식 알림 저장"""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    cursor.execute('''
        INSERT INTO stock_alerts (timestamp, code, name, price, change_rate, volume, volume_power, market_cap, reason, url)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (timestamp, code, name, price, change_rate, volume, volume_power, market_cap, reason, url))
    conn.commit()
    conn.close()

def get_latest_alerts(limit=50):
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM alerts ORDER BY id DESC LIMIT ?', (limit,))
    rows = cursor.fetchall()
    conn.close()
    
    return [dict(row) for row in rows]

def get_latest_stock_alerts(limit=50):
    """최신 주식 알림 조회"""
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM stock_alerts ORDER BY id DESC LIMIT ?', (limit,))
    rows = cursor.fetchall()
    conn.close()
    
    return [dict(row) for row in rows]

def delete_alert(alert_id):
    """특정 ID의 코인 알림을 삭제합니다."""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('DELETE FROM alerts WHERE id = ?', (alert_id,))
    conn.commit()
    conn.close()

def delete_stock_alert(alert_id):
    """특정 ID의 주식 알림을 삭제합니다."""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('DELETE FROM stock_alerts WHERE id = ?', (alert_id,))
    conn.commit()
    conn.close()

# 모듈이 로드될 때 자동으로 DB 초기화 실행
init_db()

if __name__ == "__main__":
    print("Database initialized.")
