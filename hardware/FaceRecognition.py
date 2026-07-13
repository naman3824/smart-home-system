"""
Smart Home — Face Recognition (standalone camera script)

Improvements over v1:
  • Multi-photo enrollment: put multiple images per person in the /images
    folder named  Aditya.jpeg, Aditya_2.jpeg, Aditya_3.jpeg …  — all are
    loaded and their encodings are averaged into one robust reference vector.
    More angles = far fewer false "Unknown" readings on head turns.

  • Multi-frame confirmation (CONFIRM_FRAMES): requires N consecutive frames
    to agree before logging an access event or raising an intruder alert.
    A single head-tilt or motion blur can no longer trigger a false alarm.

  • Uncertain zone (UNCERTAIN_ZONE): distances between THRESHOLD and
    THRESHOLD+UNCERTAIN_ZONE are shown as "Verifying…" on screen and are
    never logged as intruder events — the system waits for more frames.

  • Tunable constants at the top — change THRESHOLD or CONFIRM_FRAMES here
    without touching the recognition logic.
"""

import cv2
import face_recognition
import numpy as np
import os
import csv
from datetime import datetime
from collections import defaultdict

# =========================
# TUNABLES
# =========================

IMAGES_DIR     = "images"
THRESHOLD      = 0.60     # distance below which a face is "certain match"
UNCERTAIN_ZONE = 0.08     # distance 0.60–0.68 → "Verifying…" (not an alarm)
CONFIRM_FRAMES = 3        # consecutive frames that must agree before acting


# =========================
# LOAD REGISTERED FACES
# =========================

def _load_encodings_for_person(name_stem: str) -> list:
    """
    Load ALL images whose filename stem matches `name_stem` (case-insensitive):
        Aditya.jpeg, aditya_2.jpg, ADITYA_side.png …
    Returns a list of (128,) numpy encoding arrays.
    """
    encodings = []
    for fname in sorted(os.listdir(IMAGES_DIR)):
        if not fname.lower().endswith((".jpg", ".jpeg", ".png")):
            continue
        stem = os.path.splitext(fname)[0].lower()
        # Match exact name or name_N / name_anything
        if stem == name_stem.lower() or stem.startswith(name_stem.lower() + "_"):
            path = os.path.join(IMAGES_DIR, fname)
            try:
                img = face_recognition.load_image_file(path)
                found = face_recognition.face_encodings(img)
                if found:
                    encodings.append(found[0])
                    print(f"  ✅ {fname} → encoding loaded")
                else:
                    print(f"  ⚠️  {fname} → no face found, skipping")
            except Exception as e:
                print(f"  ❌ {fname} → error: {e}")
    return encodings


def _average_encodings(encodings: list) -> np.ndarray:
    """Average a list of unit-vector encodings and re-normalise."""
    avg = np.mean(encodings, axis=0)
    norm = np.linalg.norm(avg)
    return avg / norm if norm > 0 else avg


# Discover unique person names from image filenames
# (filename stem before first underscore = person name)
_stems_seen = set()
for _f in os.listdir(IMAGES_DIR):
    if _f.lower().endswith((".jpg", ".jpeg", ".png")):
        _stem = os.path.splitext(_f)[0]
        _person = _stem.split("_")[0]   # "Aditya_2" → "Aditya"
        _stems_seen.add(_person)

known_face_encodings = []
known_face_names     = []

print("\n========== LOADING REGISTERED FACES ==========")
for person in sorted(_stems_seen):
    print(f"\n{person}:")
    encs = _load_encodings_for_person(person)
    if encs:
        avg = _average_encodings(encs)
        known_face_encodings.append(avg)
        known_face_names.append(person)
        print(f"  → averaged {len(encs)} photo(s) into one reference encoding")
    else:
        print(f"  → no usable photos, skipping")

print("\n========== REGISTERED MEMBERS ==========")
for p in known_face_names:
    print(f"  • {p}")
print("=========================================\n")


# =========================
# LOGGING HELPERS
# =========================

def mark_attendance(name):
    today = datetime.now().strftime("%d-%m-%Y")
    now   = datetime.now().strftime("%H:%M:%S")
    exists = os.path.isfile("attendance.csv")
    if exists:
        with open("attendance.csv") as f:
            for row in csv.reader(f):
                if len(row) >= 3 and row[0] == name and row[2] == today:
                    return   # already marked today
    with open("attendance.csv", "a", newline="") as f:
        w = csv.writer(f)
        if not exists:
            w.writerow(["Name", "Status", "Date", "Time"])
        w.writerow([name, "Authorized", today, now])
    print(f"✅ Attendance marked: {name}")

def log_access(person, status):
    exists = os.path.isfile("access_log.csv")
    with open("access_log.csv", "a", newline="") as f:
        w = csv.writer(f)
        if not exists:
            w.writerow(["Person", "Status", "Date", "Time"])
        w.writerow([person, status,
                    datetime.now().strftime("%d-%m-%Y"),
                    datetime.now().strftime("%H:%M:%S")])

def log_unknown():
    exists = os.path.isfile("unknown_log.csv")
    with open("unknown_log.csv", "a", newline="") as f:
        w = csv.writer(f)
        if not exists:
            w.writerow(["Person", "Status", "Date", "Time"])
        w.writerow(["Intruder", "Unauthorized",
                    datetime.now().strftime("%d-%m-%Y"),
                    datetime.now().strftime("%H:%M:%S")])


# =========================
# CAMERA LOOP
# =========================

cap = cv2.VideoCapture(0)
if not cap.isOpened():
    print("❌ Could not access webcam"); exit()

# Multi-frame confirmation state
confirm_buffer = {"name": None, "count": 0}
last_acted     = {}          # name → datetime of last log/alert action
COOLDOWN_S     = 30          # seconds before re-logging the same person

print("📷 Camera started — press Q to quit\n")

while True:
    ok, frame = cap.read()
    if not ok:
        break

    small  = cv2.resize(frame, (0, 0), fx=0.5, fy=0.5)
    rgb    = cv2.cvtColor(small, cv2.COLOR_BGR2RGB)

    locs   = face_recognition.face_locations(rgb, model="hog")
    encs   = face_recognition.face_encodings(rgb, locs)

    for (top, right, bottom, left), enc in zip(locs, encs):
        # Scale coords back to full frame
        top*=2; right*=2; bottom*=2; left*=2

        resolved_name = "Unknown"
        dist_str      = ""

        if known_face_encodings:
            dists     = face_recognition.face_distance(known_face_encodings, enc)
            best_idx  = int(np.argmin(dists))
            best_dist = dists[best_idx]
            dist_str  = f"{best_dist:.2f}"

            if best_dist < THRESHOLD:
                resolved_name = known_face_names[best_idx]
            elif best_dist < THRESHOLD + UNCERTAIN_ZONE:
                resolved_name = "_uncertain_"

        # ── Confirmation buffer ──────────────────────────────────────────
        if resolved_name == confirm_buffer["name"]:
            confirm_buffer["count"] += 1
        else:
            confirm_buffer = {"name": resolved_name, "count": 1}

        confirmed  = confirm_buffer["count"] >= CONFIRM_FRAMES
        frames_left = max(0, CONFIRM_FRAMES - confirm_buffer["count"])

        # ── Display label + box ──────────────────────────────────────────
        if resolved_name == "_uncertain_":
            display  = f"Verifying… ({dist_str})"
            color    = (0, 165, 255)   # orange
        elif resolved_name == "Unknown":
            display  = f"UNKNOWN ({dist_str})" + ("" if confirmed else f" [{frames_left} left]")
            color    = (0, 0, 255)     # red
        else:
            display  = resolved_name + ("" if confirmed else f" [{frames_left} left]")
            color    = (0, 255, 0)     # green

        cv2.rectangle(frame, (left, top), (right, bottom), color, 3)
        cv2.putText(frame, display, (left, top-10),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.75, color, 2)

        # ── Act only once confirmed ──────────────────────────────────────
        if not confirmed or resolved_name == "_uncertain_":
            continue

        now = datetime.now()
        last = last_acted.get(resolved_name)
        if last and (now - last).total_seconds() < COOLDOWN_S:
            continue
        last_acted[resolved_name] = now

        if resolved_name == "Unknown":
            print("\n🚨 INTRUDER ALERT — unrecognized face confirmed over",
                  CONFIRM_FRAMES, "frames")
            log_unknown()
            log_access("Intruder", "Unauthorized")
            cv2.putText(frame, "!!! INTRUDER DETECTED !!!",
                        (20, 50), cv2.FONT_HERSHEY_SIMPLEX, 1, (0,0,255), 3)
        else:
            print(f"\n✅ {resolved_name} confirmed over {CONFIRM_FRAMES} frames")
            mark_attendance(resolved_name)
            log_access(resolved_name, "Authorized")

        # Reset buffer after acting so the next event needs fresh confirmation
        confirm_buffer = {"name": None, "count": 0}

    cv2.imshow("Smart Home — Face Recognition", frame)
    if cv2.waitKey(1) & 0xFF == ord('q'):
        break

cap.release()
cv2.destroyAllWindows()
