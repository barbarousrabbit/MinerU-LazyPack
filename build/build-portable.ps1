# Builds a relocatable package from this folder after build\setup.cmd has been run.
# Output: dist\MinerU-Portable\ and dist\MinerU-Portable.zip (about 2 GB). The package needs no install and no network.
# Run: double-click build.cmd
$ErrorActionPreference = 'Stop'
$Here = Split-Path -Parent $MyInvocation.MyCommand.Path
$Src  = Split-Path -Parent $Here
$Dist = Join-Path $Src 'dist'
$Out  = Join-Path $Dist 'MinerU-Portable'
$Zip  = Join-Path $Dist 'MinerU-Portable.zip'
function Step($m) { Write-Host "`n=== $m ===" -ForegroundColor Cyan }
function Copy-Tree($from, $to) {
    New-Item -ItemType Directory -Force -Path $to | Out-Null
    robocopy $from $to /E /MT:16 /R:2 /W:2 /NFL /NDL /NJH /NJS /NP /XD __pycache__ | Out-Null
    if ($LASTEXITCODE -ge 8) { throw "robocopy $from -> $to failed (code $LASTEXITCODE)" }
    $global:LASTEXITCODE = 0
}
function RunPy($argList, $name) {
    $log = Join-Path $Dist "test-$name.txt"
    $err = Join-Path $Dist "test-$name.err.txt"
    $all = @('-s','-E','-X','utf8','-m','mineru.kit.main') + $argList
    $p = Start-Process -FilePath (Join-Path $Out 'python\python.exe') -ArgumentList $all -WorkingDirectory $Out -NoNewWindow -Wait -PassThru -RedirectStandardOutput $log -RedirectStandardError $err
    if (Test-Path $log) { Get-Content $log | Select-Object -Last 20 | Write-Host }
    if (Test-Path $err) { Get-Content $err | Select-Object -Last 8 | Write-Host }
    return $p.ExitCode
}
$sw = [Diagnostics.Stopwatch]::StartNew()
New-Item -ItemType Directory -Force -Path $Dist | Out-Null
try {
    Start-Transcript -Path (Join-Path $Dist 'build.log') -Append | Out-Null

    Step "Locate the standalone Python used by $Src\.venv"
    $cfg = Get-Content (Join-Path $Src '.venv\pyvenv.cfg')
    $PyDir = ($cfg | Where-Object { $_ -match '^home\s*=' }) -replace '^home\s*=\s*',''
    $PyDir = $PyDir.Trim()
    if (-not (Test-Path (Join-Path $PyDir 'python.exe'))) { throw "python.exe not found in $PyDir" }
    if (-not (Test-Path (Join-Path $PyDir 'python312.dll'))) { throw "python312.dll not found in $PyDir, not a standalone build" }
    Write-Host "Python base: $PyDir"

    Step "Clean previous output"
    if (Test-Path $Out) { Remove-Item -Recurse -Force $Out }
    if (Test-Path $Zip) { Remove-Item -Force $Zip }

    Step "Copy Python runtime -> python\"
    Copy-Tree $PyDir (Join-Path $Out 'python')

    Step "Copy installed packages -> python\Lib\site-packages"
    Copy-Tree (Join-Path $Src '.venv\Lib\site-packages') (Join-Path $Out 'python\Lib\site-packages')
    if (Test-Path (Join-Path $Src '.venv\share')) { Copy-Tree (Join-Path $Src '.venv\share') (Join-Path $Out 'python\share') }

    Step "Copy models -> home\models (~2 GB)"
    Copy-Tree (Join-Path $Src 'home\models') (Join-Path $Out 'home\models')

    Step "Launchers, README, config, sample"
    Copy-Item (Join-Path $Src 'MinerU*.bat') $Out
    Copy-Item (Join-Path $Src 'MinerU-WebUI.html') $Out
    Remove-Item (Join-Path $Out 'python\Lib\EXTERNALLY-MANAGED') -ErrorAction SilentlyContinue
    Copy-Item (Join-Path $Src 'README.txt') $Out
    Copy-Tree (Join-Path $Src 'bin') (Join-Path $Out 'bin')
    Copy-Tree (Join-Path $Src 'tools') (Join-Path $Out 'tools')
    Copy-Tree (Join-Path $Src 'samples') (Join-Path $Out 'samples')
    Copy-Item (Join-Path $Here 'config.yaml') (Join-Path $Out 'home\config.yaml')

    Step "Self test inside the package (version, hardware, models, parse sample)"
    $env:MINERU_HOME = Join-Path $Out 'home'
    $env:MINERU_MODEL_SOURCE = 'modelscope'
    $env:MODELSCOPE_CACHE = Join-Path $Out 'home\cache\modelscope'
    $env:HF_HOME = Join-Path $Out 'home\cache\hf'
    $rc = RunPy @('version','--json') 'version';            if ($rc -ne 0) { throw "version failed ($rc)" }
    $rc = RunPy @('hardware','info','--json') 'hardware';   if ($rc -ne 0) { throw "hardware info failed ($rc)" }
    $rc = RunPy @('models','verify','--tier','standard') 'models'; if ($rc -ne 0) { throw "models verify failed ($rc)" }
    $md = Join-Path $Dist 'sample-test.md'
    Remove-Item $md -ErrorAction SilentlyContinue
    $rc = RunPy @('parse', "`"$(Join-Path $Out 'samples\sample.pdf')`"", '-o', "`"$md`"", '--tier', 'standard') 'parse'
    if ($rc -ne 0 -or -not (Test-Path $md)) { throw "sample parse failed ($rc)" }
    Write-Host "Sample parse OK: $md ($((Get-Item $md).Length) bytes)"

    Step "WebUI smoke test on port 7861 (up to 180 s)"
    $ProgressPreference = 'SilentlyContinue'
    $wargs = @('-s','-E','-X','utf8',"`"$(Join-Path $Out 'bin\webui_app.py')`"",'--server-name','127.0.0.1','--server-port','7861','--output-dir',"`"$(Join-Path $Dist 'webui-test-output')`"",'--tier','auto','--no-browser')
    $wp = Start-Process -FilePath (Join-Path $Out 'python\python.exe') -ArgumentList $wargs -WorkingDirectory $Out -NoNewWindow -PassThru -RedirectStandardOutput (Join-Path $Dist 'test-webui.txt') -RedirectStandardError (Join-Path $Dist 'test-webui.err.txt')
    $up = $false
    for ($i = 0; $i -lt 90 -and -not $up -and -not $wp.HasExited; $i++) {
        Start-Sleep -Seconds 2
        try { $r = Invoke-WebRequest -Uri 'http://127.0.0.1:7861/' -UseBasicParsing -TimeoutSec 3; if ($r.StatusCode -eq 200) { $up = $true } } catch {}
    }
    if (-not $wp.HasExited) { & taskkill.exe /PID $wp.Id /T /F | Out-Null }
    $global:LASTEXITCODE = 0
    if (-not $up) {
        Get-Content (Join-Path $Dist 'test-webui.err.txt') -ErrorAction SilentlyContinue | Select-Object -Last 15 | Write-Host
        throw "WebUI did not answer on http://127.0.0.1:7861"
    }
    Write-Host "WebUI answered HTTP 200"
    $fell = Select-String -Path (Join-Path $Dist 'test-webui.txt'),(Join-Path $Dist 'test-webui.err.txt') -Pattern 'Traceback' -SimpleMatch -Quiet
    if ($fell) { throw "WebUI started but the batch tab failed to load, see dist\test-webui.err.txt" }
    Write-Host "Batch tab loaded"
    Remove-Item -Recurse -Force (Join-Path $Dist 'webui-test-output') -ErrorAction SilentlyContinue

    Step "Reset per-machine state so the next machine calibrates itself"
    Copy-Item (Join-Path $Here 'config.yaml') (Join-Path $Out 'home\config.yaml') -Force
    foreach ($d in @('home\cache','home\bench-logs','home\portable.json','home\bench.json','output','bin\__pycache__','python\Lib\site-packages\__pycache__')) {
        $p = Join-Path $Out $d; if (Test-Path $p) { Remove-Item -Recurse -Force $p }
    }
    Get-ChildItem (Join-Path $Out 'home') -Force | Where-Object { $_.Name -notin @('models','config.yaml') } | ForEach-Object { Write-Host "note: extra item in home: $($_.Name)" }

    Step "Sizes"
    $bytes = (Get-ChildItem $Out -Recurse -File | Measure-Object Length -Sum).Sum
    Write-Host ("Package folder: {0:n2} GB" -f ($bytes/1GB))

    Step "Zip -> $Zip (a few minutes)"
    $tar = Get-Command tar.exe -ErrorAction SilentlyContinue
    if (-not $tar) { throw "tar.exe not found (ships with Windows 10 1803+)" }
    & tar.exe -a -c -f $Zip -C $Dist 'MinerU-Portable'
    if ($LASTEXITCODE -ne 0) { throw "tar failed ($LASTEXITCODE)" }
    Write-Host ("Zip: {0:n2} GB" -f ((Get-Item $Zip).Length/1GB))

    Step ("DONE in {0:n0} s" -f $sw.Elapsed.TotalSeconds)
    Write-Host "Folder: $Out"
    Write-Host "Zip:    $Zip"
}
catch {
    Write-Host "`nFAILED: $($_.Exception.Message)" -ForegroundColor Red
    Write-Host $_.ScriptStackTrace
}
finally {
    try { Stop-Transcript | Out-Null } catch {}
    Read-Host "Press Enter to close"
}
