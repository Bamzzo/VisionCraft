from pathlib import Path

from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parents[1]
ROOT_DIR = BASE_DIR.parent
FRONTEND_DIR = BASE_DIR / "frontend"
DATA_DIR = BASE_DIR / "backend" / "data"
PROJECTS_DIR = DATA_DIR / "projects"
CHROMA_DIR = DATA_DIR / "chroma"
DB_PATH = DATA_DIR / "visioncraft.db"


def init_environment() -> None:
    load_dotenv(BASE_DIR / ".env")
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    PROJECTS_DIR.mkdir(parents=True, exist_ok=True)
    CHROMA_DIR.mkdir(parents=True, exist_ok=True)

