"""Gunicorn settings for Render (read automatically when gunicorn starts in the project root)."""
import os

bind = f"0.0.0.0:{os.environ.get('PORT', '10000')}"

# One process with several threads. The TensorFlow model needs about 340 MB per process,
# so a second process would not fit in Render's 512 MB plans. Threads share the one model
# (predictions are serialized by a lock in ml/predictor.py), and the in-memory rate limits
# stay accurate because every request is counted in the same process.
workers = int(os.environ.get('WEB_CONCURRENCY', '1'))
threads = int(os.environ.get('GUNICORN_THREADS', '4'))

# PDF generation, sending email and the first model load can take a while
timeout = 120
graceful_timeout = 30

accesslog = '-'
errorlog = '-'


def post_worker_init(worker):
    # Start loading the model now (about 15 s) instead of on the first report upload
    from ml.predictor import warm_model_async
    warm_model_async()
