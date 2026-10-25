"""Supervise one collector and one Streamlit process. Expose only public snapshot JSON."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from datetime import datetime, timezone
import json
import logging
import os
import signal
import subprocess
import sys
import threading
import time
from .ingest import Ingestor

class PublicHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.split('?')[0] not in ('/api/state', '/health'):
            self.send_error(404); return
        path = Path(os.getenv('DATA_DIR', 'data')) / 'public.json'
        try:
            raw = path.read_bytes()
            state = json.loads(raw)
            age = (datetime.now(timezone.utc)-datetime.fromisoformat(state['published_at'])).total_seconds()
            healthy = age < 120
        except (OSError, ValueError, KeyError):
            raw, healthy = b'{"status":"starting"}', False
        body = raw if self.path.startswith('/api/state') else json.dumps(dict(healthy=healthy)).encode()
        self.send_response(200 if healthy else 503)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers(); self.wfile.write(body)

def main():
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    ingestor = Ingestor()
    thread = threading.Thread(target=ingestor.run, name='tse-collector', daemon=True)
    thread.start()
    server = ThreadingHTTPServer(('0.0.0.0', 8080), PublicHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    dashboard = subprocess.Popen([sys.executable, '-m', 'streamlit', 'run', 'src/dashboard.py', '--server.address=0.0.0.0', '--server.port=8501', '--server.headless=true', '--browser.gatherUsageStats=false'])
    stopped = threading.Event()
    def stop(*_): stopped.set()
    signal.signal(signal.SIGTERM, stop); signal.signal(signal.SIGINT, stop)
    try:
        while not stopped.wait(2):
            if not thread.is_alive() or dashboard.poll() is not None:
                raise RuntimeError('Collector or dashboard exited; container supervisor must restart')
    finally:
        ingestor.stop_event.set()
        dashboard.terminate()
        server.shutdown()
        thread.join(timeout=25)
        if not thread.is_alive(): ingestor.storage.close()

if __name__ == '__main__': main()
