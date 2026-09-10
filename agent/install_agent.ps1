<#
  Ozturk Print Agent - o'rnatish (administrator PowerShell).

    powershell -ExecutionPolicy Bypass -File install_agent.ps1
    powershell -ExecutionPolicy Bypass -File install_agent.ps1 -Uninstall

  Nima qiladi:
    1. Fayllarni C:\OzturkPrintAgent ga ko'chiradi (config.json bo'lsa saqlaydi).
    2. "OzturkPrintAgent" nomli Vazifa (Task Scheduler) yaratadi:
       Windows ishga tushganda, SYSTEM nomidan, yashirin oynada, yiqilsa
       1 daqiqada qayta ishga tushadi.
    3. Vazifani darhol ishga tushiradi.
#>
param([switch]$Uninstall)

$ErrorActionPreference = "Stop"
$TaskName = "OzturkPrintAgent"
$Target = "C:\OzturkPrintAgent"
$Src = Split-Path -Parent $MyInvocation.MyCommand.Path

if ($Uninstall) {
    schtasks /End /TN $TaskName 2>$null | Out-Null
    schtasks /Delete /TN $TaskName /F 2>$null | Out-Null
    Get-Process powershell -ErrorAction SilentlyContinue | Where-Object { $_.CommandLine -like "*OzturkPrintAgent.ps1*" } | Stop-Process -Force -ErrorAction SilentlyContinue
    Write-Host "O'chirildi: vazifa '$TaskName'. Papka qoldirildi: $Target"
    exit 0
}

New-Item -ItemType Directory -Force -Path $Target | Out-Null
Copy-Item -Force (Join-Path $Src "OzturkPrintAgent.ps1") $Target
if (-not (Test-Path (Join-Path $Target "config.json"))) {
    if (Test-Path (Join-Path $Src "config.json")) {
        Copy-Item (Join-Path $Src "config.json") $Target
    } else {
        Copy-Item (Join-Path $Src "config.example.json") (Join-Path $Target "config.json")
        Write-Warning "config.json namunadan yaratildi - $Target\config.json ni to'ldiring!"
    }
}

$action = "powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$Target\OzturkPrintAgent.ps1`""

schtasks /Delete /TN $TaskName /F 2>$null | Out-Null
schtasks /Create /TN $TaskName /SC ONSTART /RU "SYSTEM" /RL HIGHEST /F /TR $action | Out-Null

# Qo'shimcha sozlamalar (yiqilsa qayta ishga tushirish, vaqt chegarasi yo'q)
$task = Get-ScheduledTask -TaskName $TaskName
$settings = New-ScheduledTaskSettingsSet -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew -StartWhenAvailable `
    -DontStopIfGoingOnBatteries -AllowStartIfOnBatteries
Set-ScheduledTask -TaskName $TaskName -Settings $settings | Out-Null

schtasks /Run /TN $TaskName | Out-Null
Start-Sleep -Seconds 3
Write-Host "O'rnatildi. Holat:"
schtasks /Query /TN $TaskName /FO LIST | Select-String "TaskName|Status|Next Run"
Write-Host "Log: $Target\agent.log"
