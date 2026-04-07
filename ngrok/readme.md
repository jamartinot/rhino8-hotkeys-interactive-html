# Sticky Services Runbook (Local Python + ngrok)

Purpose: this is not a full setup guide. It is a record of what was configured and how to operate and troubleshoot it later.

## Setup Snapshot (What Was Done)

- Local static server is a Scheduled Task: `LocalStaticServer-8000`
- ngrok tunnel is a Scheduled Task: `Ngrok-8000`
- Local server serves only this folder:
  `C:\Users\gkayt\OneDrive\Documents\vscode\html\ngrok_tunneling_this_has_port_to_INTERNET`
- Port in use: `8000`
- Logs:
  - `C:\ProgramData\localserver\server-8000.txt`
  - `C:\ProgramData\ngrok\ngrok-8000.txt`

Important:
- All task create/update commands must be run in elevated PowerShell (Run as Administrator).
- ngrok auth token is user-scoped unless explicitly configured otherwise.

## Recent Session Notes (What We Changed And Why It Failed)

### What was done

1. Verified both tasks existed and could be started manually:
  - `LocalStaticServer-8000`
  - `Ngrok-8000`
2. Verified local server log had valid `200`/`304` request lines (server was serving files).
3. Verified ngrok failures were auth/context related earlier (`ERR_NGROK_4018`) when task context was wrong.
4. Checked task configuration and found:
  - trigger type: `Boot`
  - principal logon type: `Interactive`

### Why sticky auto-start after reboot was unreliable

- `Boot` + `Interactive` can miss startup depending on login/session timing.
- For user-scoped apps like this, `AtLogOn` is typically more reliable than `AtStartup`.

### Why the last update command failed

- The command that attempted to re-register tasks failed with `Access is denied`.
- Root cause: it was run in a non-elevated PowerShell, so `Register-ScheduledTask` could not modify task definitions.

### Concrete fix to apply (elevated PowerShell)

Run this in PowerShell as Administrator to make startup reliable at user login:

```powershell
$ErrorActionPreference = "Stop"
$tasks = "LocalStaticServer-8000","Ngrok-8000"

foreach($name in $tasks){
  $t = Get-ScheduledTask -TaskName $name
  $a = $t.Actions[0]
  $principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Highest
  $trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME

  Register-ScheduledTask -TaskName $name `
   -Action (New-ScheduledTaskAction -Execute $a.Execute -Argument $a.Arguments) `
   -Trigger $trigger `
   -Principal $principal `
   -Force | Out-Null
}
```

### Verify the fix

```powershell
Get-ScheduledTask -TaskName "LocalStaticServer-8000" | Select-Object TaskName,Triggers,Principal | Format-List
Get-ScheduledTask -TaskName "Ngrok-8000" | Select-Object TaskName,Triggers,Principal | Format-List
```

Expected:
- trigger should show `MSFT_TaskLogonTrigger`
- principal remains your user with `Interactive`

## Daily Operations (No Manual Foreground Commands)

### Start both sticky services

```powershell
Start-ScheduledTask -TaskName "LocalStaticServer-8000"
Start-ScheduledTask -TaskName "Ngrok-8000"
```

### Stop both sticky services

```powershell
Stop-ScheduledTask -TaskName "Ngrok-8000"
Stop-ScheduledTask -TaskName "LocalStaticServer-8000"
```

### Restart both sticky services

```powershell
Stop-ScheduledTask -TaskName "Ngrok-8000"
Stop-ScheduledTask -TaskName "LocalStaticServer-8000"
Start-ScheduledTask -TaskName "LocalStaticServer-8000"
Start-ScheduledTask -TaskName "Ngrok-8000"
```

## Health Checks

### Check task status

```powershell
Get-ScheduledTask -TaskName "LocalStaticServer-8000" | Get-ScheduledTaskInfo
Get-ScheduledTask -TaskName "Ngrok-8000" | Get-ScheduledTaskInfo
```

Interpretation:
- `LastTaskResult = 0` is success
- `LastTaskResult = 1` generally means command/runtime failure (check logs)
- `TaskName not found` means task was deleted or never registered

### Check local server responds

```powershell
Invoke-WebRequest http://127.0.0.1:8000
```

If this fails, ngrok cannot serve your content correctly.

## Logs

### Last 50 lines

```powershell
Get-Content C:\ProgramData\localserver\server-8000.txt -Tail 50
Get-Content C:\ProgramData\ngrok\ngrok-8000.txt -Tail 50
```

### Live tail

```powershell
Get-Content C:\ProgramData\localserver\server-8000.txt -Tail 50 -Wait
Get-Content C:\ProgramData\ngrok\ngrok-8000.txt -Tail 50 -Wait
```

Tip: open two PowerShell windows to watch both logs at the same time.

## Common Errors and What They Mean

### `ERR_NGROK_4018`

Meaning:
- ngrok auth token is missing for the account context running the task

Fix:
```powershell
ngrok config add-authtoken YOUR_NEW_AUTHTOKEN
Stop-ScheduledTask -TaskName "Ngrok-8000"
Start-ScheduledTask -TaskName "Ngrok-8000"
```

Concrete steps:
1. Run `ngrok config add-authtoken YOUR_NEW_AUTHTOKEN` in your normal user PowerShell session.
2. Restart only the ngrok task.
3. Check `Get-Content C:\ProgramData\ngrok\ngrok-8000.txt -Tail 50`.
4. Confirm the error is gone and a tunnel session appears.

### `Start-ScheduledTask ... cannot find the file specified`

Meaning:
- Task does not exist, or action path/arguments are broken

Fix:
- Recreate that task in elevated PowerShell

Concrete steps:
1. Check task existence:
  `Get-ScheduledTask -TaskName "Ngrok-8000"`
2. If not found, recreate the task from your saved task command block.
3. Start it again:
  `Start-ScheduledTask -TaskName "Ngrok-8000"`
4. Check status:
  `Get-ScheduledTask -TaskName "Ngrok-8000" | Get-ScheduledTaskInfo`

### `Access is denied` on `Register-ScheduledTask`

Meaning:
- Not running elevated PowerShell

Fix:
- Reopen PowerShell as Administrator and rerun task creation

Concrete steps:
1. Close current terminal.
2. Open PowerShell with Run as Administrator.
3. Re-run task registration command.
4. Verify task exists with `Get-ScheduledTask -TaskName "Ngrok-8000"`.

### Python log shows `WinError 10054`

Meaning:
- Client disconnected mid-request (usually harmless)

Fix:
- None, unless requests are consistently failing

Concrete steps:
1. Confirm local server still responds:
  `Invoke-WebRequest http://127.0.0.1:8000`
2. If it responds, ignore this message.
3. If it does not respond, restart local server task and recheck logs.

## Concrete Troubleshooting Flows

### Flow A: Both services look down

```powershell
Get-ScheduledTask -TaskName "LocalStaticServer-8000" | Get-ScheduledTaskInfo
Get-ScheduledTask -TaskName "Ngrok-8000" | Get-ScheduledTaskInfo
Start-ScheduledTask -TaskName "LocalStaticServer-8000"
Start-ScheduledTask -TaskName "Ngrok-8000"
Get-Content C:\ProgramData\localserver\server-8000.txt -Tail 50
Get-Content C:\ProgramData\ngrok\ngrok-8000.txt -Tail 50
```

Expected:
- Local log shows incoming requests
- ngrok log shows active session, no auth errors

### Flow B: Local works, public URL fails

```powershell
Invoke-WebRequest http://127.0.0.1:8000
Get-Content C:\ProgramData\ngrok\ngrok-8000.txt -Tail 50
```

If local works and ngrok log has auth errors, rotate/reapply token and restart ngrok task.

### Flow C: Task exists but fails immediately

```powershell
Get-ScheduledTask -TaskName "Ngrok-8000" | Get-ScheduledTaskInfo
Get-ScheduledTask -TaskName "Ngrok-8000" | Select-Object -ExpandProperty Actions | Format-List *
Get-Content C:\ProgramData\ngrok\ngrok-8000.txt -Tail 50
```

Check for:
- Wrong executable path
- Broken arguments
- Missing token

## Change Auth Token

```powershell
ngrok config add-authtoken YOUR_NEW_AUTHTOKEN
Stop-ScheduledTask -TaskName "Ngrok-8000"
Start-ScheduledTask -TaskName "Ngrok-8000"
Get-Content C:\ProgramData\ngrok\ngrok-8000.txt -Tail 50
```

## Change Served Folder (Keep Port 8000)

Use this when you want local server to expose a different folder.

```powershell
$newDir = "C:\path\to\new\folder"
$taskName = "LocalStaticServer-8000"
$arg = "-NoProfile -WindowStyle Hidden -Command `"py -3 -m http.server 8000 --directory `"$newDir`" *>> `"C:\ProgramData\localserver\server-8000.txt`"`""
$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $arg
$trigger = New-ScheduledTaskTrigger -AtStartup
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERNAME" -LogonType Interactive -RunLevel Highest

Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Principal $principal -Force
Stop-ScheduledTask -TaskName $taskName
Start-ScheduledTask -TaskName $taskName
```

Verify:
```powershell
Invoke-WebRequest http://127.0.0.1:8000
Get-Content C:\ProgramData\localserver\server-8000.txt -Tail 50
```

## Change Port (Local + ngrok)

If you move from `8000` to another port, both tasks must match.

Example with `9000`:

```powershell
$port = 9000

# Update local server task
$serveDir = "C:\Users\gkayt\OneDrive\Documents\vscode\html\ngrok_tunneling_this_has_port_to_INTERNET"
$localArg = "-NoProfile -WindowStyle Hidden -Command `"py -3 -m http.server $port --directory `"$serveDir`" *>> `"C:\ProgramData\localserver\server-$port.txt`"`""
$localAction = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $localArg
$trigger = New-ScheduledTaskTrigger -AtStartup
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERNAME" -LogonType Interactive -RunLevel Highest
Register-ScheduledTask -TaskName "LocalStaticServer-$port" -Action $localAction -Trigger $trigger -Principal $principal -Force

# Update ngrok task
$ngrokArg = "-NoProfile -WindowStyle Hidden -Command `"& 'C:\ProgramData\chocolatey\bin\ngrok.exe' http $port --log stdout *>> 'C:\ProgramData\ngrok\ngrok-$port.txt'`""
$ngrokAction = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $ngrokArg
Register-ScheduledTask -TaskName "Ngrok-$port" -Action $ngrokAction -Trigger $trigger -Principal $principal -Force

Start-ScheduledTask -TaskName "LocalStaticServer-$port"
Start-ScheduledTask -TaskName "Ngrok-$port"
```

## Quick Troubleshooting Bundle

Run this block first when something is wrong:

```powershell
Get-ScheduledTask -TaskName "LocalStaticServer-8000" | Get-ScheduledTaskInfo
Get-ScheduledTask -TaskName "Ngrok-8000" | Get-ScheduledTaskInfo
Invoke-WebRequest http://127.0.0.1:8000
Get-Content C:\ProgramData\localserver\server-8000.txt -Tail 50
Get-Content C:\ProgramData\ngrok\ngrok-8000.txt -Tail 50
```

Expected outcome:
- Local server task healthy
- ngrok task healthy
- localhost reachable
- logs show normal activity and no auth errors