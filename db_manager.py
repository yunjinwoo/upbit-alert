import sqlite3
from datetime import datetime
import os

DB_NAME = "alerts.db"

def init_db():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
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

def get_latest_alerts(limit=50):
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM alerts ORDER BY id DESC LIMIT ?', (limit,))
    rows = cursor.fetchall()
    conn.close()
    
    return [dict(row) for row in rows]

def delete_alert(alert_id):
    """특정 ID의 알림을 삭제합니다."""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('DELETE FROM alerts WHERE id = ?', (alert_id,))
    conn.commit()
    conn.close()

if __name__ == "__main__":
    init_db()
    print("Database initialized.")
