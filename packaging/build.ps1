<#
.SYNOPSIS
    Build AgeniusNote Lite Windows installer (.exe).

.DESCRIPTION
    1) Runs PyInstaller against packaging/ageniusnote_lite.spec
    2) Runs Inno Setup compiler (iscc) against packaging/installer.iss
    3) Drops final installer in dist/installer/

.PARAMETER Version
    Override version (default: read from packaging/VERSION).

.PARAMETER SkipBuild
    Skip PyInstaller, just run Inno Setup against existing dist/AgeniusNoteLite/.

.PARAMETER Iscc
    Full path to iscc.exe if not on PATH. Default tries common Inno Setup install paths.

.EXAMPLE
    .\packaging\build.ps1
    .\packaging\build.ps1 -Version 0.2.0
    .\packaging\build.ps1 -SkipBuild
#>
[CmdletBinding()]
param(
    [string]$Version,
    [switch]$SkipBuild,
    [string]$Iscc
)

$ErrorActionPreference = "Stop"

$AppDir = Split-Path -Parent $PSScriptRoot
Set-Location $AppDir

if (-not $Version) {
    $Version = (Get-Content (Join-Path $PSScriptRoot "VERSION") -Raw).Trim()
}
Write-Host ">> Building AgeniusNote Lite v$Version" -ForegroundColor Cyan

# Resolve a Python interpreter that has the project deps installed. Prefer the
# `py` launcher (CPython 3.13) over whatever `python` resolves to on PATH,
# because some environments (msys2, Anaconda) put a different python first and
# faster-whisper / PySide6 / pynput won't be installed there.
$Python = $null
$pyLauncher = Get-Command py -ErrorAction SilentlyContinue
if ($pyLauncher) {
    try {
        $candidate = & $pyLauncher.Source -3.13 -c "import sys; print(sys.executable)" 2>$null
        if ($LASTEXITCODE -eq 0 -and $candidate) { $Python = $candidate.Trim() }
    } catch { }
}
if (-not $Python) {
    $py313 = "$env:LocalAppData\Programs\Python\Python313\python.exe"
    if (Test-Path $py313) { $Python = $py313 }
}
if (-not $Python) {
    $onPath = Get-Command python -ErrorAction SilentlyContinue
    if ($onPath) { $Python = $onPath.Source }
}
if (-not $Python) { throw "No Python interpreter found." }
Write-Host ">> Using Python: $Python" -ForegroundColor Cyan

# Use a fresh timestamped build path each run so locked previous-build dirs
# (Windows Defender / Syncthing scanners) never block PyInstaller's rmtree.
$Stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$DistPath = "dist-build-$Stamp"
$WorkPath = "build-build-$Stamp"
Write-Host ">> Build path: $DistPath" -ForegroundColor Cyan

# --- 1. PyInstaller ---
if (-not $SkipBuild) {
    Write-Host ">> Running PyInstaller..." -ForegroundColor Cyan
    & $Python -m pip install --quiet --upgrade pyinstaller
    & $Python -m PyInstaller "packaging/ageniusnote_lite.spec" --noconfirm --clean --distpath $DistPath --workpath $WorkPath
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed (exit $LASTEXITCODE)" }
} else {
    Write-Host ">> Skipping PyInstaller (SkipBuild=true)" -ForegroundColor Yellow
}

# --- 2. Inno Setup ---
if (-not $Iscc) {
    $candidates = @(
        "$env:LocalAppData\Programs\Inno Setup 6\ISCC.exe",
        "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
        "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
        "$env:ProgramFiles\Inno Setup 5\ISCC.exe"
    )
    foreach ($c in $candidates) {
        if (Test-Path $c) { $Iscc = $c; break }
    }
}
if (-not $Iscc -or -not (Test-Path $Iscc)) {
    $onPath = Get-Command iscc -ErrorAction SilentlyContinue
    if ($onPath) { $Iscc = $onPath.Source }
}
if (-not $Iscc) {
    throw "Inno Setup compiler (iscc.exe) not found. Install from https://jrsoftware.org/isdl.php or pass -Iscc."
}
Write-Host ">> Using Inno Setup compiler: $Iscc" -ForegroundColor Cyan

$SrcDirParam = "..\$DistPath\AgeniusNoteLite"
$OutDirParam = "..\$DistPath\installer"
& $Iscc "/DAppVersion=$Version" "/DSrcDir=$SrcDirParam" "/DOutDir=$OutDirParam" "packaging/installer.iss"
if ($LASTEXITCODE -ne 0) { throw "Inno Setup failed (exit $LASTEXITCODE)" }

$outFile = Join-Path $AppDir "$DistPath\installer\AgeniusNoteLite-Setup-$Version.exe"
if (Test-Path $outFile) {
    Write-Host ">> SUCCESS" -ForegroundColor Green
    Write-Host "   Installer: $outFile"
    Write-Host "   Size:     $([math]::Round((Get-Item $outFile).Length / 1MB, 1)) MB"
} else {
    Write-Host ">> Build finished but installer not found at expected path: $outFile" -ForegroundColor Yellow
}
