@echo off
setlocal
if defined JOBPILOT_PYTHON goto run
if defined CONDA_PREFIX if exist "%CONDA_PREFIX%\python.exe" set "JOBPILOT_PYTHON=%CONDA_PREFIX%\python.exe"
if defined JOBPILOT_PYTHON goto run
if exist "D:\CondaEnvs\jobpilot\python.exe" set "JOBPILOT_PYTHON=D:\CondaEnvs\jobpilot\python.exe"
if not defined JOBPILOT_PYTHON set "JOBPILOT_PYTHON=python"
:run
"%JOBPILOT_PYTHON%" "%~dp0scripts\start_jobpilot.py" %*
exit /b %errorlevel%
