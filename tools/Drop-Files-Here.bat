@echo off
setlocal
title MinerU
call "%~dp0..\bin\env.cmd" || exit /b 1
if not "%~1"=="" goto loop
echo 用法: 把一个或多个 PDF / 图片 / Word / PPT / Excel 文件拖到本文件上.
echo 结果 .md 生成在源文件旁边, 默认 standard 档. 不需要先启动网页.
pause
exit /b 1
:loop
if "%~1"=="" goto done
echo.
echo 解析: "%~1"
"%PY%" %PYFLAGS% -m mineru.kit.main parse "%~1" -o "%~dpn1.md" --tier standard
shift
goto loop
:done
echo.
echo 全部结束, 结果在源文件旁边, 同名 .md
pause
