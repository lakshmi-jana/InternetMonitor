$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$stagingRoot = Join-Path $env:TEMP "InternetMonitor-package"
$packageRoot = Join-Path $stagingRoot "InternetMonitor"
$distRoot = Join-Path $projectRoot "dist"
$archive = Join-Path $distRoot "InternetMonitor.zip"

Remove-Item $stagingRoot -Recurse -Force -ErrorAction SilentlyContinue
New-Item $packageRoot -ItemType Directory -Force | Out-Null
New-Item $distRoot -ItemType Directory -Force | Out-Null

Copy-Item (Join-Path $projectRoot "internet_monitor") $packageRoot -Recurse
Get-ChildItem $packageRoot -Directory -Recurse -Filter "__pycache__" |
    Remove-Item -Recurse -Force
Copy-Item (Join-Path $projectRoot "run-monitor.bat") $packageRoot
Copy-Item (Join-Path $projectRoot "run-console.bat") $packageRoot
Copy-Item (Join-Path $projectRoot "config.example.json") $packageRoot
Copy-Item (Join-Path $projectRoot "README.md") $packageRoot

Remove-Item $archive -Force -ErrorAction SilentlyContinue
Compress-Archive -Path $packageRoot -DestinationPath $archive
Remove-Item $stagingRoot -Recurse -Force

Write-Output "Created $archive"
