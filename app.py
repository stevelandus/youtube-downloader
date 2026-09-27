import os
import uuid
import threading
from flask import Flask, request, jsonify, render_template, send_file, abort
from flask_cors import CORS
from youtube_service import (
    fetch_media_info, 
    start_download_thread, 
    download_playlist_zip_thread,
    get_job_status,
    jobs
)

app = Flask(__name__, template_folder="templates", static_folder="static")
CORS(app)

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/info", methods=["POST"])
def get_info():
    data = request.get_json() or {}
    url = data.get("url", "").strip()

    if not url:
        return jsonify({"success": False, "error": "Please provide a valid YouTube video or playlist URL."}), 400

    try:
        info = fetch_media_info(url)
        return jsonify({"success": True, "data": info})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 400

@app.route("/api/download/start", methods=["POST"])
def start_single_download():
    data = request.get_json() or {}
    url = data.get("url", "").strip()
    download_type = data.get("type", "video") # 'video' or 'audio'
    options = data.get("options", {})

    if not url:
        return jsonify({"success": False, "error": "URL parameter missing."}), 400

    job_id = str(uuid.uuid4())
    
    # Launch download process in thread
    t = threading.Thread(
        target=start_download_thread,
        args=(url, download_type, options, job_id),
        daemon=True
    )
    t.start()

    return jsonify({"success": True, "job_id": job_id})

@app.route("/api/download/playlist-zip", methods=["POST"])
def start_playlist_download():
    data = request.get_json() or {}
    urls = data.get("urls", [])
    download_type = data.get("type", "video")
    options = data.get("options", {})

    if not urls or not isinstance(urls, list):
        return jsonify({"success": False, "error": "No video URLs provided for playlist batch."}), 400

    job_id = str(uuid.uuid4())

    t = threading.Thread(
        target=download_playlist_zip_thread,
        args=(urls, download_type, options, job_id),
        daemon=True
    )
    t.start()

    return jsonify({"success": True, "job_id": job_id})

@app.route("/api/job/status/<job_id>", methods=["GET"])
def check_status(job_id):
    status_info = get_job_status(job_id)
    return jsonify(status_info)

@app.route("/api/download/file/<job_id>", methods=["GET"])
def download_file(job_id):
    job = jobs.get(job_id)
    if not job or job.get("status") != "finished":
        return abort(404, description="File not ready or job expired.")

    file_path = job.get("file_path")
    filename = job.get("filename", "download")

    if not file_path or not os.path.exists(file_path):
        return abort(404, description="File not found on server.")

    return send_file(
        file_path,
        as_attachment=True,
        download_name=filename,
        mimetype="application/octet-stream"
    )

if __name__ == "__main__":
    print("=== Starting YouTube Video & Playlist Downloader Server ===")
    print("Server running at: http://127.0.0.1:5000")
    app.run(host="0.0.0.0", port=5000, debug=True)
