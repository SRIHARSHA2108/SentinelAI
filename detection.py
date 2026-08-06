"""Video analytics engine with an optional YOLO backend and local behavior rules."""
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime
import math
import time

import cv2
import numpy as np


@dataclass
class Detection:
    label: str
    confidence: float
    box: tuple
    track_id: int = None


class CentroidTracker:
    def __init__(self, max_distance=90, max_missing=20):
        self.next_id, self.max_distance, self.max_missing = 1, max_distance, max_missing
        self.objects, self.missing = {}, {}

    def update(self, detections):
        centers = [((d.box[0] + d.box[2]) // 2, (d.box[1] + d.box[3]) // 2) for d in detections]
        available = set(self.objects)
        for det, center in zip(detections, centers):
            match = min(available, key=lambda i: math.dist(center, self.objects[i]), default=None)
            if match is None or math.dist(center, self.objects[match]) > self.max_distance:
                match, self.next_id = self.next_id, self.next_id + 1
            else:
                available.remove(match)
            self.objects[match], self.missing[match], det.track_id = center, 0, match
        for track_id in available:
            self.missing[track_id] = self.missing.get(track_id, 0) + 1
            if self.missing[track_id] > self.max_missing:
                self.objects.pop(track_id, None); self.missing.pop(track_id, None)
        return detections


class AnalyticsEngine:
    EVENT_COLOURS = {
        "intrusion": (40, 40, 240), "loitering": (0, 165, 255),
        "crowd": (190, 80, 20), "violence": (20, 20, 230),
        "abandoned_object": (180, 80, 180), "weapon": (0, 0, 255),
    }

    def __init__(self, enable_yolo=False, model_name="yolov8n.pt"):
        self.model, self.tracker = None, CentroidTracker()
        self.first_seen, self.positions = {}, defaultdict(lambda: deque(maxlen=12))
        self.static_objects, self.previous_gray = {}, None
        if enable_yolo:
            try:
                from ultralytics import YOLO
                self.model = YOLO(model_name)
            except (ImportError, RuntimeError):
                self.model = None

    def detect(self, frame):
        if self.model is None:
            return self._motion_people(frame)
        result = self.model.track(frame, persist=True, verbose=False, classes=None)[0]
        found = []
        if result.boxes is not None:
            names = result.names
            for box in result.boxes:
                xyxy = tuple(int(v) for v in box.xyxy[0].tolist())
                label = names[int(box.cls[0])]
                track_id = int(box.id[0]) if box.id is not None else None
                found.append(Detection(label, float(box.conf[0]), xyxy, track_id))
        people = [d for d in found if d.label == "person"]
        untracked = [d for d in people if d.track_id is None]
        if untracked: self.tracker.update(untracked)
        return found

    def _motion_people(self, frame):
        gray = cv2.GaussianBlur(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY), (21, 21), 0)
        if self.previous_gray is None:
            self.previous_gray = gray
            return []
        delta = cv2.absdiff(self.previous_gray, gray); self.previous_gray = gray
        mask = cv2.dilate(cv2.threshold(delta, 25, 255, cv2.THRESH_BINARY)[1], None, iterations=2)
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        detections = []
        for contour in contours:
            if cv2.contourArea(contour) < 1400: continue
            x, y, w, h = cv2.boundingRect(contour)
            detections.append(Detection("motion/person", 0.55, (x, y, x+w, y+h)))
        return self.tracker.update(detections)

    @staticmethod
    def _inside(point, polygon):
        if not polygon: return False
        contour = np.array(polygon, np.int32)
        return cv2.pointPolygonTest(contour, point, False) >= 0

    def analyse(self, frame, detections, zones=None, settings=None):
        settings = settings or {}
        now, events = time.time(), []
        people = [d for d in detections if d.label in {"person", "motion/person"}]
        weapons = {"knife", "gun", "pistol", "rifle"}
        for det in detections:
            if det.label.lower() in weapons:
                events.append(self._event("weapon", det.confidence, det.box, f"Possible {det.label} detected"))
            if det.label not in {"person", "motion/person"} and det.label.lower() not in weapons:
                center = ((det.box[0]+det.box[2])//2, (det.box[1]+det.box[3])//2)
                object_key = (det.label, det.track_id or center[0]//40, center[1]//40)
                previous = self.static_objects.get(object_key)
                if previous and math.dist(center, previous["center"]) < settings.get("static_radius", 35):
                    if now-previous["since"] >= settings.get("abandoned_seconds", 45):
                        events.append(self._event("abandoned_object", .70, det.box, f"Unattended {det.label} stationary for {int(now-previous['since'])}s"))
                else:
                    self.static_objects[object_key] = {"center": center, "since": now}
        for person in people:
            key = person.track_id or id(person)
            center = ((person.box[0]+person.box[2])//2, (person.box[1]+person.box[3])//2)
            self.first_seen.setdefault(key, now); self.positions[key].append(center)
            for zone in zones or []:
                if zone.get("active", True) and self._inside(center, zone.get("points", [])):
                    if zone.get("restricted", True):
                        events.append(self._event("intrusion", .82, person.box, f"Track {key} entered {zone.get('name', 'restricted zone')}"))
            dwell = now - self.first_seen[key]
            if dwell >= settings.get("loiter_seconds", 20):
                movement = math.dist(self.positions[key][0], self.positions[key][-1]) if len(self.positions[key]) > 1 else 0
                if movement < settings.get("loiter_radius", 80):
                    events.append(self._event("loitering", min(.95, .65+dwell/300), person.box, f"Track {key} loitering for {int(dwell)}s"))
        if len(people) >= settings.get("crowd_count", 4):
            box = self._union([p.box for p in people])
            events.append(self._event("crowd", min(.95, .55+len(people)*.06), box, f"Crowd of {len(people)} detected"))
        # A rapid, intersecting-person heuristic is a screening signal, not proof of violence.
        for i, first in enumerate(people):
            for second in people[i+1:]:
                if self._overlap(first.box, second.box) > .12:
                    speed1 = self._speed(first.track_id); speed2 = self._speed(second.track_id)
                    if speed1 + speed2 > settings.get("violence_motion", 90):
                        events.append(self._event("violence", .64, self._union([first.box, second.box]), "Rapid interaction detected; operator review required"))
        return self._dedupe(events)

    def annotate(self, frame, detections, events, zones=None):
        output = frame.copy()
        for zone in zones or []:
            pts = np.array(zone.get("points", []), np.int32)
            if len(pts) > 2: cv2.polylines(output, [pts], True, (0, 200, 255), 2)
        for det in detections:
            x1,y1,x2,y2 = det.box
            cv2.rectangle(output, (x1,y1), (x2,y2), (70,220,120), 2)
            cv2.putText(output, f"{det.label} #{det.track_id or '-'} {det.confidence:.0%}", (x1,max(18,y1-7)), cv2.FONT_HERSHEY_SIMPLEX,.48,(255,255,255),1,cv2.LINE_AA)
        for event in events:
            x1,y1,x2,y2 = event["box"]; colour = self.EVENT_COLOURS[event["type"]]
            cv2.rectangle(output,(x1,y1),(x2,y2),colour,3)
            cv2.putText(output,event["type"].replace('_',' ').upper(),(x1,min(output.shape[0]-8,y2+20)),cv2.FONT_HERSHEY_SIMPLEX,.6,colour,2)
        cv2.putText(output, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), (12,25), cv2.FONT_HERSHEY_SIMPLEX,.55,(255,255,255),1)
        return output

    def _speed(self, key):
        points = self.positions.get(key, [])
        return math.dist(points[-1], points[-3]) if len(points) >= 3 else 0

    @staticmethod
    def _overlap(a,b):
        x1,y1,x2,y2=max(a[0],b[0]),max(a[1],b[1]),min(a[2],b[2]),min(a[3],b[3])
        inter=max(0,x2-x1)*max(0,y2-y1); area=min((a[2]-a[0])*(a[3]-a[1]),(b[2]-b[0])*(b[3]-b[1]))
        return inter/area if area else 0

    @staticmethod
    def _union(boxes):
        return (min(b[0] for b in boxes),min(b[1] for b in boxes),max(b[2] for b in boxes),max(b[3] for b in boxes))

    @staticmethod
    def _event(kind, confidence, box, description): return {"type":kind,"confidence":confidence,"box":box,"description":description}

    @staticmethod
    def _dedupe(events):
        result=[]
        for event in events:
            if not any(e["type"]==event["type"] and AnalyticsEngine._overlap(e["box"],event["box"])>.5 for e in result): result.append(event)
        return result
