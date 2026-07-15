import requests
from app.config import Config
from app.utils.logger import get_logger
from app.utils.db_manager import (
    get_market_cap_history,
    get_stock_investor_raw,
    get_investor_trend_history,
    get_sector_index_cached,
    get_hts_top_view_export,
)

logger = get_logger()

# 동기화 관리 페이지의 "전체 전송" 버튼과 동일한 순서/조합 (templates/sync_admin.html 참고)
SYNC_STEPS = [
    ('stock_market_cap_daily',    lambda limit: get_market_cap_history(limit_dates=limit, fid_input_iscd='combined')),
    ('stock_investor_daily',      lambda limit: get_stock_investor_raw(limit_dates=limit)),
    ('investor_trend_daily',      lambda limit: get_investor_trend_history(exch_div='J', mrkt_div='1', limit_days=limit)),
    ('investor_trend_daily',      lambda limit: get_investor_trend_history(exch_div='J', mrkt_div='4', limit_days=limit)),
    ('sector_index_daily',        lambda limit: get_sector_index_cached('0001', limit=limit)),
    ('sector_index_daily',        lambda limit: get_sector_index_cached('1001', limit=limit)),
    ('sector_index_daily',        lambda limit: get_sector_index_cached('2001', limit=limit)),
    ('stock_hts_top_view_hourly', lambda limit: get_hts_top_view_export(limit_days=limit)),
]


def push_all_tables_to_server(server_url: str = None, limit: int = None) -> dict:
    """로컬 DB의 최근 N일치 데이터를 원격 서버로 전송한다.
    '동기화 관리' 페이지의 "전체 전송" 버튼을 브라우저 대신 Python에서 그대로 재현한 것 —
    로컬 데이터를 직접 조회해 원격 서버의 /api/sync/start → /api/sync/push(테이블별) → /api/sync/end
    순서로 호출한다.
    반환: {status, tables, rows} 또는 {status: 'error', message}
    """
    server_url = (server_url or Config.SYNC_SERVER_URL).rstrip('/')
    limit = limit or Config.SYNC_AUTO_LIMIT

    try:
        start_res = requests.post(f"{server_url}/api/sync/start", timeout=10)
        start_res.raise_for_status()
        token = start_res.json().get('token')
        if not token:
            raise ValueError("토큰 발급 실패 (응답에 token 없음)")
    except Exception as e:
        logger.error(f"[동기화] 세션 시작 실패: {e}")
        return {"status": "error", "message": str(e)}

    total_rows = 0
    synced_tables = []
    try:
        for table, fetch_fn in SYNC_STEPS:
            rows = fetch_fn(limit)
            if not rows:
                continue
            res = requests.post(
                f"{server_url}/api/sync/push",
                json={"table": table, "rows": rows},
                headers={"X-Sync-Token": token},
                timeout=30,
            )
            res.raise_for_status()
            saved = res.json().get('saved', 0)
            total_rows += saved
            synced_tables.append(table)
            logger.info(f"[동기화] {table} {saved}건 전송 완료")
    except Exception as e:
        logger.error(f"[동기화] 전송 중 에러: {e}")
    finally:
        try:
            requests.post(f"{server_url}/api/sync/end", headers={"X-Sync-Token": token}, timeout=10)
        except Exception as e:
            logger.error(f"[동기화] 세션 종료 실패: {e}")

    logger.info(f"[동기화] 전체 전송 완료 — 테이블 {len(synced_tables)}건 호출, 총 {total_rows}행 저장")
    return {"status": "ok", "tables": synced_tables, "rows": total_rows}
