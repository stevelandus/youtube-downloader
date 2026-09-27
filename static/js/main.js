/**
 * TubeGrab PRO - Frontend Client Application
 * Handles YouTube Video & Playlist downloads with custom format selectors,
 * select-all sync, and animated loading progress UI.
 */

document.addEventListener('DOMContentLoaded', () => {
    // DOM Elements
    const urlInput = document.getElementById('urlInput');
    const clearBtn = document.getElementById('clearBtn');
    const pasteBtn = document.getElementById('pasteBtn');
    const fetchBtn = document.getElementById('fetchBtn');
    const fetchSpinner = document.getElementById('fetchSpinner');
    const errorAlert = document.getElementById('errorAlert');
    const errorMessage = document.getElementById('errorMessage');

    const resultsSection = document.getElementById('resultsSection');
    const singleVideoCard = document.getElementById('singleVideoCard');
    const playlistCard = document.getElementById('playlistCard');

    // Animated Download Progress Section
    const downloadProgressSection = document.getElementById('downloadProgressSection');
    const dlStatusBadge = document.getElementById('dlStatusBadge');
    const dlActiveTitle = document.getElementById('dlActiveTitle');
    const dlProgressBar = document.getElementById('dlProgressBar');
    const dlSpeedText = document.getElementById('dlSpeedText');
    const dlPercentText = document.getElementById('dlPercentText');
    const dlCompleteBox = document.getElementById('dlCompleteBox');
    const dlSaveFileBtn = document.getElementById('dlSaveFileBtn');

    // Single Video elements
    const videoThumb = document.getElementById('videoThumb');
    const videoDuration = document.getElementById('videoDuration');
    const videoTitle = document.getElementById('videoTitle');
    const videoUploader = document.getElementById('videoUploader');
    const videoViews = document.getElementById('videoViews');
    const videoDurationText = document.getElementById('videoDurationText');
    const videoFormatsGrid = document.getElementById('videoFormatsGrid');
    const audioFormatsGrid = document.getElementById('audioFormatsGrid');

    // Playlist elements
    const playlistThumb = document.getElementById('playlistThumb');
    const playlistTitle = document.getElementById('playlistTitle');
    const playlistUploader = document.getElementById('playlistUploader');
    const playlistTotalCount = document.getElementById('playlistTotalCount');
    const playlistItemsList = document.getElementById('playlistItemsList');
    const selectAllCheckbox = document.getElementById('selectAllCheckbox');
    const selectedCountText = document.getElementById('selectedCountText');
    const downloadSelectedZipBtn = document.getElementById('downloadSelectedZipBtn');
    const batchFormatSelect = document.getElementById('batchFormatSelect');

    // State
    let currentMediaData = null;
    let currentActiveJobId = null;
    let activePollInterval = null;

    // Input handlers
    clearBtn.addEventListener('click', () => {
        urlInput.value = '';
        hideError();
    });

    pasteBtn.addEventListener('click', async () => {
        try {
            const text = await navigator.clipboard.readText();
            if (text) {
                urlInput.value = text.trim();
                hideError();
            }
        } catch (err) {
            console.warn('Clipboard read failed:', err);
        }
    });

    // Tab switcher
    document.querySelectorAll('.tab-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
            document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));

            btn.classList.add('active');
            const targetTab = document.getElementById(btn.dataset.tab);
            if (targetTab) targetTab.classList.add('active');
        });
    });

    // Fetch Link metadata
    fetchBtn.addEventListener('click', handleAnalyzeLink);
    urlInput.addEventListener('keypress', (e) => {
        if (e.key === 'Enter') handleAnalyzeLink();
    });

    async function handleAnalyzeLink() {
        const url = urlInput.value.trim();
        if (!url) {
            showError("Please paste a valid YouTube video or playlist URL.");
            return;
        }

        hideError();
        setLoading(true);
        resultsSection.classList.add('hidden');
        singleVideoCard.classList.add('hidden');
        playlistCard.classList.add('hidden');

        try {
            const response = await fetch('/api/info', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ url: url })
            });

            const data = await response.json();

            if (!data.success) {
                throw new Error(data.error || "Failed to analyze link.");
            }

            currentMediaData = data.data;
            renderMediaResult(currentMediaData);

        } catch (err) {
            showError(err.message);
        } finally {
            setLoading(false);
        }
    }

    function renderMediaResult(info) {
        resultsSection.classList.remove('hidden');

        if (info.is_playlist) {
            // Render Playlist View
            playlistCard.classList.remove('hidden');
            playlistThumb.src = info.thumbnail;
            playlistTitle.textContent = info.title;
            playlistUploader.textContent = info.uploader;
            playlistTotalCount.textContent = info.total_videos;

            renderPlaylistItems(info.entries);
        } else {
            // Render Single Video View
            singleVideoCard.classList.remove('hidden');
            videoThumb.src = info.thumbnail;
            videoDuration.textContent = info.duration_str;
            videoTitle.textContent = info.title;
            videoUploader.textContent = info.uploader;
            videoViews.textContent = info.views_str;
            videoDurationText.textContent = info.duration_str;

            renderVideoFormats(info.video_resolutions, info.webpage_url, info.title);
            renderAudioFormats(info.audio_options, info.webpage_url, info.title);
        }
    }

    function renderVideoFormats(resolutions, videoUrl, mediaTitle) {
        videoFormatsGrid.innerHTML = '';

        if (!resolutions || resolutions.length === 0) {
            resolutions = [
                { resolution: '1080p', filesize_str: 'High Quality MP4' },
                { resolution: '720p', filesize_str: 'HD MP4' },
                { resolution: '480p', filesize_str: 'SD MP4' },
                { resolution: '360p', filesize_str: 'Compact MP4' }
            ];
        }

        resolutions.forEach(res => {
            const card = document.createElement('div');
            card.className = 'format-card';
            card.innerHTML = `
                <div class="format-info">
                    <span class="res-tag"><i class="fa-solid fa-video"></i> ${res.resolution}</span>
                    <span class="size-tag">${res.filesize_str || 'MP4 Format'}</span>
                </div>
                <button class="btn-dl" data-url="${videoUrl}" data-type="video" data-res="${res.resolution}">
                    <i class="fa-solid fa-download"></i> Download MP4
                </button>
            `;

            card.querySelector('.btn-dl').addEventListener('click', (e) => {
                const btn = e.currentTarget;
                startSingleDownload(btn.dataset.url, 'video', { resolution: btn.dataset.res }, mediaTitle);
            });

            videoFormatsGrid.appendChild(card);
        });
    }

    function renderAudioFormats(audioOpts, videoUrl, mediaTitle) {
        audioFormatsGrid.innerHTML = '';

        audioOpts.forEach(opt => {
            const card = document.createElement('div');
            card.className = 'format-card';
            card.innerHTML = `
                <div class="format-info">
                    <span class="res-tag"><i class="fa-solid fa-music"></i> ${opt.format.toUpperCase()}</span>
                    <span class="size-tag">${opt.label}</span>
                </div>
                <button class="btn-dl" data-url="${videoUrl}" data-type="audio" data-format="${opt.format}" data-bitrate="${opt.bitrate}">
                    <i class="fa-solid fa-download"></i> Download Audio
                </button>
            `;

            card.querySelector('.btn-dl').addEventListener('click', (e) => {
                const btn = e.currentTarget;
                startSingleDownload(btn.dataset.url, 'audio', {
                    audio_format: btn.dataset.format,
                    bitrate: btn.dataset.bitrate
                }, mediaTitle);
            });

            audioFormatsGrid.appendChild(card);
        });
    }

    function renderPlaylistItems(entries) {
        playlistItemsList.innerHTML = '';
        selectedCountText.textContent = entries.length;

        entries.forEach(item => {
            const row = document.createElement('div');
            row.className = 'playlist-item-row';
            row.innerHTML = `
                <label class="checkbox-container">
                    <input type="checkbox" class="item-checkbox" data-url="${item.url}" checked>
                </label>
                <span class="item-index">#${item.index}</span>
                <div class="item-thumb">
                    <img src="${item.thumbnail}" alt="thumb">
                </div>
                <div class="item-info">
                    <div class="item-title" title="${item.title}">${item.title}</div>
                    <div class="item-duration"><i class="fa-regular fa-clock"></i> ${item.duration_str}</div>
                </div>
                <div class="item-controls">
                    <select class="item-format-select">
                        <optgroup label="Video">
                            <option value="video_1080p">MP4 1080p</option>
                            <option value="video_720p" selected>MP4 720p</option>
                            <option value="video_480p">MP4 480p</option>
                            <option value="video_360p">MP4 360p</option>
                        </optgroup>
                        <optgroup label="Audio">
                            <option value="audio_mp3_320">MP3 320k</option>
                            <option value="audio_mp3_192">MP3 192k</option>
                            <option value="audio_m4a_128">M4A Audio</option>
                            <option value="audio_wav_0">WAV Lossless</option>
                            <option value="audio_flac_0">FLAC Lossless</option>
                        </optgroup>
                    </select>
                    <button class="btn-dl sm-btn" data-url="${item.url}" data-title="${item.title}">
                        <i class="fa-solid fa-download"></i> Download
                    </button>
                </div>
            `;

            // Individual playlist video download button click
            row.querySelector('.btn-dl').addEventListener('click', (e) => {
                const btn = e.currentTarget;
                const selectVal = row.querySelector('.item-format-select').value;
                const itemUrl = btn.dataset.url;
                const itemTitle = btn.dataset.title;

                let downloadType = 'video';
                let options = { resolution: '720p' };

                if (selectVal.startsWith('audio')) {
                    downloadType = 'audio';
                    const parts = selectVal.split('_'); // audio_mp3_320
                    options = { audio_format: parts[1], bitrate: parts[2] };
                } else {
                    const res = selectVal.split('_')[1]; // 1080p
                    options = { resolution: res };
                }

                startSingleDownload(itemUrl, downloadType, options, itemTitle);
            });

            // Checkbox change listener to sync "Select All" status
            const checkbox = row.querySelector('.item-checkbox');
            checkbox.addEventListener('change', updateSelectedPlaylistCount);

            playlistItemsList.appendChild(row);
        });

        // Select All handler
        selectAllCheckbox.checked = true;
        selectAllCheckbox.onclick = () => {
            const checked = selectAllCheckbox.checked;
            document.querySelectorAll('.item-checkbox').forEach(cb => cb.checked = checked);
            updateSelectedPlaylistCount();
        };
    }

    // Fix: Synchronize "Select All" checkbox state when any individual checkbox changes
    function updateSelectedPlaylistCount() {
        const allBoxes = document.querySelectorAll('.item-checkbox');
        const checkedBoxes = document.querySelectorAll('.item-checkbox:checked');

        selectedCountText.textContent = checkedBoxes.length;

        // Automatically uncheck "Select All" if any checkbox is unselected
        selectAllCheckbox.checked = (allBoxes.length > 0 && checkedBoxes.length === allBoxes.length);
    }

    // Download Playlist Batch as ZIP
    downloadSelectedZipBtn.addEventListener('click', async () => {
        const checkedBoxes = document.querySelectorAll('.item-checkbox:checked');
        const urls = Array.from(checkedBoxes).map(cb => cb.dataset.url);

        if (urls.length === 0) {
            showError("Please select at least one video from playlist to download.");
            return;
        }

        const selectedOption = batchFormatSelect.value;
        let downloadType = 'video';
        let options = { resolution: '720p' };

        if (selectedOption.startsWith('audio')) {
            downloadType = 'audio';
            const parts = selectedOption.split('_');
            options = { audio_format: parts[1], bitrate: parts[2] };
        } else {
            const res = selectedOption.split('_')[1];
            options = { resolution: res };
        }

        try {
            const response = await fetch('/api/download/playlist-zip', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    urls: urls,
                    type: downloadType,
                    options: options
                })
            });

            const data = await response.json();
            if (!data.success) throw new Error(data.error || "Failed to start playlist download.");

            trackDownloadJob(data.job_id, `Playlist Batch (${urls.length} items)`);
        } catch (err) {
            showError(err.message);
        }
    });

    // Start Single Download Job
    async function startSingleDownload(url, type, options, mediaTitle) {
        try {
            const response = await fetch('/api/download/start', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    url: url,
                    type: type,
                    options: options
                })
            });

            const data = await response.json();
            if (!data.success) throw new Error(data.error || "Failed to start download.");

            const title = mediaTitle || (currentMediaData ? currentMediaData.title : 'YouTube Media');
            trackDownloadJob(data.job_id, title);
        } catch (err) {
            showError(err.message);
        }
    }

    // Animated Download UI & Real-Time Status Tracking
    function trackDownloadJob(jobId, initialTitle) {
        currentActiveJobId = jobId;

        if (activePollInterval) {
            clearInterval(activePollInterval);
        }

        // Show Animated Loading Section & Scroll to it
        downloadProgressSection.classList.remove('hidden');
        downloadProgressSection.scrollIntoView({ behavior: 'smooth', block: 'center' });

        dlStatusBadge.textContent = 'QUEUED...';
        dlStatusBadge.style.borderColor = 'rgba(245, 158, 11, 0.4)';
        dlStatusBadge.style.color = '#F59E0B';

        dlActiveTitle.textContent = initialTitle;
        dlProgressBar.style.width = '0%';
        dlPercentText.textContent = '0%';
        dlSpeedText.innerHTML = '<i class="fa-solid fa-gauge-high"></i> Initializing download stream...';
        dlCompleteBox.classList.add('hidden');

        // Poll status every 800ms
        activePollInterval = setInterval(async () => {
            try {
                const res = await fetch(`/api/job/status/${jobId}`);
                const statusData = await res.json();

                updateDownloadProgressUI(statusData);

                if (statusData.status === 'finished' || statusData.status === 'error') {
                    clearInterval(activePollInterval);
                    activePollInterval = null;

                    if (statusData.status === 'finished') {
                        // Reveal Big Save File Button & Auto Download
                        dlCompleteBox.classList.remove('hidden');
                        dlSaveFileBtn.href = `/api/download/file/${jobId}`;
                        dlSaveFileBtn.download = statusData.filename || 'download';

                        // Auto-trigger browser download dialog
                        const link = document.createElement('a');
                        link.href = `/api/download/file/${jobId}`;
                        link.download = statusData.filename || 'download';
                        document.body.appendChild(link);
                        link.click();
                        document.body.removeChild(link);
                    }
                }
            } catch (e) {
                console.error('Job status check error:', e);
            }
        }, 800);
    }

    function updateDownloadProgressUI(data) {
        if (data.title) dlActiveTitle.textContent = data.title;

        const progress = data.progress || 0;
        dlProgressBar.style.width = `${progress}%`;
        dlPercentText.textContent = `${progress}%`;

        if (data.status === 'downloading') {
            dlStatusBadge.textContent = 'DOWNLOADING...';
            dlStatusBadge.style.color = '#FF0055';
            dlStatusBadge.style.borderColor = 'rgba(255, 0, 85, 0.4)';
            dlSpeedText.innerHTML = `<i class="fa-solid fa-gauge-high"></i> Speed: ${data.speed || 'Calculating...'} | ETA: ${data.eta || 'Estimating...'}`;
        } else if (data.status === 'converting') {
            dlStatusBadge.textContent = 'PROCESSING FFMPEG...';
            dlStatusBadge.style.color = '#7928CA';
            dlStatusBadge.style.borderColor = 'rgba(121, 40, 202, 0.4)';
            dlSpeedText.innerHTML = `<i class="fa-solid fa-compact-disc fa-spin"></i> Merging video & audio streams with FFmpeg...`;
        } else if (data.status === 'finished') {
            dlStatusBadge.textContent = 'COMPLETED!';
            dlStatusBadge.style.color = '#10B981';
            dlStatusBadge.style.borderColor = 'rgba(16, 185, 129, 0.4)';
            dlSpeedText.innerHTML = `<i class="fa-solid fa-circle-check" style="color: #10B981;"></i> Download ready: ${data.filename || ''}`;
        } else if (data.status === 'error') {
            dlStatusBadge.textContent = 'FAILED';
            dlStatusBadge.style.color = '#EF4444';
            dlStatusBadge.style.borderColor = 'rgba(239, 68, 68, 0.4)';
            dlSpeedText.innerHTML = `<i class="fa-solid fa-circle-exclamation" style="color: #EF4444;"></i> ${data.error_msg || 'Download failed'}`;
        }
    }

    function setLoading(isLoading) {
        fetchBtn.disabled = isLoading;
        if (isLoading) {
            fetchSpinner.classList.remove('hidden');
            fetchBtn.querySelector('.btn-text').classList.add('hidden');
        } else {
            fetchSpinner.classList.add('hidden');
            fetchBtn.querySelector('.btn-text').classList.remove('hidden');
        }
    }

    function showError(msg) {
        errorMessage.textContent = msg;
        errorAlert.classList.remove('hidden');
    }

    function hideError() {
        errorAlert.classList.add('hidden');
    }
});
