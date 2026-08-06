import os
from datetime import datetime, timedelta, timezone
from functools import wraps
from pathlib import Path
import threading
import time
import uuid

import cv2
import numpy as np
from flask import Flask, Response, flash, jsonify, redirect, render_template, request, send_from_directory, session, url_for
from werkzeug.utils import secure_filename

from config import Config
from detection import AnalyticsEngine
from models import Alert, Camera, Incident, TrackedPerson, User, Zone, db


app = Flask(__name__)
app.config.from_object(Config)
db.init_app(app)
app.config["EVIDENCE_DIR"].mkdir(parents=True, exist_ok=True)
app.config["UPLOAD_DIR"].mkdir(parents=True, exist_ok=True)
engines, event_cooldowns = {}, {}
ALLOWED_VIDEO_EXTENSIONS = {"mp4", "mov", "avi", "mkv", "m4v", "webm"}
ALLOWED_IMAGE_EXTENSIONS = {"jpg", "jpeg", "png", "webp", "bmp"}


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("user_id"): return redirect(url_for("login"))
        return view(*args, **kwargs)
    return wrapped


def serial_incident(item):
    return {"id":item.id,"type":item.incident_type,"camera":item.camera.name,"camera_id":item.camera_id,
            "confidence":round(item.confidence*100),"time":item.detected_time.isoformat(),"status":item.status,
            "description":item.description,"image":url_for("evidence", filename=Path(item.image_path).name) if item.image_path else None}


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        user = User.query.filter_by(email=request.form.get("email", "").lower()).first()
        if user and user.check_password(request.form.get("password", "")):
            session.update(user_id=user.id, name=user.name, role=user.role)
            return redirect(url_for("dashboard"))
        flash("Invalid email or password", "danger")
    return render_template("login.html")


@app.route("/logout")
def logout(): session.clear(); return redirect(url_for("login"))


@app.route("/")
@login_required
def dashboard():
    cameras = Camera.query.order_by(Camera.id).all()
    incidents = Incident.query.order_by(Incident.detected_time.desc()).limit(12).all()
    since = datetime.now(timezone.utc) - timedelta(days=1)
    stats = {"cameras":len(cameras),"online":sum(c.status=="online" for c in cameras),
             "open":Incident.query.filter_by(status="open").count(),
             "today":Incident.query.filter(Incident.detected_time >= since).count(),
             "tracked":TrackedPerson.query.filter_by(status="active").count()}
    return render_template("dashboard.html", cameras=cameras, incidents=incidents, stats=stats)


@app.route("/cameras", methods=["GET", "POST"])
@login_required
def cameras():
    if request.method == "POST":
        camera = Camera(name=request.form["name"], location=request.form["location"], stream_url=request.form.get("stream_url") or "0")
        db.session.add(camera); db.session.commit(); flash("Camera added", "success")
        return redirect(url_for("cameras"))
    return render_template("cameras.html", cameras=Camera.query.order_by(Camera.id).all())


@app.route("/recordings", methods=["GET", "POST"])
@login_required
def recordings():
    if request.method == "POST":
        source_type = request.form.get("source_type", "video")
        if source_type == "url":
            media_url = request.form.get("media_url", "").strip()
            if not media_url.lower().startswith(("http://", "https://", "rtsp://", "rtmp://")):
                flash("Enter a direct HTTP, HTTPS, RTSP, or RTMP media URL.", "danger")
                return redirect(url_for("recordings"))
            camera = Camera(
                name=(request.form.get("name") or "URL recording")[:100],
                location=request.form.get("location") or "Remote media URL",
                stream_url=media_url,
                status="offline",
            )
            db.session.add(camera); db.session.commit()
            flash("Media URL added. Select Analyze source to begin detection.", "success")
            return redirect(url_for("recordings", camera_id=camera.id))

        upload = request.files.get("media")
        original = secure_filename(upload.filename) if upload and upload.filename else ""
        extension = original.rsplit(".", 1)[-1].lower() if "." in original else ""
        allowed = ALLOWED_IMAGE_EXTENSIONS if source_type == "photo" else ALLOWED_VIDEO_EXTENSIONS
        if not upload or not original or extension not in allowed:
            expected = "JPG, JPEG, PNG, WebP, or BMP photo" if source_type == "photo" else "MP4, MOV, AVI, MKV, M4V, or WebM video"
            flash(f"Choose a valid {expected}.", "danger")
            return redirect(url_for("recordings"))
        stored_name = f"{uuid.uuid4().hex}_{original}"
        stored_path = app.config["UPLOAD_DIR"] / stored_name
        upload.save(stored_path)
        if source_type == "photo":
            readable = cv2.imread(str(stored_path)) is not None
            frames, fps = 1, 1
        else:
            capture = cv2.VideoCapture(str(stored_path))
            readable = capture.isOpened() and int(capture.get(cv2.CAP_PROP_FRAME_COUNT)) > 0
            frames = int(capture.get(cv2.CAP_PROP_FRAME_COUNT)) if readable else 0
            fps = capture.get(cv2.CAP_PROP_FPS) if readable else 0
            capture.release()
        if not readable:
            stored_path.unlink(missing_ok=True)
            flash("The uploaded file could not be decoded as a video.", "danger")
            return redirect(url_for("recordings"))
        camera = Camera(
            name=request.form.get("name") or Path(original).stem[:100],
            location=request.form.get("location") or "Recorded footage",
            stream_url=str(stored_path),
            status="offline",
        )
        db.session.add(camera); db.session.commit()
        duration = frames / fps if fps else 0
        message = "Photo uploaded. Start analysis to inspect the image." if source_type == "photo" else f"Video uploaded ({duration:.1f}s). Start analysis from this screen."
        flash(message, "success")
        return redirect(url_for("recordings", camera_id=camera.id))
    uploaded = [item for item in Camera.query.order_by(Camera.created_at.desc()).all()
                if item.stream_url.startswith(str(app.config["UPLOAD_DIR"]))
                or item.stream_url.lower().startswith(("http://", "https://", "rtsp://", "rtmp://"))]
    return render_template(
        "recordings.html", recordings=uploaded,
        selected_id=request.args.get("camera_id", type=int),
        max_upload_mb=app.config["MAX_CONTENT_LENGTH"] // (1024 * 1024),
        serverless=app.config.get("SERVERLESS", False),
    )


@app.post("/cameras/<int:camera_id>/toggle")
@login_required
def toggle_camera(camera_id):
    camera = db.get_or_404(Camera, camera_id); camera.status = "offline" if camera.status == "online" else "online"
    db.session.commit(); return redirect(request.referrer or url_for("cameras"))


@app.route("/zones", methods=["GET", "POST"])
@login_required
def zones():
    if request.method == "POST":
        try:
            points = [[int(n) for n in pair.split(",")] for pair in request.form["coordinates"].split(";")]
            if len(points) < 3: raise ValueError
        except ValueError:
            flash("Coordinates must be at least three x,y pairs separated by semicolons", "danger")
        else:
            db.session.add(Zone(camera_id=int(request.form["camera_id"]), name=request.form["name"], polygon_coordinates=points))
            db.session.commit(); flash("Detection zone created", "success")
        return redirect(url_for("zones"))
    return render_template("zones.html", zones=Zone.query.all(), cameras=Camera.query.all())


@app.route("/incidents")
@login_required
def incidents():
    query = Incident.query
    if request.args.get("status"): query = query.filter_by(status=request.args["status"])
    return render_template("incidents.html", incidents=query.order_by(Incident.detected_time.desc()).all())


@app.post("/incidents/<int:incident_id>/status")
@login_required
def incident_status(incident_id):
    incident = db.get_or_404(Incident, incident_id)
    if request.form.get("status") in {"open","reviewing","resolved","false_positive"}: incident.status=request.form["status"]
    db.session.commit(); return redirect(request.referrer or url_for("incidents"))


@app.route("/api/incidents")
@login_required
def api_incidents():
    after = request.args.get("after", type=int, default=0)
    items = Incident.query.filter(Incident.id > after).order_by(Incident.id.desc()).limit(30).all()
    return jsonify([serial_incident(i) for i in items])


@app.post("/api/analyze-frame/<int:camera_id>")
@login_required
def analyze_browser_frame(camera_id):
    """Analyze a JPEG captured by getUserMedia in a serverless browser client."""
    camera = db.get_or_404(Camera, camera_id)
    upload = request.files.get("frame")
    if not upload:
        return jsonify(error="Missing camera frame"), 400
    payload = upload.read(2 * 1024 * 1024 + 1)
    if len(payload) > 2 * 1024 * 1024:
        return jsonify(error="Camera frame exceeds 2 MB"), 413
    frame = cv2.imdecode(np.frombuffer(payload, dtype=np.uint8), cv2.IMREAD_COLOR)
    if frame is None:
        return jsonify(error="Invalid camera frame"), 400
    if frame.shape[1] > 960:
        scale = 960 / frame.shape[1]
        frame = cv2.resize(frame, None, fx=scale, fy=scale)
    engine = engines.setdefault(
        f"browser-{camera_id}",
        AnalyticsEngine(app.config["ENABLE_YOLO"], app.config["YOLO_MODEL"]),
    )
    zones_data = [{"name": z.name, "points": z.polygon_coordinates, "active": z.active, "restricted": True} for z in camera.zones]
    detections = engine.detect(frame)
    events = engine.analyse(frame, detections, zones_data)
    rendered = engine.annotate(frame, detections, events, zones_data)
    for event in events:
        save_event(camera, rendered, event)
    ok, encoded = cv2.imencode(".jpg", rendered, [cv2.IMWRITE_JPEG_QUALITY, 76])
    if not ok:
        return jsonify(error="Unable to encode analyzed frame"), 500
    response = Response(encoded.tobytes(), mimetype="image/jpeg")
    response.headers["X-Detected-Events"] = ",".join(event["type"] for event in events)
    response.headers["Cache-Control"] = "no-store"
    return response


@app.post("/api/alerts/read")
@login_required
def alerts_read():
    Alert.query.filter_by(read=False).update({"read":True}); db.session.commit(); return jsonify({"ok":True})


@app.route("/evidence/<path:filename>")
@login_required
def evidence(filename): return send_from_directory(app.config["EVIDENCE_DIR"], filename)


def camera_source(value):
    return int(value) if value.strip().isdigit() else value


def save_event(camera, frame, event):
    key=(camera.id,event["type"]); now=time.time()
    if now-event_cooldowns.get(key,0)<15: return
    event_cooldowns[key]=now
    stamp=datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    filename=f"camera_{camera.id}_{event['type']}_{stamp}.jpg"
    cv2.imwrite(str(app.config["EVIDENCE_DIR"]/filename),frame)
    incident=Incident(incident_type=event["type"],camera_id=camera.id,confidence=event["confidence"],
                      image_path=filename,description=event["description"])
    db.session.add(incident); db.session.flush()
    db.session.add(Alert(incident_id=incident.id,message=f"{event['type'].replace('_',' ').title()} at {camera.location}"))
    db.session.commit()


def stream(camera_id):
    with app.app_context():
        camera=db.session.get(Camera,camera_id)
        if not camera: return
        camera.status="online"; db.session.commit()
        cap=cv2.VideoCapture(camera_source(camera.stream_url))
        # Local uploaded recordings must be paced to their encoded frame rate;
        # otherwise OpenCV reads them as fast as the CPU allows.
        is_recording = camera.stream_url.startswith(str(app.config["UPLOAD_DIR"]))
        source_fps = cap.get(cv2.CAP_PROP_FPS) if is_recording else 0
        frame_interval = 1.0 / source_fps if 1 <= source_fps <= 120 else 0
        next_frame_at = time.monotonic()
        engine=engines.setdefault(camera_id,AnalyticsEngine(app.config["ENABLE_YOLO"],app.config["YOLO_MODEL"]))
        try:
            while cap.isOpened():
                ok,frame=cap.read()
                if not ok: break
                if frame.shape[1]>960:
                    scale=960/frame.shape[1]; frame=cv2.resize(frame,None,fx=scale,fy=scale)
                zones_data=[{"name":z.name,"points":z.polygon_coordinates,"active":z.active,"restricted":True} for z in camera.zones]
                detections=engine.detect(frame); events=engine.analyse(frame,detections,zones_data)
                rendered=engine.annotate(frame,detections,events,zones_data)
                for event in events: save_event(camera,rendered,event)
                ok,buffer=cv2.imencode(".jpg",rendered,[cv2.IMWRITE_JPEG_QUALITY,78])
                if ok: yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n"+buffer.tobytes()+b"\r\n"
                if frame_interval:
                    next_frame_at += frame_interval
                    remaining = next_frame_at - time.monotonic()
                    if remaining > 0:
                        time.sleep(remaining)
                    elif remaining < -1:
                        # Reset after a long processing delay instead of trying to catch up.
                        next_frame_at = time.monotonic()
        finally:
            cap.release(); camera.status="offline"; db.session.commit()


@app.route("/video/<int:camera_id>")
@login_required
def video(camera_id): return Response(stream(camera_id),mimetype="multipart/x-mixed-replace; boundary=frame")


@app.route("/health")
def health():
    return jsonify(status="ok", yolo=app.config["ENABLE_YOLO"], serverless=app.config.get("SERVERLESS", False))


@app.errorhandler(413)
def upload_too_large(_error):
    limit = app.config["MAX_CONTENT_LENGTH"] // (1024 * 1024)
    flash(f"Upload exceeds the {limit} MB limit for this deployment.", "danger")
    return redirect(url_for("recordings")), 413


def initialise():
    with app.app_context():
        db.create_all()
        if not User.query.first():
            admin=User(name="Administrator",email="admin@cctv.local",role="admin"); admin.set_password("admin123")
            db.session.add(admin)
        if not Camera.query.first():
            db.session.add(Camera(name="Demo Camera",location="Main Entrance",stream_url="0",status="offline"))
        db.session.commit()


initialise()

if __name__ == "__main__":
    app.run(host="0.0.0.0",port=int(os.getenv("PORT",5001)),debug=os.getenv("FLASK_DEBUG")=="1",threaded=True)
