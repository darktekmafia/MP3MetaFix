/**
 * MP3MetaFix - Frontend Application Controller
 */

document.addEventListener('DOMContentLoaded', () => {
  // --- State ---
  const state = {
    hasSession: false,
    originalFilename: '',
    targetFilename: '',
    hasArtwork: false,
    artworkRemoved: false,
    audioDuration: 0,
    metadata: {},
  };

  // --- DOM Elements ---
  const uploadSection = document.getElementById('uploadSection');
  const editorSection = document.getElementById('editorSection');
  const dropzone = document.getElementById('dropzone');
  const fileInput = document.getElementById('fileInput');
  const uploadProgressContainer = document.getElementById('uploadProgressContainer');
  const uploadProgressBar = document.getElementById('uploadProgressBar');
  const uploadPercent = document.getElementById('uploadPercent');
  const uploadStatusText = document.getElementById('uploadStatusText');

  // Toolbar & General
  const loadedFilename = document.getElementById('loadedFilename');
  const loadedAudioSpecs = document.getElementById('loadedAudioSpecs');
  const btnNewFile = document.getElementById('btnNewFile');
  const btnSave = document.getElementById('btnSave');
  const btnSaveDownload = document.getElementById('btnSaveDownload');
  const serverStatus = document.getElementById('serverStatus');
  const versionBadge = document.getElementById('versionBadge');

  // Cover Art
  const artworkDropzone = document.getElementById('artworkDropzone');
  const artworkInput = document.getElementById('artworkInput');
  const artworkImage = document.getElementById('artworkImage');
  const artworkPlaceholder = document.getElementById('artworkPlaceholder');
  const artworkStatusBadge = document.getElementById('artworkStatusBadge');
  const btnReplaceArt = document.getElementById('btnReplaceArt');
  const btnDownloadArt = document.getElementById('btnDownloadArt');
  const btnRemoveArt = document.getElementById('btnRemoveArt');

  // Audio Player
  const audioElement = document.getElementById('audioElement');
  const btnPlayPause = document.getElementById('btnPlayPause');
  const playIcon = document.getElementById('playIcon');
  const pauseIcon = document.getElementById('pauseIcon');
  const playerSeek = document.getElementById('playerSeek');
  const playerCurrentTime = document.getElementById('playerCurrentTime');
  const playerTotalTime = document.getElementById('playerTotalTime');
  const playerVolume = document.getElementById('playerVolume');
  const btnMute = document.getElementById('btnMute');

  // Form & Tabs
  const metaForm = document.getElementById('metadataForm');
  const tabButtons = document.querySelectorAll('.tab-btn');
  const tabPanels = document.querySelectorAll('.tab-panel');
  const inputCustomFilename = document.getElementById('inputCustomFilename');
  const filenamePreview = document.getElementById('filenamePreview');
  const presetButtons = document.querySelectorAll('.preset-btn');
  const toastContainer = document.getElementById('toastContainer');

  // --- Initialize Health/Version Check ---
  fetch('/api/version')
    .then(r => r.json())
    .then(data => {
      if (data.version) versionBadge.textContent = `v${data.version}`;
    })
    .catch(() => {
      serverStatus.innerHTML = '<span class="status-dot" style="background:#f43f5e;box-shadow:0 0 8px #f43f5e"></span> Offline';
    });

  // --- Toast Notifications ---
  function showToast(message, type = 'info', duration = 3500) {
    const toast = document.createElement('div');
    toast.className = `toast toast-${type}`;
    
    let iconSvg = '';
    if (type === 'success') {
      iconSvg = '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"></path><polyline points="22 4 12 14.01 9 11.01"></polyline></svg>';
    } else if (type === 'error') {
      iconSvg = '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"></circle><line x1="15" y1="9" x2="9" y2="15"></line><line x1="9" y1="9" x2="15" y2="15"></line></svg>';
    } else {
      iconSvg = '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"></circle><line x1="12" y1="16" x2="12" y2="12"></line><line x1="12" y1="8" x2="12.01" y2="8"></line></svg>';
    }

    toast.innerHTML = iconSvg;
    const msgSpan = document.createElement('span');
    msgSpan.textContent = String(message || '');
    toast.appendChild(msgSpan);
    toastContainer.appendChild(toast);

    setTimeout(() => {
      toast.style.opacity = '0';
      toast.style.transform = 'translateY(10px) scale(0.95)';
      setTimeout(() => toast.remove(), 250);
    }, duration);
  }

  // --- Upload Handling ---
  dropzone.addEventListener('click', () => fileInput.click());
  fileInput.addEventListener('change', (e) => {
    if (e.target.files && e.target.files[0]) {
      handleFileUpload(e.target.files[0]);
    }
  });

  // Drag and drop events for dropzone
  ['dragenter', 'dragover'].forEach(eventName => {
    dropzone.addEventListener(eventName, (e) => {
      e.preventDefault();
      e.stopPropagation();
      dropzone.classList.add('drag-over');
    });
  });

  ['dragleave', 'drop'].forEach(eventName => {
    dropzone.addEventListener(eventName, (e) => {
      e.preventDefault();
      e.stopPropagation();
      dropzone.classList.remove('drag-over');
    });
  });

  dropzone.addEventListener('drop', (e) => {
    const files = e.dataTransfer.files;
    if (files && files.length > 0) {
      handleFileUpload(files[0]);
    }
  });

  function handleFileUpload(file) {
    if (!file.name.toLowerCase().endsWith('.mp3')) {
      showToast('Please select a valid .mp3 audio file', 'error');
      return;
    }

    const formData = new FormData();
    formData.append('file', file);

    uploadProgressContainer.classList.remove('hidden');
    uploadProgressBar.style.width = '20%';
    uploadPercent.textContent = '20%';
    uploadStatusText.textContent = 'Uploading MP3 stream...';

    const xhr = new XMLHttpRequest();
    xhr.open('POST', '/api/upload', true);

    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable) {
        const pct = Math.round((e.loaded / e.total) * 90);
        uploadProgressBar.style.width = `${pct}%`;
        uploadPercent.textContent = `${pct}%`;
      }
    };

    xhr.onload = () => {
      if (xhr.status === 200) {
        uploadProgressBar.style.width = '100%';
        uploadPercent.textContent = '100%';
        uploadStatusText.textContent = 'Parsing complete!';
        
        try {
          const data = JSON.parse(xhr.responseText);
          setTimeout(() => {
            loadSession(data);
            uploadProgressContainer.classList.add('hidden');
            uploadProgressBar.style.width = '0%';
          }, 300);
        } catch (err) {
          showToast('Failed to parse server response', 'error');
          uploadProgressContainer.classList.add('hidden');
        }
      } else {
        uploadProgressContainer.classList.add('hidden');
        try {
          const errData = JSON.parse(xhr.responseText);
          showToast(errData.detail || 'Upload failed', 'error');
        } catch (_) {
          showToast(`Upload failed (HTTP ${xhr.status})`, 'error');
        }
      }
    };

    xhr.onerror = () => {
      uploadProgressContainer.classList.add('hidden');
      showToast('Network error during upload', 'error');
    };

    xhr.send(formData);
  }

  // --- Load Session Into Editor ---
  function loadSession(data) {
    state.hasSession = true;
    state.originalFilename = data.original_filename;
    state.targetFilename = data.original_filename;
    state.hasArtwork = data.artwork.has_artwork;
    state.artworkRemoved = false;
    state.metadata = data.metadata;

    // Header specs
    loadedFilename.textContent = data.original_filename;
    const duration = formatTime(data.audio_info.duration || 0);
    const bitrate = data.audio_info.bitrate_kbps ? `${data.audio_info.bitrate_kbps} kbps` : 'MP3';
    const sampleRate = data.audio_info.sample_rate_hz ? `${(data.audio_info.sample_rate_hz / 1000).toFixed(1)} kHz` : '';
    const channels = data.audio_info.channels === 2 ? 'Stereo' : (data.audio_info.channels === 1 ? 'Mono' : '');
    loadedAudioSpecs.textContent = [bitrate, sampleRate, channels, duration].filter(Boolean).join(' • ');

    // Fill Form fields
    const meta = data.metadata;
    document.getElementById('inputTitle').value = meta.title || '';
    document.getElementById('inputArtist').value = meta.artist || '';
    document.getElementById('inputAlbum').value = meta.album || '';
    document.getElementById('inputAlbumArtist').value = meta.album_artist || '';
    document.getElementById('inputGenre').value = meta.genre || '';
    document.getElementById('inputYear').value = meta.year || '';
    document.getElementById('inputTrackNumber').value = meta.track_number || '';
    document.getElementById('inputTotalTracks').value = meta.total_tracks || '';
    document.getElementById('inputDiscNumber').value = meta.disc_number || '';
    document.getElementById('inputTotalDiscs').value = meta.total_discs || '';
    document.getElementById('inputComposer').value = meta.composer || '';
    document.getElementById('inputBpm').value = meta.bpm || '';
    document.getElementById('inputComment').value = meta.comment || '';
    document.getElementById('inputLyrics').value = meta.lyrics || '';

    // Artwork UI
    if (data.artwork.has_artwork && data.artwork.preview_data_url) {
      setArtworkImage(data.artwork.preview_data_url, 'Embedded');
    } else {
      clearArtworkImage();
    }

    // Audio Player setup (clean endpoint using cookie session)
    audioElement.src = '/api/stream';
    audioElement.load();
    playerSeek.value = 0;
    playerCurrentTime.textContent = '00:00';
    playerTotalTime.textContent = duration;
    pauseAudio();

    // Filename pattern setup
    updateFilenamePreview();

    // Show Editor, hide dropzone
    uploadSection.classList.add('hidden');
    editorSection.classList.remove('hidden');
    showToast('MP3 loaded and parsed successfully', 'success');
  }

  // --- Artwork Management ---
  function setArtworkImage(dataUrl, badgeText = 'Custom') {
    artworkImage.src = dataUrl;
    artworkImage.classList.remove('hidden');
    artworkPlaceholder.classList.add('hidden');
    artworkStatusBadge.textContent = badgeText;
    artworkStatusBadge.style.display = 'inline-flex';
    btnDownloadArt.disabled = false;
    btnRemoveArt.disabled = false;
    state.hasArtwork = true;
    state.artworkRemoved = false;
  }

  function clearArtworkImage() {
    artworkImage.src = '';
    artworkImage.classList.add('hidden');
    artworkPlaceholder.classList.remove('hidden');
    artworkStatusBadge.style.display = 'none';
    btnDownloadArt.disabled = true;
    btnRemoveArt.disabled = true;
    state.hasArtwork = false;
  }

  btnReplaceArt.addEventListener('click', () => artworkInput.click());
  artworkDropzone.addEventListener('click', () => artworkInput.click());

  artworkInput.addEventListener('change', (e) => {
    if (e.target.files && e.target.files[0]) {
      uploadArtworkImage(e.target.files[0]);
    }
  });

  // Drag and drop image on artwork dropzone
  ['dragenter', 'dragover'].forEach(eventName => {
    artworkDropzone.addEventListener(eventName, (e) => {
      e.preventDefault();
      e.stopPropagation();
      artworkDropzone.classList.add('drag-over');
    });
  });

  ['dragleave', 'drop'].forEach(eventName => {
    artworkDropzone.addEventListener(eventName, (e) => {
      e.preventDefault();
      e.stopPropagation();
      artworkDropzone.classList.remove('drag-over');
    });
  });

  artworkDropzone.addEventListener('drop', (e) => {
    const files = e.dataTransfer.files;
    if (files && files.length > 0) {
      uploadArtworkImage(files[0]);
    }
  });

  function uploadArtworkImage(file) {
    if (!state.hasSession) return;
    if (!file.type.startsWith('image/')) {
      showToast('Please upload an image file (JPEG, PNG, WebP)', 'error');
      return;
    }

    const formData = new FormData();
    formData.append('image', file);

    fetch('/api/artwork', {
      method: 'POST',
      body: formData,
    })
      .then(r => r.json().then(data => ({ status: r.status, body: data })))
      .then(({ status, body }) => {
        if (status === 200 && body.preview_data_url) {
          setArtworkImage(body.preview_data_url, 'Staged Art');
          showToast('Album art updated (will save on submit)', 'success');
        } else {
          showToast(body.detail || 'Failed to update artwork', 'error');
        }
      })
      .catch(() => showToast('Network error uploading artwork', 'error'));
  }

  btnDownloadArt.addEventListener('click', () => {
    if (!state.hasSession || !state.hasArtwork) return;
    window.open('/api/artwork', '_blank');
  });

  btnRemoveArt.addEventListener('click', () => {
    if (!state.hasSession) return;
    state.artworkRemoved = true;
    clearArtworkImage();
    fetch('/api/artwork', { method: 'DELETE' })
      .then(() => showToast('Cover art marked for removal', 'info'));
  });

  // --- Audio Player Controls ---
  btnPlayPause.addEventListener('click', togglePlayPause);

  function togglePlayPause() {
    if (!audioElement.src) return;
    if (audioElement.paused) {
      audioElement.play().then(() => {
        playIcon.classList.add('hidden');
        pauseIcon.classList.remove('hidden');
      }).catch(err => {
        showToast('Playback error: ' + err.message, 'error');
      });
    } else {
      pauseAudio();
    }
  }

  function pauseAudio() {
    audioElement.pause();
    playIcon.classList.remove('hidden');
    pauseIcon.classList.add('hidden');
  }

  audioElement.addEventListener('timeupdate', () => {
    if (!isNaN(audioElement.duration) && audioElement.duration > 0) {
      const pct = (audioElement.currentTime / audioElement.duration) * 100;
      playerSeek.value = pct;
      playerCurrentTime.textContent = formatTime(audioElement.currentTime);
      playerTotalTime.textContent = formatTime(audioElement.duration);
    }
  });

  audioElement.addEventListener('ended', () => {
    pauseAudio();
    playerSeek.value = 0;
  });

  playerSeek.addEventListener('input', (e) => {
    if (!isNaN(audioElement.duration)) {
      const targetTime = (e.target.value / 100) * audioElement.duration;
      audioElement.currentTime = targetTime;
    }
  });

  playerVolume.addEventListener('input', (e) => {
    audioElement.volume = parseFloat(e.target.value);
  });

  btnMute.addEventListener('click', () => {
    audioElement.muted = !audioElement.muted;
    btnMute.style.opacity = audioElement.muted ? '0.4' : '1';
  });

  function formatTime(seconds) {
    if (isNaN(seconds) || seconds < 0) return '00:00';
    const m = Math.floor(seconds / 60);
    const s = Math.floor(seconds % 60);
    return `${m.toString().padStart(2, '0')}:${s.toString().padStart(2, '0')}`;
  }

  // --- Tabs Navigation ---
  tabButtons.forEach(btn => {
    btn.addEventListener('click', () => {
      tabButtons.forEach(b => {
        b.classList.remove('active');
        b.setAttribute('aria-selected', 'false');
      });
      tabPanels.forEach(p => p.classList.remove('active'));

      btn.classList.add('active');
      btn.setAttribute('aria-selected', 'true');
      const targetId = btn.getAttribute('data-tab');
      document.getElementById(targetId).classList.add('active');
    });
  });

  // --- Filename Pattern Generator ---
  function getFormData() {
    return {
      title: document.getElementById('inputTitle').value.trim(),
      artist: document.getElementById('inputArtist').value.trim(),
      album: document.getElementById('inputAlbum').value.trim(),
      album_artist: document.getElementById('inputAlbumArtist').value.trim(),
      genre: document.getElementById('inputGenre').value.trim(),
      year: document.getElementById('inputYear').value.trim(),
      track_number: document.getElementById('inputTrackNumber').value.trim(),
      total_tracks: document.getElementById('inputTotalTracks').value.trim(),
      disc_number: document.getElementById('inputDiscNumber').value.trim(),
      total_discs: document.getElementById('inputTotalDiscs').value.trim(),
      composer: document.getElementById('inputComposer').value.trim(),
      bpm: document.getElementById('inputBpm').value.trim(),
      comment: document.getElementById('inputComment').value.trim(),
      lyrics: document.getElementById('inputLyrics').value.trim(),
      custom_filename: inputCustomFilename.value.trim(),
      remove_artwork: state.artworkRemoved,
    };
  }

  function updateFilenamePreview() {
    const data = getFormData();
    let pattern = inputCustomFilename.value.trim();

    if (!pattern) {
      if (data.artist && data.title) {
        pattern = '%artist% - %title%';
      } else {
        pattern = state.originalFilename || 'track.mp3';
      }
    }

    const padTrack = data.track_number ? data.track_number.padStart(2, '0') : '01';
    let result = pattern
      .replace(/%artist%/gi, data.artist || 'Unknown Artist')
      .replace(/%title%/gi, data.title || 'Untitled Track')
      .replace(/%album%/gi, data.album || 'Unknown Album')
      .replace(/%track%/gi, padTrack)
      .replace(/%year%/gi, data.year || '2026')
      .replace(/%genre%/gi, data.genre || 'Audio');

    if (!result.toLowerCase().endsWith('.mp3')) {
      result += '.mp3';
    }

    filenamePreview.textContent = result;
  }

  // Listen to input changes for live preview
  metaForm.querySelectorAll('input').forEach(input => {
    input.addEventListener('input', updateFilenamePreview);
  });

  presetButtons.forEach(btn => {
    btn.addEventListener('click', () => {
      inputCustomFilename.value = btn.getAttribute('data-preset');
      updateFilenamePreview();
      showToast(`Preset applied: ${btn.textContent}`, 'info', 2000);
    });
  });

  // --- Save & Download Workflow ---
  async function saveMetadata(downloadAfter = false) {
    if (!state.hasSession) return;

    const payload = getFormData();
    payload.custom_filename = filenamePreview.textContent;

    btnSave.disabled = true;
    btnSaveDownload.disabled = true;
    showToast('Saving tags and metadata to MP3...', 'info', 2000);

    try {
      const response = await fetch('/api/save', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });

      const res = await response.json();
      if (response.ok && res.success) {
        showToast('MP3 metadata saved successfully!', 'success');
        loadedFilename.textContent = res.target_filename;
        state.targetFilename = res.target_filename;

        if (downloadAfter) {
          await triggerDownload(res.target_filename);
        }
      } else {
        showToast(res.detail || 'Failed to save metadata', 'error');
      }
    } catch (e) {
      showToast('Error connecting to server', 'error');
    } finally {
      btnSave.disabled = false;
      btnSaveDownload.disabled = false;
    }
  }

  async function triggerDownload(filename) {
    if (!state.hasSession) return;
    const cleanFilename = filename || state.targetFilename || state.originalFilename || 'track.mp3';
    const downloadUrl = `/api/download/${encodeURIComponent(cleanFilename)}`;

    // Try modern File System Access API (showSaveFilePicker) so browser prompts for exact save location
    if (window.showSaveFilePicker) {
      try {
        const handle = await window.showSaveFilePicker({
          suggestedName: cleanFilename,
          types: [{
            description: 'MP3 Audio File',
            accept: { 'audio/mpeg': ['.mp3'] }
          }]
        });
        showToast('Writing MP3 file to selected folder...', 'info', 2000);
        const res = await fetch(downloadUrl);
        if (!res.ok) throw new Error(`Download failed with status ${res.status}`);
        const blob = await res.blob();
        const writable = await handle.createWritable();
        await writable.write(blob);
        await writable.close();
        showToast(`Saved successfully: ${cleanFilename}`, 'success', 4000);
        return;
      } catch (err) {
        if (err.name === 'AbortError') {
          showToast('Save cancelled by user', 'info', 2000);
          return;
        }
        console.warn('File System Access API fallback:', err);
      }
    }

    // Standard browser download fallback
    const a = document.createElement('a');
    a.href = downloadUrl;
    a.download = cleanFilename;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    showToast(`Downloading "${cleanFilename}" to your browser Downloads folder`, 'success', 4000);
  }

  btnSave.addEventListener('click', (e) => {
    e.preventDefault();
    saveMetadata(false);
  });

  btnSaveDownload.addEventListener('click', (e) => {
    e.preventDefault();
    saveMetadata(true);
  });

  // Keyboard shortcut: Ctrl+S / Cmd+S
  window.addEventListener('keydown', (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 's') {
      e.preventDefault();
      if (!editorSection.classList.contains('hidden')) {
        saveMetadata(true);
      }
    }
  });

  // Discard & New File
  btnNewFile.addEventListener('click', () => {
    if (confirm('Discard current session and upload a new MP3?')) {
      if (state.hasSession) {
        fetch('/api/session', { method: 'DELETE' }).catch(() => {});
      }
      pauseAudio();
      audioElement.src = '';
      state.hasSession = false;
      editorSection.classList.add('hidden');
      uploadSection.classList.remove('hidden');
      fileInput.value = '';
    }
  });
});
