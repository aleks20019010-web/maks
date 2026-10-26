<#
.SYNOPSIS
    Очистка диска C:. С -DryRun — только считает.
#>

param(
    [switch]$DryRun,
    [switch]$IncludeRecycleBin,
    [switch]$IncludeWindowsOld,
    [switch]$Aggressive,
    [string]$ResultFile = ""
)

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$DataDir = Join-Path (Split-Path -Parent $ScriptDir) "data"
if (-not (Test-Path -LiteralPath $DataDir)) {
    New-Item -ItemType Directory -Path $DataDir -Force | Out-Null
}
if (-not $ResultFile) {
    $ResultFile = Join-Path $DataDir "cleanup_c_result.json"
}
$LogFile = Join-Path $DataDir "cleanup_c.log"

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

function Safe-Delete($path, $desc, $dangerous = $false) {
    if (!(Test-Path -LiteralPath $path)) {
        return [PSCustomObject]@{ Desc = $desc; Path = $path; Freed = 0; Status = "skip"; Dangerous = $dangerous }
    }
    $sizeBefore = Get-SizeGB $path

    if ($DryRun) {
        return [PSCustomObject]@{ Desc = $desc; Path = $path; Freed = $sizeBefore; Status = "dry"; Dangerous = $dangerous }
    }

    try {
        Get-ChildItem -LiteralPath $path -Force -ErrorAction SilentlyContinue |
            Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
        $sizeAfter = Get-SizeGB $path
        $freed = [math]::Round($sizeBefore - $sizeAfter, 2)
        Log "OK ($desc): освобождено $freed ГБ"
        return [PSCustomObject]@{ Desc = $desc; Path = $path; Freed = $freed; Status = "ok"; Dangerous = $dangerous }
    } catch {
        Log "FAIL ($desc): $($_.Exception.Message)"
        return [PSCustomObject]@{ Desc = $desc; Path = $path; Freed = 0; Status = "fail"; Dangerous = $dangerous }
    }
}

$startTime = Get-Date
$freeBefore = [math]::Round((Get-PSDrive C).Free / 1GB, 2)
$totalGB = [math]::Round(((Get-PSDrive C).Free + (Get-PSDrive C).Used) / 1GB, 1)

Log "=== Очистка C: ($(if ($DryRun) {'DRY-RUN'} else {'реальная'})) ==="
Log "Флаги: RecycleBin=$IncludeRecycleBin, WindowsOld=$IncludeWindowsOld, Aggressive=$Aggressive"

$targets = @()

# --- Безопасное (всегда) ---
$targets += Safe-Delete "$env:TEMP" "Temp пользователя"
$targets += Safe-Delete "$env:LOCALAPPDATA\Temp" "LocalAppData\Temp"
$targets += Safe-Delete "C:\Windows\Temp" "Windows Temp"
$targets += Safe-Delete "C:\Windows\SoftwareDistribution\Download" "Windows Update Download"
$targets += Safe-Delete "C:\Windows\Logs\CBS" "CBS логи"
$targets += Safe-Delete "C:\Windows\Logs\DISM" "DISM логи"
$targets += Safe-Delete "C:\ProgramData\Jellyfin\Server\transcodes" "Jellyfin transcodes"

# --- Опасное (по флагу) ---
if ($IncludeWindowsOld) {
    $targets += Safe-Delete "C:\Windows.old" "Windows.old (откат Windows)" $true
} else {
    Log "SKIP: Windows.old (нужен -IncludeWindowsOld)"
}

if ($IncludeRecycleBin) {
    if ($DryRun) {
        $rbPath = "C:\`$Recycle.Bin"
        $rbSize = Get-SizeGB $rbPath
        $targets += [PSCustomObject]@{ Desc = "Корзина C:"; Path = $rbPath; Freed = $rbSize; Status = "dry"; Dangerous = $false }
    } else {
        try {
            Clear-RecycleBin -DriveLetter C -Force -ErrorAction Stop
            Log "OK: корзина C: очищена"
            $targets += [PSCustomObject]@{ Desc = "Корзина C:"; Path = "C:\`$Recycle.Bin"; Freed = 0; Status = "ok"; Dangerous = $false }
        } catch {
            Log "FAIL: корзина C: — $($_.Exception.Message)"
            $targets += [PSCustomObject]@{ Desc = "Корзина C:"; Path = "C:\`$Recycle.Bin"; Freed = 0; Status = "fail"; Dangerous = $false }
        }
    }
} else {
    Log "SKIP: корзина (нужен -IncludeRecycleBin)"
}

# --- Агрессивное (по флагу) ---
if ($Aggressive -and -not $DryRun) {
    Log "DISM: StartComponentCleanup (5-15 минут)..."
    try {
        Dism.exe /Online /Cleanup-Image /StartComponentCleanup /ResetBase 2>&1 | Out-Null
        if ($LASTEXITCODE -eq 0) {
            Log "DISM: OK"
        } else {
            Log "DISM: exit code $LASTEXITCODE"
        }
    } catch {
        Log "DISM: ошибка — $($_.Exception.Message)"
    }

    if (Test-Path "C:\hiberfil.sys") {
        $sizeGB = [math]::Round((Get-Item "C:\hiberfil.sys" -Force).Length / 1GB, 2)
        try {
            powercfg /h off
            if ($LASTEXITCODE -eq 0) {
                Log "OK: гибернация отключена (~$sizeGB ГБ)"
                $targets += [PSCustomObject]@{ Desc = "hiberfil.sys"; Path = "C:\hiberfil.sys"; Freed = $sizeGB; Status = "ok"; Dangerous = $false }
            }
        } catch {
            Log "FAIL: гибернация — $($_.Exception.Message)"
        }
    }
}

$freeAfter = [math]::Round((Get-PSDrive C).Free / 1GB, 2)

# FIX: Freed — сумма по Details, а не FreeAfter - FreeBefore.
# Если файлы заняты (Remove-Item не удалил), Safe-Delete вернёт Freed=0.
# Get-PSDrive округляет до 0.1 ГБ, и 0.08 «теряется». Сумма по Details — честнее.
$freed = [math]::Round(($targets | Measure-Object -Property Freed -Sum).Sum, 2)
$predicted = $freed

$result = [PSCustomObject]@{
    Mode = if ($DryRun) { "dry" } else { "real" }
    TotalGB = $totalGB
    FreeBefore = $freeBefore
    FreeAfter = $freeAfter
    Freed = $freed
    Predicted = $predicted
    Flags = [PSCustomObject]@{
        IncludeRecycleBin = [bool]$IncludeRecycleBin
        IncludeWindowsOld = [bool]$IncludeWindowsOld
        Aggressive = [bool]$Aggressive
    }
    Details = $targets
    Timestamp = (Get-Date -Format 'yyyy-MM-dd HH:mm:ss')
}

$result | ConvertTo-Json -Depth 5 | Out-File -LiteralPath $ResultFile -Encoding UTF8

Log "=== ИТОГО: было $freeBefore, стало $freeAfter, освобождено $freed ГБ ==="