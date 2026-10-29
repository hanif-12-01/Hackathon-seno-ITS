import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent

# Database config: default to SQLite in backend directory
DATABASE_URL = os.environ.get(
    "PANGANNADI_DATABASE_URL",
    f"sqlite:///{BASE_DIR}/pangannadi.db"
)
