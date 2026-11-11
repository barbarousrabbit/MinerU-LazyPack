@echo off
setlocal
title MinerU Self Check
call "%~dp0..\bin\env.cmd" || exit /b 1
echo.
echo --- 硬件识别 ---
"%PY%" %PYFLAGS% -m mineru.kit.main hardware detect
echo.
echo --- 模型校验 standard 档 ---
"%PY%" %PYFLAGS% -m mineru.kit.main models verify --tier standard
echo.
echo --- 版本 ---
"%PY%" %PYFLAGS% -m mineru.kit.main version --json
echo.
pause
