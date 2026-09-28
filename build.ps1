param(
    [string]$OutputRoot = 'dist'
)

$ErrorActionPreference = 'Stop'
$taskRoot = [System.IO.Path]::GetFullPath($PSScriptRoot)
if ([string]::IsNullOrWhiteSpace($OutputRoot)) { throw 'OutputRoot must not be empty.' }
$taskOutputRoot = if ([System.IO.Path]::IsPathRooted($OutputRoot)) {
    [System.IO.Path]::GetFullPath($OutputRoot)
} else {
    [System.IO.Path]::GetFullPath((Join-Path $taskRoot $OutputRoot))
}
$taskOutputRoot = $taskOutputRoot.TrimEnd([char[]]'\/')
$taskRootPrefix = $taskRoot.TrimEnd([char[]]'\/') + [System.IO.Path]::DirectorySeparatorChar
if (-not $taskOutputRoot.StartsWith($taskRootPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw 'OutputRoot must be a subdirectory of this project.'
}
# Reject redirected ancestors so the normalized output remains in this project.
$taskAncestor = $taskOutputRoot
while ($taskAncestor.Length -ge $taskRoot.Length) {
    if (Test-Path -LiteralPath $taskAncestor) {
        $taskEntry = Get-Item -LiteralPath $taskAncestor -Force
        if (-not $taskEntry.PSIsContainer) { throw "Output path is not a directory: $taskAncestor" }
        if ($taskEntry.Attributes -band [System.IO.FileAttributes]::ReparsePoint) {
            throw "Output path must not contain a symbolic link or junction: $taskAncestor"
        }
    }
    if ($taskAncestor.Equals($taskRoot, [System.StringComparison]::OrdinalIgnoreCase)) { break }
    $taskAncestor = [System.IO.Path]::GetDirectoryName($taskAncestor.TrimEnd([char[]]'\/'))
}
$taskDist = Join-Path $taskOutputRoot 'PhoneTrace'
if (Test-Path -LiteralPath $taskDist) {
    throw "Refusing to overwrite existing application or recordings: $taskDist. Use a new -OutputRoot, for example dist/v0.1.1."
}
$taskPython = Join-Path $taskRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $taskPython)) { throw 'Create .venv and install requirements-dev.txt first.' }
Push-Location $taskRoot
$taskOriginalPath = $env:PATH
try {
    # Avoid collecting unrelated DLLs from other apps (for example Poppler's ICU).
    $env:PATH = "$(Join-Path $taskRoot '.venv\Scripts');$env:SystemRoot\System32;$env:SystemRoot"
    # Never use --noconfirm: an existing app may be running or contain real records.
    & $taskPython -m PyInstaller --clean --distpath $taskOutputRoot PhoneTrace.spec
    if ($LASTEXITCODE -ne 0) { throw 'PyInstaller failed.' }
    New-Item -ItemType Directory -Force -Path (Join-Path $taskDist 'tools\platform-tools') | Out-Null
    foreach ($taskFile in @('adb.exe','AdbWinApi.dll','AdbWinUsbApi.dll','NOTICE.txt','source.properties')) {
        Copy-Item -LiteralPath (Join-Path $taskRoot "tools\platform-tools\$taskFile") -Destination (Join-Path $taskDist 'tools\platform-tools') -Force
    }
    foreach ($taskFile in @('README.md','THIRD_PARTY.md','LICENSE','VALIDATION.md','CHANGELOG.md','CONTRIBUTING.md')) {
        Copy-Item -LiteralPath (Join-Path $taskRoot $taskFile) -Destination $taskDist -Force
    }
    Copy-Item -LiteralPath (Join-Path $taskRoot 'licenses') -Destination $taskDist -Recurse -Force
    $taskSource = Join-Path $taskDist 'source'
    New-Item -ItemType Directory -Force -Path $taskSource | Out-Null
    foreach ($taskFile in @('main.py','build.ps1','PhoneTrace.spec','requirements.txt','requirements-dev.txt','README.md','THIRD_PARTY.md','LICENSE','INTERFACES.md','VALIDATION.md','CHANGELOG.md','CONTRIBUTING.md')) {
        Copy-Item -LiteralPath (Join-Path $taskRoot $taskFile) -Destination $taskSource -Force
    }
    foreach ($taskFolder in @('phonetrace','tests','scripts')) {
        $taskSourceFolder = Join-Path $taskSource $taskFolder
        New-Item -ItemType Directory -Force -Path $taskSourceFolder | Out-Null
        Get-ChildItem -LiteralPath (Join-Path $taskRoot $taskFolder) -Filter '*.py' -File | ForEach-Object {
            Copy-Item -LiteralPath $_.FullName -Destination $taskSourceFolder -Force
        }
    }
    foreach ($taskFolder in @('licenses','tools')) {
        Copy-Item -LiteralPath (Join-Path $taskDist $taskFolder) -Destination $taskSource -Recurse -Force
    }
    Copy-Item -LiteralPath (Join-Path $taskRoot 'assets') -Destination $taskSource -Recurse -Force
    Copy-Item -LiteralPath (Join-Path $taskRoot 'docs') -Destination $taskDist -Recurse -Force
    Copy-Item -LiteralPath (Join-Path $taskRoot 'docs') -Destination $taskSource -Recurse -Force
    Write-Output "Built: $taskDist\PhoneTrace.exe"
}
finally {
    $env:PATH = $taskOriginalPath
    Pop-Location
}
