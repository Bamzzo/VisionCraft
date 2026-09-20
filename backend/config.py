import os
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:
    def load_dotenv(path: Path, override: bool = False) -> bool:
        """Fallback parser for the ordinary KEY=VALUE local .env format."""
        if not path.exists():
            return False
        for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            if key and (override or key not in os.environ):
                os.environ[key] = value.strip().strip('"').strip("'")
        return True


BASE_DIR = Path(__file__).resolve().parents[1]
ROOT_DIR = BASE_DIR.parent
FRONTEND_DIR = BASE_DIR / "frontend"


def _resolve_data_dir() -> Path:
    """Resolve the data directory, allowing an isolated one for offline runs.

    ``VISIONCRAFT_DATA_DIR`` is a process-level override. Acceptance and browser
    test runs need it: many of them create and delete projects, so without an
    override they would write into the working database and project assets.
    It is read at import time only, never from a loaded ``.env`` file, so a
    stray value in ``.env`` cannot silently redirect real application data.
    """
    override = os.getenv("VISIONCRAFT_DATA_DIR")
    if not override:
        return BASE_DIR / "backend" / "data"
    candidate = Path(override)
    if not candidate.is_absolute():
        # Relative paths are resolved against the repository root, so an
        # override like "output/playwright/stageC/isolated" lands inside the
        # checkout rather than beside it.
        candidate = BASE_DIR / candidate
    return candidate.resolve()


DATA_DIR = _resolve_data_dir()
PROJECTS_DIR = DATA_DIR / "projects"
CHROMA_DIR = DATA_DIR / "chroma"
DB_PATH = DATA_DIR / "visioncraft.db"


def init_environment() -> None:
    # The workspace-root .env is intentionally outside the tracked repository.
    # A repository-local .env remains supported for deployment environments.
    load_dotenv(ROOT_DIR / ".env")
    load_dotenv(BASE_DIR / ".env", override=False)
    if os.getenv("ARK_API_KEY"):
        os.environ.setdefault("VOLC_API_KEY", os.environ["ARK_API_KEY"])
    if os.getenv("ARK_IMAGE_MODEL"):
        os.environ.setdefault("VOLC_IMAGE_MODEL", os.environ["ARK_IMAGE_MODEL"])
    if os.getenv("ARK_VIDEO_MODEL"):
        os.environ.setdefault("VOLC_VIDEO_MODEL", os.environ["ARK_VIDEO_MODEL"])
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    PROJECTS_DIR.mkdir(parents=True, exist_ok=True)
    CHROMA_DIR.mkdir(parents=True, exist_ok=True)

