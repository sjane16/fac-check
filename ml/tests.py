import sys
import types
from io import BytesIO
from unittest.mock import Mock, patch

import numpy as np
from django.test import SimpleTestCase
from PIL import Image

from . import predictor


class FakeKerasModel:
    def __init__(self, output):
        self.output = np.asarray([output], dtype=np.float32)
        self.input_array = None
        self.calls = 0

    def predict_on_batch(self, image_array):
        self.calls += 1
        self.input_array = np.array(image_array, copy=True)
        return self.output


def _png(color, size=(8, 8)):
    buffer = BytesIO()
    Image.new('RGB', size, color).save(buffer, format='PNG')
    buffer.seek(0)
    return buffer


class KerasInferenceTests(SimpleTestCase):
    def setUp(self):
        self.previous_model = predictor.MODEL
        predictor.MODEL = None
        self.addCleanup(setattr, predictor, 'MODEL', self.previous_model)

    def test_model_loads_only_when_requested_and_is_reused(self):
        model = Mock()
        tensorflow = types.SimpleNamespace(
            config=types.SimpleNamespace(threading=Mock()),
            keras=types.SimpleNamespace(
                models=types.SimpleNamespace(load_model=Mock(return_value=model))
            ),
        )

        with patch.dict(sys.modules, {'tensorflow': tensorflow}):
            self.assertIsNone(predictor.MODEL)
            self.assertIs(predictor._load_model(), model)
            self.assertIs(predictor._load_model(), model)

        self.assertEqual(predictor.MODEL_PATH.name, 'faccheck_cnn_model.keras')
        tensorflow.keras.models.load_model.assert_called_once_with(
            predictor.MODEL_PATH,
            compile=False,
        )
        tensorflow.config.threading.set_intra_op_parallelism_threads.assert_called_once_with(1)
        tensorflow.config.threading.set_inter_op_parallelism_threads.assert_called_once_with(1)

    def test_class_labels_match_config_and_category_map(self):
        self.assertEqual(predictor.CONFIG['class_names'], predictor.CLASS_NAMES)
        self.assertEqual(set(predictor.CLASS_NAMES), set(predictor.CATEGORY_MAP))

    def test_prediction_uses_raw_rgb_224_and_existing_result_contract(self):
        probabilities = [0.02, 0.90, 0.01, 0.02, 0.02, 0.02, 0.01]
        model = FakeKerasModel(probabilities)
        image_bytes = _png((255, 128, 0))

        with patch.object(predictor, '_load_model', return_value=model):
            result = predictor.predict_image(image_bytes)

        self.assertEqual(model.input_array.shape, (1, 224, 224, 3))
        np.testing.assert_allclose(model.input_array[0, 100, 100], [255.0, 128.0, 0.0], atol=1e-6)
        self.assertEqual(set(result), {
            'category', 'label', 'raw_label', 'confidence', 'margin', 'flagged_for_review',
        })
        self.assertEqual(result['category'], 'electrical')
        self.assertEqual(result['label'], 'Electrical')
        self.assertEqual(result['raw_label'], 'Electrical')
        self.assertAlmostEqual(result['confidence'], 0.9)
        self.assertAlmostEqual(result['margin'], 0.88)
        self.assertFalse(result['flagged_for_review'])
        self.assertEqual(image_bytes.tell(), 0)

    def test_low_confidence_and_margin_keep_others_fallback(self):
        model = FakeKerasModel([0.26, 0.25, 0.20, 0.10, 0.08, 0.06, 0.05])

        with patch.object(predictor, '_load_model', return_value=model):
            result = predictor.predict_image(_png('white'))

        self.assertEqual(result['raw_label'], 'Carpentry Masonry')
        self.assertEqual(result['label'], 'Others')
        self.assertEqual(result['category'], 'others')
        self.assertTrue(result['flagged_for_review'])

    def test_undecodable_image_raises_image_decode_error_before_loading_model(self):
        image_bytes = BytesIO(b'not an image')
        with patch.object(predictor, '_load_model') as load_model:
            with self.assertRaises(predictor.ImageDecodeError):
                predictor.predict_image(image_bytes)
        load_model.assert_not_called()
        self.assertEqual(image_bytes.tell(), 0)

    def test_background_warmup_starts_only_once(self):
        previous_started = predictor._WARMUP_STARTED
        predictor.MODEL = None
        predictor._WARMUP_STARTED = False
        self.addCleanup(setattr, predictor, '_WARMUP_STARTED', previous_started)

        with patch.object(predictor, 'Thread') as thread:
            predictor.warm_model_async()
            predictor.warm_model_async()

        thread.assert_called_once_with(
            target=predictor._warm_model,
            name='cnn-model-warmup',
            daemon=True,
        )
        thread.return_value.start.assert_called_once()

    def test_background_warmup_runs_first_inference(self):
        model = FakeKerasModel([0.1] * len(predictor.CLASS_NAMES))
        previous_started = predictor._WARMUP_STARTED
        predictor._WARMUP_STARTED = True
        self.addCleanup(setattr, predictor, '_WARMUP_STARTED', previous_started)

        with patch.object(predictor, '_load_model', return_value=model):
            predictor._warm_model()

        self.assertEqual(model.calls, 1)
        self.assertEqual(model.input_array.shape, (1, 224, 224, 3))
        self.assertFalse(predictor._WARMUP_STARTED)
