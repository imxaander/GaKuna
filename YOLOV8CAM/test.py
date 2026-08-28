import cv2
import numpy as np
import urllib.request
import time

snapshot_url = "http://192.168.1.19/capture"  # Replace with your IP

# --- Calibration Settings ---
# Step 1: Measure a known object (e.g. a piece of debris ~10cm wide)
# Step 2: Hold it exactly 30cm from the camera
# Step 3: Run the script and note the pixel width of that object
# Step 4: Plug those numbers in below

KNOWN_DISTANCE = 30.0       # cm — how far your calibration object was
KNOWN_WIDTH = 10.0          # cm — real width of your calibration object
CALIBRATION_PIXEL_WIDTH = 120  # pixels — how wide it appeared at that distance

# Calculate focal length from calibration
FOCAL_LENGTH = (CALIBRATION_PIXEL_WIDTH * KNOWN_DISTANCE) / KNOWN_WIDTH

print(f"Focal Length calculated: {FOCAL_LENGTH:.2f}")

def estimate_distance(pixel_width):
    if pixel_width == 0:
        return None
    return (KNOWN_WIDTH * FOCAL_LENGTH) / pixel_width

# --- Main Loop ---
while True:
    try:
        img_resp = urllib.request.urlopen(snapshot_url, timeout=5)
        img_np = np.array(bytearray(img_resp.read()), dtype=np.uint8)
        frame = cv2.imdecode(img_np, -1)

        if frame is None:
            continue

        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        blurred = cv2.GaussianBlur(gray, (5, 5), 0)
        _, thresh = cv2.threshold(blurred, 60, 255, cv2.THRESH_BINARY_INV)
        contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        debris_count = 0
        for cnt in contours:
            area = cv2.contourArea(cnt)
            if area > 500:
                debris_count += 1
                x, y, w, h = cv2.boundingRect(cnt)

                # Estimate distance using bounding box width
                distance = estimate_distance(w)

                # Color code by distance
                if distance < 20:
                    color = (0, 0, 255)      # Red — very close
                elif distance < 50:
                    color = (0, 165, 255)    # Orange — medium
                else:
                    color = (0, 255, 0)      # Green — far

                cv2.rectangle(frame, (x, y), (x+w, y+h), color, 2)

                # Show debris info
                label1 = f"Debris #{debris_count}"
                label2 = f"Dist: {distance:.1f} cm"
                cv2.putText(frame, label1, (x, y - 25),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 2)
                cv2.putText(frame, label2, (x, y - 8),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 2)

        # HUD at top left
        cv2.putText(frame, f"Debris Count: {debris_count}", (10, 30),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2)
        cv2.putText(frame, f"FL: {FOCAL_LENGTH:.1f}", (10, 60),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (200, 200, 200), 1)

        cv2.imshow("ESP32-CAM Debris Detection", frame)

    except Exception as e:
        print(f"Error: {e}, retrying...")
        time.sleep(1)

    if cv2.waitKey(1) & 0xFF == ord('q'):
        break

    time.sleep(0.1)

cv2.destroyAllWindows()