# Ngrok + Python Local Server Setup

This guide documents everything set up to serve one local folder with Python, tunnel it with ngrok, keep both services sticky after reboot, and verify or troubleshoot each step.

## What This Setup Does

- Starts a Python web server for only this folder:
  `C:\Users\gkayt\OneDrive\Documents\vscode\html\ngrok_tunneling_this_has_port_to_INTERNET`
- Tunnels that local server through ngrok on port `8000`
- Runs both services in the background after reboot
- Writes logs to text files so you can inspect them later

## Important Result

ngrok does not expose the whole GitHub repo. It only tunnels the local server listening on port `8000`, and that server only serves the target folder above.

## Prerequisites

- PowerShell running as Administrator for the scheduled task setup
- Python available on PATH or through the Python launcher (`py`)
- ngrok installed and authenticated

## One-Time Setup

### 1. Verify ngrok is installed

```powershell
ngrok version
```

Verify:
- You should see a version number

Troubleshoot:
- If PowerShell says ngrok is not recognized, reopen the terminal or add `C:\ProgramData\chocolatey\bin` to PATH.

### 2. Add or replace the ngrok auth token

```powershell
ngrok config add-authtoken YOUR_NEW_AUTHTOKEN
```

Verify:
- Run `ngrok http 8000`
- If ngrok starts without `ERR_NGROK_4018`, the token is working

Troubleshoot:
- If the token was already set under another account, the scheduled task may fail unless it runs under the same account context.
- If you pasted a real token before, rotate it in the ngrok dashboard.

### 3. Test the folder manually with Python

```powershell
cd "C:\Users\gkayt\OneDrive\Documents\vscode\html\ngrok_tunneling_this_has_port_to_INTERNET"
py -3 -m http.server 8000
```

Verify:
- Open `http://127.0.0.1:8000`
- You should see only that folder's contents

Troubleshoot:
- If port `8000` is in use, choose another port and update the commands below.
- If `py -3` fails, try `python -m http.server 8000`.

Stop the server with `Ctrl+C` after testing.

### 4. Create log folders

```powershell
New-Item -ItemType Directory -Path "C:\ProgramData\localserver" -Force
New-Item -ItemType Directory -Path "C:\ProgramData\ngrok" -Force
```

Verify:
- The folders should exist after the command runs

Troubleshoot:
- If a folder cannot be created, make sure PowerShell is running as Administrator.

## Sticky Local Server Task

This task starts the Python server automatically after reboot.

### 5. Create the local server scheduled task

```powershell
$ErrorActionPreference = "Stop"

$serveDir = "C:\Users\gkayt\OneDrive\Documents\vscode\html\ngrok_tunneling_this_has_port_to_INTERNET"
$port = 8000
$taskName = "LocalStaticServer-8000"
$logDir = "C:\ProgramData\localserver"
$logFile = Join-Path $logDir "server-8000.txt"

$arg = "-NoProfile -WindowStyle Hidden -Command `"py -3 -m http.server $port --directory `"$serveDir`" *>> `"$logFile`"`""
$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $arg
$trigger = New-ScheduledTaskTrigger -AtStartup
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERNAME" -LogonType Interactive -RunLevel Highest

Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Principal $principal -Force
```

Verify:
- The task should register without errors

Troubleshoot:
- If the command fails, verify the folder path is correct and the `py` launcher exists.
- If the scheduled task starts but the server does not respond, inspect the log file below.

### 6. Start the local server now

```powershell
Start-ScheduledTask -TaskName "LocalStaticServer-8000"
```

Verify:
- The task should show as running or ready
- Visit `http://127.0.0.1:8000`

Troubleshoot:
- If the page does not load, check the log file.

### 7. Check local server status and logs

```powershell
Get-ScheduledTask -TaskName "LocalStaticServer-8000" | Get-ScheduledTaskInfo
Get-Content C:\ProgramData\localserver\server-8000.txt -Tail 50
Get-Content C:\ProgramData\localserver\server-8000.txt -Tail 50 -Wait
```

Verify:
- Task status should show it is running or ready
- The log should mention the port and serving directory

Troubleshoot:
- If the log is empty, the task may not have started
- If the port is already in use, choose a new port and update the task

### 8. Stop or remove the local server task

```powershell
Stop-ScheduledTask -TaskName "LocalStaticServer-8000"
Unregister-ScheduledTask -TaskName "LocalStaticServer-8000" -Confirm:$false
```

## Sticky ngrok Task

This task starts ngrok automatically after reboot and tunnels the local server.

### 9. Create the ngrok scheduled task

```powershell
$ErrorActionPreference = "Stop"

$ngrok = (Get-Command ngrok).Source
$logDir = "C:\ProgramData\ngrok"
$logFile = Join-Path $logDir "ngrok-8000.txt"
$taskName = "Ngrok-8000"

New-Item -ItemType Directory -Path $logDir -Force | Out-Null

$arg = "-NoProfile -WindowStyle Hidden -Command `"& '$ngrok' http 8000 --log stdout *>> '$logFile'`""
$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $arg
$trigger = New-ScheduledTaskTrigger -AtStartup
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERNAME" -LogonType Interactive -RunLevel Highest

Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Principal $principal -Force
```

Verify:
- The task should register without errors

Troubleshoot:
- If ngrok auth fails under `SYSTEM`, use your user account instead so it can read the token from the same profile.

### 10. Start ngrok now

```powershell
Start-ScheduledTask -TaskName "Ngrok-8000"
```

Verify:
- `ngrok version` should still work
- `ngrok http 8000` should create a tunnel if the local server is running

Troubleshoot:
- If `ngrok http 8000` fails with `ERR_NGROK_4018`, the auth token is missing or tied to a different account context
- If the local server is not running yet, ngrok may start but the tunnel will not serve your folder content correctly

### 11. Check ngrok status and logs

```powershell
Get-ScheduledTask -TaskName "Ngrok-8000" | Get-ScheduledTaskInfo
Get-Content C:\ProgramData\ngrok\ngrok-8000.txt -Tail 50
Get-Content C:\ProgramData\ngrok\ngrok-8000.txt -Tail 50 -Wait
```

Verify:
- Task status should be running or ready
- The log should show the tunnel URL and session messages

Troubleshoot:
- If the log shows authentication failure, rerun `ngrok config add-authtoken YOUR_NEW_AUTHTOKEN`
- If the log shows the tunnel opening but the browser returns an error, confirm the local server is still running on port `8000`

### 12. Stop or remove the ngrok task

```powershell
Stop-ScheduledTask -TaskName "Ngrok-8000"
Unregister-ScheduledTask -TaskName "Ngrok-8000" -Confirm:$false
```

## Daily Use

Use these commands when you want to work with the services manually.

### Start both services manually

```powershell
cd "C:\Users\gkayt\OneDrive\Documents\vscode\html\ngrok_tunneling_this_has_port_to_INTERNET"
py -3 -m http.server 8000
```

In a second PowerShell window:

```powershell
ngrok http 8000
```

Verify:
- `http://127.0.0.1:8000` should show the local folder
- ngrok should print a public forwarding URL

Troubleshoot:
- If ngrok fails, check the ngrok log and confirm the token
- If the browser does not load locally, confirm the Python server is running

### Check whether both are running

```powershell
Get-ScheduledTask -TaskName "LocalStaticServer-8000" | Get-ScheduledTaskInfo
Get-ScheduledTask -TaskName "Ngrok-8000" | Get-ScheduledTaskInfo
```

### View the last 50 log lines

```powershell
Get-Content C:\ProgramData\localserver\server-8000.txt -Tail 50
Get-Content C:\ProgramData\ngrok\ngrok-8000.txt -Tail 50
```

### Watch logs live

```powershell
Get-Content C:\ProgramData\localserver\server-8000.txt -Tail 50 -Wait
Get-Content C:\ProgramData\ngrok\ngrok-8000.txt -Tail 50 -Wait
```

Open separate PowerShell windows if you want to watch both logs at the same time.

## Change Auth Token

Use this when you want to replace the ngrok token.

### 1. Get a new token

https://dashboard.ngrok.com/get-started/your-authtoken

### 2. Add the new token

```powershell
ngrok config add-authtoken YOUR_NEW_AUTHTOKEN
```

Verify:
- Run `ngrok http 8000`
- If it starts without auth errors, the new token is working

Troubleshoot:
- If the scheduled task still fails, stop and start it again so it reloads the token

## Common Troubleshooting

### ngrok says command not found

```powershell
where.exe ngrok
```

If nothing is returned, reopen PowerShell or add `C:\ProgramData\chocolatey\bin` to PATH.

### Python server does not start

```powershell
py -3 --version
python --version
```

If both fail, Python is not installed or is not on PATH.

### ngrok authentication fails

```powershell
Get-Content C:\ProgramData\ngrok\ngrok-8000.txt -Tail 100
```

If you see `ERR_NGROK_4018`, re-add the authtoken using the same account that runs the task.

### Port 8000 is already in use

```powershell
netstat -ano | findstr :8000
```

If another process is using the port, stop it or change the server port and update the task and ngrok command.

## Summary

- Python serves one folder on port `8000`
- ngrok tunnels that local port
- Both can start automatically at boot through scheduled tasks
- Logs are stored in `C:\ProgramData\localserver` and `C:\ProgramData\ngrok`
- Use the verification commands after each step to confirm the setup is working