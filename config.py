import os
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
IS_VERCEL = bool(os.getenv("VERCEL"))
RUNTIME_DIR = Path("/tmp/sentinel-ai") if IS_VERCEL else BASE_DIR / "instance"


class Config:
    SECRET_KEY = os.getenv("SECRET_KEY", "development-only-secret")
    SQLALCHEMY_DATABASE_URI = os.getenv(
        "DATABASE_URL", f"sqlite:///{RUNTIME_DIR / 'surveillance.db'}"
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    EVIDENCE_DIR = Path(os.getenv("EVIDENCE_DIR", RUNTIME_DIR / "evidence"))
    UPLOAD_DIR = Path(os.getenv("UPLOAD_DIR", RUNTIME_DIR / "uploads"))
    YOLO_MODEL = os.getenv("YOLO_MODEL", "yolov8n.pt")
    ENABLE_YOLO = os.getenv("ENABLE_YOLO", "false").lower() in {"1", "true", "yes"}
    # Vercel Functions enforce a 4.5 MB request-body limit. Local deployments
    # retain the project's 1000 MB upload capability.
    MAX_CONTENT_LENGTH = (4 if IS_VERCEL else 1000) * 1024 * 1024
    SERVERLESS = IS_VERCEL
