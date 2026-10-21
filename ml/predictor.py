import json
import logging
from pathlib import Path
from threading import Lock, Thread

ML_DIR = Path(__file__).resolve().parent
MODEL_PATH = ML_DIR / "faccheck_cnn_model.keras"
LABELS_PATH = ML_DIR / "class_labels.json"
CONFIG_PATH = ML_DIR / "inference_config.json"

MODEL = None
_MODEL_LOCK = Lock()
_INFERENCE_LOCK = Lock()
_WARMUP_LOCK = Lock()
_WARMUP_STARTED = False
logger = logging.getLogger(__name__)

with open(LABELS_PATH, "r", encoding="utf-8") as file:
    CLASS_NAMES = json.load(file)

with open(CONFIG_PATH, "r", encoding="utf-8") as file:
    CONFIG = json.load(file)

if CONFIG.get("class_names") and CONFIG["class_names"] != CLASS_NAMES:
    raise ValueError("inference_config.json class_names do not match class_labels.json.")

CONFIDENCE_THRESHOLD = CONFIG["confidence_threshold"]
MARGIN_THRESHOLD = CONFIG["margin_threshold"]
FALLBACK_LABEL = CONFIG["fallback_label"]

CATEGORY_MAP = {
    "Carpentry Masonry": "carpentry_masonry",
    "Electrical": "electrical",
    "Others": "others",
    "Painting": "painting",
    "Plumbing": "plumbing",
    "Refrigeration ACUs": "refrigeration_acu",
    "Vehicle Check-Up": "vehicle_checkup",
}


class ImageDecodeError(ValueError):
    """The uploaded file is not an image the model can read."""


def _load_model():
    global MODEL
    if MODEL is None:
        with _MODEL_LOCK:
            if MODEL is None:
                import tensorflow as tf
                MODEL = tf.keras.models.load_model(MODEL_PATH, compile=False)
    return MODEL


def _warm_model():
    global _WARMUP_STARTED
    try:
        import numpy as np

        model = _load_model()
        image_shape = tuple(CONFIG["image_size"]) + (3,)
        sample = np.zeros((1, *image_shape), dtype=np.float32)
        with _INFERENCE_LOCK:
            model.predict(sample, verbose=0)
    except Exception:
        logger.exception("CNN model background warm-up failed")
    finally:
        with _WARMUP_LOCK:
            _WARMUP_STARTED = False


def warm_model_async():
    """Load and trace inference in the background before the first upload."""
    global _WARMUP_STARTED
    if MODEL is not None:
        return
    with _WARMUP_LOCK:
        if MODEL is not None or _WARMUP_STARTED:
            return
        _WARMUP_STARTED = True
        Thread(target=_warm_model, name="cnn-model-warmup", daemon=True).start()


def predict_image(image_file):
    import numpy as np
    from PIL import Image, ImageOps

    image_file.seek(0)
    try:
        with Image.open(image_file) as source:
            image = ImageOps.exif_transpose(source).convert("RGB").resize(tuple(CONFIG["image_size"]))
    except (Image.DecompressionBombError, OSError, SyntaxError, ValueError) as exc:  # OSError covers UnidentifiedImageError
        raise ImageDecodeError("Uploaded photo could not be decoded.") from exc
    finally:
        image_file.seek(0)

    # The exported model contains its own MobileNetV2 Rescaling layer. Feed RGB
    # pixels in the configured 0..255 range so they are scaled exactly once.
    image_array = np.asarray(image, dtype=np.float32)
    image_array = np.expand_dims(image_array, axis=0)

    model = _load_model()
    with _INFERENCE_LOCK:
        predictions = model.predict(image_array, verbose=0)[0]

    sorted_indices = np.argsort(predictions)[::-1]

    top_index = int(sorted_indices[0])
    second_index = int(sorted_indices[1])

    raw_label = CLASS_NAMES[top_index]
    confidence = float(predictions[top_index])
    margin = float(predictions[top_index] - predictions[second_index])

    flagged_for_review = (
        confidence < CONFIDENCE_THRESHOLD
        or margin < MARGIN_THRESHOLD
    )

    final_label = FALLBACK_LABEL if flagged_for_review else raw_label
    category = CATEGORY_MAP[final_label]

    image_file.seek(0)

    return {
        "category": category,
        "label": final_label,
        "raw_label": raw_label,
        "confidence": confidence,
        "margin": margin,
        "flagged_for_review": flagged_for_review,
    }
