import os

bind = "localhost:8001"
preload_app = True
timeout = 200
max_requests = 500
max_requests_jitter = 10

# Threaded workers so one slow request can't stall the whole UI.  The
# container also runs nginx, three celery workers, and beat, so keep the
# process count low and rely on threads for I/O-bound concurrency.
worker_class = "gthread"
workers = int(os.environ.get("WEB_CONCURRENCY", "2"))
threads = int(os.environ.get("GUNICORN_THREADS", "4"))

accesslog = "-"
errorlog = "-"


def pre_fork(server, worker):  # noqa: ARG001
    """Drop DB connections the master opened while loading the app.

    preload_app runs AppConfig.ready() in the master, which can open the
    Postgres pool. A forked worker would inherit that pool's sockets (shared
    with its siblings) without the pool's background threads, so close it here
    and let each worker open its own.
    """
    from django.db import connections  # noqa: PLC0415

    for connection in connections.all(initialized_only=True):
        connection.close()
        if hasattr(connection, "close_pool"):
            connection.close_pool()
