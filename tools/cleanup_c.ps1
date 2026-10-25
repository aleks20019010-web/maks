<#
.SYNOPSIS
    Очистка диска C:. С -DryRun — только считает.
#>

param(
    [switch]$DryRun,
    [switch]$Aggressive,
    [string]$ResultFile = "D:\cleanup_c_result.json"
)

$LogFile = "D:\cleanup_c.log"

function Log($msg) {
    $line = "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] $msg"
    Write-Host $line
    Add-Content -LiteralPath $LogFile -Value $line -Encoding UTF8 -ErrorAction SilentlyContinue
}

function Get-SizeGB($path) {
    if (!(Test-Path -LiteralPath $path)) { return 0 }
    $sum = (Get-ChildItem -LiteralPath $path -Recurse -File -Force -ErrorAction SilentlyContinue |
        Measure-Object -Property Length -Sum).Sum
    if ($null -eq $sum) { return 0 }
    return [math]::Round($sum / 1GB, 2)
}

function Safe-Delete($path, $desc) {
    if (!(Test-Path -LiteralPath $path)) {
        return @{ Desc = $desc; Path = $path; Freed = 0; Status = "skip" }
    }
    $sizeBefore = Get-SizeGB $path

    if ($DryRun) {
        return @{ Desc = $desc; Path = $path; Freed = $sizeBefore; Status = "dry" }
    }

    try {
        Get-ChildItem -LiteralPath $path -Force -ErrorAction SilentlyContinue |
            Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
        $sizeAfter = Get-SizeGB $path
        $freed = [math]::Round($sizeBefore - $sizeAfter, 2)
        Log "OK ($desc): освобождено $freed ГБ"
        return @{ Desc = $desc; Path = $path; Freed = $freed; Status = "ok" }
    } catch {
        Log "FAIL ($desc): $($_.Exception.Message)"
        return @{ Desc = $desc; Path = $path; Freed = 0; Status = "fail" }
    }
}

$startTime = Get-Date
$freeBefore = [math]::Round((Get-PSDrive C).Free / 1GB, 2)
$totalGB = [math]::Round(((Get-PSDrive C).Free + (Get-PSDrive C).Used) / 1GB, 1)

Log "=== Очистка C: ($(if ($DryRun) {'DRY-RUN'} else {'реальная'})) ==="

$targets = @()

# Temp
$targets += Safe-Delete "$env:TEMP" "Temp пользователя"
$targets += Safe-Delete "$env:LOCALAPPDATA\Temp" "LocalAppData\Temp"
$targets += Safe-Delete "C:\Windows\Temp" "Windows Temp"

# Windows Update
$targets += Safe-Delete "C:\Windows\SoftwareDistribution\Download" "Windows Update Download"

# Логи
$targets += Safe-Delete "C:\Windows\Logs\CBS" "CBS логи"
$targets += Safe-Delete "C:\Windows\Logs\DISM" "DISM логи"
$targets += Safe-Delete "C:\Windows\Prefetch" "Prefetch"

# Jellyfin
$targets += Safe-Delete "C:\ProgramData\Jellyfin\Server\transcodes" "Jellyfin transcodes"

# Windows.old
$targets += Safe-Delete "C:\Windows.old" "Windows.old"

# Корзина
if (-not $DryRun) {
    try { Clear-RecycleBin -Force -ErrorAction SilentlyContinue; Log "OK: корзина очищена" } catch {}
}

# Агрессивно: WinSxS + гибернация
if ($Aggressive -and -not $DryRun) {
    Log "DISM: StartComponentCleanup (5-15 минут)..."
    try {
        Dism.exe /Online /Cleanup-Image /StartComponentCleanup /ResetBase 2>&1 | Out-Null
        Log "DISM: OK"
    } catch { Log "DISM: ошибка" }

    if (Test-Path "C:\hiberfil.sys") {
        $sizeGB = [math]::Round((Get-Item "C:\hiberfil.sys" -Force).Length / 1GB, 2)
        try { powercfg /h off; Log "OK: гибернация отключена (~$sizeGB ГБ)"; $targets += @{ Desc = "hiberfil.sys"; Freed = $sizeGB; Status = "ok" } } catch {}
    }
}

$freeAfter = [math]::Round((Get-PSDrive C).Free / 1GB, 2)
$freed = [math]::Round($freeAfter - $freeBefore, 2)
$predicted = [math]::Round(($targets | Measure-Object -Property Freed -Sum).Sum, 2)

$result = @{
    Mode = if ($DryRun) { "dry" } else { "real" }
    TotalGB = $totalGB
    FreeBefore = $freeBefore
    FreeAfter = $freeAfter
    Freed = $freed
    Predicted = $predicted
    Details = $targets
    Timestamp = (Get-Date -Format 'yyyy-MM-dd HH:mm:ss')
}

# JSON-вывод — его прочитает бот
$result | ConvertTo-Json -Depth 5 | Out-File -LiteralPath $ResultFile -Encoding UTF8

Log "=== ИТОГО: было $freeBefore, стало $freeAfter, освобождено $freed ГБ ==="