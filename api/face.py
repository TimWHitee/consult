import base64
import json
from pathlib import Path

import numpy as np

from .config import config
from .database import connect, rows_to_dicts

try:
    import face_recognition
except Exception:  # pragma: no cover - optional heavy dependency
    face_recognition = None


def face_engine_available() -> bool:
    return face_recognition is not None


def save_upload_bytes(company_id: int, employee_id: int, filename: str, content: bytes) -> str:
    suffix = Path(filename).suffix.lower() or ".jpg"
    target_dir = Path(config.STORAGE_DIR) / "faces" / str(company_id) / str(employee_id)
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"{len(list(target_dir.iterdir())) + 1}{suffix}"
    target.write_bytes(content)
    return str(target)


def save_probe_image(company_id: int, content: bytes) -> str:
    target_dir = Path(config.STORAGE_DIR) / "face_probes" / str(company_id)
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"probe_{len(list(target_dir.iterdir())) + 1}.jpg"
    target.write_bytes(content)
    return str(target)


def image_encoding(path: str) -> tuple[list[float] | None, str]:
    if face_recognition is None:
        return None, "face_engine_unavailable"

    image = face_recognition.load_image_file(path)
    boxes = face_recognition.face_locations(image, model="hog")
    if len(boxes) != 1:
        return None, "expected_one_face"

    encoding = face_recognition.face_encodings(image, known_face_locations=boxes)[0]
    return encoding.astype(np.float32).tolist(), "ok"


def recognize_base64(company_id: int, image_base64: str) -> tuple[int | None, float | None, str]:
    if "," in image_base64:
        image_base64 = image_base64.split(",", 1)[1]
    try:
        content = base64.b64decode(image_base64)
    except ValueError:
        return None, None, "invalid_face_image_base64"

    path = save_probe_image(company_id, content)
    probe, status = image_encoding(path)
    if probe is None:
        return None, None, status

    with connect() as db:
        rows = rows_to_dicts(
            db.execute(
                "SELECT employee_id, embedding_json FROM face_embeddings WHERE company_id = ?",
                (company_id,),
            ).fetchall()
        )

    if not rows:
        return None, None, "no_face_profiles"

    probe_vector = np.asarray(probe, dtype=np.float32)
    best_employee_id = None
    best_distance = None
    for row in rows:
        known = np.asarray(json.loads(row["embedding_json"]), dtype=np.float32)
        distance = float(np.linalg.norm(known - probe_vector))
        if best_distance is None or distance < best_distance:
            best_distance = distance
            best_employee_id = int(row["employee_id"])

    if best_distance is not None and best_distance <= config.FACE_THRESHOLD:
        confidence = max(0.0, min(1.0, 1.0 - best_distance))
        return best_employee_id, confidence, "ok"

    return None, best_distance, "face_not_recognized"
