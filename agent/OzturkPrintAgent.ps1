<#
  Ozturk Print Agent - restoran ichidagi chek printerlari uchun agent.

  NIMA QILADI
  ===========
  Har `poll_interval_sec` soniyada ERPNext serveridan navbatdagi chop etish
  topshiriqlarini (`Ozturk Print Job`) oladi, ESC/POS baytlarni printerning
  IP:9100 portiga yuboradi va natijani serverga qaytaradi. Ulanish DOIM
  restorandan serverga (HTTPS) - VPN, port ochish kerak emas.

  Bog'liqlik YO'Q: faqat Windows PowerShell 5.1 (har bir Windows 10/11 da bor).

  ISHGA TUSHIRISH
  ===============
    powershell -ExecutionPolicy Bypass -File OzturkPrintAgent.ps1
    (config.json shu papkada bo'lishi kerak; install_agent.ps1 buni avtomatik
     Windows ishga tushganda ishlaydigan vazifa sifatida o'rnatadi)

  CONFIG.JSON
  ===========
    {
      "site_url": "https://ozturk.erpcontrol.uz",
      "api_key": "...", "api_secret": "...",
      "branch": "O'zTurk Maksim Gorkiy",
      "agent_name": "kassa-monoblok",
      "poll_interval_sec": 2,
      "heartbeat_interval_sec": 20,
      "printer_timeout_sec": 5
    }
#>

$ErrorActionPreference = "Stop"
$AgentVersion = "1.0.0"

# -- TLS 1.2 (Windows 10 PowerShell 5.1 standart holatda faqat TLS 1.0) --
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ConfigPath = Join-Path $ScriptDir "config.json"
$LogPath = Join-Path $ScriptDir "agent.log"
$MaxLogBytes = 5MB

function Write-Log {
    param([string]$Level, [string]$Message)
    $line = "{0} [{1}] {2}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Level, $Message
    try {
        if ((Test-Path $LogPath) -and ((Get-Item $LogPath).Length -gt $MaxLogBytes)) {
            Move-Item -Force $LogPath ($LogPath + ".1")
        }
        Add-Content -Path $LogPath -Value $line -Encoding UTF8
    } catch {}
    Write-Host $line
}

function Load-Config {
    if (-not (Test-Path $ConfigPath)) { throw "config.json topilmadi: $ConfigPath" }
    $cfg = Get-Content $ConfigPath -Raw -Encoding UTF8 | ConvertFrom-Json
    foreach ($k in @("site_url", "api_key", "api_secret", "branch")) {
        if (-not $cfg.$k) { throw "config.json da '$k' yo'q" }
    }
    if (-not $cfg.agent_name) { $cfg | Add-Member -NotePropertyName agent_name -NotePropertyValue $env:COMPUTERNAME }
    if (-not $cfg.poll_interval_sec) { $cfg | Add-Member -NotePropertyName poll_interval_sec -NotePropertyValue 2 }
    if (-not $cfg.heartbeat_interval_sec) { $cfg | Add-Member -NotePropertyName heartbeat_interval_sec -NotePropertyValue 20 }
    if (-not $cfg.printer_timeout_sec) { $cfg | Add-Member -NotePropertyName printer_timeout_sec -NotePropertyValue 5 }
    $cfg.site_url = $cfg.site_url.TrimEnd("/")
    return $cfg
}

$script:Cfg = Load-Config
$script:Headers = @{ "Authorization" = "token $($Cfg.api_key):$($Cfg.api_secret)"; "Accept" = "application/json" }

function Invoke-Api {
    param([string]$Method, [hashtable]$Body)
    $url = "$($Cfg.site_url)/api/method/ozturkapp.ozturkapp.api.print_agent.$Method"
    $json = ($Body | ConvertTo-Json -Compress -Depth 5)
    $resp = Invoke-RestMethod -Uri $url -Method Post -Headers $script:Headers `
        -ContentType "application/json; charset=utf-8" -Body ([Text.Encoding]::UTF8.GetBytes($json)) -TimeoutSec 20
    return $resp.message
}

function Send-ToPrinter {
    param([string]$PrinterHost, [int]$Port, [byte[]]$Data)
    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $timeoutMs = [int]$Cfg.printer_timeout_sec * 1000
        $async = $client.BeginConnect($PrinterHost, $Port, $null, $null)
        if (-not $async.AsyncWaitHandle.WaitOne($timeoutMs, $false)) {
            throw "Printer ${PrinterHost}:${Port} javob bermadi (timeout ${timeoutMs}ms)"
        }
        $client.EndConnect($async)
        $client.SendTimeout = $timeoutMs
        $stream = $client.GetStream()
        $stream.Write($Data, 0, $Data.Length)
        $stream.Flush()
        # Printer buferini bo'shatishga ozgina vaqt - ba'zi modellar ulanish
        # darhol yopilsa oxirgi baytlarni (kesish) tashlab yuboradi.
        Start-Sleep -Milliseconds 300
        $stream.Close()
    } finally {
        $client.Close()
    }
}

function Process-Job {
    param($Job)
    $label = "{0} [{1}] '{2}' -> {3}:{4}" -f $Job.job, $Job.job_type, $Job.title, $Job.host, $Job.port
    try {
        if (-not $Job.host) { throw "Printer manzili yo'q" }
        $bytes = [Convert]::FromBase64String($Job.payload)
        Send-ToPrinter -PrinterHost $Job.host -Port ([int]$Job.port) -Data $bytes
        Invoke-Api -Method "ack" -Body @{ job = $Job.job; ok = 1; agent = $Cfg.agent_name } | Out-Null
        Write-Log "INFO" "Chop etildi: $label ($($bytes.Length) bayt)"
    } catch {
        $err = $_.Exception.Message
        Write-Log "ERROR" "Xato: $label - $err"
        try {
            Invoke-Api -Method "ack" -Body @{ job = $Job.job; ok = 0; error = $err; agent = $Cfg.agent_name } | Out-Null
        } catch {
            Write-Log "ERROR" "ack yuborilmadi ($($Job.job)): $($_.Exception.Message)"
        }
    }
}

# -- Asosiy sikl -------------------------------------------------
Write-Log "INFO" "Ozturk Print Agent v$AgentVersion ishga tushdi. Sayt: $($Cfg.site_url), filial: $($Cfg.branch), agent: $($Cfg.agent_name)"

$lastHeartbeat = [DateTime]::MinValue
$backoff = 0
$serverDown = $false

while ($true) {
    try {
        if (((Get-Date) - $lastHeartbeat).TotalSeconds -ge [int]$Cfg.heartbeat_interval_sec) {
            $info = @{ version = $AgentVersion; host = $env:COMPUTERNAME; user = $env:USERNAME }
            $hb = Invoke-Api -Method "heartbeat" -Body @{ branch = $Cfg.branch; agent = $Cfg.agent_name; info = $info }
            $lastHeartbeat = Get-Date
            if ($serverDown) { Write-Log "INFO" "Server bilan aloqa tiklandi"; $serverDown = $false }
        }

        $result = Invoke-Api -Method "pull_jobs" -Body @{ branch = $Cfg.branch; agent = $Cfg.agent_name; limit = 10 }
        $jobs = @()
        if ($result -and $result.jobs) { $jobs = @($result.jobs) }
        foreach ($job in $jobs) { Process-Job -Job $job }

        $backoff = 0
        if ($jobs.Count -eq 0) { Start-Sleep -Seconds ([int]$Cfg.poll_interval_sec) }
    } catch {
        if (-not $serverDown) { Write-Log "WARN" "Server bilan aloqa yo'q: $($_.Exception.Message)"; $serverDown = $true }
        $backoff = [Math]::Min(30, [Math]::Max(5, $backoff * 2))
        Start-Sleep -Seconds $backoff
    }
}
