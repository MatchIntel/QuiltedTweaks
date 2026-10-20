import os

bind = "0.0.0.0:" + os.environ.get("PORT", "8080")
workers = 1
threads = 8
worker_class = "gthread"
timeout = 90
keepalive = 5
limit_request_line = 4094
limit_request_fields = 60
accesslog = None
errorlog = "-"
# Keep one worker: it owns the serialized SellAuth synchronization loop.
