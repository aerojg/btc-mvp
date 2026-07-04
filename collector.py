"""
collector.py
============
비트코인 온체인 + 가격 데이터를 무료 API에서 수집하여 SQLite DB에 저장하는 모듈.

사용하는 무료 데이터 소스:
1. CoinMetrics Community API (API 키 불필요, 완전 무료)
   - https://community-api.coinmetrics.io/v4
   - 2026-06-26 기준 catalog-v2/asset-metrics?assets=btc 로 직접 확인한
     "community: true"(무료 제공) 지표만 사용한다.
   - CapMrktCurUSD(시가총액), CapMVRVCur(MVRV, CoinMetrics가 직접 계산해 제공),
     AdrActCnt(활성주소수), HashRate(해시레이트), TxCnt, SplyCur, IssTotUSD
   - ⚠️ CapRealUSD(실현가치) / DiffMean(난이도) / FeeMeanUSD(평균수수료)는
     무료 등급에서 제외되어 있어 사용하지 않는다 (요청 시 403 Forbidden).
2. CoinGecko Demo API (무료 가입만 필요, 카드 불필요. 키 없이도 호출 가능하나 불안정하므로 키 권장)
   - 가격/거래량 교차검증용 (실시간 보조 지표)

이 스크립트는 GitHub Actions에서 매일 1회 자동 실행되는 것을 전제로 설계되었습니다.
실행 비용: $0 (서버, DB, API 모두 무료 티어)
"""

import os
import sqlite3
import time
from datetime import datetime, timezone

import requests
from psychology_collector import fetch_fear_greed_index

# ---------------------------------------------------------------------------
# 설정
# ---------------------------------------------------------------------------
DB_PATH = os.environ.get("BTC_DB_PATH", "btc_onchain.db")

COINMETRICS_BASE = "https://community-api.coinmetrics.io/v4"
COINMETRICS_METRICS = [
    "PriceUSD",        # 가격 (교차검증용)
    "CapMrktCurUSD",   # 시가총액 (Market Cap)
    "CapMVRVCur",      # MVRV (CoinMetrics가 직접 계산해서 제공 -> 자체 계산 불필요)
    "AdrActCnt",       # 활성 주소 수
    "HashRate",        # 해시레이트 (네트워크 보안/채굴 참여도)
    "TxCnt",           # 일일 트랜잭션 수
    "SplyCur",         # 현재 유통량
    "IssTotUSD",       # 일일 신규 발행 가치(USD) -> Puell Multiple 계산용
]

# CoinGecko Demo API 키는 선택사항. 없으면 키 없는 퍼블릭 엔드포인트로 폴백(분당 5~15콜로 제한적).
COINGECKO_API_KEY = os.environ.get("COINGECKO_API_KEY", "")
COINGECKO_BASE = "https://api.coingecko.com/api/v3"


# ---------------------------------------------------------------------------
# DB 초기화
# ---------------------------------------------------------------------------
def init_db(db_path: str = DB_PATH) -> None:
    """daily_metrics 테이블이 없으면 생성한다."""
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS daily_metrics (
            date            TEXT PRIMARY KEY,   -- YYYY-MM-DD
            price_usd       REAL,
            cap_mrkt_usd    REAL,
            mvrv_cm         REAL,                -- CoinMetrics가 직접 계산한 MVRV
            active_addr     REAL,
            hash_rate       REAL,
            tx_count        REAL,
            supply_current  REAL,
            issuance_usd    REAL,
            cg_price_usd    REAL,                -- CoinGecko 교차검증 가격
            cg_market_cap   REAL,
            cg_volume_24h   REAL,
            collected_at    TEXT                  -- 수집 시각(UTC, 디버깅용)
        )
        """
    )
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# CoinMetrics 수집
# ---------------------------------------------------------------------------
def fetch_coinmetrics_latest(days: int = 5) -> list[dict]:
    """
    최근 N일치 온체인 지표를 CoinMetrics Community API에서 가져온다.

    ⚠️ 'sort'/'direction' 파라미터는 CoinMetrics v4 API에 존재하지 않는
    파라미터라 400 Bad Request를 유발한다. 공식 문서가 권장하는 'limit_per_asset'을
    사용해야 한다. start_time/end_time을 지정하지 않으면 API가 기본적으로
    가장 최근 데이터부터 반환한다.
    """
    url = f"{COINMETRICS_BASE}/timeseries/asset-metrics"
    params = {
        "assets": "btc",
        "metrics": ",".join(COINMETRICS_METRICS),
        "frequency": "1d",
        "limit_per_asset": days,
    }
    resp = requests.get(url, params=params, timeout=20)
    resp.raise_for_status()
    data = resp.json().get("data", [])
    return data


# ---------------------------------------------------------------------------
# CoinGecko 수집 (교차검증용 보조 지표)
# ---------------------------------------------------------------------------
def fetch_coingecko_latest() -> dict:
    """현재 가격/시가총액/24h 거래량을 가져온다. 실패해도 메인 파이프라인은 계속 진행."""
    url = f"{COINGECKO_BASE}/simple/price"
    params = {
        "ids": "bitcoin",
        "vs_currencies": "usd",
        "include_market_cap": "true",
        "include_24hr_vol": "true",
    }
    if COINGECKO_API_KEY:
        params["x_cg_demo_api_key"] = COINGECKO_API_KEY

    try:
        resp = requests.get(url, params=params, timeout=15)
        resp.raise_for_status()
        d = resp.json().get("bitcoin", {})
        return {
            "cg_price_usd": d.get("usd"),
            "cg_market_cap": d.get("usd_market_cap"),
            "cg_volume_24h": d.get("usd_24h_vol"),
        }
    except requests.RequestException as e:
        print(f"[경고] CoinGecko 수집 실패 (무시하고 진행): {e}")
        return {"cg_price_usd": None, "cg_market_cap": None, "cg_volume_24h": None}


# ---------------------------------------------------------------------------
# 저장
# ---------------------------------------------------------------------------
# =====================================================
# DB에 데이터 저장 (UPSERT)
# =====================================================

# =====================================================
# DB에 데이터 저장 (UPSERT)
# =====================================================

def upsert_rows(rows: list[dict], cg: dict, fng: dict, db_path: str) -> int:
    """CoinMetrics 일별 rows를 DB에 upsert. 기장 최신 버전"""
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    now_iso = datetime.now(timezone.utc).isoformat()

    inserted = 0
    rows_sorted = sorted(rows, key=lambda r: r["time"], reverse=True)
    latest_date = rows_sorted[0]["time"][:10] if rows_sorted else None

    for r in rows_sorted:
        date = r["time"][:10]  # 2026-06-26T00:00:00

        def f(key):
            v = r.get(key)
            return float(v) if v is not None else None

        cg_vals = cg if date == latest_date else {}

        # FNG 데이터 (매일 1개씩만 업데이트)
        fng_value = None
        if fng and fng.get('value') is not None:
            fng_value = fng['value']

        cur.execute(
            """
            INSERT INTO daily_metrics (
                date, price_usd, cap_mrkt_usd, mvrv_cm, active_addr,
                hash_rate, tx_count, supply_current, issuance_usd,
                cg_price_usd, cg_market_cap, cg_volume_24h,
                collected_at, fear_greed_index
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(date) DO UPDATE SET
                price_usd = excluded.price_usd,
                cap_mrkt_usd = excluded.cap_mrkt_usd,
                mvrv_cm = excluded.mvrv_cm,
                active_addr = excluded.active_addr,
                hash_rate = excluded.hash_rate,
                tx_count = excluded.tx_count,
                supply_current = excluded.supply_current,
                issuance_usd = excluded.issuance_usd,
                cg_price_usd = COALESCE(excluded.cg_price_usd, cg_price_usd),
                cg_market_cap = COALESCE(excluded.cg_market_cap, cg_market_cap),
                cg_volume_24h = COALESCE(excluded.cg_volume_24h, cg_volume_24h),
                collected_at = excluded.collected_at,
                fear_greed_index = COALESCE(excluded.fear_greed_index, fear_greed_index)
            """,
            (
                date,
                f("PriceUSD"),
                f("CapMrktCurUSD"),
                f("CapMVRVCur"),
                f("AdrActCnt"),
                f("HashRate"),
                f("TxCnt"),
                f("SplyCur"),
                f("IssTotUSD"),
                cg_vals.get("cg_price_usd"),
                cg_vals.get("cg_market_cap"),
                cg_vals.get("cg_volume_24h"),
                now_iso,
                fng_value
            ),
        )
        inserted += 1

    conn.commit()
    conn.close()
    return inserted


# ---------------------------------------------------------------------------
# 메인
# ---------------------------------------------------------------------------
def run(db_path: str = DB_PATH) -> None:
    print(f"[{datetime.now(timezone.utc).isoformat()}] 데이터 수집 시작")
    init_db(db_path)

    cm_rows = fetch_coinmetrics_latest(days=14)
    if not cm_rows:
        raise RuntimeError("CoinMetrics에서 데이터를 받지 못했습니다. API 상태를 확인하세요.")

    cg_data = fetch_coingecko_latest()
    fng_data = fetch_fear_greed_index()
    time.sleep(1)  # 무료 API 매너 호출 (rate limit 보호)

    n = upsert_rows(cm_rows, cg_data, fng_data, db_path)
    latest_row = max(cm_rows, key=lambda r: r["time"])
    print(f"  -> {n}개 행 upsert 완료 (db: {db_path})")
    print(f"  -> 최신 날짜: {latest_row['time'][:10]}, "
          f"PriceUSD={latest_row.get('PriceUSD')}, "
          f"CapMrktCurUSD={latest_row.get('CapMrktCurUSD')}, "
          f"CapMVRVCur={latest_row.get('CapMVRVCur')}")


if __name__ == "__main__":
    run()