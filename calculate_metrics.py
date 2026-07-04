"""
calculate_metrics.py
=====================
collector.py가 쌓아둔 원본 온체인 데이터로부터 파생 지표를 계산한다.

계산하는 지표:
- MVRV            = CoinMetrics가 직접 계산해서 제공하는 mvrv_cm 값을 그대로 사용
                    (CapRealUSD가 무료 등급에서 빠져 자체 계산이 불가능해짐에 따라,
                     CoinMetrics 제공값을 그대로 신뢰하는 방식으로 변경)
- MVRV Z(자체)    = (오늘 MVRV - 누적 MVRV 평균) / 누적 MVRV 표준편차
                    ⚠️ 이건 정식 'MVRV Z-Score'(시가총액-실현가치를 표준편차로 나누는 공식)가
                    아니라, 누적 히스토리 기준으로 자체 정규화한 근사 지표다.
                    정식 공식에 필요한 실현가치(CapRealUSD)가 무료 등급에 없어
                    동일하게 재현할 수 없기 때문. 상대적 위치 파악용으로만 참고.
- Puell Multiple  = 당일 발행가치(USD) / 365일 이동평균 발행가치(USD)
- NVT(proxy)      = CapMrktCurUSD / TxCnt
                    ※ 정식 NVT는 '온체인 이전가치(transfer value)'를 분모로 쓰지만
                      해당 지표는 무료 티어에 없어 TxCnt로 근사한 '참고용 보조지표'임을
                      대시보드에 명시해야 함 (과신 금지)
- 종합 밸류에이션 스코어 (0~100)
    = 위 3개 지표를 각각 '누적 히스토리 내 백분위(percentile rank)'로 환산한 뒤 평균
    = 100에 가까울수록 역사적으로 과열, 0에 가까울수록 역사적으로 저평가 구간

콜드스타트 한계: 누적 데이터가 적을 초기(예: 며칠~몇 주)에는 percentile/표준편차의
통계적 의미가 약하다. README의 "초기 시드 데이터" 섹션 참고.
"""

import sqlite3
import statistics
from dataclasses import dataclass
from typing import Optional

import pandas as pd

from collector import DB_PATH


@dataclass
class ValuationResult:
    date: str
    price_usd: float
    mvrv: Optional[float]
    mvrv_z: Optional[float]
    puell: Optional[float]
    nvt_proxy: Optional[float]
    score_0_100: Optional[float]
    band: str


def _load_history(db_path: str = DB_PATH) -> pd.DataFrame:
    conn = sqlite3.connect(db_path)
    df = pd.read_sql_query("SELECT * FROM daily_metrics ORDER BY date ASC", conn)
    conn.close()
    return df


def _percentile_rank(series: pd.Series, value: float) -> Optional[float]:
    """series 내에서 value가 차지하는 백분위(0~100)를 반환.

    value가 NaN이면 (pandas 비교 연산이 NaN에 대해 항상 False를 반환하는 탓에)
    실제로는 '데이터 없음'인데도 0.0(최저 백분위)으로 잘못 계산되므로 반드시
    None으로 처리해야 한다.
    """
    clean = series.dropna()
    if len(clean) < 5 or value is None or pd.isna(value):
        return None
    rank = (clean < value).sum() / len(clean) * 100
    return round(rank, 1)


def _band_from_score(score: Optional[float]) -> str:
    if score is None:
        return "데이터 부족"
    if score >= 85:
        return "과열 (역사적 고평가 구간)"
    if score >= 60:
        return "다소 고평가"
    if score >= 40:
        return "중립"
    if score >= 15:
        return "다소 저평가"
    return "저평가 (역사적 매집 구간)"


def calculate_all(db_path: str = DB_PATH) -> ValuationResult:
    df = _load_history(db_path)
    if df.empty:
        raise RuntimeError("daily_metrics 테이블이 비어있습니다. 먼저 collector.py를 실행하세요.")

    # 파생 컬럼 계산 (전체 히스토리에 대해 벡터화 연산)
    df["puell_ma365"] = df["issuance_usd"].rolling(window=365, min_periods=7).mean()
    df["puell"] = df["issuance_usd"] / df["puell_ma365"]
    df["nvt_proxy"] = df["cap_mrkt_usd"] / df["tx_count"]

    latest = df.iloc[-1]

    # MVRV 자체 정규화 Z (정식 공식이 아닌 근사치, 위 모듈 docstring 참고)
    mvrv_clean = df["mvrv_cm"].dropna()
    mvrv_z = None
    if len(mvrv_clean) >= 5 and pd.notna(latest["mvrv_cm"]):
        mean = statistics.fmean(mvrv_clean.tolist())
        std = statistics.pstdev(mvrv_clean.tolist())
        if std > 0:
            mvrv_z = round((latest["mvrv_cm"] - mean) / std, 3)

    mvrv_pct = _percentile_rank(df["mvrv_cm"], latest["mvrv_cm"])
    puell_pct = _percentile_rank(df["puell"], latest["puell"])
    nvt_pct = _percentile_rank(df["nvt_proxy"], latest["nvt_proxy"])

    sub_scores = [s for s in (mvrv_pct, puell_pct, nvt_pct) if s is not None]
    composite = round(sum(sub_scores) / len(sub_scores), 1) if sub_scores else None

    result = ValuationResult(
        date=latest["date"],
        price_usd=latest["price_usd"],
        mvrv=round(latest["mvrv_cm"], 3) if pd.notna(latest["mvrv_cm"]) else None,
        mvrv_z=mvrv_z,
        puell=round(latest["puell"], 3) if pd.notna(latest["puell"]) else None,
        nvt_proxy=round(latest["nvt_proxy"], 1) if pd.notna(latest["nvt_proxy"]) else None,
        score_0_100=composite,
        band=_band_from_score(composite),
    )
    return result


def _ensure_derived_table(db_path: str = DB_PATH) -> None:
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS derived_metrics (
            date TEXT PRIMARY KEY,
            price_usd REAL,
            mvrv REAL,
            mvrv_z REAL,
            puell REAL,
            nvt_proxy REAL,
            score_0_100 REAL,
            band TEXT
        )
        """
    )
    conn.commit()
    conn.close()


def save_result(result: ValuationResult, db_path: str = DB_PATH) -> None:
    _ensure_derived_table(db_path)
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        INSERT INTO derived_metrics (date, price_usd, mvrv, mvrv_z, puell, nvt_proxy, score_0_100, band)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(date) DO UPDATE SET
            price_usd=excluded.price_usd, mvrv=excluded.mvrv, mvrv_z=excluded.mvrv_z,
            puell=excluded.puell, nvt_proxy=excluded.nvt_proxy,
            score_0_100=excluded.score_0_100, band=excluded.band
        """,
        (result.date, result.price_usd, result.mvrv, result.mvrv_z,
         result.puell, result.nvt_proxy, result.score_0_100, result.band),
    )
    conn.commit()
    conn.close()


if __name__ == "__main__":
    res = calculate_all()
    save_result(res)
    print(f"[{res.date}] 가격=${res.price_usd:,.0f}  "
          f"MVRV={res.mvrv}  MVRV-Z(자체)={res.mvrv_z}  Puell={res.puell}  "
          f"종합스코어={res.score_0_100} ({res.band})")