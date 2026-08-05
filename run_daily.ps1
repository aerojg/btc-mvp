<#
.SYNOPSIS
    btc_mvp 일일 데이터 파이프라인 (Windows 작업 스케줄러용)

.DESCRIPTION
    매일 아침 06:00에 실행되며, PC가 꺼져 있었다면 부팅/로그온 직후에 실행된다.
    (같은 날 이미 성공했다면 중복 실행하지 않는다 -> logs\.last_success 스탬프)

    처리 순서:
      1) 네트워크 대기 (부팅 직후 실행되는 경우 대비)
      2) git 동기화  - origin/main을 로컬로 당겨온다.
                       btc_onchain.db는 '재생성 가능한 파생물'로 취급해,
                       커밋되지 않은 변경분(대시보드 실행 중 write 등)은 버린다.
      3) collector.py         - CoinMetrics / CoinGecko / Fear&Greed 수집
      4) calculate_metrics.py - 파생지표 전체 히스토리 재계산(backfill)
      5) DB가 바뀌었으면 commit & push -> Streamlit Cloud 자동 재배포

    로그: logs\daily_YYYY-MM.log (월 단위 파일, UTF-8)

.PARAMETER Force
    같은 날 이미 성공했더라도 다시 실행한다. (수동 테스트용)

.PARAMETER NoPush
    수집/계산까지만 하고 commit/push는 건너뛴다.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File C:\btc_mvp\run_daily.ps1 -Force
#>
[CmdletBinding()]
param(
    [switch]$Force,
    [switch]$NoPush
)

$ErrorActionPreference = 'Stop'
$OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONIOENCODING = 'utf-8'

$Root      = $PSScriptRoot
$Python    = Join-Path $Root 'venv\Scripts\python.exe'
$LogDir    = Join-Path $Root 'logs'
$StampFile = Join-Path $LogDir '.last_success'
$DbFile    = 'btc_onchain.db'

if (-not (Test-Path $LogDir)) { New-Item -ItemType Directory -Path $LogDir | Out-Null }
$LogFile = Join-Path $LogDir ('daily_{0}.log' -f (Get-Date -Format 'yyyy-MM'))

# ---------------------------------------------------------------------------
# 헬퍼
# ---------------------------------------------------------------------------
function Write-Log {
    param([string]$Message, [string]$Level = 'INFO')
    $line = '{0} [{1}] {2}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $Level, $Message
    Write-Host $line
    Add-Content -Path $LogFile -Value $line -Encoding utf8
}

function Invoke-Native {
    <#
        네이티브 exe(git/python)를 호출하고 stdout+stderr와 종료코드를 함께 돌려준다.
        PowerShell 5.1은 $ErrorActionPreference='Stop' 상태에서 네이티브 stderr를
        리디렉션하면 NativeCommandError로 죽어버리므로, 호출 구간만 Continue로 낮춘다.
    #>
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [Parameter(Mandatory = $true)][string[]]$Arguments
    )
    $prev = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $out = & $FilePath @Arguments 2>&1 | ForEach-Object { $_.ToString() }
        $code = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $prev
    }
    return [pscustomobject]@{
        ExitCode = $code
        Output   = ($out -join [Environment]::NewLine)
    }
}

function Invoke-Git {
    param([Parameter(ValueFromRemainingArguments = $true)][string[]]$GitArgs)
    return Invoke-Native -FilePath 'git' -Arguments $GitArgs
}

function Write-CommandLog {
    param([string]$Label, $Result)
    if (-not [string]::IsNullOrWhiteSpace($Result.Output)) {
        foreach ($l in ($Result.Output -split "`r?`n")) {
            if (-not [string]::IsNullOrWhiteSpace($l)) { Write-Log ("  {0}| {1}" -f $Label, $l.TrimEnd()) }
        }
    }
}

function Wait-Network {
    <#
        부팅 직후에는 아직 인터넷이 안 붙어있을 수 있으므로 최대 3분 대기.
        단순 포트 검사 대신 실제로 필요한 동작(origin 접근)을 그대로 시험해 본다.
        DNS/프록시/TLS/자격증명 경로까지 한 번에 확인되기 때문이다.
    #>
    param([int]$TimeoutSec = 180)
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    while ($true) {
        $probe = Invoke-Git ls-remote --exit-code origin HEAD
        if ($probe.ExitCode -eq 0) { return $true }
        if ((Get-Date) -ge $deadline) {
            Write-CommandLog 'git' $probe
            return $false
        }
        Write-Log '네트워크 대기 중...'
        Start-Sleep -Seconds 10
    }
}

function Test-AlreadyRanToday {
    if ($Force) { return $false }
    if (-not (Test-Path $StampFile)) { return $false }
    $stamp = (Get-Content $StampFile -Raw -ErrorAction SilentlyContinue)
    if ($null -eq $stamp) { return $false }
    return ($stamp.Trim() -eq (Get-Date -Format 'yyyy-MM-dd'))
}

# ---------------------------------------------------------------------------
# git 동기화
#   btc_onchain.db는 API에서 다시 만들어낼 수 있는 파생물이고 origin이 정본이다.
#   따라서 커밋되지 않은 로컬 DB 변경분은 미련 없이 버리고 origin 기준으로 맞춘다.
#   (대시보드가 페이지 로드마다 derived_metrics를 쓰기 때문에 이 상태가 흔하다)
# ---------------------------------------------------------------------------
function Sync-Repo {
    $r = Invoke-Git fetch origin
    Write-CommandLog 'git' $r
    if ($r.ExitCode -ne 0) { throw "git fetch 실패 (exit $($r.ExitCode))" }

    $dirty = (Invoke-Git status --porcelain -- $DbFile).Output
    if (-not [string]::IsNullOrWhiteSpace($dirty)) {
        Write-Log "커밋되지 않은 $DbFile 변경분을 폐기하고 origin 기준으로 재수집합니다."
        $null = Invoke-Git checkout -- $DbFile
    }

    $behind = [int](Invoke-Git rev-list --count 'HEAD..origin/main').Output
    $ahead  = [int](Invoke-Git rev-list --count 'origin/main..HEAD').Output
    Write-Log "origin/main 대비 ahead=$ahead behind=$behind"

    if ($behind -eq 0) { return }

    if ($ahead -eq 0) {
        $r = Invoke-Git merge --ff-only origin/main
        Write-CommandLog 'git' $r
        if ($r.ExitCode -ne 0) { throw "fast-forward 실패 (exit $($r.ExitCode))" }
        return
    }

    # 분기 상태(로컬 커밋 + 원격 커밋). DB 하나만 충돌하면 자동 정리하고,
    # 소스코드가 얽혀 있으면 사람이 처리해야 하므로 중단한다.
    Write-Log '로컬/원격이 분기되어 rebase를 시도합니다.' 'WARN'
    $r = Invoke-Git rebase origin/main
    Write-CommandLog 'git' $r
    if ($r.ExitCode -eq 0) { return }

    $conflicts = @((Invoke-Git diff --name-only --diff-filter=U).Output -split "`r?`n" |
                   Where-Object { -not [string]::IsNullOrWhiteSpace($_) })
    if ($conflicts.Count -eq 1 -and $conflicts[0] -eq $DbFile) {
        Write-Log "$DbFile 충돌 -> origin 버전 채택 후 rebase 계속 (수집 단계에서 다시 채워짐)"
        $null = Invoke-Git checkout --ours -- $DbFile
        $null = Invoke-Git add $DbFile
        $r = Invoke-Git -c core.editor=true rebase --continue
        Write-CommandLog 'git' $r
        if ($r.ExitCode -ne 0) {
            $null = Invoke-Git rebase --abort
            throw 'DB 충돌 자동 해결에 실패했습니다.'
        }
        return
    }

    $null = Invoke-Git rebase --abort
    throw "소스코드가 원격과 충돌합니다($($conflicts -join ', ')). 수동으로 정리해 주세요."
}

# ---------------------------------------------------------------------------
# 메인
# ---------------------------------------------------------------------------
Set-Location $Root
Write-Log ('=' * 70)
Write-Log "실행 시작 (Force=$Force, NoPush=$NoPush)"

if (Test-AlreadyRanToday) {
    Write-Log '오늘 이미 성공적으로 실행되었습니다. 건너뜁니다. (강제 실행: -Force)'
    exit 0
}

if (-not (Test-Path $Python)) {
    Write-Log "venv 파이썬을 찾을 수 없습니다: $Python" 'ERROR'
    Write-Log '  python -m venv venv 로 가상환경을 먼저 만들어 주세요.' 'ERROR'
    exit 1
}

try {
    if (-not (Wait-Network)) { throw '네트워크에 연결할 수 없어 중단합니다.' }

    Sync-Repo

    Write-Log 'collector.py 실행'
    $r = Invoke-Native -FilePath $Python -Arguments @('collector.py')
    Write-CommandLog 'py' $r
    if ($r.ExitCode -ne 0) { throw "collector.py 실패 (exit $($r.ExitCode))" }

    Write-Log 'calculate_metrics.py 실행'
    $r = Invoke-Native -FilePath $Python -Arguments @('calculate_metrics.py')
    Write-CommandLog 'py' $r
    if ($r.ExitCode -ne 0) { throw "calculate_metrics.py 실패 (exit $($r.ExitCode))" }

    $changed = (Invoke-Git status --porcelain -- $DbFile).Output
    if ([string]::IsNullOrWhiteSpace($changed)) {
        Write-Log 'DB 변경 없음 (이미 최신). commit/push 생략.'
    } elseif ($NoPush) {
        Write-Log 'DB가 갱신되었으나 -NoPush 지정으로 commit/push를 생략합니다.'
    } else {
        $msg = 'Daily data update (local): {0} KST' -f (Get-Date -Format 'yyyy-MM-dd HH:mm')
        $null = Invoke-Git add $DbFile
        $r = Invoke-Git commit -m $msg
        Write-CommandLog 'git' $r
        if ($r.ExitCode -ne 0) { throw "commit 실패 (exit $($r.ExitCode))" }

        # 비대화식 실행이므로 자격증명 프롬프트가 뜨면 그대로 멈춰버린다.
        # 프롬프트 대신 즉시 실패하도록 막아두고, 실패는 로그로 남긴다.
        $env:GIT_TERMINAL_PROMPT = '0'
        $env:GCM_INTERACTIVE = 'never'
        $r = Invoke-Git push origin main
        Write-CommandLog 'git' $r
        if ($r.ExitCode -ne 0) {
            # Actions와 경합했을 수 있으니 한 번만 재동기화 후 재시도
            Write-Log 'push 실패 -> 재동기화 후 1회 재시도' 'WARN'
            Sync-Repo
            $r = Invoke-Git push origin main
            Write-CommandLog 'git' $r
            if ($r.ExitCode -ne 0) { throw "push 실패 (exit $($r.ExitCode)). 자격증명을 확인하세요." }
        }
        Write-Log 'GitHub push 완료 -> Streamlit Cloud가 자동 재배포합니다.'
    }

    Set-Content -Path $StampFile -Value (Get-Date -Format 'yyyy-MM-dd') -Encoding utf8
    Write-Log '실행 성공'
    exit 0
}
catch {
    Write-Log $_.Exception.Message 'ERROR'
    Write-Log '실행 실패' 'ERROR'
    exit 1
}
