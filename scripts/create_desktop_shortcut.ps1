$ErrorActionPreference = "Stop"

$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$launcherPath = Join-Path $projectRoot "Bug Voice Reporter.pyw"
$iconPath = Join-Path $projectRoot "app\assets\bug_hunter_logo.ico"
$desktopPath = [Environment]::GetFolderPath("Desktop")
$shortcutPath = Join-Path $desktopPath "Bug Voice Reporter.lnk"

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
$shortcut.Description = "Abre o Bug Voice Reporter sem terminal"
$shortcut.Save()

Write-Host "Atalho criado em: $shortcutPath"
