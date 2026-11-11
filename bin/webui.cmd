@echo off
rem Usage: webui.cmd local | lan
call "%~dp0env.cmd" || exit /b 1
set "LANFLAG="
if /i "%~1"=="lan" set "LANFLAG=--lan"
echo.
echo ==============================================
echo   MinerU 4 文档提取
echo ==============================================
echo   启动页:  http://127.0.0.1:7859   在这里选择运行模式
echo   主页面:  http://127.0.0.1:7860
if /i not "%~1"=="lan" goto startit
echo   局域网:  同一网络下的其他电脑或手机, 在浏览器输入下面任一地址
"%PY%" -s -E "%~dp0lanip.py"
echo.
echo   [注意] 局域网模式没有密码, 同一网络里的任何人都能打开并上传文件, 只在可信网络使用.
echo   [注意] 第一次运行 Windows 防火墙会弹窗, 请勾选 专用网络 并点 允许访问.
:startit
echo.
echo   浏览器会自动打开启动页. 使用期间保持本窗口开启.
echo   关闭本窗口 = 停止服务并释放显存.
echo.
"%PY%" %PYFLAGS% "%~dp0launcher.py" %LANFLAG%
echo.
echo 已退出. 如果是启动失败, 请看上方的报错信息.
pause
