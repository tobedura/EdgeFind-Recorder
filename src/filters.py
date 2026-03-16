import cv2
import numpy as np

_clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))


def apply_canny(frame: np.ndarray) -> np.ndarray:
    lab = cv2.cvtColor(frame, cv2.COLOR_BGR2LAB)

    edges_combined = None
    for i in range(3):
        channel = _clahe.apply(lab[:, :, i])
        blurred = cv2.GaussianBlur(channel, (5, 5), 0)
        v = np.median(blurred)
        lower = int(max(0, 0.5 * v))
        upper = int(min(255, 1.5 * v))
        edges = cv2.Canny(blurred, lower, upper)
        if edges_combined is None:
            edges_combined = edges
        else:
            edges_combined = cv2.bitwise_or(edges_combined, edges)

    return cv2.cvtColor(edges_combined, cv2.COLOR_GRAY2BGR)
