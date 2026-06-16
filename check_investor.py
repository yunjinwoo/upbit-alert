import sys, sqlite3
sys.stdout.reconfigure(encoding='utf-8')
from app.config import Config

print('DB 경로:', Config.DB_NAME)
conn = sqlite3.connect(Config.DB_NAME)
cursor = conn.cursor()
cursor.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
tables = [r[0] for r in cursor.fetchall()]
print('테이블 목록:', tables)
print()

if 'stock_investor_daily' in tables:
    cursor.execute('SELECT COUNT(*) FROM stock_investor_daily')
    print('stock_investor_daily 건수:', cursor.fetchone()[0])
    cursor.execute('SELECT DISTINCT date FROM stock_investor_daily ORDER BY date DESC LIMIT 5')
    print('최근 날짜:', [r[0] for r in cursor.fetchall()])
    cursor.execute('SELECT code, name, date, frgn_ntby_qty, orgn_ntby_qty FROM stock_investor_daily LIMIT 3')
    print('샘플 데이터:')
    for r in cursor.fetchall():
        print(' ', r)
else:
    print('stock_investor_daily 테이블 없음!')
    print('=> 서버를 재시작해야 init_db()가 테이블을 생성합니다.')

conn.close()
