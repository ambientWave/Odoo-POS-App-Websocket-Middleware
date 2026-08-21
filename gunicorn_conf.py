import multiprocessing

# Gunicorn configuration for FastAPI/Uvicorn
bind = "0.0.0.0:8000"
workers = multiprocessing.cpu_count() * 2 + 1
worker_class = "uvicorn.workers.UvicornWorker"

# SSL is handled by Nginx (Reverse Proxy)
# No certfile or keyfile needed here for internal traffic.

# Logging
accesslog = "-"
errorlog = "-"
loglevel = "info"

# Timeout
timeout = 120
keepalive = 5
