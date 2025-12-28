from flask import Flask, send_file, abort
from threading import Thread
import os
import time

app = Flask(__name__)

# ✅ these will be injected from main.py
download_links = {}
DOWNLOAD_TTL = 300  # default 5 min

@app.route("/")
def home():
    return "TB_LOADER Alive ✅"

@app.route("/d/<token>")
def download_file(token):
    rec = download_links.get(token)
    if not rec:
        return abort(404)

    # expiry check
    if time.time() > rec.get("expires", 0):
        try:
            os.remove(rec["path"])
        except:
            pass
        download_links.pop(token, None)
        return abort(410)

    path = rec["path"]
    if not os.path.exists(path):
        download_links.pop(token, None)
        return abort(404)

    return send_file(path, as_attachment=True)

def run():
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))

def keep_alive(dl_links_ref=None, ttl=300):
    global download_links, DOWNLOAD_TTL
    if dl_links_ref is not None:
        download_links = dl_links_ref
    DOWNLOAD_TTL = ttl

    t = Thread(target=run)
    t.daemon = True
    t.start()
