import cv2
import face_recognition
import numpy as np
import os
import csv
from datetime import datetime
from tkinter import Tk, messagebox

# =========================
# LOAD REGISTERED FACES
# =========================

known_face_encodings = []
known_face_names = []

path = "images"

for file in os.listdir(path):

    if file.lower().endswith((".jpg", ".jpeg", ".png")):

        image_path = os.path.join(path, file)

        try:
            image = face_recognition.load_image_file(image_path)

            encodings = face_recognition.face_encodings(image)

            if len(encodings) > 0:
                known_face_encodings.append(encodings[0])
                known_face_names.append(os.path.splitext(file)[0])

                print(f"✅ {file} Loaded Successfully")

            else:
                print(f"❌ No face found in {file}")

        except Exception as e:
            print(f"❌ Error loading {file}")
            print(e)

print("\n========== REGISTERED MEMBERS ==========")

for person in known_face_names:
    print(person)

print("========================================\n")


# =========================
# ATTENDANCE FUNCTION
# =========================

def mark_attendance(name):

    today_date = datetime.now().strftime("%d-%m-%Y")
    current_time = datetime.now().strftime("%H:%M:%S")

    file_exists = os.path.isfile("attendance.csv")
    already_marked = False

    if file_exists:

        with open("attendance.csv", "r") as f:

            reader = csv.reader(f)

            for row in reader:

                if len(row) >= 4:

                    if row[0] == name and row[2] == today_date:
                        already_marked = True
                        break

    if not already_marked:

        with open("attendance.csv", "a", newline="") as f:

            writer = csv.writer(f)

            if not file_exists:

                writer.writerow([
                    "Name",
                    "Status",
                    "Date",
                    "Time"
                ])

            writer.writerow([
                name,
                "Authorized",
                today_date,
                current_time
            ])

        print(f"✅ Attendance Marked: {name}")


# =========================
# ACCESS LOG
# =========================

def log_access(person, status):

    file_exists = os.path.isfile("access_log.csv")

    with open("access_log.csv", "a", newline="") as f:

        writer = csv.writer(f)

        if not file_exists:

            writer.writerow([
                "Person",
                "Status",
                "Date",
                "Time"
            ])

        writer.writerow([
            person,
            status,
            datetime.now().strftime("%d-%m-%Y"),
            datetime.now().strftime("%H:%M:%S")
        ])


# =========================
# UNKNOWN LOG
# =========================

def log_unknown():

    file_exists = os.path.isfile("unknown_log.csv")

    with open("unknown_log.csv", "a", newline="") as f:

        writer = csv.writer(f)

        if not file_exists:

            writer.writerow([
                "Person",
                "Status",
                "Date",
                "Time"
            ])

        writer.writerow([
            "Intruder",
            "Unauthorized",
            datetime.now().strftime("%d-%m-%Y"),
            datetime.now().strftime("%H:%M:%S")
        ])


# =========================
# START CAMERA
# =========================

cap = cv2.VideoCapture(0)

if not cap.isOpened():
    print("❌ Could not access webcam")
    exit()

alert_shown = False

while True:

    success, frame = cap.read()

    if not success:
        break

    # Convert frame to RGB
    rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

    # Detect faces
    face_locations = face_recognition.face_locations(rgb_frame)

    # Generate encodings
    face_encodings = face_recognition.face_encodings(
        rgb_frame,
        face_locations
    )

    for (top, right, bottom, left), face_encoding in zip(
        face_locations,
        face_encodings
    ):

        name = "Unknown"

        if len(known_face_encodings) > 0:

            matches = face_recognition.compare_faces(
                known_face_encodings,
                face_encoding
            )

            face_distances = face_recognition.face_distance(
                known_face_encodings,
                face_encoding
            )

            best_match_index = np.argmin(face_distances)

            # Recognition threshold
            if (
                matches[best_match_index]
                and face_distances[best_match_index] < 0.55
            ):

                name = known_face_names[best_match_index]

                mark_attendance(name)

                log_access(name, "Authorized")

                alert_shown = False

            else:

                if not alert_shown:

                    print("\n🚨 SECURITY ALERT 🚨")
                    print("UNREGISTERED PERSON DETECTED\n")

                    log_unknown()
                    log_access(
                        "Intruder",
                        "Unauthorized"
                    )

                    root = Tk()
                    root.withdraw()

                    messagebox.showwarning(
                        "SECURITY ALERT",
                        "UNREGISTERED PERSON DETECTED!"
                    )

                    root.destroy()

                    alert_shown = True

        # Color based on recognition
        color = (0, 255, 0)

        if name == "Unknown":
            color = (0, 0, 255)

        # Draw rectangle
        cv2.rectangle(
            frame,
            (left, top),
            (right, bottom),
            color,
            3
        )

        # Display name
        cv2.putText(
            frame,
            name,
            (left, top - 10),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.8,
            color,
            2
        )

        # Intruder Warning
        if name == "Unknown":

            cv2.putText(
                frame,
                "!!! INTRUDER DETECTED !!!",
                (20, 50),
                cv2.FONT_HERSHEY_SIMPLEX,
                1,
                (0, 0, 255),
                3
            )

    cv2.imshow(
        "Face Recognition Attendance System",
        frame
    )

    if cv2.waitKey(1) & 0xFF == ord('q'):
        break

# =========================
# CLEANUP
# =========================

cap.release()
cv2.destroyAllWindows()
