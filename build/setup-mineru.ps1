# Installs the runtime into this folder so MinerU.bat can run from here:
#   .venv\        Python 3.12 with MinerU (pinned to the version this pack was tested with)
#   home\models\  model files for the standard tier, about 2 GB
# Nothing is written outside this folder except the uv tool itself if it is missing.
# Run: double-click setup.cmd. Safe to run again; finished steps are skipped. Log: build\setup.log
$ErrorActionPreference = 'Stop'
$MinerUVersion = '4.2.0'
$Here = Split-Path -Parent $MyInvocation.MyCommand.Path
$Root = Split-Path -Parent $Here
Set-Location $Root
function Step($m) { Write-Host "`n=== $m ===" -ForegroundColor Cyan }
function Check($what) { if ($LASTEXITCODE -ne 0) { throw "$what failed (exit code $LASTEXITCODE)" } }
$sw = [Diagnostics.Stopwatch]::StartNew()
try {
    Start-Transcript -Path (Join-Path $Here 'setup.log') -Append | Out-Null
    Write-Host "Install root: $Root"
    if ($Root -match '[^\x00-\x7F]') {
        Write-Host "WARNING: the path contains non-English characters. The VLM engine cannot load from such a path." -ForegroundColor Yellow
        Write-Host "         Move the folder to a plain English path such as D:\MinerU-LazyPack and run setup again." -ForegroundColor Yellow
    }

    Step "Graphics card"
    try { nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv } catch { Write-Host "nvidia-smi not found (fine on AMD, Intel or CPU-only machines)" }
    $global:LASTEXITCODE = 0

    Step "uv (Python package manager)"
    $env:Path = "$env:USERPROFILE\.local\bin;$env:LOCALAPPDATA\Microsoft\WinGet\Links;$env:Path"
    if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
        if (Get-Command winget -ErrorAction SilentlyContinue) {
            Write-Host "uv not found, installing with winget"
            winget install --id astral-sh.uv -e --silent --accept-source-agreements --accept-package-agreements
        } elseif (Get-Command python -ErrorAction SilentlyContinue) {
            Write-Host "uv not found, installing with pip"
            python -m pip install --user -U uv
            $env:Path = "$(python -c 'import sysconfig;print(sysconfig.get_path(\"scripts\",\"nt_user\"))');$env:Path"
        } else {
            throw "Neither uv, winget nor python found. Install uv first: https://docs.astral.sh/uv/"
        }
        $env:Path = "$env:USERPROFILE\.local\bin;$env:LOCALAPPDATA\Microsoft\WinGet\Links;$env:Path"
    }
    uv --version
    Check "uv"

    Step "Python 3.12 environment in .venv"
    uv venv "$Root\.venv" --python 3.12 --allow-existing
    Check "uv venv"
    $Py = "$Root\.venv\Scripts\python.exe"

    Step "MinerU $MinerUVersion"
    $Mirrors = @(
        "https://mirrors.aliyun.com/pypi/simple/",
        "https://mirrors.cloud.tencent.com/pypi/simple",
        "https://mirrors.ustc.edu.cn/pypi/simple",
        "https://pypi.tuna.tsinghua.edu.cn/simple",
        "https://pypi.org/simple"
    )
    $installed = $false
    foreach ($m in $Mirrors) {
        Write-Host "Trying index: $m" -ForegroundColor Yellow
        uv pip install --python $Py "mineru==$MinerUVersion" --index-url $m
        if ($LASTEXITCODE -eq 0) { $installed = $true; break }
        Write-Host "Index $m failed, trying the next one" -ForegroundColor Yellow
    }
    if (-not $installed) { throw "Could not install mineru from any package index" }

    Step "Models for the standard tier (about 2 GB, skipped when already present)"
    $MHome = Join-Path $Root 'home'
    New-Item -ItemType Directory -Force -Path $MHome | Out-Null
    if (-not (Test-Path (Join-Path $MHome 'config.yaml'))) { Copy-Item (Join-Path $Here 'config.yaml') (Join-Path $MHome 'config.yaml') }
    $env:MINERU_HOME = $MHome
    $env:MINERU_MODEL_SOURCE = 'modelscope'
    $env:MODELSCOPE_CACHE = Join-Path $MHome 'cache\modelscope'
    $env:HF_HOME = Join-Path $MHome 'cache\hf'
    # Commands that draw progress bars trip a Windows PowerShell 5.1 bug when run directly,
    # so they run through Start-Process with their output written to files.
    $LogDir = Join-Path $Here 'setup-logs'
    New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
    function Native($argList, $name) {
        $out = Join-Path $LogDir "$name.txt"
        $err = Join-Path $LogDir "$name.err.txt"
        $all = @('-s', '-E', '-X', 'utf8', '-m', 'mineru.kit.main') + $argList
        $p = Start-Process -FilePath $Py -ArgumentList $all -WorkingDirectory $Root -NoNewWindow -Wait -PassThru -RedirectStandardOutput $out -RedirectStandardError $err
        if (Test-Path $out) { Get-Content $out | Select-Object -Last 25 | Write-Host }
        if (Test-Path $err) { Get-Content $err | Select-Object -Last 10 | Write-Host }
        return $p.ExitCode
    }
    $rc = Native @('models', 'verify', '--tier', 'standard') 'models-verify'
    if ($rc -ne 0) {
        Write-Host "Downloading models from ModelScope. No progress is shown here; this takes a few minutes." -ForegroundColor Yellow
        $rc = Native @('models', 'download', '--tier', 'standard', '--source', 'modelscope') 'models-download'
        if ($rc -ne 0) { throw "Model download failed (exit code $rc). See build\setup-logs\models-download.err.txt" }
    } else { Write-Host "Models already present" }

    Step "Hardware check"
    $rc = Native @('hardware', 'detect') 'hardware'
    $rc = Native @('version', '--json') 'version'

    Step ("DONE in {0:n0} s" -f $sw.Elapsed.TotalSeconds)
    Write-Host "Double-click MinerU.bat in $Root to start." -ForegroundColor Green
    Write-Host "To make a package for other people, double-click build\build.cmd afterwards."
}
catch {
    Write-Host "`nFAILED: $($_.Exception.Message)" -ForegroundColor Red
    Write-Host $_.ScriptStackTrace
}
finally {
    try { Stop-Transcript | Out-Null } catch {}
    Read-Host "Press Enter to close"
}
