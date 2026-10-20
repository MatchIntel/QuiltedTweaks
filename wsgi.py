from server import App, start_sync_worker, validate_production, wsgi_for

app = App()
validate_production(app)
start_sync_worker(app)
application = wsgi_for(app)
