from pathlib import Path
import sys

from PyInstaller.utils.hooks import collect_all, copy_metadata


project_root = Path(SPEC).resolve().parents[1]
sys.path.insert(0, str(project_root))

from release_assets import OCR_MODEL_NAMES, model_runtime_files, verify_release_assets


template_root = project_root / "documents" / "templates"
required_templates = (
    template_root / "buy_contract_template.docx",
    template_root / "sale_contract_one_seller_template.docx",
)
missing_templates = [str(path) for path in required_templates if not path.is_file()]
if missing_templates:
    raise FileNotFoundError(f"Required contract templates are missing: {missing_templates}")

datas = [
    (str(project_root / "streamlit_app.py"), "."),
    (str(template_root), "documents/templates"),
]
binaries = []
hiddenimports = ["app"]

for package in ("streamlit", "paddle", "paddleocr", "paddlex", "playwright", "docxtpl"):
    package_datas, package_binaries, package_hiddenimports = collect_all(package)
    datas += package_datas
    binaries += package_binaries
    hiddenimports += package_hiddenimports

# PaddleX checks these OCR dependencies through importlib.metadata at runtime.
# Their modules alone are insufficient in a frozen application.
for distribution in (
    "imagesize",
    "opencv-contrib-python",
    "pyclipper",
    "pypdfium2",
    "python-bidi",
    "shapely",
):
    datas += copy_metadata(distribution)

verified_assets = verify_release_assets(project_root)
for model_name in OCR_MODEL_NAMES:
    model_asset = verified_assets[f"ocr/{model_name}"]
    destination = f"bundled_assets/paddlex/official_models/{model_name}"
    datas.extend((str(path), destination) for path in model_runtime_files(model_asset))

browser_name, browser_asset = next(
    (name.removeprefix("browser/"), asset)
    for name, asset in verified_assets.items()
    if name.startswith("browser/")
)
datas.append((str(browser_asset.path), f"playwright-browsers/{browser_name}"))

a = Analysis(
    [str(project_root / "desktop_launcher.py")],
    pathex=[str(project_root)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["pytest", "pytest_asyncio"],
    noarchive=False,
    optimize=1,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="YavlenaKYCManager",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="YavlenaKYCManager",
)
