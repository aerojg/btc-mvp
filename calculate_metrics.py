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

⚠️ CoinMetrics 데이터 확정 지연(data lag)
   CoinMetrics는 가장 최근 날짜의 행을 '미완성' 상태로 먼저 내려준다. PriceUSD /
   HashRate / TxCnt는 들어있지만 CapMVRVCur·CapMrktCurUSD·SplyCur는 null인
   상태로 하루 정도 유지되다가 다음날 채워진다. 따라서
   - 무조건 마지막 행(df.iloc[-1])을 기준으로 삼으면 매일 지표가 N/A로 표시된다.
     -> mvrv_cm이 실제로 채워진 '가장 최근 확정일'을 기준일로 사용한다.
   - 계산 결과를 그날 하루치만 저장하면, 나중에 원본이 채워져도 derived_metrics의
     과거 행은 NULL인 채로 영구히 남는다.
     -> 매 실행마다 전체 히스토리를 다시 계산해 upsert 한다(backfill).
"""

import sqlite3
import statistics
from dataclasses import dataclass
from typing import Optional

import pandas as pd

from collector import DB_PATH


# 종합 스코어를 신뢰하려면 3개 구성지표(MVRV/Puell/NVT) 중 최소 몇 개가 필요한지.
# 1개만 살아있을 때 평균을 내면 'Puell 하나짜리 점수'가 '종합 스코어'로 둔갑해
# 밴드 판정이 크게 튄다(실제로 2026-08-01 행이 Puell 단독으로 91.9점 '과열'이 됨).
MIN_SCORE_COMPONENTS = 2


@dataclass
class ValuationResult:
    date: str                 # 지표 기준일 = mvrv_cm이 확정된 가장 최근 날짜
    price_usd: float
    mvrv: Optional[float]
    mvrv_z: Optional[float]
    puell: Optional[float]
    nvt_proxy: Optional[float]
    score_0_100: Optional[float]
    band: str
    latest_date: Optional[str] = None   # DB에 수집된 가장 최근 날짜(미확정 포함)
    is_pending: bool = False            # 마지막 수집일이 아직 미확정이라 기준일이 뒤로 밀렸는지
    components: int = 0                 # 종합 스코어에 실제로 반영된 구성지표 개수


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


def _with_derived_columns(df: pd.DataFrame) -> pd.DataFrame:
    """원본 히스토리에 파생 컬럼(puell, nvt_proxy)을 붙여 반환."""
    df = df.copy()
    df["puell_ma365"] = df["issuance_usd"].rolling(window=365, min_periods=7).mean()
    df["puell"] = df["issuance_usd"] / df["puell_ma365"]
    df["nvt_proxy"] = df["cap_mrkt_usd"] / df["tx_count"]
    return df


def _anchor_index(df: pd.DataFrame) -> int:
    """지표 기준일로 삼을 행의 위치를 고른다.

    CoinMetrics는 최근 1~2일치를 mvrv_cm=null인 미완성 상태로 먼저 내려준다.
    그 행을 기준으로 삼으면 대시보드 전체가 N/A가 되므로, mvrv_cm이 실제로
    채워진 가장 최근 행을 기준일로 사용한다. (하나도 없으면 마지막 행)
    """
    valid = df.index[df["mvrv_cm"].notna()]
    return int(valid[-1]) if len(valid) else int(df.index[-1])


def _result_at(df: pd.DataFrame, idx: int) -> ValuationResult:
    """idx 행 기준의 밸류에이션 결과를 계산한다.

    Z-Score/백분위는 '그 날짜까지 쌓인 데이터'(expanding window)만 사용한다.
    전체 히스토리를 쓰면 과거 행을 재계산할 때 미래 데이터가 섞여(look-ahead)
    그날 실제로 보였던 값과 달라지기 때문이다.
    """
    hist = df.loc[:idx]
    row = df.loc[idx]

    # MVRV 자체 정규화 Z (정식 공식이 아닌 근사치, 위 모듈 docstring 참고)
    mvrv_clean = hist["mvrv_cm"].dropna()
    mvrv_z = None
    if len(mvrv_clean) >= 5 and pd.notna(row["mvrv_cm"]):
        mean = statistics.fmean(mvrv_clean.tolist())
        std = statistics.pstdev(mvrv_clean.tolist())
        if std > 0:
            mvrv_z = round((row["mvrv_cm"] - mean) / std, 3)

    mvrv_pct = _percentile_rank(hist["mvrv_cm"], row["mvrv_cm"])
    puell_pct = _percentile_rank(hist["puell"], row["puell"])
    nvt_pct = _percentile_rank(hist["nvt_proxy"], row["nvt_proxy"])

    sub_scores = [s for s in (mvrv_pct, puell_pct, nvt_pct) if s is not None]
    composite = (round(sum(sub_scores) / len(sub_scores), 1)
                 if len(sub_scores) >= MIN_SCORE_COMPONENTS else None)

    return ValuationResult(
        date=row["date"],
        price_usd=row["price_usd"],
        mvrv=round(row["mvrv_cm"], 3) if pd.notna(row["mvrv_cm"]) else None,
        mvrv_z=mvrv_z,
        puell=round(row["puell"], 3) if pd.notna(row["puell"]) else None,
        nvt_proxy=round(row["nvt_proxy"], 1) if pd.notna(row["nvt_proxy"]) else None,
        score_0_100=composite,
        band=_band_from_score(composite),
        latest_date=df.iloc[-1]["date"],
        is_pending=idx != int(df.index[-1]),
        components=len(sub_scores),
    )


def calculate_all(db_path: str = DB_PATH) -> ValuationResult:
    """가장 최근 '확정된' 날짜 기준의 밸류에이션 결과를 반환."""
    df = _load_history(db_path)
    if df.empty:
        raise RuntimeError("daily_metrics 테이블이 비어있습니다. 먼저 collector.py를 실행하세요.")

    df = _with_derived_columns(df)
    return _result_at(df, _anchor_index(df))


def calculate_history(db_path: str = DB_PATH) -> list[ValuationResult]:
    """전체 히스토리를 날짜별로 다시 계산한다(backfill용).

    CoinMetrics가 뒤늦게 채워준 값이 derived_metrics에 반영되도록, 매 실행마다
    과거 행까지 통째로 재계산한다. 예전처럼 당일 1행만 저장하면 수집 시점에
    NULL이었던 과거 행이 영구히 NULL로 남는다.
    """
    df = _load_history(db_path)
    if df.empty:
        raise RuntimeError("daily_metrics 테이블이 비어있습니다. 먼저 collector.py를 실행하세요.")

    df = _with_derived_columns(df)
    return [_result_at(df, idx) for idx in df.index]


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


_UPSERT_SQL = """
    INSERT INTO derived_metrics (date, price_usd, mvrv, mvrv_z, puell, nvt_proxy, score_0_100, band)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(date) DO UPDATE SET
        price_usd=excluded.price_usd, mvrv=excluded.mvrv, mvrv_z=excluded.mvrv_z,
        puell=excluded.puell, nvt_proxy=excluded.nvt_proxy,
        score_0_100=excluded.score_0_100, band=excluded.band
"""


def _as_params(r: ValuationResult) -> tuple:
    # numpy 스칼라는 sqlite3가 바인딩하지 못하므로 파이썬 기본형으로 변환한다.
    def n(v):
        return float(v) if v is not None and not pd.isna(v) else None

    return (str(r.date), n(r.price_usd), n(r.mvrv), n(r.mvrv_z),
            n(r.puell), n(r.nvt_proxy), n(r.score_0_100), r.band)


def save_result(result: ValuationResult, db_path: str = DB_PATH) -> None:
    _ensure_derived_table(db_path)
    conn = sqlite3.connect(db_path)
    conn.execute(_UPSERT_SQL, _as_params(result))
    conn.commit()
    conn.close()


def save_history(results: list[ValuationResult], db_path: str = DB_PATH) -> int:
    """전체 히스토리 결과를 한 번에 upsert 한다."""
    _ensure_derived_table(db_path)
    conn = sqlite3.connect(db_path)
    conn.executemany(_UPSERT_SQL, [_as_params(r) for r in results])
    conn.commit()
    conn.close()
    return len(results)


if __name__ == "__main__":
    history = calculate_history()
    save_history(history)
    res = calculate_all()
    print(f"  -> derived_metrics {len(history)}개 행 재계산/저장 완료")
    if res.is_pending:
        print(f"  -> {res.latest_date} 행은 CoinMetrics 미확정(mvrv 없음) 상태라 "
              f"기준일을 {res.date}로 사용")
    print(f"[{res.date}] 가격=${res.price_usd:,.0f}  "
          f"MVRV={res.mvrv}  MVRV-Z(자체)={res.mvrv_z}  Puell={res.puell}  "
          f"종합스코어={res.score_0_100} ({res.band}, 구성지표 {res.components}/3)")