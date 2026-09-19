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

  // Audio Player & Waveform Visualizer
  const audioElement = document.getElementById('audioElement');
  const btnPlayPause = document.getElementById('btnPlayPause');
  const playIcon = document.getElementById('playIcon');
  const pauseIcon = document.getElementById('pauseIcon');
  const waveformContainer = document.getElementById('waveformContainer');
  const waveformCanvas = document.getElementById('waveformCanvas');
  const waveformPlayhead = document.getElementById('waveformPlayhead');
  const waveformHoverLine = document.getElementById('waveformHoverLine');
  const waveformTooltip = document.getElementById('waveformTooltip');
  const waveformLoader = document.getElementById('waveformLoader');
  const waveformStatusBadge = document.getElementById('waveformStatusBadge');
  const playerCurrentTime = document.getElementById('playerCurrentTime');
  const playerTotalTime = document.getElementById('playerTotalTime');
  const playerVolume = document.getElementById('playerVolume');
  const btnMute = document.getElementById('btnMute');
  let waveformVisualizer = null;

  // Form & Tabs
  const metaForm = document.getElementById('metadataForm');
  const tabButtons = document.querySelectorAll('.tab-btn');
  const tabPanels = document.querySelectorAll('.tab-panel');
  const inputCustomFilename = document.getElementById('inputCustomFilename');
  const filenamePreview = document.getElementById('filenamePreview');
  const presetButtons = document.querySelectorAll('.preset-btn');
  const toastContainer = document.getElementById('toastContainer');
  const inputComment = document.getElementById('inputComment');

  // --- Canned Comments & Presets Elements ---
  const selectCannedComment = document.getElementById('selectCannedComment');
  const btnSaveCannedComment = document.getElementById('btnSaveCannedComment');
  const btnManageCannedComments = document.getElementById('btnManageCannedComments');
  const cannedCommentsModal = document.getElementById('cannedCommentsModal');
  const btnCloseCannedModal = document.getElementById('btnCloseCannedModal');
  const formAddPreset = document.getElementById('formAddPreset');
  const inputPresetLabel = document.getElementById('inputPresetLabel');
  const inputPresetText = document.getElementById('inputPresetText');
  const cannedPresetsList = document.getElementById('cannedPresetsList');
  const cannedPresetCount = document.getElementById('cannedPresetCount');
  const btnResetPresets = document.getElementById('btnResetPresets');

  // --- Version & Update Manager Elements ---
  const updateBadge = document.getElementById('updateBadge');
  const btnVersionModal = document.getElementById('btnVersionModal');
  const versionModal = document.getElementById('versionModal');
  const btnCloseVersionModal = document.getElementById('btnCloseVersionModal');
  const sysVersion = document.getElementById('sysVersion');
  const sysGitCommit = document.getElementById('sysGitCommit');
  const sysGitBranch = document.getElementById('sysGitBranch');
  const sysRuntimeMode = document.getElementById('sysRuntimeMode');
  const sysRepoLink = document.getElementById('sysRepoLink');
  const updateCheckedAt = document.getElementById('updateCheckedAt');
  const btnCheckUpdatesNow = document.getElementById('btnCheckUpdatesNow');
  const updateUpToDateCard = document.getElementById('updateUpToDateCard');
  const upToDateMsg = document.getElementById('upToDateMsg');
  const updateAvailableCard = document.getElementById('updateAvailableCard');
  const availableVersionTag = document.getElementById('availableVersionTag');
  const availableReleaseDate = document.getElementById('availableReleaseDate');
  const availableReleaseTitle = document.getElementById('availableReleaseTitle');
  const availableReleaseNotes = document.getElementById('availableReleaseNotes');
  const btnLaunchUpdater = document.getElementById('btnLaunchUpdater');

  // Terminal Modal Elements
  const updaterModal = document.getElementById('updaterModal');
  const terminalLogs = document.getElementById('terminalLogs');
  const terminalLogContainer = document.getElementById('terminalLogContainer');
  const terminalStatusBadge = document.getElementById('terminalStatusBadge');
  const terminalSpinner = document.getElementById('terminalSpinner');
  const terminalProgressText = document.getElementById('terminalProgressText');
  const terminalActions = document.getElementById('terminalActions');
  const btnReloadAfterUpdate = document.getElementById('btnReloadAfterUpdate');

  let activeUpdateData = null;

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

  let pendingArrayBuffer = null;

  async function handleFileUpload(file) {
    if (!file.name.toLowerCase().endsWith('.mp3')) {
      showToast('Please select a valid .mp3 audio file', 'error');
      return;
    }

    try {
      pendingArrayBuffer = await file.arrayBuffer();
    } catch (_) {
      pendingArrayBuffer = null;
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
    playerCurrentTime.textContent = '00:00';
    playerTotalTime.textContent = duration;
    pauseAudio();

    // Waveform Visualizer setup
    if (waveformVisualizer) {
      waveformVisualizer.reset();
      if (pendingArrayBuffer) {
        waveformVisualizer.loadFromBuffer(pendingArrayBuffer);
        pendingArrayBuffer = null;
      } else {
        fetch('/api/stream')
          .then(res => {
            if (!res.ok) throw new Error('Stream fetch failed');
            return res.arrayBuffer();
          })
          .then(buf => waveformVisualizer.loadFromBuffer(buf))
          .catch(err => console.warn('Stream buffer fetch error for waveform:', err));
      }
    }

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

  // --- Audio Player & Waveform Visualizer ---
  class WaveformVisualizer {
    constructor(options) {
      this.container = options.container;
      this.canvas = options.canvas;
      this.playhead = options.playhead;
      this.hoverLine = options.hoverLine;
      this.tooltip = options.tooltip;
      this.loader = options.loader;
      this.statusBadge = options.statusBadge;
      this.audio = options.audio;
      this.currentTimeEl = options.currentTimeEl;
      this.totalTimeEl = options.totalTimeEl;

      this.ctx = this.canvas ? this.canvas.getContext('2d') : null;
      this.peaks = null;
      this.audioBuffer = null;
      this.audioCtx = null;
      this.isDragging = false;
      this.duration = 0;
      this.currentProgress = 0;
      this.animFrameId = null;

      if (this.container && this.canvas && this.audio) {
        this.initEvents();
        this.setupResizeObserver();
      }
    }

    initEvents() {
      if (this.container) {
        // Hover guide and tooltip
        this.container.addEventListener('mousemove', (e) => this.handleMouseMove(e));
        this.container.addEventListener('mouseleave', () => this.handleMouseLeave());

        // Drag / Seek interactions with Pointer capture
        this.container.addEventListener('pointerdown', (e) => {
          if (!this.audio || !this.audio.src) return;
          this.isDragging = true;
          try {
            this.container.setPointerCapture(e.pointerId);
          } catch (_) {}
          this.seekFromPointer(e);
        });

        this.container.addEventListener('pointermove', (e) => {
          if (this.isDragging) {
            this.seekFromPointer(e);
          }
        });

        const stopDragging = (e) => {
          if (this.isDragging) {
            this.isDragging = false;
            try {
              this.container.releasePointerCapture(e.pointerId);
            } catch (_) {}
          }
        };

        this.container.addEventListener('pointerup', stopDragging);
        this.container.addEventListener('pointercancel', stopDragging);

        // Keyboard accessibility
        this.container.addEventListener('keydown', (e) => {
          if (!this.audio || !this.audio.src || !this.duration) return;
          if (e.key === 'ArrowLeft') {
            e.preventDefault();
            this.audio.currentTime = Math.max(0, this.audio.currentTime - 5);
            this.updateProgress();
          } else if (e.key === 'ArrowRight') {
            e.preventDefault();
            this.audio.currentTime = Math.min(this.duration, this.audio.currentTime + 5);
            this.updateProgress();
          } else if (e.key === 'Home') {
            e.preventDefault();
            this.audio.currentTime = 0;
            this.updateProgress();
          } else if (e.key === 'End') {
            e.preventDefault();
            this.audio.currentTime = this.duration;
            this.updateProgress();
          } else if (e.key === ' ' || e.key === 'Enter') {
            e.preventDefault();
            togglePlayPause();
          }
        });
      }

      if (this.audio) {
        // Audio element bindings
        this.audio.addEventListener('timeupdate', () => {
          if (!this.isDragging) {
            this.updateProgress();
          }
        });

        this.audio.addEventListener('play', () => {
          this.startProgressLoop();
        });

        this.audio.addEventListener('pause', () => {
          this.stopProgressLoop();
          this.updateProgress();
        });

        this.audio.addEventListener('ended', () => {
          this.stopProgressLoop();
          pauseAudio();
          this.currentProgress = 0;
          if (this.playhead) this.playhead.style.left = '0%';
          if (this.currentTimeEl) this.currentTimeEl.textContent = '00:00';
          this.render();
        });
      }
    }

    setupResizeObserver() {
      if (!this.container) return;
      if (window.ResizeObserver) {
        const ro = new ResizeObserver(() => {
          this.resizeAndRender();
        });
        ro.observe(this.container);
      } else {
        window.addEventListener('resize', () => this.resizeAndRender());
      }
    }

    startProgressLoop() {
      const loop = () => {
        if (this.audio && !this.audio.paused && !this.isDragging) {
          this.updateProgress();
          this.animFrameId = requestAnimationFrame(loop);
        }
      };
      cancelAnimationFrame(this.animFrameId);
      this.animFrameId = requestAnimationFrame(loop);
    }

    stopProgressLoop() {
      if (this.animFrameId) {
        cancelAnimationFrame(this.animFrameId);
        this.animFrameId = null;
      }
    }

    handleMouseMove(e) {
      if (!this.container) return;
      const dur = this.duration || (this.audio && !isNaN(this.audio.duration) ? this.audio.duration : 0);
      if (!dur) return;
      const rect = this.container.getBoundingClientRect();
      const x = Math.max(0, Math.min(e.clientX - rect.left, rect.width));
      const pct = rect.width > 0 ? x / rect.width : 0;
      const hoverTime = pct * dur;

      if (this.hoverLine) {
        this.hoverLine.style.opacity = '1';
        this.hoverLine.style.left = `${x}px`;
      }

      if (this.tooltip) {
        this.tooltip.style.opacity = '1';
        this.tooltip.textContent = formatTime(hoverTime);

        const tooltipWidth = this.tooltip.offsetWidth || 42;
        const halfWidth = tooltipWidth / 2;
        const clampedX = Math.max(halfWidth + 4, Math.min(rect.width - halfWidth - 4, x));
        this.tooltip.style.left = `${clampedX}px`;
      }
    }

    handleMouseLeave() {
      if (!this.isDragging) {
        if (this.hoverLine) this.hoverLine.style.opacity = '0';
        if (this.tooltip) this.tooltip.style.opacity = '0';
      }
    }

    seekFromPointer(e) {
      if (!this.container || !this.audio) return;
      const dur = this.duration || (!isNaN(this.audio.duration) ? this.audio.duration : 0);
      if (!dur || dur <= 0) return;
      const rect = this.container.getBoundingClientRect();
      const x = Math.max(0, Math.min(e.clientX - rect.left, rect.width));
      const pct = rect.width > 0 ? x / rect.width : 0;

      this.currentProgress = pct;
      this.audio.currentTime = pct * dur;
      this.updateProgress();

      if (this.hoverLine) {
        this.hoverLine.style.opacity = '1';
        this.hoverLine.style.left = `${x}px`;
      }
      if (this.tooltip) {
        this.tooltip.style.opacity = '1';
        this.tooltip.textContent = formatTime(this.audio.currentTime);
        const tooltipWidth = this.tooltip.offsetWidth || 42;
        const halfWidth = tooltipWidth / 2;
        const clampedX = Math.max(halfWidth + 4, Math.min(rect.width - halfWidth - 4, x));
        this.tooltip.style.left = `${clampedX}px`;
      }
    }

    updateProgress() {
      if (!this.audio) return;
      const dur = this.duration || (!isNaN(this.audio.duration) ? this.audio.duration : 0);
      if (isNaN(dur) || dur <= 0) return;
      this.duration = dur;
      this.currentProgress = Math.max(0, Math.min(1, this.audio.currentTime / dur));

      const pct = this.currentProgress * 100;
      if (this.playhead) {
        this.playhead.style.left = `${pct}%`;
      }
      if (this.container) {
        this.container.setAttribute('aria-valuenow', Math.round(pct));
      }

      if (this.currentTimeEl) {
        this.currentTimeEl.textContent = formatTime(this.audio.currentTime);
      }
      if (this.totalTimeEl && (!this.totalTimeEl.textContent || this.totalTimeEl.textContent === '00:00')) {
        this.totalTimeEl.textContent = formatTime(dur);
      }

      this.render();
    }

    async loadFromBuffer(arrayBuffer) {
      try {
        this.showLoader(true);
        if (this.statusBadge) {
          this.statusBadge.textContent = 'Rendering...';
        }

        const AudioCtxClass = window.AudioContext || window.webkitAudioContext;
        if (!this.audioCtx) {
          this.audioCtx = new AudioCtxClass();
        }

        const bufferCopy = arrayBuffer.slice(0);
        this.audioBuffer = await this.audioCtx.decodeAudioData(bufferCopy);
        this.duration = this.audioBuffer.duration;

        this.extractPeaks();
        this.resizeAndRender();
        this.showLoader(false);

        if (this.statusBadge) {
          this.statusBadge.textContent = 'Ready';
        }
      } catch (err) {
        console.warn('Waveform audio decoding error:', err);
        this.generateFallbackPeaks();
        this.resizeAndRender();
        this.showLoader(false);
        if (this.statusBadge) {
          this.statusBadge.textContent = 'Loaded';
        }
      }
    }

    showLoader(show) {
      if (!this.loader) return;
      if (show) {
        this.loader.classList.add('loading');
      } else {
        this.loader.classList.remove('loading');
      }
    }

    extractPeaks() {
      if (!this.audioBuffer) return;
      const channelL = this.audioBuffer.getChannelData(0);
      const hasStereo = this.audioBuffer.numberOfChannels > 1;
      const channelR = hasStereo ? this.audioBuffer.getChannelData(1) : null;
      const totalSamples = channelL.length;

      const barCount = 400;
      const blockSize = Math.floor(totalSamples / barCount);
      this.peaks = new Float32Array(barCount);

      for (let i = 0; i < barCount; i++) {
        const start = i * blockSize;
        const end = Math.min(start + blockSize, totalSamples);
        let sum = 0;
        let peak = 0;
        const step = Math.max(1, Math.floor((end - start) / 50));

        let count = 0;
        for (let j = start; j < end; j += step) {
          const valL = Math.abs(channelL[j]);
          const val = hasStereo ? (valL + Math.abs(channelR[j])) * 0.5 : valL;
          sum += val * val;
          if (val > peak) peak = val;
          count++;
        }

        const rms = count > 0 ? Math.sqrt(sum / count) : 0;
        const combined = (peak * 0.6) + (rms * 1.4);
        this.peaks[i] = Math.max(0.06, Math.min(1.0, Math.pow(combined, 0.75)));
      }
    }

    generateFallbackPeaks() {
      const barCount = 200;
      this.peaks = new Float32Array(barCount);
      for (let i = 0; i < barCount; i++) {
        this.peaks[i] = 0.15 + 0.7 * Math.abs(Math.sin(i * 0.08) * Math.cos(i * 0.03));
      }
    }

    resizeAndRender() {
      if (!this.canvas || !this.ctx || !this.container) return;
      const dpr = window.devicePixelRatio || 1;
      const rect = this.container.getBoundingClientRect();
      const cssWidth = Math.floor(rect.width) || 300;
      const cssHeight = Math.floor(rect.height) || 64;

      this.canvas.width = cssWidth * dpr;
      this.canvas.height = cssHeight * dpr;
      this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

      this.render();
    }

    render() {
      if (!this.canvas || !this.ctx || !this.container) return;
      if (!this.peaks || !this.peaks.length) return;
      const rect = this.container.getBoundingClientRect();
      const width = rect.width || 300;
      const height = rect.height || 64;

      this.ctx.clearRect(0, 0, width, height);

      const barWidth = 3;
      const barGap = 2;
      const step = barWidth + barGap;
      const numBars = Math.floor(width / step);
      if (numBars <= 0) return;

      const progressX = this.currentProgress * width;

      const playedGrad = this.ctx.createLinearGradient(0, 0, 0, height);
      playedGrad.addColorStop(0, '#38bdf8');
      playedGrad.addColorStop(1, '#a855f7');

      const unplayedGrad = this.ctx.createLinearGradient(0, 0, 0, height);
      unplayedGrad.addColorStop(0, 'rgba(148, 163, 184, 0.45)');
      unplayedGrad.addColorStop(1, 'rgba(71, 85, 105, 0.35)');

      const maxBarHeight = height - 10;
      const centerY = height / 2;

      for (let i = 0; i < numBars; i++) {
        const peakIdx = Math.floor((i / numBars) * this.peaks.length);
        const val = this.peaks[peakIdx] || 0.06;
        const barH = Math.max(3, val * maxBarHeight);
        const x = i * step + 1;
        const y = centerY - barH / 2;
        const radius = 1.5;

        const isPlayed = (x + barWidth) <= progressX;
        const isPartiallyPlayed = x < progressX && (x + barWidth) > progressX;

        if (isPartiallyPlayed) {
          this.drawRoundedRect(x, y, barWidth, barH, radius, unplayedGrad);
          this.ctx.save();
          this.ctx.beginPath();
          this.ctx.rect(0, 0, progressX, height);
          this.ctx.clip();
          this.drawRoundedRect(x, y, barWidth, barH, radius, playedGrad);
          this.ctx.restore();
        } else if (isPlayed) {
          this.drawRoundedRect(x, y, barWidth, barH, radius, playedGrad);
        } else {
          this.drawRoundedRect(x, y, barWidth, barH, radius, unplayedGrad);
        }
      }
    }

    drawRoundedRect(x, y, w, h, r, fillStyle) {
      if (!this.ctx) return;
      this.ctx.fillStyle = fillStyle;
      this.ctx.beginPath();
      if (this.ctx.roundRect) {
        this.ctx.roundRect(x, y, w, h, r);
      } else {
        this.ctx.rect(x, y, w, h);
      }
      this.ctx.fill();
    }

    reset() {
      this.stopProgressLoop();
      this.peaks = null;
      this.audioBuffer = null;
      this.currentProgress = 0;
      this.duration = 0;
      if (this.playhead) this.playhead.style.left = '0%';
      if (this.hoverLine) this.hoverLine.style.opacity = '0';
      if (this.tooltip) this.tooltip.style.opacity = '0';
      if (this.statusBadge) this.statusBadge.textContent = 'Ready';
      this.showLoader(false);
      if (this.ctx && this.canvas) {
        this.ctx.clearRect(0, 0, this.canvas.width, this.canvas.height);
      }
    }
  }

  waveformVisualizer = new WaveformVisualizer({
    container: waveformContainer,
    canvas: waveformCanvas,
    playhead: waveformPlayhead,
    hoverLine: waveformHoverLine,
    tooltip: waveformTooltip,
    loader: waveformLoader,
    statusBadge: waveformStatusBadge,
    audio: audioElement,
    currentTimeEl: playerCurrentTime,
    totalTimeEl: playerTotalTime
  });

  if (btnPlayPause) {
    btnPlayPause.addEventListener('click', togglePlayPause);
  }

  function togglePlayPause() {
    if (!audioElement || !audioElement.src) return;
    if (audioElement.paused) {
      audioElement.play().then(() => {
        if (playIcon) playIcon.classList.add('hidden');
        if (pauseIcon) pauseIcon.classList.remove('hidden');
      }).catch(err => {
        showToast('Playback error: ' + err.message, 'error');
      });
    } else {
      pauseAudio();
    }
  }

  function pauseAudio() {
    if (audioElement) audioElement.pause();
    if (playIcon) playIcon.classList.remove('hidden');
    if (pauseIcon) pauseIcon.classList.add('hidden');
  }

  if (playerVolume) {
    playerVolume.addEventListener('input', (e) => {
      if (audioElement) audioElement.volume = parseFloat(e.target.value);
    });
  }

  if (btnMute) {
    btnMute.addEventListener('click', () => {
      if (!audioElement) return;
      audioElement.muted = !audioElement.muted;
      btnMute.style.opacity = audioElement.muted ? '0.4' : '1';
    });
  }

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
      if (waveformVisualizer) {
        waveformVisualizer.reset();
      }
      audioElement.src = '';
      state.hasSession = false;
      editorSection.classList.add('hidden');
      uploadSection.classList.remove('hidden');
      fileInput.value = '';
    }
  });

  // =====================================================================
  // Version Details & In-App Web Updater Controller
  // =====================================================================

  async function loadSystemInfo() {
    try {
      const res = await fetch('/api/version');
      if (!res.ok) throw new Error('Version API error');
      const data = await res.json();

      if (data.version) {
        versionBadge.textContent = `v${data.version}`;
        sysVersion.textContent = `v${data.version}`;
      }
      if (data.git_commit) {
        sysGitCommit.textContent = data.git_commit;
      } else {
        sysGitCommit.textContent = 'Standalone';
      }
      if (data.git_branch) {
        sysGitBranch.textContent = data.git_branch;
      }
      if (data.is_systemd_service) {
        sysRuntimeMode.textContent = 'Systemd Service (Boot)';
      } else {
        sysRuntimeMode.textContent = 'Standalone / Local';
      }
      if (data.github_repo) {
        sysRepoLink.textContent = data.github_repo;
        sysRepoLink.href = data.github_repo_url || `https://github.com/${data.github_repo}`;
      }
    } catch (err) {
      console.warn('Could not load system info:', err);
      serverStatus.innerHTML = '<span class="status-dot" style="background:#f43f5e;box-shadow:0 0 8px #f43f5e"></span> Offline';
    }
  }

  async function checkForUpdates(force = false, silent = false) {
    const spinner = btnCheckUpdatesNow ? btnCheckUpdatesNow.querySelector('.spin-on-load') : null;
    if (spinner) spinner.classList.add('spinning');
    if (btnCheckUpdatesNow) btnCheckUpdatesNow.disabled = true;

    try {
      const url = force ? '/api/updates/check?force=true' : '/api/updates/check';
      const res = await fetch(url);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      activeUpdateData = data;

      if (data.checked_at) {
        updateCheckedAt.textContent = `Last checked: ${data.checked_at}`;
      }

      if (data.update_available) {
        // Show notification badge in navbar
        updateBadge.classList.remove('hidden');

        // Populate update modal card
        availableVersionTag.textContent = `v${data.latest_version}`;
        availableReleaseTitle.textContent = data.release_name || `Release v${data.latest_version}`;
        
        if (data.published_at) {
          const dateStr = new Date(data.published_at).toLocaleDateString(undefined, {
            year: 'numeric', month: 'short', day: 'numeric'
          });
          availableReleaseDate.textContent = `Published: ${dateStr}`;
        } else {
          availableReleaseDate.textContent = '';
        }

        // Render release notes safely
        availableReleaseNotes.textContent = data.release_notes || 'No release notes provided.';

        updateAvailableCard.classList.remove('hidden');
        updateUpToDateCard.classList.add('hidden');

        if (!silent) {
          showToast(`Update available: v${data.latest_version}!`, 'info', 4000);
        }
      } else {
        // Up to date
        updateBadge.classList.add('hidden');
        updateAvailableCard.classList.add('hidden');
        updateUpToDateCard.classList.remove('hidden');
        
        if (data.error) {
          upToDateMsg.textContent = data.error;
        } else {
          upToDateMsg.textContent = `You are running the latest version (v${data.current_version}).`;
        }

        if (!silent) {
          showToast('MP3MetaFix is up to date!', 'success', 3000);
        }
      }
    } catch (err) {
      console.warn('Update check failed:', err);
      if (!silent) {
        showToast('Could not check for updates. Check internet connection.', 'error', 3500);
      }
    } finally {
      if (spinner) spinner.classList.remove('spinning');
      if (btnCheckUpdatesNow) btnCheckUpdatesNow.disabled = false;
    }
  }

  function openVersionModal() {
    loadSystemInfo();
    checkForUpdates(false, true);
    if (versionModal) {
      versionModal.classList.remove('hidden');
    }
  }

  function closeVersionModal() {
    if (versionModal) {
      versionModal.classList.add('hidden');
    }
  }

  async function startInAppUpdate() {
    showToast('In-app update installation is currently disabled. Please run ./install.sh --update in the server terminal.', 'info', 5000);
  }

  // --- Canned Comments & Quick Presets Controller ---
  const CANNED_STORAGE_KEY = 'mp3metafix_canned_comments';
  const DEFAULT_CANNED_PRESETS = [
    { id: 'preset-suno-profile', label: 'Suno Profile', text: 'https://suno.com/@username' },
    { id: 'preset-suno-ai', label: 'Suno AI Tag', text: 'Generated with Suno AI' },
    { id: 'preset-rights', label: 'Rights Reserved', text: 'All Rights Reserved' },
    { id: 'preset-master', label: 'Mastering Note', text: 'Mastered for Streaming' }
  ];

  function getCannedPresets() {
    try {
      const stored = localStorage.getItem(CANNED_STORAGE_KEY);
      if (stored) {
        const parsed = JSON.parse(stored);
        if (Array.isArray(parsed) && parsed.length > 0) {
          return parsed;
        }
      }
    } catch (e) {
      console.warn('Failed to parse canned presets from localStorage:', e);
    }
    return [...DEFAULT_CANNED_PRESETS];
  }

  function saveCannedPresets(presets) {
    try {
      localStorage.setItem(CANNED_STORAGE_KEY, JSON.stringify(presets));
    } catch (e) {
      console.warn('Failed to save canned presets to localStorage:', e);
    }
  }

  function renderCannedCommentDropdown() {
    if (!selectCannedComment) return;
    const presets = getCannedPresets();

    selectCannedComment.innerHTML = '';
    const placeholderOption = document.createElement('option');
    placeholderOption.value = '';
    placeholderOption.disabled = true;
    placeholderOption.selected = true;
    placeholderOption.textContent = '⚡ Canned Presets...';
    selectCannedComment.appendChild(placeholderOption);

    presets.forEach(preset => {
      const opt = document.createElement('option');
      opt.value = preset.text;
      opt.textContent = `${preset.label}: ${preset.text}`;
      selectCannedComment.appendChild(opt);
    });
  }

  function applyCannedComment(text) {
    if (!inputComment) return;
    inputComment.value = text;
    inputComment.dispatchEvent(new Event('input', { bubbles: true }));
    inputComment.dispatchEvent(new Event('change', { bubbles: true }));
    showToast('Comment preset applied!', 'success', 2500);
  }

  function renderCannedPresetsModalList() {
    if (!cannedPresetsList) return;
    const presets = getCannedPresets();
    if (cannedPresetCount) {
      cannedPresetCount.textContent = presets.length;
    }

    cannedPresetsList.innerHTML = '';

    if (presets.length === 0) {
      const emptyCard = document.createElement('div');
      emptyCard.className = 'preset-empty-state';
      emptyCard.textContent = 'No presets saved yet. Add your first comment preset above!';
      cannedPresetsList.appendChild(emptyCard);
      return;
    }

    presets.forEach(preset => {
      const card = document.createElement('div');
      card.className = 'canned-preset-card';
      card.dataset.presetId = preset.id;

      const mainGroup = document.createElement('div');
      mainGroup.className = 'preset-card-main';

      const labelEl = document.createElement('span');
      labelEl.className = 'preset-card-label';
      labelEl.textContent = preset.label;

      const textEl = document.createElement('span');
      textEl.className = 'preset-card-text';
      textEl.textContent = preset.text;

      mainGroup.appendChild(labelEl);
      mainGroup.appendChild(textEl);

      const actionsGroup = document.createElement('div');
      actionsGroup.className = 'preset-card-actions';

      // Use Button
      const btnApply = document.createElement('button');
      btnApply.type = 'button';
      btnApply.className = 'btn btn-primary btn-xs';
      btnApply.title = 'Apply this preset to Comment field';
      btnApply.textContent = 'Use';
      btnApply.addEventListener('click', () => {
        applyCannedComment(preset.text);
        closeCannedModal();
      });

      // Edit Button
      const btnEdit = document.createElement('button');
      btnEdit.type = 'button';
      btnEdit.className = 'btn btn-secondary btn-xs';
      btnEdit.title = 'Edit preset';
      btnEdit.textContent = 'Edit';
      btnEdit.addEventListener('click', () => {
        showInlineEditPreset(card, preset);
      });

      // Delete Button
      const btnDelete = document.createElement('button');
      btnDelete.type = 'button';
      btnDelete.className = 'btn btn-danger btn-xs';
      btnDelete.title = 'Delete preset';
      btnDelete.textContent = '✕';
      btnDelete.addEventListener('click', () => {
        deleteCannedPreset(preset.id);
      });

      actionsGroup.appendChild(btnApply);
      actionsGroup.appendChild(btnEdit);
      actionsGroup.appendChild(btnDelete);

      card.appendChild(mainGroup);
      card.appendChild(actionsGroup);

      cannedPresetsList.appendChild(card);
    });
  }

  function showInlineEditPreset(cardElement, preset) {
    cardElement.innerHTML = '';

    const editContainer = document.createElement('div');
    editContainer.className = 'preset-inline-edit';

    const editLabelInput = document.createElement('input');
    editLabelInput.type = 'text';
    editLabelInput.className = 'form-input';
    editLabelInput.value = preset.label;
    editLabelInput.placeholder = 'Preset Label';
    editLabelInput.maxLength = 60;

    const editTextInput = document.createElement('input');
    editTextInput.type = 'text';
    editTextInput.className = 'form-input';
    editTextInput.value = preset.text;
    editTextInput.placeholder = 'Comment Text';
    editTextInput.maxLength = 255;

    const btnSaveEdit = document.createElement('button');
    btnSaveEdit.type = 'button';
    btnSaveEdit.className = 'btn btn-primary btn-xs';
    btnSaveEdit.textContent = 'Save';
    btnSaveEdit.addEventListener('click', () => {
      const newLabel = editLabelInput.value.trim();
      const newText = editTextInput.value.trim();
      if (!newLabel || !newText) {
        showToast('Label and Comment text cannot be empty', 'error');
        return;
      }
      updateCannedPreset(preset.id, newLabel, newText);
    });

    const btnCancelEdit = document.createElement('button');
    btnCancelEdit.type = 'button';
    btnCancelEdit.className = 'btn btn-secondary btn-xs';
    btnCancelEdit.textContent = 'Cancel';
    btnCancelEdit.addEventListener('click', () => {
      renderCannedPresetsModalList();
    });

    editContainer.appendChild(editLabelInput);
    editContainer.appendChild(editTextInput);
    editContainer.appendChild(btnSaveEdit);
    editContainer.appendChild(btnCancelEdit);

    cardElement.appendChild(editContainer);
    editLabelInput.focus();
  }

  function addCannedPreset(label, text) {
    const trimmedLabel = label.trim();
    const trimmedText = text.trim();
    if (!trimmedLabel || !trimmedText) {
      showToast('Please provide both a label and comment text', 'error');
      return;
    }

    const presets = getCannedPresets();
    presets.unshift({
      id: 'preset-' + Date.now(),
      label: trimmedLabel,
      text: trimmedText
    });

    saveCannedPresets(presets);
    renderCannedCommentDropdown();
    renderCannedPresetsModalList();
    showToast('New comment preset added!', 'success');
  }

  function updateCannedPreset(id, newLabel, newText) {
    const presets = getCannedPresets();
    const target = presets.find(p => p.id === id);
    if (target) {
      target.label = newLabel;
      target.text = newText;
      saveCannedPresets(presets);
      renderCannedCommentDropdown();
      renderCannedPresetsModalList();
      showToast('Preset updated!', 'success');
    }
  }

  function deleteCannedPreset(id) {
    let presets = getCannedPresets();
    presets = presets.filter(p => p.id !== id);
    saveCannedPresets(presets);
    renderCannedCommentDropdown();
    renderCannedPresetsModalList();
    showToast('Preset deleted', 'info');
  }

  function resetCannedPresetsToDefaults() {
    saveCannedPresets(DEFAULT_CANNED_PRESETS);
    renderCannedCommentDropdown();
    renderCannedPresetsModalList();
    showToast('Restored default presets', 'info');
  }

  function openCannedModal() {
    if (cannedCommentsModal) {
      renderCannedPresetsModalList();
      cannedCommentsModal.classList.remove('hidden');
    }
  }

  function closeCannedModal() {
    if (cannedCommentsModal) {
      cannedCommentsModal.classList.add('hidden');
    }
  }

  // Event Listeners for Canned Comments
  if (selectCannedComment) {
    selectCannedComment.addEventListener('change', (e) => {
      if (e.target.value) {
        applyCannedComment(e.target.value);
        e.target.selectedIndex = 0;
      }
    });
  }

  if (btnSaveCannedComment) {
    btnSaveCannedComment.addEventListener('click', () => {
      const currentComment = inputComment ? inputComment.value.trim() : '';
      if (!currentComment) {
        showToast('Please enter a comment before saving as a preset.', 'info');
        if (inputComment) inputComment.focus();
        return;
      }
      let defaultLabel = 'Custom Note';
      if (currentComment.includes('suno.com/')) {
        defaultLabel = 'Suno Profile';
      } else if (currentComment.length <= 25) {
        defaultLabel = currentComment;
      } else {
        defaultLabel = currentComment.substring(0, 22) + '...';
      }

      const userLabel = prompt('Enter a label for this preset:', defaultLabel);
      if (userLabel !== null) {
        addCannedPreset(userLabel.trim() || defaultLabel, currentComment);
      }
    });
  }

  if (btnManageCannedComments) {
    btnManageCannedComments.addEventListener('click', openCannedModal);
  }

  if (btnCloseCannedModal) {
    btnCloseCannedModal.addEventListener('click', closeCannedModal);
  }

  if (formAddPreset) {
    formAddPreset.addEventListener('submit', (e) => {
      e.preventDefault();
      const label = inputPresetLabel ? inputPresetLabel.value : '';
      const text = inputPresetText ? inputPresetText.value : '';
      if (label && text) {
        addCannedPreset(label, text);
        if (inputPresetLabel) inputPresetLabel.value = '';
        if (inputPresetText) inputPresetText.value = '';
      }
    });
  }

  if (btnResetPresets) {
    btnResetPresets.addEventListener('click', () => {
      if (confirm('Are you sure you want to restore default presets? Any custom presets will be reset.')) {
        resetCannedPresetsToDefaults();
      }
    });
  }

  // Event Listeners for Version & Updater
  if (versionBadge) {
    versionBadge.addEventListener('click', openVersionModal);
  }
  if (btnVersionModal) {
    btnVersionModal.addEventListener('click', openVersionModal);
  }
  if (updateBadge) {
    updateBadge.addEventListener('click', openVersionModal);
  }
  if (btnCloseVersionModal) {
    btnCloseVersionModal.addEventListener('click', closeVersionModal);
  }
  if (btnCheckUpdatesNow) {
    btnCheckUpdatesNow.addEventListener('click', () => checkForUpdates(true, false));
  }
  if (btnLaunchUpdater) {
    btnLaunchUpdater.addEventListener('click', startInAppUpdate);
  }

  // Close modals on escape key or backdrop click
  window.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
      if (versionModal && !versionModal.classList.contains('hidden')) {
        closeVersionModal();
      }
      if (cannedCommentsModal && !cannedCommentsModal.classList.contains('hidden')) {
        closeCannedModal();
      }
    }
  });

  if (versionModal) {
    versionModal.addEventListener('click', (e) => {
      if (e.target === versionModal) {
        closeVersionModal();
      }
    });
  }

  if (cannedCommentsModal) {
    cannedCommentsModal.addEventListener('click', (e) => {
      if (e.target === cannedCommentsModal) {
        closeCannedModal();
      }
    });
  }

  // Initial setup & silent background update check
  renderCannedCommentDropdown();
  loadSystemInfo();
  setTimeout(() => {
    checkForUpdates(false, true);
  }, 1500);
});
