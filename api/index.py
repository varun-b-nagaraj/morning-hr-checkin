from pathlib import Path
import sys

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.append(str(BASE_DIR))

from app import app  # noqa: E402

# Vercel expects a WSGI app named "app"
