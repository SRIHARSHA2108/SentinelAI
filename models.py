from datetime import datetime, timezone
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import check_password_hash, generate_password_hash


db = SQLAlchemy()


def utcnow():
    return datetime.now(timezone.utc)


class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(150), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(30), default="monitor", nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow)

    def set_password(self, password):
        # PBKDF2 works on Apple/Xcode Python builds that do not expose hashlib.scrypt.
        self.password_hash = generate_password_hash(password, method="pbkdf2:sha256:600000")
    def check_password(self, password): return check_password_hash(self.password_hash, password)


class Camera(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    location = db.Column(db.String(150), nullable=False)
    stream_url = db.Column(db.String(500), nullable=False, default="0")
    status = db.Column(db.String(30), default="offline")
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow)
    zones = db.relationship("Zone", backref="camera", cascade="all, delete-orphan", lazy=True)


class Zone(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    camera_id = db.Column(db.Integer, db.ForeignKey("camera.id"), nullable=False)
    name = db.Column(db.String(100), nullable=False)
    polygon_coordinates = db.Column(db.JSON, nullable=False, default=list)
    allowed_start = db.Column(db.String(5), default="06:00")
    allowed_end = db.Column(db.String(5), default="22:00")
    active = db.Column(db.Boolean, default=True)


class TrackedPerson(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    camera_id = db.Column(db.Integer, db.ForeignKey("camera.id"), nullable=False)
    track_key = db.Column(db.String(80), nullable=False, index=True)
    entry_time = db.Column(db.DateTime(timezone=True), default=utcnow)
    exit_time = db.Column(db.DateTime(timezone=True))
    last_seen = db.Column(db.DateTime(timezone=True), default=utcnow)
    person_image = db.Column(db.String(500))
    status = db.Column(db.String(30), default="active")


class Incident(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    incident_type = db.Column(db.String(60), nullable=False, index=True)
    camera_id = db.Column(db.Integer, db.ForeignKey("camera.id"), nullable=False)
    tracking_id = db.Column(db.Integer, db.ForeignKey("tracked_person.id"))
    confidence = db.Column(db.Float, default=0.0)
    detected_time = db.Column(db.DateTime(timezone=True), default=utcnow, index=True)
    image_path = db.Column(db.String(500))
    video_path = db.Column(db.String(500))
    description = db.Column(db.String(500))
    status = db.Column(db.String(30), default="open", index=True)
    camera = db.relationship("Camera", backref="incidents")


class Alert(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    incident_id = db.Column(db.Integer, db.ForeignKey("incident.id"), nullable=False)
    message = db.Column(db.String(500), nullable=False)
    sent_to = db.Column(db.String(255), default="dashboard")
    sent_time = db.Column(db.DateTime(timezone=True), default=utcnow)
    delivery_status = db.Column(db.String(30), default="delivered")
    read = db.Column(db.Boolean, default=False)
    incident = db.relationship("Incident", backref="alerts")
