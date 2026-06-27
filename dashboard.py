"""
dashboard.py
============
SQLite DB(btc_onchain.db)를 읽어 시각화하는 Streamlit 대시보드.

로컬 실행: streamlit run dashboard.py
무료 배포: Streamlit Community Cloud (share.streamlit.io)에 이 리포지토리를 연결하면
          서버비 $0으로 외부에서 접속 가능한 URL이 생성됨.
"""

import sqlite3
from typing import Optional

import pandas as pd
import streamlit as st

from collector import DB_PATH
from calculate_metrics import calculate_all, save_result

st.set_page_config(page_title="비트코인 적정가치 분석 (MVP)", layout="wide")

# ---------------------------------------------------------------------------
# 설명 카드용 CSS (한 번만 주입)
# ---------------------------------------------------------------------------
st.markdown(
    """
    <style>
    .metric-explain-card {
        background-color: rgba(255,255,255,0.04);
        border-radius: 12px;
        padding: 18px 20px 16px 20px;
        margin-bottom: 14px;
        border-left: 5px solid #8b949e;
        height: 100%;
    }
    .metric-explain-card h4 {
        margin: 0 0 2px 0;
        font-size: 1.05rem;
        display: flex;
        align-items: center;
        gap: 8px;
        flex-wrap: wrap;
    }
    .metric-explain-card .value-chip {
        display: inline-block;
        font-weight: 700;
        font-size: 0.8rem;
        padding: 3px 11px;
        border-radius: 999px;
        white-space: nowrap;
    }
    .metric-explain-card .section-label {
        font-size: 0.74rem;
        opacity: 0.6;
        margin-top: 12px;
        margin-bottom: 3px;
        text-transform: uppercase;
        letter-spacing: 0.04em;
    }
    .metric-explain-card p {
        margin: 0;
        font-size: 0.91rem;
        line-height: 1.55;
        opacity: 0.92;
    }
    .border-blue   { border-left-color: #58a6ff; }
    .border-green  { border-left-color: #3fb950; }
    .border-yellow { border-left-color: #e3b341; }
    .border-red    { border-left-color: #f85149; }
    .border-gray   { border-left-color: #8b949e; }
    .badge-blue   { background-color: rgba(88,166,255,0.18);  color: #79c0ff; }
    .badge-green  { background-color: rgba(63,185,80,0.18);   color: #56d364; }
    .badge-yellow { background-color: rgba(227,179,65,0.18);  color: #e3b341; }
    .badge-red    { background-color: rgba(248,81,73,0.18);   color: #ff7b72; }
    .badge-gray   { background-color: rgba(139,148,158,0.18); color: #8b949e; }
    </style>
    """,
    unsafe_allow_html=True,
)


@st.cache_data(ttl=3600)
def load_data(db_path: str = DB_PATH) -> pd.DataFrame:
    conn = sqlite3.connect(db_path)
    df = pd.read_sql_query(
        "SELECT * FROM daily_metrics ORDER BY date ASC", conn, parse_dates=["date"]
    )
    conn.close()
    return df


# ---------------------------------------------------------------------------
# 공통 카드 렌더링 헬퍼
# ---------------------------------------------------------------------------
def _render_card(icon: str, title: str, value_display: str, level_label: str,
                  color: str, definition: str, interpretation: str,
                  value_caption: str = "지금 수준") -> str:
    return f"""
    <div class="metric-explain-card border-{color}">
        <h4>{icon} {title}
            <span class="value-chip badge-{color}">{level_label}</span>
        </h4>
        <div class="section-label">무엇을 의미하나요?</div>
        <p>{definition}</p>
        <div class="section-label">{value_caption}({value_display})은 어떤가요?</div>
        <p>{interpretation}</p>
    </div>
    """


def _format_pct(p: Optional[float]) -> str:
    if p is None:
        return "N/A"
    sign = "+" if p >= 0 else ""
    return f"{sign}{p:.1f}%"


def _calc_trend_pct(series: pd.Series) -> Optional[float]:
    """시리즈의 첫 값 대비 마지막 값의 변화율(%)을 계산."""
    s = series.dropna()
    if len(s) < 2 or s.iloc[0] == 0:
        return None
    return (s.iloc[-1] - s.iloc[0]) / abs(s.iloc[0]) * 100


# ---------------------------------------------------------------------------
# 지표값 -> (수준 라벨, 색상, 해석 문구) 매핑
# 값이 바뀌면 해석 문구도 자동으로 함께 바뀐다 (하드코딩된 스냅샷이 아님).
# ---------------------------------------------------------------------------
def _mvrv_level(v):
    if v is None:
        return ("데이터 없음", "gray", "아직 계산할 데이터가 충분하지 않습니다.")
    if v < 1.0:
        return ("저평가 구간", "blue",
                "평균 보유자의 매수 단가보다 현재가가 낮은 상태입니다. "
                "과거 사이클에서는 바닥권 매집 구간과 자주 겹쳤던 영역입니다.")
    if v < 2.0:
        return ("정상/회복 구간", "green",
                "평균 보유자가 적정 수준의 수익을 보고 있는 상태로, "
                "특별히 과열되거나 저평가된 신호는 아닙니다.")
    if v < 3.5:
        return ("상승 국면", "yellow",
                "평균 보유자의 수익이 커지고 있는 구간입니다. "
                "상승장 중반부에서 흔히 나타나지만, 과열 신호는 아직 아닙니다.")
    return ("과열 구간", "red",
            "과거 사이클의 최고점 부근에서 반복적으로 나타났던 수준입니다. "
            "추격 매수에 신중함이 필요한 구간으로 흔히 해석됩니다.")


def _mvrv_z_level(z):
    if z is None:
        return ("데이터 없음", "gray", "아직 계산할 데이터가 충분하지 않습니다.")
    if z < -1.0:
        return ("평균보다 뚜렷이 낮음", "blue",
                "지금까지 누적된 데이터의 평균보다 1 표준편차 이상 낮은 상태입니다. "
                "상대적으로 저평가된 구간일 가능성을 시사합니다.")
    if z < 1.0:
        return ("평균 부근", "green",
                "누적 데이터의 평균과 비슷한 수준으로, 통계적으로 중립적인 상태입니다.")
    if z < 2.0:
        return ("평균보다 높음", "yellow",
                "누적 데이터의 평균보다 다소 높은 상태입니다. 추이를 지켜볼 구간입니다.")
    return ("평균보다 매우 높음", "red",
            "누적 데이터의 평균보다 2 표준편차 이상 높은, 통계적으로 드문 상태입니다.")


def _score_level(score):
    if score is None:
        return ("데이터 없음", "gray", "아직 계산할 데이터가 충분하지 않습니다.")
    if score >= 85:
        return ("과열", "red",
                "MVRV·Puell·NVT 세 지표를 종합했을 때, 누적 히스토리 상위 15% 안에 드는 "
                "고평가 구간입니다.")
    if score >= 60:
        return ("다소 고평가", "yellow",
                "세 지표 종합 결과가 누적 히스토리의 평균보다 다소 높은 쪽에 위치합니다.")
    if score >= 40:
        return ("중립", "green",
                "세 지표 종합 결과가 누적 히스토리의 중간 구간에 위치한, 특별한 신호가 없는 상태입니다.")
    if score >= 15:
        return ("다소 저평가", "blue",
                "세 지표 종합 결과가 누적 히스토리의 평균보다 다소 낮은 쪽에 위치합니다.")
    return ("저평가", "blue",
            "MVRV·Puell·NVT 세 지표를 종합했을 때, 누적 히스토리 하위 15% 안에 드는 "
            "저평가 구간입니다.")


def _price_trend(pct: Optional[float]):
    if pct is None:
        return ("데이터 없음", "gray", "추세를 계산하기엔 데이터가 부족합니다.")
    if pct > 2:
        return ("상승", "green", f"표시된 기간 동안 가격이 {_format_pct(pct)} 상승했습니다.")
    if pct < -2:
        return ("하락", "red", f"표시된 기간 동안 가격이 {_format_pct(pct)} 하락했습니다.")
    return ("보합", "gray", f"표시된 기간 동안 가격 변화가 {_format_pct(pct)}로 크지 않았습니다.")


def _active_addr_trend(pct: Optional[float]):
    if pct is None:
        return ("데이터 없음", "gray", "추세를 계산하기엔 데이터가 부족합니다.")
    if pct > 5:
        return ("활동 증가", "green",
                f"표시된 기간 동안 활성 주소 수가 {_format_pct(pct)} 증가했습니다. "
                "온체인 활동(사용량)이 늘고 있다는 신호로 해석될 수 있습니다.")
    if pct < -5:
        return ("활동 감소", "red",
                f"표시된 기간 동안 활성 주소 수가 {_format_pct(pct)} 감소했습니다. "
                "온체인 활동이 줄고 있다는 신호일 수 있습니다.")
    return ("큰 변화 없음", "gray",
            f"표시된 기간 동안 활성 주소 수 변화가 {_format_pct(pct)}로 미미합니다.")


def _hash_rate_trend(pct: Optional[float]):
    if pct is None:
        return ("데이터 없음", "gray", "추세를 계산하기엔 데이터가 부족합니다.")
    if pct > 5:
        return ("채굴 참여 증가", "green",
                f"표시된 기간 동안 해시레이트가 {_format_pct(pct)} 증가했습니다. "
                "채굴자들의 네트워크 참여(보안 수준)가 강화되고 있다는 신호입니다.")
    if pct < -5:
        return ("채굴 참여 감소", "red",
                f"표시된 기간 동안 해시레이트가 {_format_pct(pct)} 감소했습니다. "
                "채굴자 이탈(수익성 악화에 따른 항복 등) 가능성을 시사할 수 있습니다.")
    return ("안정적", "gray",
            f"표시된 기간 동안 해시레이트 변화가 {_format_pct(pct)}로 안정적입니다.")


# ---------------------------------------------------------------------------
# 상단 핵심 지표 설명 카드 4개 (현재가 기준 시점 + MVRV + MVRV Z + 종합 스코어)
# ---------------------------------------------------------------------------
def render_top_metric_explainers(result, last_collected_date=None) -> None:
    mvrv_label, mvrv_color, mvrv_text = _mvrv_level(result.mvrv)
    z_label, z_color, z_text = _mvrv_z_level(result.mvrv_z)
    score_label, score_color, score_text = _score_level(result.score_0_100)

    if last_collected_date is not None:
        kst_str = last_collected_date.strftime("%Y년 %m월 %d일") + " 오전 9시"
    else:
        kst_str = "알 수 없음"

    c0, c1, c2, c3 = st.columns(4)
    with c0:
        st.markdown(
            _render_card(
                "🕒", "현재가 기준 시점", kst_str,
                "일 1회 스냅샷", "gray",
                "이 대시보드의 '현재가'는 실시간 거래소 시세가 아니라, GitHub Actions가 "
                "매일 자동으로 데이터를 수집하는 시점(UTC 00:00, 한국시간 오전 9시)에 기록된 "
                "값입니다. 비트코인 가격은 1초 단위로 바뀌지만, 함께 표시되는 MVRV 등 온체인 "
                "지표와 동일한 기준 시점으로 비교하기 위해 가격도 하루 한 번만 갱신됩니다.",
                "다음 자동 갱신은 내일 같은 시각(오전 9시)에 이루어집니다.",
                value_caption="가장 최근 수집",
            ),
            unsafe_allow_html=True,
        )
    with c1:
        st.markdown(
            _render_card(
                "📐", "MVRV", f"{result.mvrv}" if result.mvrv is not None else "N/A",
                mvrv_label, mvrv_color,
                "시가총액을 '실현가치(보유자들이 실제 코인을 매수한 평균 가격 기준 총액)'로 "
                "나눈 값입니다. CoinMetrics가 직접 계산해서 무료로 제공하는 값을 그대로 사용합니다. "
                "1.0보다 크면 평균 보유자가 수익권, 작으면 손실권이라는 뜻입니다.",
                mvrv_text,
            ),
            unsafe_allow_html=True,
        )
    with c2:
        st.markdown(
            _render_card(
                "📏", "MVRV Z (자체 정규화)",
                f"{result.mvrv_z}" if result.mvrv_z is not None else "N/A",
                z_label, z_color,
                "MVRV가 '지금까지 쌓인 데이터 평균'에서 얼마나(표준편차 기준) 벗어나 있는지를 "
                "나타내는 자체 계산 지표입니다. ⚠️ 정식 'MVRV Z-Score' 공식(실현가치 기반)이 아니라, "
                "실현가치 데이터가 무료 등급에 없어 누적 MVRV 히스토리로 근사한 값입니다.",
                z_text,
            ),
            unsafe_allow_html=True,
        )
    with c3:
        st.markdown(
            _render_card(
                "🧮", "종합 밸류에이션 스코어",
                f"{result.score_0_100}/100" if result.score_0_100 is not None else "N/A",
                score_label, score_color,
                "MVRV, Puell Multiple, NVT(근사치) 세 지표를 각각 '누적 히스토리 내 백분위'로 "
                "환산한 뒤 평균한 값입니다. 100에 가까울수록 역사적으로 과열, 0에 가까울수록 "
                "역사적으로 저평가된 구간이라는 뜻입니다.",
                score_text,
            ),
            unsafe_allow_html=True,
        )


# ---------------------------------------------------------------------------
# 컬럼(용어) 글로서리 (원본 데이터 탭용)
# ---------------------------------------------------------------------------
_GLOSSARY = [
    ("date", "날짜 (UTC 기준)"),
    ("price_usd", "비트코인 가격 — CoinMetrics 기준 (USD)"),
    ("cap_mrkt_usd", "시가총액 (가격 × 유통량, USD)"),
    ("mvrv_cm", "MVRV — CoinMetrics가 직접 계산해 제공하는 값"),
    ("active_addr", "활성 주소 수 — 그날 거래에 참여한 고유 주소 수"),
    ("hash_rate", "해시레이트 — 네트워크 전체 채굴 연산력 (H/s)"),
    ("tx_count", "일일 트랜잭션 수"),
    ("supply_current", "현재 유통량 (BTC)"),
    ("issuance_usd", "일일 신규 발행 가치 (채굴 보상, USD)"),
    ("cg_price_usd", "CoinGecko 교차검증 가격 (USD)"),
    ("cg_market_cap", "CoinGecko 교차검증 시가총액"),
    ("cg_volume_24h", "CoinGecko 24시간 거래량"),
    ("collected_at", "데이터 수집 시각 (UTC, 디버깅용)"),
]


def main():
    st.title("📊 비트코인 현재가치 적정성 분석 (MVP)")
    st.caption("데이터 소스: CoinMetrics Community API(온체인, 무료) + CoinGecko(가격 교차검증, 무료)")

    df = load_data()
    if df.empty:
        st.warning("아직 수집된 데이터가 없습니다. `python collector.py`를 먼저 실행하세요.")
        return

    # 최신 종합 스코어 계산 (캐시 없이 매번 최신 계산)
    try:
        result = calculate_all()
        save_result(result)
    except Exception as e:
        st.error(f"지표 계산 중 오류: {e}")
        return

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("현재가 (USD)", f"${result.price_usd:,.0f}" if result.price_usd else "N/A")
    col2.metric("MVRV (CoinMetrics 제공)", result.mvrv if result.mvrv is not None else "N/A")
    col3.metric("MVRV Z(자체 정규화)", result.mvrv_z if result.mvrv_z is not None else "N/A")
    col4.metric("종합 밸류에이션 스코어", f"{result.score_0_100}/100" if result.score_0_100 else "N/A")

    st.subheader(f"판단: {result.band}")
    if df.shape[0] < 30:
        st.info(
            "⚠️ 누적 데이터가 30일 미만입니다. 백분위/Z-Score의 통계적 신뢰도가 낮으니 "
            "참고용으로만 활용하세요. README의 '초기 시드 데이터' 섹션을 참고해 과거 데이터를 보강할 수 있습니다."
        )

    st.markdown("#### 📖 지표 풀이")
    render_top_metric_explainers(result, df["date"].max())

    st.divider()

    tab1, tab2, tab3 = st.tabs(["가격 & MVRV", "온체인 활동", "원본 데이터"])

    # ----------------------------------------------------------------- tab1
    with tab1:
        st.line_chart(df.set_index("date")[["price_usd"]], height=300)

        latest_price = df["price_usd"].dropna()
        price_pct = _calc_trend_pct(df["price_usd"])
        p_label, p_color, p_text = _price_trend(price_pct)
        st.markdown(
            _render_card(
                "💵", "비트코인 가격",
                f"${latest_price.iloc[-1]:,.0f}" if not latest_price.empty else "N/A",
                p_label, p_color,
                "거래소들의 실제 거래 데이터를 기반으로 CoinMetrics가 산출한 일별 가격입니다. "
                "오른쪽 '원본 데이터' 탭의 CoinGecko 가격과 비교해 이상치를 점검할 수 있습니다.",
                p_text,
                value_caption="표시 기간 추세",
            ),
            unsafe_allow_html=True,
        )

        st.line_chart(df.set_index("date")[["mvrv_cm"]], height=250)

        latest_mvrv = df["mvrv_cm"].dropna()
        mvrv_label, mvrv_color, mvrv_text = _mvrv_level(
            latest_mvrv.iloc[-1] if not latest_mvrv.empty else None
        )
        st.markdown(
            _render_card(
                "📐", "MVRV 추이",
                f"{latest_mvrv.iloc[-1]:.3f}" if not latest_mvrv.empty else "N/A",
                mvrv_label, mvrv_color,
                "위에서 설명한 MVRV가 시간에 따라 어떻게 움직여왔는지 보여주는 차트입니다. "
                "선이 올라갈수록 평균 보유자의 수익이 커지고, 내려갈수록 줄어듭니다. "
                "(과거 사이클 참고치: 3.5 이상 과열권 / 1.0 이하 저평가권)",
                mvrv_text,
            ),
            unsafe_allow_html=True,
        )

    # ----------------------------------------------------------------- tab2
    with tab2:
        c1, c2 = st.columns(2)
        with c1:
            st.line_chart(df.set_index("date")[["active_addr"]], height=250)
            addr_series = df["active_addr"].dropna()
            addr_pct = _calc_trend_pct(df["active_addr"])
            a_label, a_color, a_text = _active_addr_trend(addr_pct)
            st.markdown(
                _render_card(
                    "👥", "활성 주소 수",
                    f"{addr_series.iloc[-1]:,.0f}" if not addr_series.empty else "N/A",
                    a_label, a_color,
                    "그날 비트코인 네트워크에서 실제로 코인을 보내거나 받은 고유 주소의 수입니다. "
                    "거래소 내부 이동 등도 일부 포함되어 '실사용자 수'와는 차이가 있지만, "
                    "네트워크 활동량을 보여주는 대표적인 참고 지표입니다.",
                    a_text,
                    value_caption="표시 기간 추세",
                ),
                unsafe_allow_html=True,
            )
        with c2:
            st.line_chart(df.set_index("date")[["hash_rate"]], height=250)
            hr_series = df["hash_rate"].dropna()
            hr_pct = _calc_trend_pct(df["hash_rate"])
            h_label, h_color, h_text = _hash_rate_trend(hr_pct)
            hr_display = f"{hr_series.iloc[-1] / 1e18:.1f} EH/s" if not hr_series.empty else "N/A"
            st.markdown(
                _render_card(
                    "⛏️", "해시레이트",
                    hr_display,
                    h_label, h_color,
                    "전체 채굴자들이 비트코인 네트워크에 투입하고 있는 연산력의 총합입니다. "
                    "높을수록 네트워크를 공격하기 어려워져 보안성이 높아지고, "
                    "채굴자들이 네트워크에 적극적으로 참여하고 있다는 뜻으로도 해석됩니다.",
                    h_text,
                    value_caption="표시 기간 추세",
                ),
                unsafe_allow_html=True,
            )

    # ----------------------------------------------------------------- tab3
    with tab3:
        st.dataframe(df.tail(30), width="stretch")
        with st.expander("📚 컬럼(용어) 설명 보기"):
            glossary_md = "| 컬럼명 | 의미 |\n|---|---|\n" + "\n".join(
                f"| `{col}` | {desc} |" for col, desc in _GLOSSARY
            )
            st.markdown(glossary_md)

    st.divider()
    st.caption(
        "⚠️ 본 도구는 투자 자문이 아닙니다. 모든 지표는 과거 데이터 기반 참고 지표이며, "
        "비트코인 가격은 온체인 펀더멘털 외에도 매크로/심리/규제 등 다양한 요인에 더 크게 좌우될 수 있습니다."
    )


if __name__ == "__main__":
    main()