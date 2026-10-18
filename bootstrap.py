"""Double-click launcher. Installs Playwright only when missing."""
from pathlib import Path
import importlib.util
import json
import subprocess
import sys
import urllib.request
import webbrowser

root = Path(__file__).resolve().parent
server_file = root / "data" / "server.json"
if server_file.exists():
    try:
        info = json.loads(server_file.read_text("utf-8"))
        url = info["url"]
        if url.startswith("http://127.0.0.1:"):
            with urllib.request.urlopen(url, timeout=2) as response:
                page = response.read(80000).decode("utf-8")
            if "LOCAL COMMENT WORKSPACE" in page or "MULTI-ACCOUNT COMMENT WORKSPACE" in page:
                webbrowser.open(url)
                raise SystemExit(0)
    except (OSError, ValueError, KeyError):
        pass

if not importlib.util.find_spec("playwright"):
    print("Installing Playwright. This is required only on the first run.", flush=True)
    subprocess.check_call([sys.executable, "-m", "pip", "install", "-r", str(root / "requirements.txt")])
subprocess.check_call([sys.executable, str(root / "app.py")])
