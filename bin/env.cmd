@echo off
rem Shared environment for MinerU Portable. Everything stays inside this folder.
for %%I in ("%~dp0..") do set "ROOT=%%~fI\"
set "PY=%ROOT%python\python.exe"
if not exist "%PY%" set "PY=%ROOT%.venv\Scripts\python.exe"
set "PYFLAGS=-s -E -X utf8"
set "MINERU_HOME=%ROOT%home"
set "MINERU_MODEL_SOURCE=modelscope"
set "MODELSCOPE_CACHE=%ROOT%home\cache\modelscope"
set "HF_HOME=%ROOT%home\cache\hf"
set "PATH=%ROOT%python;%ROOT%python\Scripts;%ROOT%.venv\Scripts;%PATH%"
if exist "%PY%" goto pyok
echo [错误] 找不到运行环境: %PY%
echo 如果这是整合包, 请确认压缩包已完整解压.
echo 如果这是从 GitHub 下载的源码, 请先双击 build\setup.cmd 安装运行环境和模型.
pause
exit /b 1
:pyok
"%PY%" -s -E -c "import sys;sys.exit(0 if sys.argv[1].isascii() else 1)" "%ROOT%."
if not errorlevel 1 goto pathok
echo.
echo [警告] 当前路径含中文或特殊字符: %ROOT%
echo        VLM 引擎 llama.cpp 在这种路径下可能无法加载, standard 和 advanced 档会失败.
echo        建议把整个文件夹移到纯英文路径, 例如 D:\MinerU-Portable
echo.
:pathok
exit /b 0
