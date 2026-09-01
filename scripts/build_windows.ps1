[CmdletBinding()]
param(
    [switch]$SkipInstaller,
    [ValidatePattern('^\.[A-Za-z0-9._-]+$')]
    [string]$BuildEnvironmentName = ".packaging-venv"
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
if ($BuildEnvironmentName -in @(".", "..")) {
    throw "The packaging environment name must identify a project-local child directory."
}
$buildEnvironment = Join-Path $projectRoot $BuildEnvironmentName
$python = Join-Path $buildEnvironment "Scripts\python.exe"
$lockFile = Join-Path $projectRoot "requirements-packaging.lock.txt"
$browserRoot = Join-Path $projectRoot "packaging\playwright-browsers"
$ocrRoot = Join-Path $projectRoot ".local\paddlex"
$spec = Join-Path $projectRoot "packaging\YavlenaKYCManager.spec"
$installerScript = Join-Path $projectRoot "packaging\YavlenaKYCManager.iss"

if (-not $IsWindows -and $env:OS -ne "Windows_NT") {
    throw "The Windows application must be built on Windows."
}
if (-not (Get-Command py -ErrorAction SilentlyContinue)) {
    throw "The Python launcher is required on the Windows build machine."
}
if (-not (Test-Path -LiteralPath $lockFile -PathType Leaf)) {
    throw "The pinned Windows packaging lock file is missing."
}
$expectedBuildRoot = [System.IO.Path]::GetFullPath($buildEnvironment)
if (-not $expectedBuildRoot.StartsWith($projectRoot + [System.IO.Path]::DirectorySeparatorChar)) {
    throw "The packaging environment resolved outside the project directory."
}

Push-Location $projectRoot
try {
    & py -3.11 -m venv --clear $buildEnvironment
    if ($LASTEXITCODE -ne 0) { throw "A clean Python 3.11 packaging environment could not be created." }

    & $python -m pip install --requirement $lockFile
    if ($LASTEXITCODE -ne 0) { throw "Packaging dependencies could not be installed." }

    & $python -c "import importlib.metadata as m; owners=sorted(d.metadata['Name'].lower() for d in m.distributions() if d.metadata['Name'].lower() in {'opencv-contrib-python','opencv-python','opencv-python-headless'}); assert owners == ['opencv-contrib-python'], f'Conflicting OpenCV distributions: {owners}'; import cv2; print(f'OpenCV {cv2.__version__} is provided by {owners[0]}.')"
    if ($LASTEXITCODE -ne 0) { throw "The packaging environment contains conflicting OpenCV distributions." }

    $previousBrowserRoot = $env:PLAYWRIGHT_BROWSERS_PATH
    try {
        $env:PLAYWRIGHT_BROWSERS_PATH = $browserRoot
        & $python -m playwright install chromium --no-shell
        if ($LASTEXITCODE -ne 0) { throw "The packaged Chromium runtime could not be prepared." }
    }
    finally {
        if ($null -eq $previousBrowserRoot) {
            Remove-Item Env:PLAYWRIGHT_BROWSERS_PATH -ErrorAction SilentlyContinue
        }
        else {
            $env:PLAYWRIGHT_BROWSERS_PATH = $previousBrowserRoot
        }
    }

    $previousOcrRoot = $env:PADDLE_PDX_CACHE_HOME
    try {
        $env:PADDLE_PDX_CACHE_HOME = $ocrRoot
        & $python -c "from ocr import PaddleOcrEngine; PaddleOcrEngine(); print('OCR models are ready.')"
        if ($LASTEXITCODE -ne 0) { throw "The packaged OCR models could not be prepared." }
    }
    finally {
        if ($null -eq $previousOcrRoot) {
            Remove-Item Env:PADDLE_PDX_CACHE_HOME -ErrorAction SilentlyContinue
        }
        else {
            $env:PADDLE_PDX_CACHE_HOME = $previousOcrRoot
        }
    }

    $requiredAssets = @(
        (Join-Path $projectRoot "documents\templates\buy_contract_template.docx"),
        (Join-Path $projectRoot "documents\templates\sale_contract_one_seller_template.docx"),
        (Join-Path $ocrRoot "official_models\PP-OCRv5_mobile_det\inference.json"),
        (Join-Path $ocrRoot "official_models\cyrillic_PP-OCRv5_mobile_rec\inference.json"),
        (Join-Path $ocrRoot "official_models\PP-LCNet_x1_0_doc_ori\inference.json")
    )
    foreach ($asset in $requiredAssets) {
        if (-not (Test-Path -LiteralPath $asset -PathType Leaf)) {
            throw "Required release asset is missing: $asset"
        }
    }
    if (-not (Get-ChildItem -LiteralPath $browserRoot -Recurse -File -Filter "chrome.exe" -ErrorAction SilentlyContinue)) {
        throw "The packaged headed Chromium executable is missing."
    }

    & $python -c "from pathlib import Path; from release_assets import verify_release_assets; verify_release_assets(Path.cwd()); print('Release asset hashes are verified.')"
    if ($LASTEXITCODE -ne 0) { throw "The packaged OCR or Chromium assets do not match the reviewed manifest." }

    & $python -m pytest -q
    if ($LASTEXITCODE -ne 0) { throw "Tests failed; no package was created." }

    & $python -m PyInstaller --noconfirm --clean $spec
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller did not create the application bundle." }

    $bundleExecutable = Join-Path $projectRoot "dist\YavlenaKYCManager\YavlenaKYCManager.exe"
    & $python (Join-Path $projectRoot "scripts\smoke_windows_bundle.py") --executable $bundleExecutable --browser-root $browserRoot
    if ($LASTEXITCODE -ne 0) { throw "The packaged application failed its end-to-end smoke check." }

    if (-not $SkipInstaller) {
        $isccCandidates = @(@(
            "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe",
            "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
            "$env:ProgramFiles\Inno Setup 6\ISCC.exe"
        ) | Where-Object { $_ -and (Test-Path -LiteralPath $_ -PathType Leaf) })
        if ($isccCandidates.Count -eq 0) {
            throw "Inno Setup 6 was not found. Install it or build with -SkipInstaller."
        }
        # Assign the selected element before using the call operator so the
        # full path (including spaces) is treated as one command.
        $isccCompiler = $isccCandidates[0]
        & $isccCompiler $installerScript
        if ($LASTEXITCODE -ne 0) { throw "The Windows installer could not be created." }
    }
}
finally {
    Pop-Location
}
