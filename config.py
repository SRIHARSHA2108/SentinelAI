import os
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent


class Config:
    SECRET_KEY = os.getenv("SECRET_KEY", "development-only-secret")
    SQLALCHEMY_DATABASE_URI = os.getenv(
        "DATABASE_URL", f"sqlite:///{BASE_DIR / 'instance' / 'surveillance.db'}"
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    EVIDENCE_DIR = Path(os.getenv("EVIDENCE_DIR", BASE_DIR / "instance" / "evidence"))
    UPLOAD_DIR = Path(os.getenv("UPLOAD_DIR", BASE_DIR / "instance" / "uploads"))
    YOLO_MODEL = os.getenv("YOLO_MODEL", "yolov8n.pt")
    ENABLE_YOLO = os.getenv("ENABLE_YOLO", "false").lower() in {"1", "true", "yes"}
    MAX_CONTENT_LENGTH = 1000 * 1024 * 1024
