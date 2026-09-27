import os
import sys
import re
import time
import zipfile
import threading
import uuid
import subprocess
import shutil
import base64
from pathlib import Path
import requests
import yt_dlp
from yt_dlp.utils import download_range_func
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
COOKIE_FILE_PATH = BASE_DIR / "cookies.txt"

# In-memory storage for active/completed download jobs
jobs = {}

def init_cookies():
    """
    Check if cookies are provided via environment variables (ideal for Render/Docker/Railway cloud deployments).
    Supports YOUTUBE_COOKIES (raw Netscape string) or YOUTUBE_COOKIES_BASE64.
    """
    raw_cookies = os.environ.get("YOUTUBE_COOKIES", "").strip()
    b64_cookies = os.environ.get("YOUTUBE_COOKIES_BASE64", "").strip()

    if raw_cookies:
        try:
            with open(COOKIE_FILE_PATH, "w", encoding="utf-8") as f:
                f.write(raw_cookies)
            print("[TubeGrab] Successfully generated cookies.txt from YOUTUBE_COOKIES env var.")
        except Exception as e:
            print(f"[TubeGrab] Error writing YOUTUBE_COOKIES: {e}")
    elif b64_cookies:
        try:
            decoded = base64.b64decode(b64_cookies).decode('utf-8')
            with open(COOKIE_FILE_PATH, "w", encoding="utf-8") as f:
                f.write(decoded)
            print("[TubeGrab] Successfully generated cookies.txt from YOUTUBE_COOKIES_BASE64 env var.")
        except Exception as e:
            print(f"[TubeGrab] Error writing YOUTUBE_COOKIES_BASE64: {e}")

# Initialize cookies on module load
init_cookies()

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

def normalize_youtube_url(url):
    """
    Normalizes various YouTube URL formats (live, shorts, youtu.be, embed)
    into standard canonical watch / playlist URLs and strips tracking parameters.
    """
    if not url:
        return url
    url = url.strip()
    
    # Handle /live/, /shorts/, youtu.be, /embed/, /v/
    match = re.search(r'(?:youtube\.com/(?:live/|shorts/|embed/|v/)|youtu\.be/)([a-zA-Z0-9_-]{11})', url)
    if match:
        video_id = match.group(1)
        list_match = re.search(r'[?&]list=([a-zA-Z0-9_-]+)', url)
        if list_match:
            return f"https://www.youtube.com/watch?v={video_id}&list={list_match.group(1)}"
        return f"https://www.youtube.com/watch?v={video_id}"
    return url

def parse_time_to_seconds(time_val):
    """Convert HH:MM:SS or MM:SS or seconds string/number into seconds float"""
    if time_val is None:
        return None
    if isinstance(time_val, (int, float)):
        return float(time_val)
    time_str = str(time_val).strip()
    if not time_str:
        return None
    parts = time_str.split(':')
    try:
        if len(parts) == 1:
            return float(parts[0])
        elif len(parts) == 2:
            return int(parts[0]) * 60 + float(parts[1])
        elif len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
    except (ValueError, TypeError):
        return None
    return None

def trim_media_file(input_file, start_sec, end_sec):
    """
    Trim downloaded media file using FFmpeg to exact start/end timestamps.
    """
    input_path = Path(input_file)
    if not input_path.exists():
        return False
        
    output_path = input_path.with_name(f"trim_{input_path.name}")
    
    cmd = ['ffmpeg', '-y']
    if start_sec is not None and start_sec > 0:
        cmd.extend(['-ss', str(start_sec)])
    if end_sec is not None and end_sec > 0:
        if start_sec is not None and start_sec > 0:
            cmd.extend(['-to', str(end_sec)])
        else:
            cmd.extend(['-t', str(end_sec)])
            
    cmd.extend(['-i', str(input_path), '-c', 'copy', str(output_path)])
    
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode == 0 and output_path.exists() and output_path.stat().st_size > 0:
        input_path.unlink(missing_ok=True)
        output_path.rename(input_path)
        return True
    else:
        # Fallback with re-encoding in case stream copy fails at non-keyframes
        cmd_fallback = ['ffmpeg', '-y']
        if start_sec is not None and start_sec > 0:
            cmd_fallback.extend(['-ss', str(start_sec)])
        if end_sec is not None and end_sec > 0:
            cmd_fallback.extend(['-to', str(end_sec)])
        cmd_fallback.extend(['-i', str(input_path), str(output_path)])
        res2 = subprocess.run(cmd_fallback, capture_output=True, text=True)
        if res2.returncode == 0 and output_path.exists() and output_path.stat().st_size > 0:
            input_path.unlink(missing_ok=True)
            output_path.rename(input_path)
            return True
            
    return False

def get_base_ydl_opts(client_list=None):
    """
    Returns robust yt-dlp options configured for cloud IP compatibility and reliability.
    """
    if client_list is None:
        client_list = ['android', 'ios', 'tv', 'mweb']

    opts = {
        'quiet': True,
        'no_warnings': True,
        'nocheckcertificate': True,
        'geo_bypass': True,
        'retries': 3,
        'fragment_retries': 3,
        'socket_timeout': 20,
        'live_from_start': True,
        'extractor_args': {
            'youtube': {
                'player_client': client_list,
                'player_skip': ['configs']
            }
        },
        'http_headers': {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
            'Accept-Language': 'en-US,en;q=0.9',
        }
    }

    # Auto-detect cookies.txt in root directory
    if COOKIE_FILE_PATH.exists() and COOKIE_FILE_PATH.stat().st_size > 0:
        opts['cookiefile'] = str(COOKIE_FILE_PATH)

    # Optional proxy support via environment variable
    proxy = os.environ.get("PROXY_URL") or os.environ.get("HTTP_PROXY") or os.environ.get("HTTPS_PROXY")
    if proxy:
        opts['proxy'] = proxy

    return opts

def fetch_oembed_fallback(url):
    """
    Fallback metadata extractor using official YouTube oEmbed API when cloud IP is bot-blocked.
    Never blocked by YouTube datacenter IP checks.
    """
    clean_url = normalize_youtube_url(url)
    match = re.search(r'[?&]v=([a-zA-Z0-9_-]{11})', clean_url)
    if not match:
        match = re.search(r'([a-zA-Z0-9_-]{11})', clean_url)
    video_id = match.group(1) if match else "video"

    try:
        resp = requests.get(
            f"https://www.youtube.com/oembed?url=https://www.youtube.com/watch?v={video_id}&format=json",
            timeout=8
        )
        if resp.status_code == 200:
            data = resp.json()
            title = data.get('title', 'YouTube Video')
            uploader = data.get('author_name', 'YouTube Creator')
            thumbnail = f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"

            return {
                'is_playlist': False,
                'id': video_id,
                'title': title,
                'uploader': uploader,
                'duration': None,
                'duration_str': 'Full Duration',
                'views': None,
                'views_str': 'Available',
                'thumbnail': thumbnail,
                'webpage_url': f"https://www.youtube.com/watch?v={video_id}",
                'video_resolutions': [
                    {'resolution': '1080p', 'height': 1080, 'ext': 'mp4', 'filesize': 0, 'filesize_str': 'Full HD 1080p'},
                    {'resolution': '720p', 'height': 720, 'ext': 'mp4', 'filesize': 0, 'filesize_str': 'HD 720p (Recommended)'},
                    {'resolution': '480p', 'height': 480, 'ext': 'mp4', 'filesize': 0, 'filesize_str': 'Standard 480p'},
                    {'resolution': '360p', 'height': 360, 'ext': 'mp4', 'filesize': 0, 'filesize_str': 'Compact 360p'}
                ],
                'audio_options': [
                    {'format': 'mp3', 'label': 'MP3 (High Quality 320kbps)', 'bitrate': '320'},
                    {'format': 'mp3', 'label': 'MP3 (Standard Quality 192kbps)', 'bitrate': '192'},
                    {'format': 'm4a', 'label': 'M4A (AAC Audio)', 'bitrate': '128'},
                    {'format': 'wav', 'label': 'WAV (Uncompressed Lossless)', 'bitrate': '0'}
                ]
            }
    except Exception as err:
        print(f"[TubeGrab] oEmbed fallback error: {err}")
    return None

def fetch_media_info(url):
    """
    Fetches detailed metadata for a video or playlist with fallback client retries
    and graceful oEmbed fallback on cloud hosting.
    """
    # Refresh cookies if updated
    init_cookies()

    # Normalize URL (handles /live/, /shorts/, youtu.be, etc.)
    url = normalize_youtube_url(url)

    client_attempts = [
        ['android', 'ios'],
        ['web']
    ]

    last_error = None
    info = None

    for clients in client_attempts:
        ydl_opts = get_base_ydl_opts(clients)
        ydl_opts.update({
            'extract_flat': 'in_playlist',
            'skip_download': True,
        })
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)
                if info:
                    break
        except Exception as e:
            last_error = str(e)
            continue

    if not info:
        # Check if oEmbed fallback can retrieve video metadata
        fallback_data = fetch_oembed_fallback(url)
        if fallback_data:
            return fallback_data

        if 'Sign in to confirm you' in str(last_error):
            raise RuntimeError(
                "YouTube Bot Check: Cloud IP is blocked by YouTube. "
                "Please configure YOUTUBE_COOKIES in Render Environment Variables or run locally."
            )
        raise RuntimeError(f"Failed to fetch video/playlist info: {last_error or 'Unknown error'}")

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
        formats = info.get('formats', [])
        res_map = {}
        
        for f in formats:
            height = f.get('height')
            vcodec = f.get('vcodec', 'none')
            if height and vcodec != 'none':
                res_key = f"{height}p"
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
    """Thread target function to download video or audio using yt-dlp with trimming support"""
    clean_old_files()
    init_cookies()
    url = normalize_youtube_url(url)

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
        
        ydl_opts = get_base_ydl_opts()
        ydl_opts.update({
            'outtmpl': out_template,
            'progress_hooks': [lambda d: progress_hook(d, job_id)],
        })

        # Check trimming parameters
        start_sec = parse_time_to_seconds(options.get('start_time'))
        end_sec = parse_time_to_seconds(options.get('end_time'))
        is_trimmed = (start_sec is not None and start_sec > 0) or (end_sec is not None and end_sec > 0)

        if is_trimmed:
            ydl_opts['download_ranges'] = download_range_func(None, [(start_sec or 0, end_sec or float('inf'))])
            ydl_opts['force_keyframes_at_cuts'] = True

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
        else:
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
            
            matched_files = list(DOWNLOADS_DIR.glob(f"{job_id}_*"))
            if not matched_files:
                raise RuntimeError("File download completed but output file could not be located.")
            
            final_file = matched_files[0]
            
            # Post-cut verification with FFmpeg if trimming requested
            if is_trimmed:
                try:
                    trim_media_file(final_file, start_sec, end_sec)
                except Exception as trim_err:
                    print(f"Warning: post-trim failed, using raw download: {trim_err}")

            clip_tag = "_clip" if is_trimmed else ""
            clean_filename = f"{sanitize_filename(title)}{clip_tag}{final_file.suffix}"
            
            jobs[job_id].update({
                'status': 'finished',
                'progress': 100.0,
                'filename': clean_filename,
                'file_path': str(final_file),
                'title': f"{title} (Trimmed Clip)" if is_trimmed else title
            })

    except Exception as e:
        err_str = str(e)
        print(f"Error in download thread for job {job_id}: {err_str}")
        if 'Sign in to confirm you' in err_str:
            err_str = (
                "YouTube Cloud IP Block: Render server IP is blocked by YouTube BotGuard. "
                "Please add YOUTUBE_COOKIES in Render Environment Variables or run TubeGrab locally."
            )
        jobs[job_id].update({
            'status': 'error',
            'error_msg': err_str
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
        for idx, raw_url in enumerate(urls, 1):
            video_url = normalize_youtube_url(raw_url)
            sub_id = f"{job_id}_sub_{idx}"
            out_template = str(DOWNLOADS_DIR / f"{sub_id}_%(title)s.%(ext)s")
            
            base_progress = ((idx - 1) / total_count) * 100
            jobs[job_id].update({
                'status': 'downloading',
                'progress': round(base_progress, 1),
                'speed': f'Item {idx}/{total_count}',
                'eta': f'Processing {idx}/{total_count}'
            })

            ydl_opts = get_base_ydl_opts()
            ydl_opts.update({
                'outtmpl': out_template,
            })

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
                original_name = fpath.name.replace(f"{fpath.name.split('_')[0]}_{fpath.name.split('_')[1]}_", "")
                zipf.write(fpath, arcname=original_name)

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
