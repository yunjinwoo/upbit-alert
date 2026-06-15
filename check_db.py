import sqlite3, os, sys
sys.stdout.reconfigure(encoding='utf-8')
for db in ['alerts.db', 'stock_monitor.db']:
    print(f'===== {db} ({os.path.getsize(db):,} bytes) =====')
    conn = sqlite3.connect(db)
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
    tables = [r[0] for r in cursor.fetchall()]
    print('tables:', tables)
    for t in tables:
        cursor.execute(f'SELECT COUNT(*) FROM "{t}"')
        cnt = cursor.fetchone()[0]
        info = f'  {t}: {cnt}건'
        try:
            cursor.execute(f'SELECT * FROM "{t}" LIMIT 1')
            cols = [d[0] for d in cursor.description]
            if 'date' in cols and cnt > 0:
                cursor.execute(f'SELECT MIN(date), MAX(date) FROM "{t}"')
                r = cursor.fetchone()
                info += f' | {r[0]} ~ {r[1]}'
        except:
            pass
        print(info)
    conn.close()
    print()
