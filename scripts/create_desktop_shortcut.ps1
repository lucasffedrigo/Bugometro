$ErrorActionPreference = "Stop"

$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$appName = "Bug$([char]0x00F4)metro"
$launcherPath = Join-Path $projectRoot "$appName.pyw"
$iconPath = Join-Path $projectRoot "app\assets\bug_hunter_logo.ico"
$desktopPath = [Environment]::GetFolderPath("Desktop")
$shortcutPath = Join-Path $desktopPath "$appName.lnk"

if (-not (Test-Path -LiteralPath $launcherPath)) {
    throw "Launcher nao encontrado: $launcherPath"
}

if (-not (Test-Path -LiteralPath $iconPath)) {
    throw "Icone nao encontrado: $iconPath"
}

$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($shortcutPath)
$shortcut.TargetPath = $launcherPath
$shortcut.WorkingDirectory = $projectRoot
$shortcut.IconLocation = "$iconPath,0"
$shortcut.Description = "Abre o $appName sem terminal"
$shortcut.Save()

Write-Host "Atalho criado em: $shortcutPath"
