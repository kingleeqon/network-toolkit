@echo off
rem ===================================================================
rem  Push to GitHub via REST API (for machines that cannot reach github.com)
rem
rem  One-time setup (PAT needs 'repo' scope):
rem      setx GITHUB_TOKEN ghp_xxxxxx
rem      (setx applies to new terminals only - open a NEW terminal after)
rem
rem  Usage:
rem      scripts\push.cmd             push
rem      scripts\push.cmd --dry-run   show what would be pushed
rem
rem  If your machine can reach github.com directly, use: git push origin main
rem ===================================================================
cd /d "%~dp0.."

if not "%GITHUB_TOKEN%"=="" goto have_token
echo [ERROR] GITHUB_TOKEN is not set.
echo.
echo   Run this once (PAT needs repo scope):
echo       setx GITHUB_TOKEN ghp_xxxxxx
echo   Then open a NEW terminal and run this script again.
exit /b 2

:have_token
python scripts\push_via_api.py %*
if errorlevel 1 goto failed
echo.
echo [OK] push done
exit /b 0

:failed
echo.
echo [FAIL] push failed - see the error above
exit /b 1
