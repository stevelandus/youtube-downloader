import os
import sys
import re
import time
import zipfile
import threading
import uuid
from pathlib import Path
import yt_dlp
import static_ffmpeg

# Ensure ffmpeg and ffprobe are in system PATH
try:
    static_ffmpeg.add_paths()
except Exception as e:
    print(f"Warning setting static_ffmpeg paths: {e}")

# Directory configuration
BASE_DIR = Path(__file__).resolve().parent
DOWNLOADS_DIR = BASE_DIR / "downloads"
DOWNLOADS_DIR.mkdir(exist_ok=True)

# In-memory storage for active/completed download jobs
# Structure: { job_id: { 'status': 'queued'|'downloading'|'converting'|'finished'|'error', 'progress': 0, 'speed': '', 'eta': '', 'filename': '', 'file_path': '', 'title': '', 'error_msg': '' } }
jobs = {}

def clean_old_files(max_age_seconds=3600):
    """Background task to remove files older than max_age_seconds"""
    now = time.time()
    for item in DOWNLOADS_DIR.glob("*"):
        if item.is_file() and (now - item.stat().st_mtime) > max_age_seconds:
            try:
                item.unlink()
            except Exception as e:
                print(f"Error removing old file {item}: {e}")

def get_job_status(job_id):
    """Retrieve current status of a download job"""
    return jobs.get(job_id, {'status': 'not_found'})

def sanitize_filename(name):
    """Sanitize string for safe filenames"""
    name = re.sub(r'[\\/*?:"<>|]', '', name)
    return name.strip() or "video"

def parse_url_type(url):
    """Detect if URL is playlist or single video"""
    if "playlist?list=" in url or "&list=" in url:
        return "playlist"
    return "video"

def fetch_media_info(url):
    """
    Fetches detailed metadata for a video or playlist without downloading.
    Returns dict with media metadata and available formats.
    """
    ydl_opts = {
        'quiet': True,
        'no_warnings': True,
        'extract_flat': 'in_playlist',  # Fast extraction for playlists
        'skip_download': True,
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        try:
            info = ydl.extract_info(url, download=False)
        except Exception as e:
            raise RuntimeError(f"Failed to fetch video/playlist info: {str(e)}")

    if not info:
        raise RuntimeError("No media info could be retrieved from the provided URL.")

    # Check if entry is a playlist or single video
    is_playlist = info.get('_type') == 'playlist' or 'entries' in info

    if is_playlist:
        entries = []
        raw_entries = info.get('entries', [])
        for idx, entry in enumerate(raw_entries, 1):
            if not entry:
                continue
            v_id = entry.get('id') or entry.get('url', '')
            v_url = entry.get('url') if (entry.get('url') and entry.get('url').startswith('http')) else f"https://www.youtube.com/watch?v={v_id}"
            entries.append({
                'index': idx,
                'id': v_id,
                'title': entry.get('title', f'Video {idx}'),
                'duration': entry.get('duration'),
                'duration_str': format_duration(entry.get('duration')),
                'uploader': entry.get('uploader') or entry.get('channel') or info.get('title', 'YouTube Channel'),
                'thumbnail': entry.get('thumbnail') or (entry.get('thumbnails')[-1]['url'] if entry.get('thumbnails') else f"https://i.ytimg.com/vi/{v_id}/hqdefault.jpg"),
                'url': v_url
            })

        return {
            'is_playlist': True,
            'title': info.get('title', 'YouTube Playlist'),
            'uploader': info.get('uploader') or info.get('channel', 'YouTube User'),
            'total_videos': len(entries),
            'thumbnail': info.get('thumbnail') or (entries[0]['thumbnail'] if entries else ''),
            'entries': entries
        }
    else:
        # Single Video Detailed Formats Analysis
        formats = info.get('formats', [])
        res_map = {}
        
        # Extract unique resolutions with format details
        for f in formats:
            height = f.get('height')
            vcodec = f.get('vcodec', 'none')
            if height and vcodec != 'none':
                res_key = f"{height}p"
                # Keep highest bitrate or format with audio if available
                filesize = f.get('filesize') or f.get('filesize_approx') or 0
                if res_key not in res_map or filesize > res_map[res_key].get('filesize', 0):
                    res_map[res_key] = {
                        'format_id': f.get('format_id'),
                        'resolution': res_key,
                        'height': height,
                        'ext': f.get('ext', 'mp4'),
                        'filesize': filesize,
                        'filesize_str': format_filesize(filesize),
                        'fps': f.get('fps'),
                        'vcodec': f.get('vcodec')
                    }

        # Sort resolutions descending (1080p, 720p, 480p, etc.)
        sorted_resolutions = sorted(res_map.values(), key=lambda x: x['height'], reverse=True)

        return {
            'is_playlist': False,
            'id': info.get('id'),
            'title': info.get('title', 'YouTube Video'),
            'uploader': info.get('uploader') or info.get('channel', 'YouTube Channel'),
            'duration': info.get('duration'),
            'duration_str': format_duration(info.get('duration')),
            'views': info.get('view_count', 0),
            'views_str': f"{info.get('view_count', 0):,}" if info.get('view_count') else 'N/A',
            'thumbnail': info.get('thumbnail') or f"https://i.ytimg.com/vi/{info.get('id')}/hqdefault.jpg",
            'webpage_url': info.get('webpage_url', url),
            'video_resolutions': sorted_resolutions,
            'audio_options': [
                {'format': 'mp3', 'label': 'MP3 (High Quality 320kbps)', 'bitrate': '320'},
                {'format': 'mp3', 'label': 'MP3 (Standard Quality 192kbps)', 'bitrate': '192'},
                {'format': 'm4a', 'label': 'M4A (AAC Audio)', 'bitrate': '128'},
                {'format': 'wav', 'label': 'WAV (Uncompressed Lossless)', 'bitrate': '0'},
                {'format': 'flac', 'label': 'FLAC (Lossless Compressed)', 'bitrate': '0'}
            ]
        }

def format_duration(seconds):
    """Convert seconds to hh:mm:ss format"""
    if not seconds:
        return "Unknown"
    seconds = int(seconds)
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    secs = seconds % 60
    if hours > 0:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"

def format_filesize(bytes_size):
    """Format bytes to human readable string (MB/GB)"""
    if not bytes_size:
        return "Est. ~"
    mb = bytes_size / (1024 * 1024)
    if mb >= 1000:
        return f"{mb / 1024:.2f} GB"
    return f"{mb:.1f} MB"

def progress_hook(d, job_id):
    """yt-dlp progress callback hook"""
    if job_id not in jobs:
        return
    
    if d['status'] == 'downloading':
        total = d.get('total_bytes') or d.get('total_bytes_estimate') or 0
        downloaded = d.get('downloaded_bytes', 0)
        percentage = (downloaded / total * 100) if total > 0 else 0
        
        speed = d.get('_speed_str', '').strip()
        eta = d.get('_eta_str', '').strip()
        
        jobs[job_id].update({
            'status': 'downloading',
            'progress': round(percentage, 1),
            'speed': speed or 'Calculating...',
            'eta': eta or 'Estimating...',
        })
    elif d['status'] == 'finished':
        jobs[job_id].update({
            'status': 'converting',
            'progress': 95.0,
            'speed': 'Processing',
            'eta': 'Almost done'
        })

def start_download_thread(url, download_type, options, job_id):
    """Thread target function to download video or audio using yt-dlp"""
    clean_old_files()
    jobs[job_id] = {
        'status': 'queued',
        'progress': 0,
        'speed': '',
        'eta': '',
        'filename': '',
        'file_path': '',
        'title': '',
        'error_msg': ''
    }

    try:
        out_template = str(DOWNLOADS_DIR / f"{job_id}_%(title)s.%(ext)s")
        
        ydl_opts = {
            'outtmpl': out_template,
            'quiet': True,
            'no_warnings': True,
            'progress_hooks': [lambda d: progress_hook(d, job_id)],
            'nocheckcertificate': True
        }

        if download_type == 'audio':
            audio_format = options.get('audio_format', 'mp3')
            bitrate = options.get('bitrate', '320')
            ydl_opts.update({
                'format': 'bestaudio/best',
                'postprocessors': [{
                    'key': 'FFmpegExtractAudio',
                    'preferredcodec': audio_format,
                    'preferredquality': bitrate if bitrate != '0' else None,
                }],
            })
        else: # video format
            resolution = options.get('resolution', 'best')
            if resolution == 'best' or not resolution:
                format_spec = 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best'
            else:
                target_height = resolution.replace('p', '')
                format_spec = f'bestvideo[height<={target_height}][ext=mp4]+bestaudio[ext=m4a]/bestvideo[height<={target_height}]+bestaudio/best[height<={target_height}]/best'
            
            ydl_opts.update({
                'format': format_spec,
                'merge_output_format': 'mp4',
            })

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            title = info.get('title', 'Downloaded Media')
            
            # Find the actual output file created
            matched_files = list(DOWNLOADS_DIR.glob(f"{job_id}_*"))
            if not matched_files:
                raise RuntimeError("File download completed but output file could not be located.")
            
            final_file = matched_files[0]
            clean_filename = f"{sanitize_filename(title)}{final_file.suffix}"
            
            jobs[job_id].update({
                'status': 'finished',
                'progress': 100.0,
                'filename': clean_filename,
                'file_path': str(final_file),
                'title': title
            })

    except Exception as e:
        print(f"Error in download thread for job {job_id}: {e}")
        jobs[job_id].update({
            'status': 'error',
            'error_msg': str(e)
        })

def download_playlist_zip_thread(urls, download_type, options, job_id):
    """
    Downloads multiple playlist videos and bundles them into a single ZIP file for batch download.
    """
    clean_old_files()
    jobs[job_id] = {
        'status': 'queued',
        'progress': 0,
        'speed': '',
        'eta': '',
        'filename': '',
        'file_path': '',
        'title': 'Playlist Bundle',
        'error_msg': ''
    }

    total_count = len(urls)
    if total_count == 0:
        jobs[job_id].update({'status': 'error', 'error_msg': 'No videos selected for playlist download.'})
        return

    downloaded_files = []
    
    try:
        for idx, video_url in enumerate(urls, 1):
            sub_id = f"{job_id}_sub_{idx}"
            out_template = str(DOWNLOADS_DIR / f"{sub_id}_%(title)s.%(ext)s")
            
            # Update batch progress
            base_progress = ((idx - 1) / total_count) * 100
            jobs[job_id].update({
                'status': 'downloading',
                'progress': round(base_progress, 1),
                'speed': f'Item {idx}/{total_count}',
                'eta': f'Processing {idx}/{total_count}'
            })

            ydl_opts = {
                'outtmpl': out_template,
                'quiet': True,
                'no_warnings': True,
                'nocheckcertificate': True
            }

            if download_type == 'audio':
                audio_format = options.get('audio_format', 'mp3')
                bitrate = options.get('bitrate', '192')
                ydl_opts.update({
                    'format': 'bestaudio/best',
                    'postprocessors': [{
                        'key': 'FFmpegExtractAudio',
                        'preferredcodec': audio_format,
                        'preferredquality': bitrate,
                    }],
                })
            else:
                resolution = options.get('resolution', '720p')
                target_height = resolution.replace('p', '') if resolution != 'best' else '720'
                format_spec = f'bestvideo[height<={target_height}][ext=mp4]+bestaudio[ext=m4a]/best[height<={target_height}]/best'
                ydl_opts.update({
                    'format': format_spec,
                    'merge_output_format': 'mp4',
                })

            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                try:
                    ydl.extract_info(video_url, download=True)
                    matches = list(DOWNLOADS_DIR.glob(f"{sub_id}_*"))
                    if matches:
                        downloaded_files.append(matches[0])
                except Exception as item_err:
                    print(f"Error downloading playlist item {video_url}: {item_err}")

        if not downloaded_files:
            raise RuntimeError("Failed to download any of the selected playlist videos.")

        # Create Zip archive
        jobs[job_id].update({
            'status': 'converting',
            'progress': 95.0,
            'speed': 'Creating ZIP package...',
            'eta': 'Finalizing archive'
        })

        zip_filename = f"Playlist_Batch_{job_id[:8]}.zip"
        zip_path = DOWNLOADS_DIR / f"{job_id}_playlist.zip"

        with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
            for fpath in downloaded_files:
                # Remove prefix sub_id
                original_name = fpath.name.replace(f"{fpath.name.split('_')[0]}_{fpath.name.split('_')[1]}_", "")
                zipf.write(fpath, arcname=original_name)

        # Cleanup individual temp files after zipping
        for fpath in downloaded_files:
            try:
                fpath.unlink()
            except Exception:
                pass

        jobs[job_id].update({
            'status': 'finished',
            'progress': 100.0,
            'filename': zip_filename,
            'file_path': str(zip_path),
            'title': f'Playlist ({len(downloaded_files)} videos)'
        })

    except Exception as e:
        print(f"Error zipping playlist job {job_id}: {e}")
        jobs[job_id].update({
            'status': 'error',
            'error_msg': str(e)
        })
