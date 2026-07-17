@echo off
setlocal
cd /d "%~dp0"

"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Stop'; $root=(Resolve-Path '.').Path; $port=8766; $url='http://127.0.0.1:8766'; $existing=Get-CimInstance Win32_Process | Where-Object { $_.Name -match '^(python|pythonw|py)\.exe$' -and $_.CommandLine -like '*boss_java_apply_panel.py*' } | Select-Object -First 1; if (-not $existing) { $python=Join-Path $root '.venv\Scripts\python.exe'; if (Test-Path $python) { $panelArgs=@('boss_java_apply_panel.py','--port',[string]$port) } else { $launcher=Get-Command py -ErrorAction SilentlyContinue; if (-not $launcher) { throw 'Python runtime not found. Expected .venv\Scripts\python.exe or py launcher.' }; $python=$launcher.Source; $panelArgs=@('-3','boss_java_apply_panel.py','--port',[string]$port) }; $out=Join-Path $root 'boss_java_apply_panel.out.log'; $err=Join-Path $root 'boss_java_apply_panel.err.log'; Start-Process -FilePath $python -ArgumentList $panelArgs -WorkingDirectory $root -RedirectStandardOutput $out -RedirectStandardError $err -WindowStyle Hidden; Start-Sleep -Seconds 2 }; Start-Process $url"

endlocal
