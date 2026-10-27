$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$pythonw = Join-Path $projectRoot 'venv\Scripts\pythonw.exe'
$entryPoint = Join-Path $projectRoot 'facetouch_pro_desktop.pyw'
if (-not (Test-Path -LiteralPath $pythonw) -or -not (Test-Path -LiteralPath $entryPoint)) {
    throw 'Missing FaceTouch Pro launcher or venv Python. Run from the complete project.'
}

# Generate a small FC icon locally; no download or dependency installation.
Add-Type -AssemblyName System.Drawing
Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public static class FaceCheckIconHandle {
    [DllImport("user32.dll")]
    public static extern bool DestroyIcon(IntPtr handle);
}
'@
$assetDirectory = Join-Path $projectRoot 'assets'
New-Item -ItemType Directory -Path $assetDirectory -Force | Out-Null
$iconPath = Join-Path $assetDirectory 'facecheck.ico'
$customIconPath = Join-Path $assetDirectory 'facecheck-logo.ico'
if (Test-Path -LiteralPath $customIconPath) {
    $iconPath = $customIconPath
} else {
$bitmap = New-Object System.Drawing.Bitmap 64,64
$graphics = [System.Drawing.Graphics]::FromImage($bitmap)
$font = New-Object System.Drawing.Font 'Segoe UI',24,([System.Drawing.FontStyle]::Bold)
$background = New-Object System.Drawing.SolidBrush ([System.Drawing.Color]::FromArgb(23,92,211))
$graphics.FillRectangle($background,0,0,64,64)
$graphics.TextRenderingHint = [System.Drawing.Text.TextRenderingHint]::AntiAliasGridFit
$graphics.DrawString('FT',$font,[System.Drawing.Brushes]::White,3,10)
$iconHandle = $bitmap.GetHicon()
$icon = [System.Drawing.Icon]::FromHandle($iconHandle)
$stream = [System.IO.File]::Open($iconPath,[System.IO.FileMode]::Create)
try { $icon.Save($stream) }
finally {
    $stream.Dispose()
    $icon.Dispose()
    [FaceCheckIconHandle]::DestroyIcon($iconHandle) | Out-Null
    $font.Dispose()
    $background.Dispose()
    $graphics.Dispose()
    $bitmap.Dispose()
}
}

$desktopDirectory = [Environment]::GetFolderPath('DesktopDirectory')
if (-not $desktopDirectory) { throw 'Windows Desktop directory not found.' }
$shortcutPath = Join-Path $desktopDirectory 'FaceTouch Pro.lnk'
$shell = New-Object -ComObject WScript.Shell
$oldShortcutPath = Join-Path $desktopDirectory 'FaceCheck.lnk'
# Migrate only our own legacy shortcut; preserve any unrelated user shortcut.
if ((Test-Path -LiteralPath $oldShortcutPath) -and -not (Test-Path -LiteralPath $shortcutPath)) {
    $oldShortcut = $shell.CreateShortcut($oldShortcutPath)
    $oldEntry = Join-Path $projectRoot 'facecheck_desktop.pyw'
    if ($oldShortcut.TargetPath -eq $pythonw -and $oldShortcut.Arguments -eq ('"' + $oldEntry + '"')) {
        Move-Item -LiteralPath $oldShortcutPath -Destination $shortcutPath
    }
}
$shortcut = $shell.CreateShortcut($shortcutPath)
if ((Test-Path -LiteralPath $shortcutPath) -and $shortcut.TargetPath -and
    $shortcut.TargetPath -ne $pythonw) {
    throw "A different shortcut already exists: $shortcutPath"
}
$shortcut.TargetPath = $pythonw
$shortcut.Arguments = '"' + $entryPoint + '"'
$shortcut.WorkingDirectory = $projectRoot
$shortcut.Description = 'FaceTouch Pro - Khuon mat, van tay va kiem soat ra vao'
$shortcut.IconLocation = "$iconPath,0"
$shortcut.WindowStyle = 1
$shortcut.Save()
Write-Output "Created: $shortcutPath"
Write-Output "Target: $($shortcut.TargetPath) $($shortcut.Arguments)"
