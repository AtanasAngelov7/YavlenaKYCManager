# Yavlena KYC Manager

A small, local application for extracting identity-document data, reviewing it, and eventually entering approved values into an authorized website workflow.

The current implementation covers separate front/back uploads, case storage, image/PDF preprocessing, PaddleOCR, conservative Bulgarian identity-field parsing, validation, operator review, and JSON output. Website automation is intentionally not configured until the target website and its permitted workflow are known.

## Requirements

- Windows with Python 3.11 recommended.
- Internet access during the initial installation and first PaddleOCR model download.
- Documents that you are authorized to process.

## Setup

Open PowerShell in the project directory:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

PaddleOCR downloads its small mobile detection model and Bulgarian Cyrillic recognition model on first use. They are cached under the ignored project-local `.local/` directory; later OCR processing runs locally.

## Run

The simplest option on Windows is to double-click `run.cmd`, or run it from PowerShell:

```powershell
.\run.cmd
```

You can also start the application without activating the virtual environment:

```powershell
.\.venv\Scripts\python.exe -m streamlit run app.py --server.headless true --browser.gatherUsageStats false
```

When startup succeeds, open `http://localhost:8501` if the browser does not open automatically. Uploaded files and results are written under `cases/`, which is excluded from Git because it may contain personal data.

Running only `streamlit run app.py` works after activating `.venv`, but `streamlit` is not installed globally and therefore may not be found in a new terminal.

## Test

```powershell
python -m pip install -r requirements-dev.txt
python -m pytest
```

## Current limitations

- The initial parser is generic and must be tuned against authorized sample documents.
- MRZ parsing is not implemented yet.
- Automatic perspective correction is not implemented yet.
- Website automation and download retrieval are not configured yet.
- The workflow processes one case at a time.
- CPU inference favors compatibility over maximum speed on Windows.

See [documents/solution.md](documents/solution.md) for the agreed design and [documents/progress.md](documents/progress.md) for current progress.
