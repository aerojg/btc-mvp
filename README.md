# 비트코인 현재가치 적정성 & 미래가치 동향 자동분석 시스템 (MVP)

> 1단계(온체인+가격) MVP. 운영 원칙: **무료 우선, 불가피한 경우에만 최소 비용.**
> ⚠️ 본 시스템은 투자 자문이 아닌 참고용 분석 도구입니다.

---

## 1. 무엇이 들어있나

| 파일 | 역할 |
|---|---|
| `collector.py` | CoinMetrics(온체인) + CoinGecko(가격)에서 데이터 수집 → SQLite 저장 |
| `calculate_metrics.py` | MVRV, MVRV Z-Score, Puell Multiple, 종합 밸류에이션 스코어(0~100) 계산 |
| `dashboard.py` | Streamlit 대시보드 (가격/MVRV 차트, 온체인 활동, 종합 판단) |
| `.github/workflows/daily_collect.yml` | 매일 자동 수집 (GitHub Actions, 무료) |
| `requirements.txt` | 의존성 목록 |

---

## 2. 비용 구조 (1단계 MVP)

| 구성 요소 | 선택한 서비스 | 비용 |
|---|---|---|
| 온체인 데이터 | CoinMetrics Community API (키 불필요) | **$0** |
| 가격 데이터 | CoinGecko Demo API (무료 가입, 카드 불필요) | **$0** |
| 자동 실행 스케줄러 | GitHub Actions (public repo 무제한 / private 월 2,000분) | **$0** |
| 데이터 저장소 | SQLite 파일 (리포지토리에 직접 커밋) | **$0** |
| 대시보드 호스팅 | Streamlit Community Cloud | **$0** |
| **합계** | | **$0/월** |

> 단, CoinMetrics Community Data는 "비상업적 용도(non-commercial use)" 조건의 무료 라이선스입니다.
> 유튜브 콘텐츠 제작용 참고자료로 쓰는 정도는 통상 문제 없지만, 추후 이 시스템 자체를 유료 상품화한다면
> 별도로 상업 라이선스/유료 플랜 검토가 필요합니다.

---

## 3. 로컬에서 빠르게 시작하기

```bash
# 1) 의존성 설치
pip install -r requirements.txt

# 2) 데이터 1회 수집
python collector.py

# 3) 지표 계산
python calculate_metrics.py

# 4) 대시보드 실행
streamlit run dashboard.py
```

브라우저에서 `http://localhost:8501` 접속.

---

## 4. 완전 자동화 + 무료 배포 (3단계)

1. **GitHub 리포지토리 생성** 후 이 폴더 전체를 push
2. (선택) CoinGecko 무료 키를 발급받아 리포지토리 `Settings → Secrets → Actions`에
   `COINGECKO_API_KEY`로 등록 — 키 없이도 동작하지만, 키가 있으면 호출 한도가
   분당 5~15콜 → 30콜로 안정화됩니다.
3. `.github/workflows/daily_collect.yml`이 매일 자동으로 데이터를 수집하고
   `btc_onchain.db`를 커밋합니다. → **서버 운영 없이 매일 자동 갱신**
4. [share.streamlit.io](https://share.streamlit.io)에서 본인 GitHub 계정으로 로그인 →
   이 리포지토리 연결 → `dashboard.py` 지정 → 배포.
   → 외부에서 접속 가능한 URL이 생성됩니다 (예: 유튜브 영상 썸네일/설명란에 첨부 가능).

---

## 4-1. 내 PC에서도 매일 자동 실행 (Windows 작업 스케줄러)

GitHub Actions가 멈추거나(리포지토리 60일 무활동 시 스케줄 워크플로가 비활성화됨)
지연될 때를 대비한 로컬 백업 파이프라인입니다. 등록은 한 번만 하면 됩니다.

```powershell
powershell -ExecutionPolicy Bypass -File C:\btc_mvp\setup_scheduled_task.ps1
```

- **매일 06:00**에 실행됩니다.
- **PC가 06:00에 꺼져 있었다면**, 켜서 로그온한 뒤 3분 후에 실행됩니다.
  (작업 스케줄러의 `StartWhenAvailable` + 로그온 트리거 이중 안전장치)
- 같은 날 이미 성공했다면 중복 실행하지 않습니다 (`logs\.last_success` 날짜 스탬프).
- 하는 일: `git pull` → `collector.py` → `calculate_metrics.py` → DB 변경 시 `commit & push`
  → Streamlit Cloud 자동 재배포.
- 로그: `logs\daily_YYYY-MM.log` (git에는 올라가지 않음)

| 명령 | 용도 |
|---|---|
| `powershell -ExecutionPolicy Bypass -File .\run_daily.ps1 -Force` | 지금 즉시 1회 실행 |
| `powershell -ExecutionPolicy Bypass -File .\run_daily.ps1 -Force -NoPush` | 푸시 없이 수집만 테스트 |
| `Start-ScheduledTask -TaskName 'BTC-MVP Daily Collect'` | 스케줄러를 통해 실행 |
| `Get-ScheduledTaskInfo -TaskName 'BTC-MVP Daily Collect'` | 마지막 실행 결과/다음 실행 시각 |
| `.\setup_scheduled_task.ps1 -At 07:30` | 실행 시각 변경 |
| `.\setup_scheduled_task.ps1 -Unregister` | 등록 해제 |

> **실행 시각에 대한 참고**: 06:00 KST = 전날 21:00 UTC입니다. CoinMetrics는 UTC 일 단위로
> 데이터를 확정하므로, 06시 로컬 실행이 09시 KST에 도는 GitHub Actions보다 데이터가
> 더 신선하지는 않습니다. 이 작업의 목적은 신선도가 아니라 **이중화(백업)와 로컬 DB 최신 유지**입니다.

> **자격증명**: `git push`가 Windows 자격 증명 관리자에 저장된 GitHub 토큰을 사용하므로,
> 작업은 반드시 *로그온한 사용자 계정*으로 실행되도록 등록됩니다. SYSTEM 계정으로 바꾸면
> 자격증명에 접근하지 못해 push가 실패합니다.

---

## 5. 초기 시드 데이터 (콜드스타트 보완, 선택사항)

MVRV Z-Score나 백분위 점수는 누적 데이터가 많을수록 신뢰도가 올라갑니다.
처음부터 며칠치만 쌓으면 통계적 의미가 약하므로, 아래 방법으로 과거분을 무료로 보강할 수 있습니다.

- [`coinmetrics/data`](https://github.com/coinmetrics/data) 공개 GitHub 저장소: CoinMetrics
  Community 데이터의 일별 아카이브를 무료로 클론하여 `daily_metrics` 테이블에
  과거분을 일괄 적재 가능 (스크립트는 2단계 확장 시 추가 제공 가능).
- 이 작업이 번거롭다면, 그냥 매일 자동 수집을 켜놓고 1~2개월 운영하면
  자연스럽게 통계적으로 의미 있는 누적 데이터가 쌓입니다.

---

## 6. 한계 및 주의사항 (반드시 읽어주세요)

- **MVRV/Puell/NVT(proxy)는 모두 "역사적 분포 대비 위치"를 보여주는 참고지표**일 뿐,
  비트코인의 객관적인 "적정가"를 산출하는 것이 아닙니다.
- `nvt_proxy`는 정식 NVT(거래소 이전가치 기준)가 아니라 거래건수(TxCnt) 기반 근사치입니다.
  무료 티어의 한계이며, 대시보드에도 이 점을 명시해 두었습니다.
- 온체인 지표만으로는 매크로(금리, 유동성)·심리(공포-탐욕)·규제 이벤트 등
  가격에 더 즉각적인 영향을 주는 요인들을 반영하지 못합니다 → 2~3단계에서 보강.
- **CoinMetrics 확정 지연(data lag)**: 가장 최근 1~2일치는 `PriceUSD`/`TxCnt`만 먼저
  들어오고 `CapMVRVCur`·`CapMrktCurUSD`·`SplyCur`는 하루 정도 뒤에 채워집니다.
  그래서 대시보드의 "현재가(실시간)"와 "온체인 기준일"은 서로 다를 수 있습니다.
  - `calculate_metrics.py`는 무조건 마지막 행이 아니라 **MVRV가 확정된 가장 최근 날짜**를
    기준일로 잡고, 각 지표 카드에 `🔗 온체인 기준일`을 함께 표기합니다.
  - 매 실행마다 `derived_metrics` **전체 히스토리를 재계산(backfill)** 하므로,
    나중에 원본이 채워지면 과거 행도 자동으로 정정됩니다.
  - 종합 스코어는 MVRV·Puell·NVT 중 **최소 2개가 확정된 날에만** 산출합니다
    (1개만으로 평균을 내면 특정 지표 하나가 '종합' 점수로 둔갑해 밴드가 크게 튑니다).

---

## 7. 다음 단계 로드맵

### 2단계 — 심리(Sentiment) 데이터 추가 (무료)

| 지표 | 무료 소스 | 비고 |
|---|---|---|
| 공포-탐욕 지수 | Alternative.me API (키 불필요) | 완전 무료, 일별 갱신 |
| 검색량 트렌드 | `pytrends` (Google Trends 비공식 라이브러리) | 무료, 비공식이라 가끔 차단될 수 있음 |
| 소셜 언급량 | Reddit API (PRAW, 무료 티어) | r/bitcoin 게시물 수·감성 |

→ `sentiment_collector.py` 모듈을 추가하고, 종합 스코어 계산에 심리축을 추가 가중치로
반영하는 방식으로 확장 (온체인 60% + 심리 40% 등 가중치는 백테스트로 조정 권장).

### 3단계 — 매크로(Macro) 데이터 추가 (무료)

| 지표 | 무료 소스 | 비고 |
|---|---|---|
| 미국 기준금리, M2 통화량, CPI | FRED API (미연준, 무료 키 신청) | 공식 정부 데이터, 가장 신뢰도 높음 |
| 달러 인덱스(DXY), 금 가격, 나스닥 | `yfinance` (Yahoo Finance 비공식 라이브러리) | 무료, 비공식이라 가끔 끊길 수 있음 |
| 미국 국채 금리 | FRED API | 위와 동일 |

→ 매크로 레짐(긴축/완화)을 분류해 "온체인상 저평가인데 매크로가 긴축적이면
점수를 보수적으로 조정"하는 식의 조건부 가중치 로직을 추가하는 것을 권장합니다.

### 유일하게 비용이 발생할 수 있는 지점: 자연어 리포트

수치를 사람이 읽기 좋은 문장으로 자동 요약하려면 LLM 호출이 필요해 비용이 발생합니다.
"무료 우선" 원칙에 따라 다음 순서로 도입을 권장합니다.

1. **1차(무료)**: if-else 규칙 기반 템플릿 문장 (예: "MVRV-Z가 7 이상이면 '과열' 문구 출력")
2. **2차(최소비용, 불가피한 경우)**: 하루 1회, 짧은 입력(수치 요약 수백 토큰)으로
   Claude API를 호출해 자연어 해설 생성. 이 정도 사용량은 월 비용이 1달러 미만 수준으로
   매우 작습니다.

---

## 8. 면책

이 시스템과 출력 결과는 투자 자문이 아니며, 저는 재정 자문가가 아닙니다.
실제 투자 판단은 본인 책임 하에, 더 다양한 정보를 종합해 신중히 내리시기 바랍니다.
