/**
 * MP3MetaProjects Studio Client Subsystem
 * Handles Track Workspaces (Takes, Stems, Lyrics) & Album / EP Release Sequencing.
 */

(function () {
  'use strict';

  // --- State ---
  const state = {
    currentView: 'tracks', // 'tracks' | 'albums' | 'editor'
    tracks: [],
    albums: [],
    trackFilter: 'all',
    albumFilter: 'all',
    trackSearch: '',
    albumSearch: '',
    quota: null,
    activeAlbum: null,
    pickTrackEditingEntryId: null,
    pickTrackTakesLoaded: false,
    pickTrackLoadSeq: 0,
    pickTrackModalSessionId: 0,
    pendingRefreshPreview: null,
    loadedProjectTake: null,
    expandedTrackIds: new Set(),
    isUploadingTake: false,
  };

  // --- DOM Elements ---
  const dom = {};

  function initDom() {
    // Views
    dom.viewTracks = document.getElementById('viewTracks');
    dom.viewAlbums = document.getElementById('viewAlbums');
    dom.viewStems = document.getElementById('viewStems');
    dom.viewEditor = document.getElementById('viewEditor');

    // Sidebar Nav
    dom.navProjectsTracks = document.getElementById('navProjectsTracks');
    dom.navProjectsAlbums = document.getElementById('navProjectsAlbums');
    dom.navProjectsStems = document.getElementById('navProjectsStems');
    dom.navProjectsEditor = document.getElementById('navProjectsEditor');
    dom.btnNavTracks = document.getElementById('btnNavTracks');
    dom.btnNavAlbums = document.getElementById('btnNavAlbums');
    dom.btnNavStems = document.getElementById('btnNavStems');
    dom.btnNavEditor = document.getElementById('btnNavEditor');
    dom.navTracksCount = document.getElementById('navTracksCount');
    dom.navAlbumsCount = document.getElementById('navAlbumsCount');

    // Stems Studio Controls
    dom.stemsTrackSelect = document.getElementById('stemsTrackSelect');
    dom.stemsTakeSelect = document.getElementById('stemsTakeSelect');
    dom.btnToggleBatchUpload = document.getElementById('btnToggleBatchUpload');
    dom.btnDownloadStemPack = document.getElementById('btnDownloadStemPack');
    dom.stemsBatchUploadSection = document.getElementById('stemsBatchUploadSection');
    dom.stemsBatchDropzone = document.getElementById('stemsBatchDropzone');
    dom.stemsBatchFileInput = document.getElementById('stemsBatchFileInput');
    dom.stemsStagingContainer = document.getElementById('stemsStagingContainer');
    dom.stemsStagedCount = document.getElementById('stemsStagedCount');
    dom.btnClearStagedStems = document.getElementById('btnClearStagedStems');
    dom.btnUploadAllStagedStems = document.getElementById('btnUploadAllStagedStems');
    dom.stemsUploadProgressBarContainer = document.getElementById('stemsUploadProgressBarContainer');
    dom.stemsUploadProgressBar = document.getElementById('stemsUploadProgressBar');
    dom.stemsStagingList = document.getElementById('stemsStagingList');
    dom.stemsMixerSection = document.getElementById('stemsMixerSection');
    dom.btnMasterStemPlay = document.getElementById('btnMasterStemPlay');
    dom.stemsMasterTimeDisplay = document.getElementById('stemsMasterTimeDisplay');
    dom.stemsMasterSeek = document.getElementById('stemsMasterSeek');
    dom.stemsMasterVolume = document.getElementById('stemsMasterVolume');
    dom.btnResetStemMixer = document.getElementById('btnResetStemMixer');
    dom.stemsChannelsList = document.getElementById('stemsChannelsList');
    dom.stemsEmptyState = document.getElementById('stemsEmptyState');

    // Quota
    dom.projectsQuotaText = document.getElementById('projectsQuotaText');
    dom.projectsQuotaBar = document.getElementById('projectsQuotaBar');

    // Track Studio Controls
    dom.tracksSearchInput = document.getElementById('tracksSearchInput');
    dom.tracksFilterPills = document.querySelectorAll('.storage-filter-pill');
    dom.tracksGridContainer = document.getElementById('tracksGridContainer');
    dom.tracksEmptyState = document.getElementById('tracksEmptyState');
    dom.btnRefreshTracks = document.getElementById('btnRefreshTracks');
    dom.btnNewTrack = document.getElementById('btnNewTrack');
    dom.btnEmptyNewTrack = document.getElementById('btnEmptyNewTrack');

    // Album Studio Controls
    dom.albumsSearchInput = document.getElementById('albumsSearchInput');
    dom.albumsFilterPills = document.querySelectorAll('.album-filter-pill');
    dom.albumsGridContainer = document.getElementById('albumsGridContainer');
    dom.albumsEmptyState = document.getElementById('albumsEmptyState');
    dom.btnRefreshAlbums = document.getElementById('btnRefreshAlbums');
    dom.btnNewAlbum = document.getElementById('btnNewAlbum');
    dom.btnEmptyNewAlbum = document.getElementById('btnEmptyNewAlbum');

    // Modals
    dom.modalNewTrack = document.getElementById('modalNewTrack');
    dom.formNewTrack = document.getElementById('formNewTrack');
    dom.modalUploadTake = document.getElementById('modalUploadTake');
    dom.formUploadTake = document.getElementById('formUploadTake');
    dom.uploadTakeTrackId = document.getElementById('uploadTakeTrackId');
    dom.uploadTakeTitleDisplay = document.getElementById('uploadTakeTitleDisplay');
    dom.modalUploadStem = document.getElementById('modalUploadStem');
    dom.formUploadStem = document.getElementById('formUploadStem');
    dom.uploadStemTrackId = document.getElementById('uploadStemTrackId');
    dom.uploadStemTakeId = document.getElementById('uploadStemTakeId');
    dom.uploadStemTakeDisplay = document.getElementById('uploadStemTakeDisplay');
    dom.uploadStemRole = document.getElementById('uploadStemRole');
    dom.uploadStemFileInput = document.getElementById('uploadStemFileInput');

    // Save to Project Modal
    dom.modalSaveToProject = document.getElementById('modalSaveToProject');
    dom.formSaveToProject = document.getElementById('formSaveToProject');
    dom.selectSaveTargetTrack = document.getElementById('selectSaveTargetTrack');
    dom.radioSaveUpdateTake = document.getElementById('radioSaveUpdateTake');
    dom.labelRadioSaveUpdateTake = document.getElementById('labelRadioSaveUpdateTake');
    dom.radioSaveNewTrack = document.getElementById('radioSaveNewTrack');
    dom.radioSaveExistingTrack = document.getElementById('radioSaveExistingTrack');
    dom.saveProjectUpdateGroup = document.getElementById('saveProjectUpdateGroup');
    dom.saveProjectUpdateNoticeText = document.getElementById('saveProjectUpdateNoticeText');
    dom.saveProjectNewTrackFields = document.getElementById('saveProjectNewTrackFields');
    dom.btnSaveToProject = document.getElementById('btnSaveToProject');

    // Album Modals
    dom.modalNewAlbum = document.getElementById('modalNewAlbum');
    dom.formNewAlbum = document.getElementById('formNewAlbum');
    dom.modalAlbumDetail = document.getElementById('modalAlbumDetail');
    dom.albumDetailId = document.getElementById('albumDetailId');
    dom.albumDetailRevisionPill = document.getElementById('albumDetailRevisionPill');
    dom.albumDetailFinalizedBadge = document.getElementById('albumDetailFinalizedBadge');
    dom.albumRevisionContainer = document.getElementById('albumRevisionContainer');
    dom.albumRevisionList = document.getElementById('albumRevisionList');
    dom.albumDetailArtwork = document.getElementById('albumDetailArtwork');
    dom.albumDetailPlaceholder = document.getElementById('albumDetailPlaceholder');
    dom.inputAlbumCoverFile = document.getElementById('inputAlbumCoverFile');
    dom.editAlbumTitle = document.getElementById('editAlbumTitle');
    dom.editAlbumArtist = document.getElementById('editAlbumArtist');
    dom.editAlbumYear = document.getElementById('editAlbumYear');
    dom.editAlbumGenre = document.getElementById('editAlbumGenre');
    dom.editAlbumCatalog = document.getElementById('editAlbumCatalog');
    dom.albumTrackCountDisplay = document.getElementById('albumTrackCountDisplay');
    dom.albumSequencerTbody = document.getElementById('albumSequencerTbody');
    dom.btnAddTrackToSequencer = document.getElementById('btnAddTrackToSequencer');
    dom.btnFinalizeAlbum = document.getElementById('btnFinalizeAlbum');
    dom.btnSaveAlbumDetails = document.getElementById('btnSaveAlbumDetails');

    dom.modalPickTrackForAlbum = document.getElementById('modalPickTrackForAlbum');
    dom.modalPickTrackTitle = document.getElementById('modalPickTrackTitle');
    dom.selectPickTrack = document.getElementById('selectPickTrack');
    dom.pickTrackTakesGroup = document.getElementById('pickTrackTakesGroup');
    dom.pickTrackTakesList = document.getElementById('pickTrackTakesList');
    dom.btnConfirmAddTrackToAlbum = document.getElementById('btnConfirmAddTrackToAlbum');

    dom.modalRefreshAlbumEntry = document.getElementById('modalRefreshAlbumEntry');
    dom.refreshEntrySubtitle = document.getElementById('refreshEntrySubtitle');
    dom.refreshStatusNotice = document.getElementById('refreshStatusNotice');
    dom.refreshAlbumCopySummary = document.getElementById('refreshAlbumCopySummary');
    dom.refreshAlbumCopyAudio = document.getElementById('refreshAlbumCopyAudio');
    dom.refreshSourceTakeSummary = document.getElementById('refreshSourceTakeSummary');
    dom.refreshSourceTakeAudio = document.getElementById('refreshSourceTakeAudio');
    dom.refreshChangesContainer = document.getElementById('refreshChangesContainer');
    dom.refreshMetadataChangesList = document.getElementById('refreshMetadataChangesList');
    dom.refreshLyricsDiffContainer = document.getElementById('refreshLyricsDiffContainer');
    dom.refreshLyricsDiffPre = document.getElementById('refreshLyricsDiffPre');
    dom.btnConfirmRefreshAlbumEntry = document.getElementById('btnConfirmRefreshAlbumEntry');
  }

  // --- View Switcher ---
  function switchStudioView(viewName, optTrackId, optTakeId) {
    state.currentView = viewName;

    if (viewName === 'editor') {
      if (dom.viewTracks) dom.viewTracks.classList.add('hidden');
      if (dom.viewAlbums) dom.viewAlbums.classList.add('hidden');
      if (dom.viewStems) dom.viewStems.classList.add('hidden');
      if (dom.viewEditor) dom.viewEditor.classList.remove('hidden');
      if (dom.navProjectsTracks) dom.navProjectsTracks.classList.remove('active');
      if (dom.navProjectsAlbums) dom.navProjectsAlbums.classList.remove('active');
      if (dom.navProjectsStems) dom.navProjectsStems.classList.remove('active');
      if (dom.navProjectsEditor) dom.navProjectsEditor.classList.add('active');
    } else if (viewName === 'albums') {
      if (dom.viewTracks) dom.viewTracks.classList.add('hidden');
      if (dom.viewEditor) dom.viewEditor.classList.add('hidden');
      if (dom.viewStems) dom.viewStems.classList.add('hidden');
      if (dom.viewAlbums) dom.viewAlbums.classList.remove('hidden');
      if (dom.navProjectsTracks) dom.navProjectsTracks.classList.remove('active');
      if (dom.navProjectsEditor) dom.navProjectsEditor.classList.remove('active');
      if (dom.navProjectsStems) dom.navProjectsStems.classList.remove('active');
      if (dom.navProjectsAlbums) dom.navProjectsAlbums.classList.add('active');
      fetchAlbums();
    } else if (viewName === 'stems') {
      if (dom.viewTracks) dom.viewTracks.classList.add('hidden');
      if (dom.viewAlbums) dom.viewAlbums.classList.add('hidden');
      if (dom.viewEditor) dom.viewEditor.classList.add('hidden');
      if (dom.viewStems) dom.viewStems.classList.remove('hidden');
      if (dom.navProjectsTracks) dom.navProjectsTracks.classList.remove('active');
      if (dom.navProjectsAlbums) dom.navProjectsAlbums.classList.remove('active');
      if (dom.navProjectsEditor) dom.navProjectsEditor.classList.remove('active');
      if (dom.navProjectsStems) dom.navProjectsStems.classList.add('active');
      initStemsManagerView(optTrackId, optTakeId);
    } else {
      if (dom.viewAlbums) dom.viewAlbums.classList.add('hidden');
      if (dom.viewEditor) dom.viewEditor.classList.add('hidden');
      if (dom.viewStems) dom.viewStems.classList.add('hidden');
      if (dom.viewTracks) dom.viewTracks.classList.remove('hidden');
      if (dom.navProjectsAlbums) dom.navProjectsAlbums.classList.remove('active');
      if (dom.navProjectsEditor) dom.navProjectsEditor.classList.remove('active');
      if (dom.navProjectsStems) dom.navProjectsStems.classList.remove('active');
      if (dom.navProjectsTracks) dom.navProjectsTracks.classList.add('active');
      fetchTracks();
    }
  }

  // --- Toast Helper ---
  function showToast(msg, type = 'info', duration = 3000) {
    if (typeof window.showToast === 'function') {
      window.showToast(msg, type, duration);
      return;
    }
    const container = document.getElementById('toastContainer');
    if (!container) return;
    const toast = document.createElement('div');
    toast.className = `toast toast-${type}`;
    const span = document.createElement('span');
    span.textContent = msg;
    toast.appendChild(span);
    container.appendChild(toast);
    setTimeout(() => {
      toast.style.opacity = '0';
      toast.style.transform = 'translateY(10px) scale(0.95)';
      setTimeout(() => toast.remove(), 250);
    }, duration);
  }

  // --- API Fetchers ---
  async function fetchQuota() {
    try {
      const res = await fetch('/api/storage/quota');
      if (res.ok) {
        state.quota = await res.json();
        renderQuota();
      }
    } catch (e) {
      console.warn('Could not fetch storage quota', e);
    }
  }

  function renderQuota() {
    if (!state.quota || !dom.projectsQuotaText || !dom.projectsQuotaBar) return;
    const usedBytes = state.quota.used_bytes || 0;
    const limitBytes = state.quota.max_quota_bytes || state.quota.limit_bytes || 0;
    const usedMb = state.quota.used_mb !== undefined ? state.quota.used_mb.toFixed(1) : (usedBytes / (1024 * 1024)).toFixed(1);
    const limitMb = state.quota.max_quota_mb !== undefined ? state.quota.max_quota_mb.toFixed(0) : (limitBytes / (1024 * 1024)).toFixed(0);
    dom.projectsQuotaText.textContent = `${usedMb} MB / ${limitMb} MB`;
    
    const pct = Math.min(state.quota.used_percent !== undefined ? state.quota.used_percent : (state.quota.percent_used || 0), 100);
    dom.projectsQuotaBar.style.width = `${pct}%`;
    dom.projectsQuotaBar.className = 'quota-bar-fill';
    if (pct > 90) {
      dom.projectsQuotaBar.classList.add('quota-danger');
    } else if (pct > 75) {
      dom.projectsQuotaBar.classList.add('quota-warning');
    }
  }

  async function fetchTracks() {
    try {
      const res = await fetch('/api/projects/tracks');
      if (res.ok) {
        state.tracks = await res.json();
        if (dom.navTracksCount) dom.navTracksCount.textContent = state.tracks.length;
        renderTracksList();
      }
    } catch (e) {
      showToast('Error loading track workspaces', 'error');
    }
  }

  async function fetchAlbums() {
    try {
      const res = await fetch('/api/projects/albums');
      if (res.ok) {
        state.albums = await res.json();
        if (dom.navAlbumsCount) dom.navAlbumsCount.textContent = state.albums.length;
        renderAlbumsList();
      }
    } catch (e) {
      showToast('Error loading albums', 'error');
    }
  }

  // --- Render Tracks View ---
  function renderTracksList() {
    if (!dom.tracksGridContainer) return;
    dom.tracksGridContainer.innerHTML = '';

    let filtered = state.tracks;
    if (state.trackFilter !== 'all') {
      filtered = filtered.filter(t => t.status === state.trackFilter);
    }
    if (state.trackSearch) {
      const q = state.trackSearch.toLowerCase();
      filtered = filtered.filter(t =>
        (t.title && t.title.toLowerCase().includes(q)) ||
        (t.artist && t.artist.toLowerCase().includes(q)) ||
        (t.musical_key && t.musical_key.toLowerCase().includes(q)) ||
        (t.notes && t.notes.toLowerCase().includes(q))
      );
    }

    if (filtered.length === 0) {
      if (dom.tracksEmptyState) dom.tracksEmptyState.classList.remove('hidden');
      return;
    }

    if (dom.tracksEmptyState) dom.tracksEmptyState.classList.add('hidden');

    filtered.forEach(track => {
      const card = createTrackCard(track);
      dom.tracksGridContainer.appendChild(card);
    });
  }

  function createTrackCard(track) {
    const card = document.createElement('div');
    card.className = 'track-workspace-card';
    card.dataset.trackId = track.id;

    // Header Row
    const headerRow = document.createElement('div');
    headerRow.className = 'track-card-header';

    const infoStack = document.createElement('div');
    infoStack.className = 'track-info-stack';

    const titleRow = document.createElement('div');
    titleRow.className = 'track-title-row';

    const titleEl = document.createElement('h3');
    titleEl.className = 'track-card-title';
    titleEl.textContent = track.title || 'Untitled Track';
    titleRow.appendChild(titleEl);

    const revBadge = document.createElement('span');
    revBadge.className = 'revision-pill';
    revBadge.textContent = `Rev ${track.revision || 1}`;
    titleRow.appendChild(revBadge);

    const statusBadge = document.createElement('span');
    statusBadge.className = `status-pill status-${track.status || 'draft'}`;
    statusBadge.textContent = (track.status || 'draft').replace('_', ' ').toUpperCase();
    titleRow.appendChild(statusBadge);

    infoStack.appendChild(titleRow);

    const metaDetails = document.createElement('div');
    metaDetails.className = 'track-meta-details';

    if (track.artist) {
      const artistSpan = document.createElement('span');
      artistSpan.className = 'meta-pill';
      artistSpan.textContent = `👤 ${track.artist}`;
      metaDetails.appendChild(artistSpan);
    }

    if (track.bpm) {
      const bpmSpan = document.createElement('span');
      bpmSpan.className = 'meta-pill';
      bpmSpan.textContent = `⚡ ${track.bpm} BPM`;
      metaDetails.appendChild(bpmSpan);
    }

    if (track.musical_key) {
      const keySpan = document.createElement('span');
      keySpan.className = 'meta-pill';
      keySpan.textContent = `🎵 ${track.musical_key}`;
      metaDetails.appendChild(keySpan);
    }

    const takesSpan = document.createElement('span');
    takesSpan.className = 'meta-pill meta-pill-takes';
    takesSpan.textContent = `🗂️ ${track.takes_count || 0} Take${(track.takes_count || 0) === 1 ? '' : 's'}`;
    metaDetails.appendChild(takesSpan);

    infoStack.appendChild(metaDetails);
    headerRow.appendChild(infoStack);

    // Actions
    const headerActions = document.createElement('div');
    headerActions.className = 'track-card-actions';

    const btnAddTake = document.createElement('button');
    btnAddTake.className = 'btn btn-secondary btn-sm';
    btnAddTake.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="14" height="14"><line x1="12" y1="5" x2="12" y2="19"></line><line x1="5" y1="12" x2="19" y2="12"></line></svg><span>Add Take</span>';
    btnAddTake.addEventListener('click', () => openUploadTakeModal(track.id, track.title));
    headerActions.appendChild(btnAddTake);

    const btnExpand = document.createElement('button');
    btnExpand.className = 'btn btn-secondary btn-sm btn-icon-only btn-toggle-takes';
    btnExpand.title = 'View Takes & Stems';
    btnExpand.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="14" height="14"><polyline points="6 9 12 15 18 9"></polyline></svg>';
    headerActions.appendChild(btnExpand);

    const btnDelete = document.createElement('button');
    btnDelete.className = 'btn btn-secondary btn-sm btn-icon-only btn-delete-track';
    btnDelete.title = 'Delete Track Workspace';
    btnDelete.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="14" height="14"><polyline points="3 6 5 6 21 6"></polyline><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path></svg>';
    btnDelete.addEventListener('click', () => confirmDeleteTrack(track.id, track.title));
    headerActions.appendChild(btnDelete);

    headerRow.appendChild(headerActions);
    card.appendChild(headerRow);

    if (track.notes) {
      const notesEl = document.createElement('div');
      notesEl.className = 'track-card-notes';
      notesEl.textContent = `📝 ${track.notes}`;
      card.appendChild(notesEl);
    }

    // Takes Drawer
    const takesDrawer = document.createElement('div');
    takesDrawer.className = 'takes-drawer hidden';
    card.appendChild(takesDrawer);

    btnExpand.addEventListener('click', async () => {
      const isHidden = takesDrawer.classList.contains('hidden');
      if (isHidden) {
        state.expandedTrackIds.add(track.id);
        takesDrawer.classList.remove('hidden');
        btnExpand.classList.add('expanded');
        takesDrawer.innerHTML = '<div class="takes-loading-spinner">Loading takes & stems...</div>';
        await loadTakesIntoDrawer(track.id, takesDrawer);
      } else {
        state.expandedTrackIds.delete(track.id);
        takesDrawer.classList.add('hidden');
        btnExpand.classList.remove('expanded');
      }
    });

    if (state.expandedTrackIds.has(track.id)) {
      takesDrawer.classList.remove('hidden');
      btnExpand.classList.add('expanded');
      takesDrawer.innerHTML = '<div class="takes-loading-spinner">Loading takes & stems...</div>';
      loadTakesIntoDrawer(track.id, takesDrawer);
    }

    return card;
  }

  async function loadTakesIntoDrawer(trackId, drawerEl) {
    try {
      const res = await fetch(`/api/projects/tracks/${trackId}`);
      if (!res.ok) {
        drawerEl.innerHTML = '<div class="takes-error">Failed to load takes.</div>';
        return;
      }
      const data = await res.json();
      drawerEl.innerHTML = '';

      const takes = data.takes || [];
      if (takes.length === 0) {
        const emptyTakes = document.createElement('div');
        emptyTakes.className = 'takes-empty-notice';
        emptyTakes.textContent = 'No audio takes uploaded yet. Click "Add Take" above to upload your first take.';
        drawerEl.appendChild(emptyTakes);
        return;
      }

      const takesList = document.createElement('div');
      takesList.className = 'takes-list-grid';

      takes.forEach(take => {
        const takeRow = createTakeRow(data.track, take);
        takesList.appendChild(takeRow);
      });

      drawerEl.appendChild(takesList);
    } catch (e) {
      drawerEl.innerHTML = '<div class="takes-error">Network error loading takes.</div>';
    }
  }

  function createTakeRow(track, take) {
    const row = document.createElement('div');
    row.className = `take-item-card ${take.is_master ? 'take-is-master' : ''}`;
    row.dataset.takeId = take.id;

    const topBar = document.createElement('div');
    topBar.className = 'take-top-bar';

    const titleGroup = document.createElement('div');
    titleGroup.className = 'take-title-group';

    const labelEl = document.createElement('span');
    labelEl.className = 'take-label';
    labelEl.textContent = take.label || 'Take';
    titleGroup.appendChild(labelEl);

    const takeRevBadge = document.createElement('span');
    takeRevBadge.className = 'revision-pill';
    takeRevBadge.textContent = `Rev ${take.revision || 1}`;
    titleGroup.appendChild(takeRevBadge);

    if (take.is_master) {
      const masterBadge = document.createElement('span');
      masterBadge.className = 'master-badge';
      masterBadge.textContent = '★ MASTER';
      titleGroup.appendChild(masterBadge);
    }

    const fmtPill = document.createElement('span');
    fmtPill.className = 'format-pill';
    fmtPill.textContent = (take.format || 'mp3').toUpperCase();
    titleGroup.appendChild(fmtPill);

    const sizePill = document.createElement('span');
    sizePill.className = 'size-pill';
    sizePill.textContent = `${((take.size_bytes || 0) / (1024 * 1024)).toFixed(1)} MB`;
    titleGroup.appendChild(sizePill);

    topBar.appendChild(titleGroup);

    // Take Actions
    const actions = document.createElement('div');
    actions.className = 'take-actions';

    // Open Editor
    const btnOpenEditor = document.createElement('button');
    btnOpenEditor.className = 'btn btn-primary btn-sm';
    btnOpenEditor.title = 'Open in MP3MetaFix Editor';
    btnOpenEditor.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="13" height="13"><polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"></polygon></svg><span>Open Editor</span>';
    btnOpenEditor.addEventListener('click', () => loadTakeIntoSession(track.id, take.id));
    actions.appendChild(btnOpenEditor);

    // Add Stem
    const btnAddStem = document.createElement('button');
    btnAddStem.className = 'btn btn-secondary btn-sm';
    btnAddStem.title = 'Add separated stem (Vocals, Inst, Bass, Drums)';
    btnAddStem.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="13" height="13"><line x1="12" y1="5" x2="12" y2="19"></line><line x1="5" y1="12" x2="19" y2="12"></line></svg><span>+ Stem</span>';
    btnAddStem.addEventListener('click', () => openUploadStemModal(track.id, take.id, take.label));
    actions.appendChild(btnAddStem);

    // Set as Master (if not master)
    if (!take.is_master) {
      const btnMakeMaster = document.createElement('button');
      btnMakeMaster.className = 'btn btn-secondary btn-sm';
      btnMakeMaster.textContent = '★ Set Master';
      btnMakeMaster.addEventListener('click', () => setTakeMaster(track.id, take.id));
      actions.appendChild(btnMakeMaster);
    }

    // Download Audio
    const btnDownload = document.createElement('a');
    btnDownload.className = 'btn btn-secondary btn-sm btn-icon-only';
    btnDownload.title = 'Download Audio';
    btnDownload.href = `/api/projects/tracks/${track.id}/takes/${take.id}/download`;
    btnDownload.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="13" height="13"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"></path><polyline points="7 10 12 15 17 10"></polyline><line x1="12" y1="15" x2="12" y2="3"></line></svg>';
    actions.appendChild(btnDownload);

    // Delete Take
    const btnDeleteTake = document.createElement('button');
    btnDeleteTake.className = 'btn btn-secondary btn-sm btn-icon-only';
    btnDeleteTake.title = 'Delete Take';
    btnDeleteTake.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="13" height="13"><polyline points="3 6 5 6 21 6"></polyline><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path></svg>';
    btnDeleteTake.addEventListener('click', () => confirmDeleteTake(track.id, take.id, take.label));
    actions.appendChild(btnDeleteTake);

    topBar.appendChild(actions);
    row.appendChild(topBar);

    if (take.prompt || take.notes) {
      const notesContainer = document.createElement('div');
      notesContainer.className = 'take-notes-container';

      if (take.prompt) {
        const promptEl = document.createElement('div');
        promptEl.className = 'take-prompt-text';
        promptEl.textContent = `✨ Prompt: ${take.prompt}`;
        notesContainer.appendChild(promptEl);
      }

      if (take.notes) {
        const notesEl = document.createElement('div');
        notesEl.className = 'take-revision-notes';
        notesEl.textContent = `📋 Revision Notes: ${take.notes}`;
        notesContainer.appendChild(notesEl);
      }

      row.appendChild(notesContainer);
    }

    // Stems Rack
    const stems = take.stems ? (Array.isArray(take.stems) ? take.stems : Object.values(take.stems)) : [];
    if (stems.length > 0) {
      const stemsRack = document.createElement('div');
      stemsRack.className = 'stems-rack';

      const stemsHeading = document.createElement('div');
      stemsHeading.className = 'stems-rack-heading';
      stemsHeading.style.display = 'flex';
      stemsHeading.style.alignItems = 'center';
      stemsHeading.style.justifyContent = 'space-between';

      const headingText = document.createElement('span');
      headingText.textContent = `Separated Stems (${stems.length}):`;
      stemsHeading.appendChild(headingText);

      const btnOpenInStems = document.createElement('button');
      btnOpenInStems.type = 'button';
      btnOpenInStems.className = 'btn btn-secondary btn-xs';
      btnOpenInStems.style.fontSize = '0.75rem';
      btnOpenInStems.style.padding = '2px 8px';
      btnOpenInStems.textContent = '🎛 Open in Stems Studio';
      btnOpenInStems.addEventListener('click', (e) => {
        e.stopPropagation();
        switchStudioView('stems', track.id, take.id);
      });
      stemsHeading.appendChild(btnOpenInStems);
      stemsRack.appendChild(stemsHeading);

      const stemsList = document.createElement('div');
      stemsList.className = 'stems-badges-list';

      stems.forEach(stem => {
        const stemBadge = document.createElement('div');
        stemBadge.className = 'stem-pill-badge';

        const roleSpan = document.createElement('span');
        roleSpan.className = 'stem-role-label';
        roleSpan.textContent = (stem.role || 'other').toUpperCase();
        stemBadge.appendChild(roleSpan);

        const nameSpan = document.createElement('span');
        nameSpan.className = 'stem-name-label';
        nameSpan.textContent = stem.filename;
        stemBadge.appendChild(nameSpan);

        // Download Stem Link
        const dlBtn = document.createElement('a');
        dlBtn.className = 'stem-delete-btn';
        dlBtn.title = 'Download Stem';
        dlBtn.href = `/api/projects/tracks/${track.id}/takes/${take.id}/stems/${stem.id}/download`;
        dlBtn.download = stem.filename;
        dlBtn.style.textDecoration = 'none';
        dlBtn.style.color = 'inherit';
        dlBtn.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="11" height="11"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"></path><polyline points="7 10 12 15 17 10"></polyline><line x1="12" y1="15" x2="12" y2="3"></line></svg>';
        dlBtn.addEventListener('click', (e) => e.stopPropagation());
        stemBadge.appendChild(dlBtn);

        const delBtn = document.createElement('button');
        delBtn.className = 'stem-delete-btn';
        delBtn.title = 'Delete Stem';
        delBtn.innerHTML = '×';
        delBtn.addEventListener('click', (e) => {
          e.stopPropagation();
          deleteStem(track.id, take.id, stem.id);
        });
        stemBadge.appendChild(delBtn);

        stemsList.appendChild(stemBadge);
      });

      stemsRack.appendChild(stemsList);
      row.appendChild(stemsRack);
    }

    return row;
  }

  // --- Render Albums View ---
  function renderAlbumsList() {
    if (!dom.albumsGridContainer) return;
    dom.albumsGridContainer.innerHTML = '';

    let filtered = state.albums;
    if (state.albumFilter !== 'all') {
      filtered = filtered.filter(a => a.status === state.albumFilter);
    }
    if (state.albumSearch) {
      const q = state.albumSearch.toLowerCase();
      filtered = filtered.filter(a =>
        (a.title && a.title.toLowerCase().includes(q)) ||
        (a.album_artist && a.album_artist.toLowerCase().includes(q)) ||
        (a.genre && a.genre.toLowerCase().includes(q)) ||
        (a.catalog_number && a.catalog_number.toLowerCase().includes(q))
      );
    }

    if (filtered.length === 0) {
      if (dom.albumsEmptyState) dom.albumsEmptyState.classList.remove('hidden');
      return;
    }

    if (dom.albumsEmptyState) dom.albumsEmptyState.classList.add('hidden');

    filtered.forEach(album => {
      const card = createAlbumCard(album);
      dom.albumsGridContainer.appendChild(card);
    });
  }

  function createAlbumCard(album) {
    const card = document.createElement('div');
    card.className = 'track-workspace-card album-card-item';
    card.dataset.albumId = album.id;

    const headerRow = document.createElement('div');
    headerRow.className = 'track-card-header';

    const infoStack = document.createElement('div');
    infoStack.className = 'track-info-stack';

    const titleRow = document.createElement('div');
    titleRow.className = 'track-title-row';

    const titleEl = document.createElement('h3');
    titleEl.className = 'track-card-title';
    titleEl.textContent = album.title || 'Untitled Album';
    titleRow.appendChild(titleEl);

    const revBadge = document.createElement('span');
    revBadge.className = 'revision-pill';
    revBadge.textContent = `Rev ${album.revision || 1}`;
    titleRow.appendChild(revBadge);

    const statusBadge = document.createElement('span');
    statusBadge.className = `status-pill status-${album.status || 'draft'}`;
    statusBadge.textContent = (album.status || 'draft').replace('_', ' ').toUpperCase();
    titleRow.appendChild(statusBadge);

    infoStack.appendChild(titleRow);

    const metaDetails = document.createElement('div');
    metaDetails.className = 'track-meta-details';

    if (album.album_artist) {
      const artistSpan = document.createElement('span');
      artistSpan.className = 'meta-pill';
      artistSpan.textContent = `👤 ${album.album_artist}`;
      metaDetails.appendChild(artistSpan);
    }

    if (album.year) {
      const yearSpan = document.createElement('span');
      yearSpan.className = 'meta-pill';
      yearSpan.textContent = `📅 ${album.year}`;
      metaDetails.appendChild(yearSpan);
    }

    if (album.genre) {
      const genreSpan = document.createElement('span');
      genreSpan.className = 'meta-pill';
      genreSpan.textContent = `🏷️ ${album.genre}`;
      metaDetails.appendChild(genreSpan);
    }

    const count = (album.tracks || album.track_ids || []).length;
    const countSpan = document.createElement('span');
    countSpan.className = 'meta-pill meta-pill-takes';
    countSpan.textContent = `🎵 ${count} Track${count === 1 ? '' : 's'}`;
    metaDetails.appendChild(countSpan);

    infoStack.appendChild(metaDetails);
    headerRow.appendChild(infoStack);

    // Header Actions
    const headerActions = document.createElement('div');
    headerActions.className = 'track-card-actions';

    const btnInspect = document.createElement('button');
    btnInspect.className = 'btn btn-primary btn-sm';
    btnInspect.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="14" height="14"><rect x="3" y="3" width="18" height="18" rx="2" ry="2"></rect><circle cx="12" cy="12" r="5"></circle></svg><span>Sequencer & Credits</span>';
    btnInspect.addEventListener('click', () => openAlbumDetailModal(album.id));
    headerActions.appendChild(btnInspect);

    const isFinalized = !!(album.is_finalized || album.status === 'completed');
    if (!isFinalized) {
      const btnDelete = document.createElement('button');
      btnDelete.className = 'btn btn-secondary btn-sm btn-icon-only btn-delete-track';
      btnDelete.title = 'Delete Album';
      btnDelete.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="14" height="14"><polyline points="3 6 5 6 21 6"></polyline><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path></svg>';
      btnDelete.addEventListener('click', () => confirmDeleteAlbum(album.id, album.title, album.revision || 1));
      headerActions.appendChild(btnDelete);
    }

    headerRow.appendChild(headerActions);
    card.appendChild(headerRow);

    if (album.credits) {
      const creditsEl = document.createElement('div');
      creditsEl.className = 'track-card-notes';
      creditsEl.textContent = `📝 ${album.credits}`;
      card.appendChild(creditsEl);
    }

    return card;
  }

  function formatDurationSeconds(sec) {
    const val = Number(sec);
    if (!val || val <= 0) return '';
    const mins = Math.floor(val / 60);
    const secs = Math.floor(val % 60);
    return `${mins}:${secs.toString().padStart(2, '0')}`;
  }

  function applyAlbumModalLockState(album) {
    const isFinalized = !!(album && (album.is_finalized || album.status === 'completed'));
    if (dom.albumDetailFinalizedBadge) {
      dom.albumDetailFinalizedBadge.classList.toggle('hidden', !isFinalized);
    }
    [
      dom.editAlbumTitle,
      dom.editAlbumArtist,
      dom.editAlbumYear,
      dom.editAlbumGenre,
      dom.editAlbumCatalog,
      dom.inputAlbumCoverFile,
      dom.btnAddTrackToSequencer,
      dom.btnFinalizeAlbum,
      dom.btnSaveAlbumDetails,
    ].forEach(el => {
      if (el) el.disabled = isFinalized;
    });
  }

  function renderAlbumRevisionHistory(album) {
    if (!dom.albumRevisionContainer || !dom.albumRevisionList) return;
    dom.albumRevisionList.innerHTML = '';
    const revs = Array.isArray(album.revisions) ? album.revisions : [];
    if (revs.length > 0) {
      dom.albumRevisionContainer.classList.remove('hidden');
      [...revs].reverse().forEach(r => {
        const item = document.createElement('div');
        item.className = 'revision-history-item';

        const badge = document.createElement('span');
        badge.className = 'revision-pill';
        badge.textContent = `Rev ${r.revision}`;
        item.appendChild(badge);

        const summary = document.createElement('span');
        summary.className = 'revision-history-summary';
        summary.textContent = r.change_summary || 'Updated album';
        item.appendChild(summary);

        const dateSpan = document.createElement('span');
        dateSpan.className = 'revision-history-date';
        if (r.timestamp) {
          const d = new Date(r.timestamp * 1000);
          dateSpan.textContent = d.toLocaleDateString() + ' ' + d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
        }
        item.appendChild(dateSpan);

        dom.albumRevisionList.appendChild(item);
      });
    } else {
      dom.albumRevisionContainer.classList.add('hidden');
    }
  }

  // --- Album Detail / Sequencer Modal ---
  async function openAlbumDetailModal(albumId) {
    closeRefreshModal();
    try {
      const res = await fetch(`/api/projects/albums/${albumId}`);
      if (!res.ok) {
        showToast('Failed to load album details', 'error');
        return;
      }
      const album = await res.json();
      state.activeAlbum = album;

      if (!Array.isArray(state.activeAlbum.tracks)) {
        if (Array.isArray(state.activeAlbum.track_ids)) {
          state.activeAlbum.tracks = state.activeAlbum.track_ids.map((tid, i) => ({
            track_number: i + 1,
            disc_number: 1,
            track_id: tid,
            take_id: null,
            custom_title: null,
            is_placeholder: true,
          }));
        } else {
          state.activeAlbum.tracks = [];
        }
      }

      dom.albumDetailId.value = album.id;
      if (dom.albumDetailRevisionPill) {
        dom.albumDetailRevisionPill.textContent = `Rev ${album.revision || 1}`;
      }
      dom.editAlbumTitle.value = album.title || '';
      dom.editAlbumArtist.value = album.album_artist || '';
      dom.editAlbumYear.value = album.year || '';
      dom.editAlbumGenre.value = album.genre || '';
      dom.editAlbumCatalog.value = album.catalog_number || '';

      if (album.has_cover) {
        dom.albumDetailArtwork.src = `/api/projects/albums/${album.id}/artwork?t=${Date.now()}`;
        dom.albumDetailArtwork.classList.remove('hidden');
        dom.albumDetailPlaceholder.classList.add('hidden');
      } else {
        dom.albumDetailArtwork.src = '';
        dom.albumDetailArtwork.classList.add('hidden');
        dom.albumDetailPlaceholder.classList.remove('hidden');
      }

      applyAlbumModalLockState(album);
      renderAlbumRevisionHistory(album);
      renderSequencerTable(state.activeAlbum);

      if (dom.modalAlbumDetail) dom.modalAlbumDetail.classList.remove('hidden');
    } catch (e) {
      showToast('Network error loading album', 'error');
    }
  }

  function renderSequencerTable(album) {
    if (!dom.albumSequencerTbody) return;
    dom.albumSequencerTbody.innerHTML = '';

    const isFinalized = !!(album && (album.is_finalized || album.status === 'completed'));
    const tracks = album.tracks || [];
    if (dom.albumTrackCountDisplay) dom.albumTrackCountDisplay.textContent = tracks.length;

    if (tracks.length === 0) {
      const emptyTr = document.createElement('tr');
      const emptyTd = document.createElement('td');
      emptyTd.colSpan = 5;
      emptyTd.className = 'text-center text-muted py-3';
      emptyTd.textContent = 'No tracks sequenced yet. Click "+ Add Track from Workspace" above.';
      emptyTr.appendChild(emptyTd);
      dom.albumSequencerTbody.appendChild(emptyTr);
      return;
    }

    tracks.forEach((entry, index) => {
      const trackId = typeof entry === 'string' ? entry : entry.track_id;
      const track = state.tracks.find(t => t.id === trackId);
      const tr = document.createElement('tr');

      const tdNum = document.createElement('td');
      tdNum.className = 'font-mono text-dim';
      tdNum.textContent = String(entry.track_number || (index + 1)).padStart(2, '0');
      tr.appendChild(tdNum);

      const tdTitle = document.createElement('td');
      const titleDiv = document.createElement('div');
      titleDiv.className = 'font-bold';
      titleDiv.textContent =
        entry.custom_title ||
        (entry.snapshot && entry.snapshot.source_track_title) ||
        (track ? track.title : `[Track ${trackId.slice(0, 8)}]`);
      tdTitle.appendChild(titleDiv);

      const takePill = document.createElement('div');
      takePill.style.fontSize = '0.75rem';
      takePill.style.marginTop = '0.2rem';
      if (entry.is_placeholder || !entry.take_id) {
        takePill.style.color = '#fbbf24';
        takePill.textContent = '⚠ Placeholder — No Take Selected';
      } else if (entry.snapshot) {
        const durText = formatDurationSeconds(entry.snapshot.duration_seconds);
        const fmtText = (entry.snapshot.format || 'mp3').toUpperCase();
        const revText = entry.snapshot.source_take_revision || 1;
        takePill.style.opacity = '0.85';
        takePill.textContent = `🎵 ${entry.snapshot.source_take_label} • ${fmtText}${durText ? ' (' + durText + ')' : ''} • Rev ${revText}${entry.has_owned_audio ? ' • Owned Copy' : ''}`;
      } else {
        takePill.style.opacity = '0.75';
        takePill.textContent = `🎵 Take ${entry.take_id.slice(0, 10)}`;
      }
      tdTitle.appendChild(takePill);
      tr.appendChild(tdTitle);

      const tdArtist = document.createElement('td');
      tdArtist.className = 'text-muted';
      tdArtist.textContent =
        (track && track.artist) ||
        (entry.snapshot && entry.snapshot.source_track_artist) ||
        '—';
      tr.appendChild(tdArtist);

      const tdDisc = document.createElement('td');
      tdDisc.className = 'font-mono text-dim';
      tdDisc.textContent = String(entry.disc_number || 1);
      tr.appendChild(tdDisc);

      const tdActions = document.createElement('td');
      const actionGroup = document.createElement('div');
      actionGroup.className = 'sequencer-action-btns';
      actionGroup.style.flexWrap = 'wrap';

      if (!isFinalized) {
        const btnChangeTake = document.createElement('button');
        btnChangeTake.type = 'button';
        btnChangeTake.className = 'btn btn-secondary btn-xs';
        btnChangeTake.title = entry.is_placeholder ? 'Assign Take' : 'Change Selected Take';
        btnChangeTake.textContent = entry.is_placeholder ? 'Select Take' : 'Take';
        btnChangeTake.addEventListener('click', () => openPickTrackModal(entry));
        actionGroup.appendChild(btnChangeTake);

        if (!entry.is_placeholder && entry.take_id) {
          const btnRefresh = document.createElement('button');
          btnRefresh.type = 'button';
          btnRefresh.className = 'btn btn-secondary btn-xs';
          btnRefresh.title = 'Compare & Refresh from Source Take';
          btnRefresh.textContent = '↻';
          btnRefresh.addEventListener('click', () => openRefreshEntryModal(album.id, entry.id));
          actionGroup.appendChild(btnRefresh);
        }

        if (index > 0) {
          const btnUp = document.createElement('button');
          btnUp.type = 'button';
          btnUp.className = 'btn btn-ghost btn-xs';
          btnUp.title = 'Move Up';
          btnUp.textContent = '▲';
          btnUp.addEventListener('click', () => moveAlbumTrack(index, -1));
          actionGroup.appendChild(btnUp);
        }

        if (index < tracks.length - 1) {
          const btnDown = document.createElement('button');
          btnDown.type = 'button';
          btnDown.className = 'btn btn-ghost btn-xs';
          btnDown.title = 'Move Down';
          btnDown.textContent = '▼';
          btnDown.addEventListener('click', () => moveAlbumTrack(index, 1));
          actionGroup.appendChild(btnDown);
        }

        const btnRemove = document.createElement('button');
        btnRemove.type = 'button';
        btnRemove.className = 'btn btn-ghost btn-xs text-danger';
        btnRemove.title = 'Remove from Album';
        btnRemove.textContent = '✕';
        btnRemove.addEventListener('click', () => removeAlbumTrack(entry));
        actionGroup.appendChild(btnRemove);
      }

      if (entry.has_owned_audio && entry.id) {
        const dlBtn = document.createElement('a');
        dlBtn.className = 'btn btn-ghost btn-xs';
        dlBtn.title = 'Download Album Copy';
        dlBtn.href = `/api/projects/albums/${album.id}/entries/${entry.id}/download`;
        dlBtn.textContent = '⬇';
        actionGroup.appendChild(dlBtn);
      }

      tdActions.appendChild(actionGroup);
      tr.appendChild(tdActions);

      dom.albumSequencerTbody.appendChild(tr);
    });
  }

  function moveAlbumTrack(currentIndex, offset) {
    if (!state.activeAlbum || !state.activeAlbum.tracks) return;
    if (state.activeAlbum.is_finalized || state.activeAlbum.status === 'completed') return;
    const tracks = [...state.activeAlbum.tracks];
    const targetIndex = currentIndex + offset;
    if (targetIndex < 0 || targetIndex >= tracks.length) return;

    const temp = tracks[currentIndex];
    tracks[currentIndex] = tracks[targetIndex];
    tracks[targetIndex] = temp;

    tracks.forEach((t, i) => {
      t.track_number = i + 1;
    });

    state.activeAlbum.tracks = tracks;
    state.activeAlbum.track_ids = tracks.map(t => t.track_id);
    renderSequencerTable(state.activeAlbum);
  }

  async function removeAlbumTrack(entry) {
    if (!state.activeAlbum || !entry || !entry.id) return;
    if (state.activeAlbum.is_finalized || state.activeAlbum.status === 'completed') return;
    if (
      !confirm(
        'Remove this entry from the Album?\n\n' +
        '• Only this Album entry will be removed.\n' +
        '• Source Track and Take files will not be deleted.'
      )
    ) {
      return;
    }
    const albumId = state.activeAlbum.id;
    const expectedRevision = encodeURIComponent(state.activeAlbum.revision || 1);
    try {
      const res = await fetch(`/api/projects/albums/${albumId}/entries/${entry.id}?expected_revision=${expectedRevision}`, {
        method: 'DELETE',
      });
      if (res.ok) {
        const updated = await res.json();
        state.activeAlbum = updated;
        if (dom.albumDetailRevisionPill) {
          dom.albumDetailRevisionPill.textContent = `Rev ${updated.revision || 1}`;
        }
        renderAlbumRevisionHistory(updated);
        renderSequencerTable(state.activeAlbum);
        fetchAlbums();
        fetchQuota();
      } else {
        const err = await res.json();
        showToast(err.detail || 'Failed to remove entry from album', 'error');
      }
    } catch (e) {
      showToast('Network error removing entry', 'error');
    }
  }

  function closePickTrackModal() {
    state.pickTrackModalSessionId += 1;
    state.pickTrackLoadSeq += 1;
    state.pickTrackTakesLoaded = false;
    state.pickTrackEditingEntryId = null;
    if (dom.btnConfirmAddTrackToAlbum) dom.btnConfirmAddTrackToAlbum.disabled = true;
    if (dom.modalPickTrackForAlbum) dom.modalPickTrackForAlbum.classList.add('hidden');
  }

  async function loadTakesForPickTrackModal(trackId, preferredTakeId = null, preferPlaceholder = false) {
    const reqSeq = ++state.pickTrackLoadSeq;
    const modalSession = state.pickTrackModalSessionId;
    const targetTrackId = trackId || '';
    const targetEntryId = state.pickTrackEditingEntryId;

    const isStalePickRequest = () => (
      reqSeq !== state.pickTrackLoadSeq
      || modalSession !== state.pickTrackModalSessionId
      || state.pickTrackEditingEntryId !== targetEntryId
      || (dom.selectPickTrack && (dom.selectPickTrack.value || '') !== targetTrackId)
    );

    state.pickTrackTakesLoaded = false;
    if (dom.btnConfirmAddTrackToAlbum) dom.btnConfirmAddTrackToAlbum.disabled = true;
    if (!dom.pickTrackTakesGroup || !dom.pickTrackTakesList) return;
    dom.pickTrackTakesList.innerHTML = '';

    if (!targetTrackId) {
      dom.pickTrackTakesGroup.classList.add('hidden');
      return;
    }

    dom.pickTrackTakesGroup.classList.remove('hidden');
    const loadingEl = document.createElement('div');
    loadingEl.className = 'takes-loading-spinner';
    loadingEl.textContent = 'Loading takes...';
    dom.pickTrackTakesList.appendChild(loadingEl);

    try {
      const res = await fetch(`/api/projects/tracks/${targetTrackId}`);
      if (isStalePickRequest()) return;
      if (!res.ok) {
        dom.pickTrackTakesList.innerHTML = '';
        const errEl = document.createElement('div');
        errEl.className = 'takes-error';
        errEl.textContent = 'Failed to load takes for selected track. Please close and retry.';
        dom.pickTrackTakesList.appendChild(errEl);
        state.pickTrackTakesLoaded = false;
        if (dom.btnConfirmAddTrackToAlbum) dom.btnConfirmAddTrackToAlbum.disabled = true;
        return;
      }
      const data = await res.json();
      if (isStalePickRequest()) return;
      const track = data.track || {};
      const takes = Array.isArray(data.takes) ? data.takes : [];
      dom.pickTrackTakesList.innerHTML = '';

      const editingEntry = (targetEntryId && state.activeAlbum && Array.isArray(state.activeAlbum.tracks))
        ? state.activeAlbum.tracks.find(t => t.id === targetEntryId)
        : null;
      const editingRecordedEntry = !!(
        editingEntry && (!editingEntry.is_placeholder || editingEntry.has_owned_audio || editingEntry.take_id)
      );

      let defaultVal = editingRecordedEntry ? null : '';
      if (!preferPlaceholder && takes.length > 0) {
        if (preferredTakeId && takes.some(t => t.id === preferredTakeId)) {
          defaultVal = preferredTakeId;
        } else if (!editingRecordedEntry) {
          const masterTake = takes.find(t => t.is_master) || takes.find(t => t.id === track.primary_take_id) || takes[0];
          defaultVal = masterTake ? masterTake.id : '';
        }
      }

      function appendTakeRadioRow(value, titleText, metaText, isMasterBadge, checked) {
        const lbl = document.createElement('label');
        lbl.style.display = 'flex';
        lbl.style.alignItems = 'center';
        lbl.style.gap = '0.6rem';
        lbl.style.padding = '0.6rem 0.75rem';
        lbl.style.minHeight = '44px';
        lbl.style.borderRadius = '8px';
        lbl.style.border = '1px solid rgba(255, 255, 255, 0.12)';
        lbl.style.background = 'rgba(15, 23, 42, 0.45)';
        lbl.style.cursor = 'pointer';

        const radio = document.createElement('input');
        radio.type = 'radio';
        radio.name = 'pickAlbumTakeId';
        radio.value = value;
        radio.checked = !!checked;
        lbl.appendChild(radio);

        const body = document.createElement('div');
        body.style.flex = '1';
        body.style.minWidth = '0';

        const top = document.createElement('div');
        top.style.display = 'flex';
        top.style.alignItems = 'center';
        top.style.flexWrap = 'wrap';
        top.style.gap = '0.4rem';

        const labelSpan = document.createElement('span');
        labelSpan.style.fontWeight = '600';
        labelSpan.textContent = titleText;
        top.appendChild(labelSpan);

        if (isMasterBadge) {
          const badge = document.createElement('span');
          badge.className = 'master-badge';
          badge.textContent = '★ MASTER';
          top.appendChild(badge);
        }

        body.appendChild(top);

        if (metaText) {
          const sub = document.createElement('div');
          sub.style.fontSize = '0.78rem';
          sub.style.opacity = '0.8';
          sub.textContent = metaText;
          body.appendChild(sub);
        }

        lbl.appendChild(body);
        dom.pickTrackTakesList.appendChild(lbl);
      }

      if (takes.length === 0) {
        if (editingRecordedEntry) {
          const unavailableEl = document.createElement('div');
          unavailableEl.className = 'takes-error';
          unavailableEl.textContent = 'Source take unavailable; existing album recording is preserved.';
          dom.pickTrackTakesList.appendChild(unavailableEl);
          state.pickTrackTakesLoaded = false;
          if (dom.btnConfirmAddTrackToAlbum) dom.btnConfirmAddTrackToAlbum.disabled = true;
          return;
        }
        appendTakeRadioRow(
          '',
          'Placeholder (No Takes Uploaded)',
          'No recordings exist yet for this Track Workspace. Will be added as an editable placeholder.',
          false,
          true
        );
      } else {
        if (editingRecordedEntry && preferredTakeId && !takes.some(t => t.id === preferredTakeId)) {
          const warnEl = document.createElement('div');
          warnEl.className = 'takes-error';
          warnEl.textContent = 'Source take unavailable; existing album recording is preserved.';
          dom.pickTrackTakesList.appendChild(warnEl);
        }
        takes.forEach(take => {
          const durStr = formatDurationSeconds(take.duration_seconds);
          const mbStr = `${((take.size_bytes || 0) / (1024 * 1024)).toFixed(2)} MB`;
          const metaParts = [
            (take.format || 'mp3').toUpperCase(),
            durStr || null,
            mbStr,
            `Rev ${take.revision || 1}`,
          ].filter(Boolean);
          const isMaster = !!(take.is_master || (track.primary_take_id && track.primary_take_id === take.id));
          appendTakeRadioRow(take.id, take.label || 'Take', metaParts.join(' • '), isMaster, defaultVal === take.id);
        });

        if (!editingRecordedEntry) {
          appendTakeRadioRow(
            '',
            'Placeholder (Unassigned — Choose Take Later)',
            'Keep entry in tracklist without copying a recording yet.',
            false,
            defaultVal === ''
          );
        }
      }
      state.pickTrackTakesLoaded = true;
      if (dom.btnConfirmAddTrackToAlbum) {
        dom.btnConfirmAddTrackToAlbum.disabled = !!(editingRecordedEntry && !defaultVal);
      }
    } catch (e) {
      if (isStalePickRequest()) return;
      dom.pickTrackTakesList.innerHTML = '';
      const errEl = document.createElement('div');
      errEl.className = 'takes-error';
      errEl.textContent = 'Network error loading track takes. Please close and retry.';
      dom.pickTrackTakesList.appendChild(errEl);
      state.pickTrackTakesLoaded = false;
      if (dom.btnConfirmAddTrackToAlbum) dom.btnConfirmAddTrackToAlbum.disabled = true;
    }
  }

  async function openPickTrackModal(editingEntry = null) {
    if (!dom.selectPickTrack || !state.activeAlbum) return;
    if (state.activeAlbum.is_finalized || state.activeAlbum.status === 'completed') {
      showToast('Finalized albums cannot be modified', 'error');
      return;
    }

    const entry = (editingEntry && editingEntry.id && editingEntry.track_id) ? editingEntry : null;
    state.pickTrackModalSessionId += 1;
    state.pickTrackLoadSeq += 1;
    state.pickTrackEditingEntryId = entry ? entry.id : null;
    state.pickTrackTakesLoaded = false;
    if (dom.btnConfirmAddTrackToAlbum) dom.btnConfirmAddTrackToAlbum.disabled = true;

    dom.selectPickTrack.innerHTML = '<option value="">-- Choose Track --</option>';
    state.tracks.forEach(t => {
      const opt = document.createElement('option');
      opt.value = t.id;
      const takeCount = t.takes_count || 0;
      const takeSuffix = takeCount === 0 ? 'No takes — Placeholder' : `${takeCount} take${takeCount === 1 ? '' : 's'}`;
      opt.textContent = `${t.title}${t.artist ? ` - ${t.artist}` : ''} (${takeSuffix})`;
      dom.selectPickTrack.appendChild(opt);
    });

    if (entry) {
      if (dom.modalPickTrackTitle) dom.modalPickTrackTitle.textContent = 'Change Selected Recording';
      if (dom.btnConfirmAddTrackToAlbum) dom.btnConfirmAddTrackToAlbum.textContent = 'Save Take Selection';
      dom.selectPickTrack.value = entry.track_id;
      dom.selectPickTrack.disabled = true;
      await loadTakesForPickTrackModal(entry.track_id, entry.take_id, !!(entry.is_placeholder || !entry.take_id));
    } else {
      if (dom.modalPickTrackTitle) dom.modalPickTrackTitle.textContent = 'Add Track to Album';
      if (dom.btnConfirmAddTrackToAlbum) dom.btnConfirmAddTrackToAlbum.textContent = 'Add to Album';
      dom.selectPickTrack.disabled = false;
      dom.selectPickTrack.value = '';
      if (dom.pickTrackTakesGroup) dom.pickTrackTakesGroup.classList.add('hidden');
      if (dom.pickTrackTakesList) dom.pickTrackTakesList.innerHTML = '';
    }

    if (dom.modalPickTrackForAlbum) dom.modalPickTrackForAlbum.classList.remove('hidden');
  }

  async function confirmAddTrackToAlbum() {
    const trackId = dom.selectPickTrack ? dom.selectPickTrack.value : '';
    if (!trackId) {
      showToast('Please select a track workspace', 'error');
      return;
    }
    if (!state.activeAlbum) return;

    const checkedRadio = dom.pickTrackTakesList
      ? dom.pickTrackTakesList.querySelector('input[name="pickAlbumTakeId"]:checked')
      : null;
    if (!state.pickTrackTakesLoaded || !checkedRadio) {
      showToast('Please wait for track takes to finish loading or select a valid recording option.', 'error');
      return;
    }
    let currentEditingEntry = null;
    if (state.pickTrackEditingEntryId) {
      currentEditingEntry = Array.isArray(state.activeAlbum.tracks)
        ? state.activeAlbum.tracks.find(t => t.id === state.pickTrackEditingEntryId)
        : null;
      if (!currentEditingEntry || currentEditingEntry.track_id !== trackId) {
        showToast('Album entry context changed. Please reopen the take picker.', 'error');
        closePickTrackModal();
        return;
      }
    }
    const takeId = checkedRadio.value ? checkedRadio.value : null;
    if (
      currentEditingEntry &&
      takeId !== null &&
      (currentEditingEntry.has_owned_audio || currentEditingEntry.take_id !== null) &&
      takeId !== currentEditingEntry.take_id
    ) {
      if (
        !confirm(
          'Replace this Album entry\'s recording with the selected Take?\n\n' +
          '• Only this Album entry\'s recording will change.\n' +
          '• The previous Album recording will be safely retained.\n' +
          '• Other Album entries will not be affected.'
        )
      ) {
        return;
      }
    }
    const albumId = state.activeAlbum.id;
    const expectedRevision = state.activeAlbum.revision || 1;

    if (dom.btnConfirmAddTrackToAlbum) dom.btnConfirmAddTrackToAlbum.disabled = true;
    try {
      let res;
      if (state.pickTrackEditingEntryId) {
        res = await fetch(`/api/projects/albums/${albumId}/entries/${state.pickTrackEditingEntryId}`, {
          method: 'PATCH',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            expected_revision: expectedRevision,
            take_id: takeId,
            clear_take_to_placeholder: takeId === null,
          }),
        });
      } else {
        res = await fetch(`/api/projects/albums/${albumId}/entries`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            expected_revision: expectedRevision,
            track_id: trackId,
            take_id: takeId,
            disc_number: 1,
          }),
        });
      }

      if (res.ok) {
        const updated = await res.json();
        state.activeAlbum = updated;
        state.pickTrackEditingEntryId = null;
        if (dom.albumDetailRevisionPill) {
          dom.albumDetailRevisionPill.textContent = `Rev ${updated.revision || 1}`;
        }
        renderAlbumRevisionHistory(updated);
        renderSequencerTable(state.activeAlbum);
        closePickTrackModal();
        showToast(takeId ? '✨ Recording copied into album!' : '📌 Placeholder added to album tracklist', 'success');
        fetchAlbums();
        fetchQuota();
      } else {
        let detail = 'Failed to update album tracklist';
        try {
          const err = await res.json();
          if (err && err.detail) detail = err.detail;
        } catch (_) {}
        showToast(detail, 'error');
      }
    } catch (e) {
      showToast('Network error saving album recording', 'error');
    } finally {
      if (dom.btnConfirmAddTrackToAlbum) dom.btnConfirmAddTrackToAlbum.disabled = false;
    }
  }

  let refreshPreviewRequestSeq = 0;

  function invalidateRefreshContext() {
    refreshPreviewRequestSeq += 1;
    state.pendingRefreshPreview = null;
    if (dom.btnConfirmRefreshAlbumEntry) {
      dom.btnConfirmRefreshAlbumEntry.disabled = true;
    }
  }

  function closeRefreshModal() {
    invalidateRefreshContext();
    if (dom.modalRefreshAlbumEntry) {
      dom.modalRefreshAlbumEntry.classList.add('hidden');
    }
  }

  async function openRefreshEntryModal(albumId, entryId) {
    if (!dom.modalRefreshAlbumEntry) return;
    const activeAlbum = state.activeAlbum;
    if (!activeAlbum || activeAlbum.id !== albumId || !Number.isInteger(activeAlbum.revision) || activeAlbum.revision < 1) {
      invalidateRefreshContext();
      showToast('Album context changed; reload album before refreshing.', 'error');
      return;
    }

    const capturedAlbumId = activeAlbum.id;
    const capturedRevision = activeAlbum.revision;
    const capturedEntryId = entryId;
    invalidateRefreshContext();
    const requestSeq = refreshPreviewRequestSeq;
    const isCurrentContext = () =>
      requestSeq === refreshPreviewRequestSeq &&
      Boolean(state.activeAlbum) &&
      state.activeAlbum.id === capturedAlbumId &&
      state.activeAlbum.revision === capturedRevision;

    if (dom.refreshStatusNotice) {
      dom.refreshStatusNotice.textContent = '';
      dom.refreshStatusNotice.classList.add('hidden');
    }
    if (dom.refreshChangesContainer) dom.refreshChangesContainer.classList.add('hidden');
    if (dom.refreshLyricsDiffContainer) dom.refreshLyricsDiffContainer.classList.add('hidden');
    if (dom.btnConfirmRefreshAlbumEntry) dom.btnConfirmRefreshAlbumEntry.disabled = true;

    try {
      const res = await fetch(`/api/projects/albums/${capturedAlbumId}/entries/${capturedEntryId}/refresh-preview`);
      if (!isCurrentContext()) return;
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        if (!isCurrentContext()) return;
        showToast(err.detail || 'Failed to load refresh comparison', 'error');
        return;
      }
      const preview = await res.json();
      if (!isCurrentContext()) return;

      if (dom.refreshEntrySubtitle) {
        const trackTitle = (preview.album_snapshot && preview.album_snapshot.source_track_title) || preview.track_id;
        dom.refreshEntrySubtitle.textContent = `${trackTitle} • Compare current album recording with latest source take`;
      }

      if (dom.refreshAlbumCopySummary) {
        if (preview.album_snapshot) {
          const a = preview.album_snapshot;
          const copiedDate = a.copied_at ? new Date(a.copied_at * 1000).toLocaleString() : '—';
          dom.refreshAlbumCopySummary.textContent = `${a.source_take_label} • Rev ${a.source_take_revision} • ${(a.format || '').toUpperCase()} • ${formatDurationSeconds(a.duration_seconds) || '0:00'} • Copied ${copiedDate}`;
        } else {
          dom.refreshAlbumCopySummary.textContent = 'No album-owned copy exists yet (legacy reference).';
        }
      }

      if (dom.refreshAlbumCopyAudio) {
        dom.refreshAlbumCopyAudio.src = preview.album_audio_stream_url || '';
      }

      if (!preview.source_available) {
        if (dom.refreshSourceTakeSummary) {
          dom.refreshSourceTakeSummary.textContent = 'Source take unavailable / deleted.';
        }
        if (dom.refreshSourceTakeAudio) dom.refreshSourceTakeAudio.src = '';
        if (dom.refreshStatusNotice) {
          dom.refreshStatusNotice.textContent = preview.unavailable_reason || 'Source take is unavailable. Existing album recording will be preserved.';
          dom.refreshStatusNotice.classList.remove('hidden');
        }
        dom.modalRefreshAlbumEntry.classList.remove('hidden');
        return;
      }

      if (dom.refreshSourceTakeSummary && preview.source_snapshot) {
        const s = preview.source_snapshot;
        const modDate = s.source_take_updated_at ? new Date(s.source_take_updated_at * 1000).toLocaleString() : '—';
        dom.refreshSourceTakeSummary.textContent = `${s.source_take_label} • Rev ${s.source_take_revision} • ${(s.format || '').toUpperCase()} • ${formatDurationSeconds(s.duration_seconds) || '0:00'} • Updated ${modDate}`;
      }

      if (dom.refreshSourceTakeAudio) {
        dom.refreshSourceTakeAudio.src = preview.source_audio_stream_url || '';
      }

      if (dom.refreshMetadataChangesList && dom.refreshChangesContainer) {
        dom.refreshMetadataChangesList.innerHTML = '';
        const changes = Array.isArray(preview.metadata_changes) ? preview.metadata_changes : [];
        if (changes.length > 0 || preview.audio_changed) {
          dom.refreshChangesContainer.classList.remove('hidden');
          if (preview.audio_changed) {
            const audioRow = document.createElement('div');
            audioRow.textContent = '🔊 Audio file checksum / audio stream differs from source take.';
            audioRow.style.color = '#38bdf8';
            dom.refreshMetadataChangesList.appendChild(audioRow);
          }
          changes.forEach(c => {
            const row = document.createElement('div');
            row.textContent = `${c.label || c.field}: "${c.old ?? '—'}" → "${c.new ?? '—'}"`;
            dom.refreshMetadataChangesList.appendChild(row);
          });
        }
      }

      if (dom.refreshLyricsDiffContainer && dom.refreshLyricsDiffPre) {
        if (preview.lyrics_changed) {
          dom.refreshLyricsDiffContainer.classList.remove('hidden');
          dom.refreshLyricsDiffPre.textContent = preview.lyrics_diff || '(Lyrics changed)';
        }
      }

      if (!preview.has_changes && dom.refreshStatusNotice) {
        dom.refreshStatusNotice.textContent = 'Album copy is already up to date with the latest source take.';
        dom.refreshStatusNotice.classList.remove('hidden');
      }

      state.pendingRefreshPreview = Object.freeze({
        albumId: capturedAlbumId,
        entryId: capturedEntryId,
        expected_revision: capturedRevision,
        expected_source_revision: preview.expected_source_revision,
        expected_source_audio_sha256: preview.expected_source_audio_sha256,
        expected_source_updated_at: preview.expected_source_updated_at,
        expected_source_metadata_fingerprint: preview.expected_source_metadata_fingerprint || null,
        requestSeq,
      });
      if (dom.btnConfirmRefreshAlbumEntry) {
        dom.btnConfirmRefreshAlbumEntry.disabled = false;
      }
      dom.modalRefreshAlbumEntry.classList.remove('hidden');
    } catch (e) {
      if (!isCurrentContext()) return;
      showToast('Network error loading refresh preview', 'error');
    }
  }

  async function confirmRefreshAlbumEntry() {
    const pending = state.pendingRefreshPreview;
    if (!pending) return;

    if (
      pending.requestSeq !== refreshPreviewRequestSeq ||
      !state.activeAlbum ||
      state.activeAlbum.id !== pending.albumId ||
      state.activeAlbum.revision !== pending.expected_revision ||
      !Number.isInteger(pending.expected_revision) ||
      pending.expected_revision < 1
    ) {
      closeRefreshModal();
      showToast('Album changed since refresh preview was opened; reload and try again.', 'error', 5000);
      return;
    }

    if (dom.btnConfirmRefreshAlbumEntry) dom.btnConfirmRefreshAlbumEntry.disabled = true;
    try {
      const res = await fetch(`/api/projects/albums/${pending.albumId}/entries/${pending.entryId}/refresh`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          expected_revision: pending.expected_revision,
          expected_source_revision: pending.expected_source_revision,
          expected_source_audio_sha256: pending.expected_source_audio_sha256,
          expected_source_updated_at: pending.expected_source_updated_at,
          expected_source_metadata_fingerprint: pending.expected_source_metadata_fingerprint || null,
        }),
      });

      if (res.ok) {
        const updated = await res.json();
        state.activeAlbum = updated;
        state.pendingRefreshPreview = null;
        if (dom.albumDetailRevisionPill) {
          dom.albumDetailRevisionPill.textContent = `Rev ${updated.revision || 1}`;
        }
        renderAlbumRevisionHistory(updated);
        renderSequencerTable(state.activeAlbum);
        if (dom.modalRefreshAlbumEntry) dom.modalRefreshAlbumEntry.classList.add('hidden');
        showToast('✨ Album entry refreshed from source take!', 'success');
        fetchAlbums();
        fetchQuota();
      } else {
        const err = await res.json().catch(() => ({}));
        showToast(err.detail || 'Could not refresh album entry', 'error', 5000);
      }
    } catch (e) {
      showToast('Network error confirming refresh', 'error');
    } finally {
      if (dom.btnConfirmRefreshAlbumEntry) dom.btnConfirmRefreshAlbumEntry.disabled = false;
    }
  }

  async function handleFinalizeAlbum() {
    if (!state.activeAlbum) return;
    const albumId = state.activeAlbum.id;
    if (!confirm('Finalize this album release? Finalized albums become locked and cannot have tracks, takes, recordings, or metadata modified.')) {
      return;
    }
    if (dom.btnFinalizeAlbum) dom.btnFinalizeAlbum.disabled = true;
    const expectedRevision = encodeURIComponent(state.activeAlbum.revision || 1);
    try {
      const res = await fetch(`/api/projects/albums/${albumId}/finalize?expected_revision=${expectedRevision}`, {
        method: 'POST',
      });
      if (res.ok) {
        const finalized = await res.json();
        state.activeAlbum = finalized;
        if (dom.albumDetailRevisionPill) {
          dom.albumDetailRevisionPill.textContent = `Rev ${finalized.revision || 1}`;
        }
        applyAlbumModalLockState(finalized);
        renderAlbumRevisionHistory(finalized);
        renderSequencerTable(finalized);
        showToast('🔒 Album finalized and locked!', 'success');
        fetchAlbums();
      } else {
        let msg = 'Album finalization failed validation.';
        try {
          const err = await res.json();
          if (err && err.detail) {
            msg = typeof err.detail === 'string' ? err.detail : (err.detail.message || msg);
          }
        } catch (_) {}
        showToast(msg, 'error', 6000);
        if (dom.btnFinalizeAlbum) dom.btnFinalizeAlbum.disabled = false;
      }
    } catch (e) {
      showToast('Network error finalizing album', 'error');
      if (dom.btnFinalizeAlbum) dom.btnFinalizeAlbum.disabled = false;
    }
  }

  async function saveAlbumChanges() {
    if (!state.activeAlbum) return;
    if (state.activeAlbum.is_finalized) {
      showToast('Finalized albums are locked and cannot be edited.', 'error');
      return;
    }
    const albumId = state.activeAlbum.id;

    const payload = {
      title: dom.editAlbumTitle.value.trim() || state.activeAlbum.title,
      album_artist: dom.editAlbumArtist.value.trim() || null,
      year: dom.editAlbumYear.value ? parseInt(dom.editAlbumYear.value, 10) : null,
      genre: dom.editAlbumGenre.value.trim() || null,
      expected_revision: state.activeAlbum.revision || 1,
      tracks: state.activeAlbum.tracks || [],
    };

    try {
      const res = await fetch(`/api/projects/albums/${albumId}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });

      if (res.ok) {
        const updated = await res.json().catch(() => null);
        if (updated && updated.id) {
          state.activeAlbum = updated;
          if (dom.albumDetailRevisionPill) {
            dom.albumDetailRevisionPill.textContent = `Rev ${updated.revision || 1}`;
          }
          renderAlbumRevisionHistory(updated);
        }
        showToast('✨ Album project updated successfully!', 'success');
        if (dom.modalAlbumDetail) dom.modalAlbumDetail.classList.add('hidden');
        fetchAlbums();
      } else {
        const err = await res.json().catch(() => ({}));
        const msg = err.detail && typeof err.detail === 'object' ? (err.detail.message || 'Failed to update album') : (err.detail || 'Failed to update album');
        showToast(msg, 'error');
      }
    } catch (e) {
      showToast('Network error saving album', 'error');
    }
  }

  async function handleAlbumCoverUpload(file) {
    if (!state.activeAlbum) return;
    if (state.activeAlbum.is_finalized) {
      showToast('Finalized albums are locked and cannot be edited.', 'error');
      return;
    }
    const albumId = state.activeAlbum.id;

    const formData = new FormData();
    formData.append('expected_revision', String(state.activeAlbum.revision || 1));
    formData.append('file', file);

    showToast('Uploading album cover art...', 'info', 2000);
    try {
      const res = await fetch(`/api/projects/albums/${albumId}/artwork`, {
        method: 'POST',
        body: formData,
      });

      if (res.ok) {
        const data = await res.json().catch(() => null);
        const updated = (data && data.album) ? data.album : (data && data.id ? data : null);
        if (state.activeAlbum && state.activeAlbum.id === albumId) {
          if (updated && updated.id === albumId) {
            state.activeAlbum = updated;
            if (dom.albumDetailRevisionPill) {
              dom.albumDetailRevisionPill.textContent = `Rev ${updated.revision || 1}`;
            }
            renderAlbumRevisionHistory(updated);
          }
          if (dom.albumDetailArtwork) {
            dom.albumDetailArtwork.src = `/api/projects/albums/${albumId}/artwork?t=${Date.now()}`;
            dom.albumDetailArtwork.classList.remove('hidden');
          }
          if (dom.albumDetailPlaceholder) {
            dom.albumDetailPlaceholder.classList.add('hidden');
          }
        }
        showToast('✨ Master album artwork saved!', 'success');
        fetchAlbums();
      } else {
        const err = await res.json().catch(() => ({}));
        const msg = err.detail && typeof err.detail === 'object' ? (err.detail.message || 'Failed to upload artwork') : (err.detail || 'Failed to upload artwork');
        showToast(msg, 'error');
      }
    } catch (e) {
      showToast('Network error uploading cover art', 'error');
    }
  }

  // --- Session Loader & Save to Project ---
  function clearLoadedProjectTakeRef() {
    state.loadedProjectTake = null;
    sessionStorage.removeItem('mp3metafix_loaded_project_track');
  }

  function setLoadedProjectTakeRef(ref) {
    state.loadedProjectTake = (ref && ref.track_id && ref.take_id) ? ref : null;
  }

  window.clearLoadedProjectTake = clearLoadedProjectTakeRef;
  window.setLoadedProjectTake = setLoadedProjectTakeRef;
  window.addEventListener('mp3metafix:loaded-take-cleared', () => {
    state.loadedProjectTake = null;
  });

  async function loadTakeIntoSession(trackId, takeId) {
    showToast('Loading take into Editor...', 'info', 1500);
    try {
      const res = await fetch(`/api/projects/tracks/${trackId}/takes/${takeId}/load-session`, {
        method: 'POST',
      });
      if (res.ok) {
        const data = await res.json();
        const track = state.tracks.find(t => t.id === trackId);
        state.loadedProjectTake = data.loaded_project_take || {
          track_id: trackId,
          take_id: takeId,
          track_title: track ? track.title : 'Workspace Track',
          take_label: data.label || 'Master Take',
          filename: data.filename || 'track.mp3',
        };
        sessionStorage.setItem('mp3metafix_loaded_project_track', JSON.stringify(state.loadedProjectTake));
        showToast('✨ Track loaded into editor!', 'success');
        if (typeof window.restoreSession === 'function') {
          await window.restoreSession();
        }
        switchStudioView('editor');
      } else {
        const err = await res.json();
        showToast(err.detail || 'Failed to load take into editor', 'error');
      }
    } catch (e) {
      showToast('Network error loading take', 'error');
    }
  }

  function updateSaveTargetModeUI() {
    const mode = document.querySelector('input[name="saveProjectTargetMode"]:checked')?.value || 'new';
    const selectGrp = document.getElementById('saveProjectExistingTrackGroup');
    if (mode === 'update') {
      if (dom.saveProjectUpdateGroup) dom.saveProjectUpdateGroup.classList.remove('hidden');
      if (dom.saveProjectNewTrackFields) dom.saveProjectNewTrackFields.classList.add('hidden');
      if (selectGrp) selectGrp.classList.add('hidden');
    } else if (mode === 'existing') {
      if (dom.saveProjectUpdateGroup) dom.saveProjectUpdateGroup.classList.add('hidden');
      if (dom.saveProjectNewTrackFields) dom.saveProjectNewTrackFields.classList.add('hidden');
      if (selectGrp) selectGrp.classList.remove('hidden');
    } else {
      if (dom.saveProjectUpdateGroup) dom.saveProjectUpdateGroup.classList.add('hidden');
      if (dom.saveProjectNewTrackFields) dom.saveProjectNewTrackFields.classList.remove('hidden');
      if (selectGrp) selectGrp.classList.add('hidden');
    }
  }

  async function openSaveToProjectModal() {
    if (!dom.modalSaveToProject) return;
    if (dom.formSaveToProject) dom.formSaveToProject.reset();

    // Synchronize loaded project take reference with sessionStorage and active track list
    let loadedTake = null;
    try {
      const stored = sessionStorage.getItem('mp3metafix_loaded_project_track');
      if (stored) loadedTake = JSON.parse(stored);
    } catch (e) {}

    if (
      !loadedTake ||
      !loadedTake.track_id ||
      !loadedTake.take_id ||
      !state.tracks.some(t => t.id === loadedTake.track_id)
    ) {
      loadedTake = null;
      clearLoadedProjectTakeRef();
    } else {
      state.loadedProjectTake = loadedTake;
    }

    // Populate existing tracks dropdown
    if (dom.selectSaveTargetTrack) {
      dom.selectSaveTargetTrack.innerHTML = '';
      state.tracks.forEach(track => {
        const opt = document.createElement('option');
        opt.value = track.id;
        opt.textContent = track.title || 'Untitled Track';
        if (loadedTake && loadedTake.track_id === track.id) {
          opt.selected = true;
        }
        dom.selectSaveTargetTrack.appendChild(opt);
      });
    }

    const selectGrp = document.getElementById('saveProjectExistingTrackGroup');

    if (loadedTake && loadedTake.track_id && loadedTake.take_id) {
      if (dom.labelRadioSaveUpdateTake) dom.labelRadioSaveUpdateTake.classList.remove('hidden');
      if (dom.radioSaveUpdateTake) {
        dom.radioSaveUpdateTake.disabled = false;
        dom.radioSaveUpdateTake.checked = true;
      }
      if (dom.saveProjectUpdateNoticeText) {
        dom.saveProjectUpdateNoticeText.textContent = `${loadedTake.track_title || 'Workspace Track'} • ${loadedTake.take_label || 'Current Take'}`;
      }
      if (dom.saveProjectUpdateGroup) dom.saveProjectUpdateGroup.classList.remove('hidden');
      if (dom.saveProjectNewTrackFields) dom.saveProjectNewTrackFields.classList.add('hidden');
      if (selectGrp) selectGrp.classList.add('hidden');

      const takeLabelInput = document.getElementById('saveProjectTakeLabel');
      if (takeLabelInput) takeLabelInput.value = loadedTake.take_label || '';
    } else {
      if (dom.labelRadioSaveUpdateTake) dom.labelRadioSaveUpdateTake.classList.add('hidden');
      if (dom.radioSaveUpdateTake) {
        dom.radioSaveUpdateTake.disabled = true;
        dom.radioSaveUpdateTake.checked = false;
      }
      if (dom.saveProjectUpdateGroup) dom.saveProjectUpdateGroup.classList.add('hidden');

      if (state.tracks.length === 0) {
        if (dom.radioSaveNewTrack) dom.radioSaveNewTrack.checked = true;
        if (dom.radioSaveExistingTrack) dom.radioSaveExistingTrack.disabled = true;
        if (dom.saveProjectNewTrackFields) dom.saveProjectNewTrackFields.classList.remove('hidden');
        if (selectGrp) selectGrp.classList.add('hidden');
      } else {
        if (dom.radioSaveExistingTrack) dom.radioSaveExistingTrack.disabled = false;
        if (dom.radioSaveNewTrack) dom.radioSaveNewTrack.checked = true;
        if (dom.saveProjectNewTrackFields) dom.saveProjectNewTrackFields.classList.remove('hidden');
        if (selectGrp) selectGrp.classList.add('hidden');
      }
    }

    // Pre-fill Title from loaded filename or title tag if available
    const titleInput = document.getElementById('saveProjectTrackTitle');
    const loadedTitleTag = document.getElementById('inputTitle');
    const loadedFilename = document.getElementById('loadedFilename');
    if (titleInput) {
      titleInput.value = (loadedTitleTag && loadedTitleTag.value) || (loadedFilename && loadedFilename.textContent.replace(/\.[^.]+$/, '')) || '';
    }

    dom.modalSaveToProject.classList.remove('hidden');
  }

  function closeSaveToProjectModal() {
    if (dom.modalSaveToProject) dom.modalSaveToProject.classList.add('hidden');
  }

  async function handleSaveToProjectSubmit(e) {
    e.preventDefault();
    const mode = document.querySelector('input[name="saveProjectTargetMode"]:checked')?.value || 'new';
    const newTitle = (document.getElementById('saveProjectTrackTitle').value || '').trim();
    const targetTrackId = dom.selectSaveTargetTrack ? dom.selectSaveTargetTrack.value : null;
    const takeLabel = (document.getElementById('saveProjectTakeLabel').value || '').trim();
    const notes = (document.getElementById('saveProjectNotes').value || '').trim();
    const isMaster = document.getElementById('saveProjectIsMaster').checked;

    let payload = {};

    if (mode === 'update') {
      let loadedTake = null;
      try {
        const stored = sessionStorage.getItem('mp3metafix_loaded_project_track');
        if (stored) loadedTake = JSON.parse(stored);
      } catch (err) {}
      if (!loadedTake) loadedTake = state.loadedProjectTake;
      if (!loadedTake || !loadedTake.track_id || !loadedTake.take_id) {
        clearLoadedProjectTakeRef();
        showToast('No active loaded workspace take to update', 'error');
        return;
      }
      payload = {
        track_id: loadedTake.track_id,
        take_id: loadedTake.take_id,
        take_label: takeLabel || loadedTake.take_label || 'Master Take',
        notes: notes || null,
        is_master: isMaster,
      };
    } else if (mode === 'existing') {
      if (!targetTrackId) {
        showToast('Please select a target track workspace', 'error');
        return;
      }
      payload = {
        track_id: targetTrackId,
        take_label: takeLabel || 'Editor Take',
        notes: notes || null,
        is_master: isMaster,
      };
    } else {
      if (!newTitle) {
        showToast('Please enter a track title', 'error');
        return;
      }
      payload = {
        new_track_title: newTitle,
        take_label: takeLabel || 'Editor Take',
        notes: notes || null,
        is_master: isMaster,
      };
    }

    showToast(mode === 'update' ? 'Updating workspace take...' : 'Saving audio into project workspace...', 'info', 2000);
    try {
      const res = await fetch('/api/projects/ingest-session', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });

      if (res.ok) {
        showToast(mode === 'update' ? '✨ Track take updated in workspace!' : '✨ Audio file saved into Project Storage!', 'success');
        closeSaveToProjectModal();
        fetchTracks();
        fetchQuota();
      } else {
        const err = await res.json();
        showToast(err.detail || 'Failed to save into project storage', 'error');
      }
    } catch (err) {
      showToast('Network error saving to project', 'error');
    }
  }

  // --- Actions & Helpers ---
  function openUploadTakeModal(trackId, title) {
    dom.uploadTakeTrackId.value = trackId;
    if (dom.uploadTakeTitleDisplay) dom.uploadTakeTitleDisplay.textContent = title;
    if (dom.formUploadTake) dom.formUploadTake.reset();
    if (dom.modalUploadTake) dom.modalUploadTake.classList.remove('hidden');
  }

  function openUploadStemModal(trackId, takeId, label) {
    dom.uploadStemTrackId.value = trackId;
    dom.uploadStemTakeId.value = takeId;
    if (dom.uploadStemTakeDisplay) dom.uploadStemTakeDisplay.textContent = label;
    if (dom.formUploadStem) dom.formUploadStem.reset();
    if (dom.modalUploadStem) dom.modalUploadStem.classList.remove('hidden');
  }

  async function setTakeMaster(trackId, takeId) {
    try {
      const res = await fetch(`/api/projects/tracks/${trackId}/takes/${takeId}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ is_master: true }),
      });
      if (res.ok) {
        state.expandedTrackIds.add(trackId);
        showToast('★ Take designated as Master!', 'success');
        fetchTracks();
      } else {
        const err = await res.json();
        showToast(err.detail || 'Failed to update master take', 'error');
      }
    } catch (e) {
      showToast('Network error updating take', 'error');
    }
  }

  async function deleteStem(trackId, takeId, stemId) {
    if (!confirm('Are you sure you want to delete this separated stem?')) return;
    try {
      const res = await fetch(`/api/projects/tracks/${trackId}/takes/${takeId}/stems/${stemId}`, {
        method: 'DELETE',
      });
      if (res.ok) {
        state.expandedTrackIds.add(trackId);
        showToast('Stem deleted', 'info');
        fetchTracks();
        fetchQuota();
      }
    } catch (e) {
      showToast('Error deleting stem', 'error');
    }
  }

  async function confirmDeleteTake(trackId, takeId, label) {
    if (!confirm(`Are you sure you want to permanently delete take "${label}"?`)) return;
    try {
      const res = await fetch(`/api/projects/tracks/${trackId}/takes/${takeId}`, {
        method: 'DELETE',
      });
      if (res.ok) {
        state.expandedTrackIds.add(trackId);
        showToast('Take deleted', 'info');
        fetchTracks();
        fetchQuota();
      }
    } catch (e) {
      showToast('Error deleting take', 'error');
    }
  }

  async function confirmDeleteTrack(trackId, title) {
    if (!confirm(`Are you sure you want to delete Track Workspace "${title}" and all its takes/stems?`)) return;
    try {
      const res = await fetch(`/api/projects/tracks/${trackId}`, {
        method: 'DELETE',
      });
      if (res.ok) {
        state.expandedTrackIds.delete(trackId);
        showToast('Track Workspace deleted', 'info');
        fetchTracks();
        fetchQuota();
      }
    } catch (e) {
      showToast('Error deleting track', 'error');
    }
  }

  async function confirmDeleteAlbum(albumId, title, expectedRevision = 1) {
    if (!confirm(`Are you sure you want to delete Album Release "${title}"?\n\nSource Tracks and Takes will not be deleted.`)) return;
    const revParam = encodeURIComponent(expectedRevision || 1);
    try {
      const res = await fetch(`/api/projects/albums/${albumId}?expected_revision=${revParam}`, {
        method: 'DELETE',
      });
      if (res.ok) {
        if (state.activeAlbum && state.activeAlbum.id === albumId) {
          state.activeAlbum = null;
          if (dom.modalAlbumDetail) dom.modalAlbumDetail.classList.add('hidden');
        }
        showToast('Album Release deleted', 'info');
        fetchAlbums();
        fetchQuota();
      } else {
        const err = await res.json().catch(() => ({}));
        const msg = err.detail && typeof err.detail === 'object' ? (err.detail.message || 'Could not delete album') : (err.detail || 'Could not delete album');
        showToast(msg, 'error', 5000);
      }
    } catch (e) {
      showToast('Error deleting album', 'error');
    }
  }

  // --- Form Submissions ---
  async function handleNewTrackSubmit(e) {
    e.preventDefault();
    const title = (document.getElementById('newTrackTitle').value || '').trim();
    const artist = (document.getElementById('newTrackArtist').value || '').trim();
    const bpmVal = document.getElementById('newTrackBpm').value;
    const musicalKey = (document.getElementById('newTrackKey').value || '').trim();
    const notes = (document.getElementById('newTrackNotes').value || '').trim();
    const statusVal = document.getElementById('newTrackStatus').value || 'draft';

    if (!title) {
      showToast('Track title is required', 'error');
      return;
    }

    const payload = {
      title,
      artist: artist || null,
      bpm: bpmVal ? parseInt(bpmVal, 10) : null,
      musical_key: musicalKey || null,
      notes: notes || null,
      status: statusVal,
    };

    try {
      const res = await fetch('/api/projects/tracks', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });

      if (res.ok) {
        showToast('✨ Track Workspace created!', 'success');
        if (dom.modalNewTrack) dom.modalNewTrack.classList.add('hidden');
        if (dom.formNewTrack) dom.formNewTrack.reset();
        fetchTracks();
        fetchQuota();
      } else {
        const err = await res.json();
        showToast(err.detail || 'Failed to create track', 'error');
      }
    } catch (e) {
      showToast('Network error creating track', 'error');
    }
  }

  async function handleNewAlbumSubmit(e) {
    e.preventDefault();
    const title = (document.getElementById('newAlbumTitle').value || '').trim();
    const albumArtist = (document.getElementById('newAlbumArtist').value || '').trim();
    const yearVal = document.getElementById('newAlbumYear').value;
    const genre = (document.getElementById('newAlbumGenre').value || '').trim();
    const credits = (document.getElementById('newAlbumCredits').value || '').trim();
    const statusVal = document.getElementById('newAlbumStatus').value || 'draft';

    if (!title) {
      showToast('Album title is required', 'error');
      return;
    }

    const payload = {
      title,
      album_artist: albumArtist || null,
      year: yearVal ? parseInt(yearVal, 10) : null,
      genre: genre || null,
      credits: credits || null,
      status: statusVal,
    };

    try {
      const res = await fetch('/api/projects/albums', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });

      if (res.ok) {
        showToast('✨ Album Release Project created!', 'success');
        if (dom.modalNewAlbum) dom.modalNewAlbum.classList.add('hidden');
        if (dom.formNewAlbum) dom.formNewAlbum.reset();
        fetchAlbums();
        fetchQuota();
      } else {
        const err = await res.json();
        showToast(err.detail || 'Failed to create album', 'error');
      }
    } catch (e) {
      showToast('Network error creating album', 'error');
    }
  }

  async function handleUploadTakeSubmit(e) {
    e.preventDefault();
    if (state.isUploadingTake) return;

    const trackId = dom.uploadTakeTrackId.value;
    const fileInput = document.getElementById('uploadTakeFileInput');
    const label = (document.getElementById('uploadTakeLabel').value || '').trim();
    const prompt = (document.getElementById('uploadTakePrompt').value || '').trim();
    const notes = (document.getElementById('uploadTakeNotes').value || '').trim();
    const isMaster = document.getElementById('uploadTakeIsMaster').checked;

    const file = fileInput && fileInput.files ? fileInput.files[0] : null;
    if (!file) {
      showToast('Please select an audio file to upload.', 'error');
      return;
    }
    if (!/\.(mp3|m4a|wav)$/i.test(file.name)) {
      showToast('Unsupported file format. Only .mp3, .m4a, and .wav are allowed.', 'error');
      return;
    }
    if (file.size < 32) {
      showToast('The selected audio file is empty or too small.', 'error');
      return;
    }
    const maxUploadMb = Number(window.MP3MetaFixMaxUploadSizeMb) || 150;
    if (file.size > maxUploadMb * 1024 * 1024) {
      showToast(`File exceeds the ${maxUploadMb}MB upload limit.`, 'error');
      return;
    }

    const submitBtn = dom.formUploadTake ? dom.formUploadTake.querySelector('button[type="submit"]') : null;
    const originalBtnText = submitBtn ? submitBtn.textContent : 'Upload Take';
    state.isUploadingTake = true;
    if (submitBtn) {
      submitBtn.disabled = true;
      submitBtn.textContent = 'Uploading...';
    }

    const formData = new FormData();
    formData.append('file', file);
    if (label) formData.append('label', label);
    if (prompt) formData.append('prompt', prompt);
    if (notes) formData.append('notes', notes);
    formData.append('is_master', isMaster ? 'true' : 'false');

    showToast('Uploading audio take...', 'info', 2000);
    try {
      const res = await fetch(`/api/projects/tracks/${trackId}/takes`, {
        method: 'POST',
        body: formData,
      });

      if (res.ok) {
        state.expandedTrackIds.add(trackId);
        showToast('✨ Audio take uploaded successfully!', 'success');
        if (dom.modalUploadTake) dom.modalUploadTake.classList.add('hidden');
        if (dom.formUploadTake) dom.formUploadTake.reset();
        await fetchTracks();
        fetchQuota();
      } else {
        let detail = `Failed to upload take (HTTP ${res.status})`;
        try {
          const err = await res.json();
          if (err && err.detail) detail = err.detail;
        } catch (_) {
          // Preserve HTTP status detail for non-JSON error responses
        }
        showToast(detail, 'error');
      }
    } catch (err) {
      showToast('Network error uploading take', 'error');
    } finally {
      state.isUploadingTake = false;
      if (submitBtn) {
        submitBtn.disabled = false;
        submitBtn.textContent = originalBtnText;
      }
    }
  }

  function autoDetectStemRoleFromFilename(filename) {
    if (!filename) return null;
    const lower = filename.toLowerCase();
    if (lower.includes('lead vocal') || lower.includes('lead_vocal') || lower.includes('lead_vox') || lower.includes('lead vocals') || lower.includes('lead_voc') || lower.includes('0 lead') || lower.includes('lead-vocal') || lower.includes('lead vox')) return 'lead_vocals';
    if (lower.includes('backing vocal') || lower.includes('backing_vocal') || lower.includes('backing vocals') || lower.includes('bgv') || lower.includes('harmonies') || lower.includes('1 backing') || lower.includes('backing-vocal') || lower.includes('bkg vocal')) return 'backing_vocals';
    if (lower.includes('vocal') || lower.includes('vox') || lower.includes('voice') || lower.includes('acapella') || lower.includes('acappella')) return 'vocals';
    if (lower.includes('percussion') || lower.includes('perc')) return 'percussion';
    if (lower.includes('drum') || lower.includes('beat') || lower.includes('snare') || lower.includes('kick')) return 'drums';
    if (lower.includes('bass')) return 'bass';
    if (lower.includes('acoustic guitar') || lower.includes('acoustic_guitar') || lower.includes('acoustic-guitar')) return 'acoustic_guitar';
    if (lower.includes('electric guitar') || lower.includes('electric_guitar') || lower.includes('electric-guitar')) return 'electric_guitar';
    if (lower.includes('guitar') || lower.includes('gtr')) return 'guitar';
    if (lower.includes('piano')) return 'piano';
    if (lower.includes('keyboard') || lower.includes('keys') || lower.includes('rhodes') || lower.includes('organ') || lower.includes('clav')) return 'keyboard';
    if (lower.includes('string') || lower.includes('violin') || lower.includes('cello') || lower.includes('viola')) return 'strings';
    if (lower.includes('brass') || lower.includes('horn') || lower.includes('trumpet') || lower.includes('trombone') || lower.includes('sax')) return 'brass';
    if (lower.includes('woodwind') || lower.includes('flute') || lower.includes('clarinet') || lower.includes('oboe')) return 'woodwinds';
    if (lower.includes('synth') || lower.includes('pad') || lower.includes('lead_synth') || lower.includes('lead synth')) return 'synth';
    if (lower.includes('fx') || lower.includes('effect') || lower.includes('sfx') || lower.includes('ambience') || lower.includes('sound fx')) return 'fx';
    if (lower.includes('instrumental') || lower.includes('inst')) return 'instrumental';
    if (lower.includes('other') || lower.includes('misc') || lower.includes('rest')) return 'other';
    return null;
  }

  // --- Upload Stem ---
  async function handleUploadStemSubmit(e) {
    e.preventDefault();
    const trackId = dom.uploadStemTrackId.value;
    const takeId = dom.uploadStemTakeId.value;
    const fileInput = dom.uploadStemFileInput || document.getElementById('uploadStemFileInput');
    const role = (dom.uploadStemRole ? dom.uploadStemRole.value : document.getElementById('uploadStemRole')?.value) || 'other';

    if (!fileInput.files || fileInput.files.length === 0) {
      showToast('Please select a stem audio file', 'error');
      return;
    }

    const formData = new FormData();
    formData.append('file', fileInput.files[0]);
    formData.append('role', role);

    showToast('Uploading separated stem...', 'info', 2000);
    try {
      const res = await fetch(`/api/projects/tracks/${trackId}/takes/${takeId}/stems`, {
        method: 'POST',
        body: formData,
      });

      if (res.ok) {
        state.expandedTrackIds.add(trackId);
        showToast('✨ Stem uploaded successfully!', 'success');
        if (dom.modalUploadStem) dom.modalUploadStem.classList.add('hidden');
        fetchTracks();
        fetchQuota();
      } else {
        const err = await res.json();
        showToast(err.detail || 'Failed to upload stem', 'error');
      }
    } catch (e) {
      showToast('Network error uploading stem', 'error');
    }
  }

  // --- Stems Manager Controller State & Logic ---
  const stemsState = {
    selectedTrackId: null,
    selectedTakeId: null,
    stagedFiles: [],
    audioElements: new Map(), // stemId -> Audio
    channelStates: new Map(), // stemId -> { isMuted: false, isSolo: false, volume: 1.0 }
    isPlaying: false,
    masterVolume: 1.0,
    duration: 0,
    isUpdatingSeek: false,
    animFrame: null,
  };

  async function initStemsManagerView(targetTrackId, targetTakeId) {
    if (!state.tracks || state.tracks.length === 0) {
      await fetchTracks();
    }
    populateStemsTrackSelect(targetTrackId, targetTakeId);
  }

  function populateStemsTrackSelect(preferredTrackId, preferredTakeId) {
    if (!dom.stemsTrackSelect) return;
    dom.stemsTrackSelect.innerHTML = '<option value="">Select Track Workspace...</option>';

    (state.tracks || []).forEach(t => {
      const opt = document.createElement('option');
      opt.value = t.id;
      opt.textContent = `${t.title || 'Untitled Track'} (${t.takes_count || 0} takes)`;
      dom.stemsTrackSelect.appendChild(opt);
    });

    if (preferredTrackId && state.tracks.some(t => t.id === preferredTrackId)) {
      dom.stemsTrackSelect.value = preferredTrackId;
    } else if (state.tracks && state.tracks.length > 0) {
      dom.stemsTrackSelect.value = state.tracks[0].id;
    }

    onStemsTrackChanged(preferredTakeId);
  }

  async function onStemsTrackChanged(preferredTakeId) {
    const trackId = dom.stemsTrackSelect ? dom.stemsTrackSelect.value : null;
    stemsState.selectedTrackId = trackId;

    if (!dom.stemsTakeSelect) return;
    dom.stemsTakeSelect.innerHTML = '<option value="">Select Take...</option>';

    if (!trackId) {
      renderStemsMixer(null, null);
      return;
    }

    try {
      const res = await fetch(`/api/projects/tracks/${trackId}`);
      if (!res.ok) throw new Error('Failed to load track details');
      const trackDetail = await res.json();
      const takes = trackDetail.takes || [];

      takes.forEach(take => {
        const opt = document.createElement('option');
        opt.value = take.id;
        const stemList = take.stems ? (Array.isArray(take.stems) ? take.stems : Object.values(take.stems)) : [];
        const stemCount = stemList.length;
        opt.textContent = `${take.label || 'Take'} ${take.is_master ? '★ MASTER' : ''} (${stemCount} stem${stemCount === 1 ? '' : 's'})`;
        dom.stemsTakeSelect.appendChild(opt);
      });

      let chosenTakeId = null;
      if (preferredTakeId && takes.some(t => t.id === preferredTakeId)) {
        chosenTakeId = preferredTakeId;
      } else if (takes.length > 0) {
        // Prioritize take with stems if master has no stems
        const hasStems = (t) => t.stems && (Array.isArray(t.stems) ? t.stems.length > 0 : Object.keys(t.stems).length > 0);
        const takeWithStems = takes.find(t => t.is_master && hasStems(t))
          || takes.find(t => hasStems(t))
          || takes.find(t => t.is_master)
          || takes[0];
        chosenTakeId = takeWithStems ? takeWithStems.id : takes[0].id;
      }

      dom.stemsTakeSelect.value = chosenTakeId || '';
      stemsState.selectedTakeId = chosenTakeId;

      const activeTake = takes.find(t => t.id === chosenTakeId);
      renderStemsMixer(trackDetail.track || trackDetail, activeTake);
    } catch (err) {
      showToast('Error loading takes for stems', 'error');
    }
  }

  function renderStemsMixer(track, take) {
    stopAllStems();
    if (!dom.stemsChannelsList) return;
    dom.stemsChannelsList.innerHTML = '';

    const trackObj = (track && track.track) ? track.track : track;
    const trackId = (trackObj && trackObj.id) || stemsState.selectedTrackId;
    const stems = take && take.stems ? (Array.isArray(take.stems) ? take.stems : Object.values(take.stems)) : [];

    if (dom.btnDownloadStemPack) {
      dom.btnDownloadStemPack.disabled = stems.length === 0;
    }

    if (stems.length === 0) {
      if (dom.stemsMixerSection) dom.stemsMixerSection.classList.add('hidden');
      if (dom.stemsEmptyState) dom.stemsEmptyState.classList.remove('hidden');
      return;
    }

    if (dom.stemsMixerSection) dom.stemsMixerSection.classList.remove('hidden');
    if (dom.stemsEmptyState) dom.stemsEmptyState.classList.add('hidden');

    stems.forEach(stem => {
      stemsState.channelStates.set(stem.id, { isMuted: false, isSolo: false, volume: 1.0 });

      // Create audio element for stem
      const audio = new Audio(`/api/projects/tracks/${trackId}/takes/${take.id}/stems/${stem.id}/stream`);
      audio.preload = 'metadata';
      audio.addEventListener('loadedmetadata', () => {
        if (audio.duration && audio.duration > stemsState.duration) {
          stemsState.duration = audio.duration;
          updateStemsMasterTimeDisplay(0, stemsState.duration);
        }
      });
      audio.addEventListener('ended', () => {
        stemsState.isPlaying = false;
        if (dom.btnMasterStemPlay) dom.btnMasterStemPlay.textContent = '▶';
      });
      stemsState.audioElements.set(stem.id, audio);

      // Create Channel Card UI
      const card = document.createElement('div');
      card.className = 'stem-channel-card';
      card.id = `stemCard_${stem.id}`;

      // Left Info
      const left = document.createElement('div');
      left.className = 'stem-channel-left';

      const roleBadge = document.createElement('span');
      roleBadge.className = 'stem-channel-role';
      roleBadge.textContent = (stem.role || 'other').replace('_', ' ').toUpperCase();
      left.appendChild(roleBadge);

      const meta = document.createElement('div');
      meta.className = 'stem-channel-meta';

      const nameEl = document.createElement('span');
      nameEl.className = 'stem-channel-name';
      nameEl.textContent = stem.filename || `stem_${stem.role}`;
      meta.appendChild(nameEl);

      const sizeEl = document.createElement('span');
      sizeEl.className = 'stem-channel-filesize';
      const mb = (stem.size_bytes / (1024 * 1024)).toFixed(1);
      sizeEl.textContent = `${mb} MB • ${(stem.format || '').toUpperCase().replace('.', '')}`;
      meta.appendChild(sizeEl);

      left.appendChild(meta);
      card.appendChild(left);

      // Controls
      const controls = document.createElement('div');
      controls.className = 'stem-channel-controls';

      // Mute button
      const muteBtn = document.createElement('button');
      muteBtn.type = 'button';
      muteBtn.className = 'btn-stem-mute';
      muteBtn.textContent = 'M';
      muteBtn.title = 'Mute Stem';
      muteBtn.addEventListener('click', () => {
        const st = stemsState.channelStates.get(stem.id);
        if (!st) return;
        st.isMuted = !st.isMuted;
        muteBtn.classList.toggle('active', st.isMuted);
        card.classList.toggle('muted', st.isMuted);
        applyStemAudibility();
      });
      controls.appendChild(muteBtn);

      // Solo button
      const soloBtn = document.createElement('button');
      soloBtn.type = 'button';
      soloBtn.className = 'btn-stem-solo';
      soloBtn.textContent = 'S';
      soloBtn.title = 'Solo Stem';
      soloBtn.addEventListener('click', () => {
        const st = stemsState.channelStates.get(stem.id);
        if (!st) return;
        st.isSolo = !st.isSolo;
        soloBtn.classList.toggle('active', st.isSolo);
        applyStemAudibility();
      });
      controls.appendChild(soloBtn);

      // Volume slider
      const volSlider = document.createElement('input');
      volSlider.type = 'range';
      volSlider.className = 'stem-channel-vol';
      volSlider.min = '0';
      volSlider.max = '1';
      volSlider.step = '0.05';
      volSlider.value = '1';
      volSlider.title = 'Stem Volume';
      volSlider.addEventListener('input', (e) => {
        const st = stemsState.channelStates.get(stem.id);
        if (!st) return;
        st.volume = parseFloat(e.target.value);
        applyStemAudibility();
      });
      controls.appendChild(volSlider);

      // Download button
      const dlBtn = document.createElement('a');
      dlBtn.className = 'btn-stem-icon';
      dlBtn.title = 'Download Stem';
      dlBtn.href = `/api/projects/tracks/${trackId}/takes/${take.id}/stems/${stem.id}/download`;
      dlBtn.download = stem.filename;
      dlBtn.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="14" height="14"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"></path><polyline points="7 10 12 15 17 10"></polyline><line x1="12" y1="15" x2="12" y2="3"></line></svg>';
      controls.appendChild(dlBtn);

      // Delete button
      const delBtn = document.createElement('button');
      delBtn.type = 'button';
      delBtn.className = 'btn-stem-icon btn-danger';
      delBtn.title = 'Delete Stem';
      delBtn.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="14" height="14"><polyline points="3 6 5 6 21 6"></polyline><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path></svg>';
      delBtn.addEventListener('click', async () => {
        if (!confirm(`Delete stem "${stem.filename}"?`)) return;
        const existingAudio = stemsState.audioElements.get(stem.id);
        if (existingAudio) {
          existingAudio.pause();
          existingAudio.src = '';
          existingAudio.load();
          stemsState.audioElements.delete(stem.id);
          stemsState.channelStates.delete(stem.id);
        }
        try {
          const res = await fetch(`/api/projects/tracks/${trackId}/takes/${take.id}/stems/${stem.id}`, { method: 'DELETE' });
          if (res.ok) {
            showToast('Stem deleted', 'info');
            onStemsTrackChanged(take.id);
            fetchTracks();
            fetchQuota();
          } else {
            showToast('Failed to delete stem', 'error');
          }
        } catch (e) {
          showToast('Network error deleting stem', 'error');
        }
      });
      controls.appendChild(delBtn);

      card.appendChild(controls);
      dom.stemsChannelsList.appendChild(card);
    });
  }

  function applyStemAudibility() {
    let hasSolo = false;
    for (const [, st] of stemsState.channelStates) {
      if (st.isSolo) {
        hasSolo = true;
        break;
      }
    }

    for (const [stemId, audio] of stemsState.audioElements) {
      const st = stemsState.channelStates.get(stemId);
      if (!st) continue;
      let audible = true;
      if (hasSolo) {
        audible = st.isSolo && !st.isMuted;
      } else {
        audible = !st.isMuted;
      }
      audio.volume = audible ? (st.volume * stemsState.masterVolume) : 0;
    }
  }

  function stopAllStems() {
    stemsState.isPlaying = false;
    if (dom.btnMasterStemPlay) dom.btnMasterStemPlay.textContent = '▶';
    for (const [, audio] of stemsState.audioElements) {
      audio.pause();
      audio.src = '';
      audio.load();
    }
    stemsState.audioElements.clear();
    stemsState.channelStates.clear();
    stemsState.duration = 0;
    if (stemsState.animFrame) {
      cancelAnimationFrame(stemsState.animFrame);
      stemsState.animFrame = null;
    }
  }

  function toggleMasterStemPlay() {
    if (stemsState.audioElements.size === 0) return;

    if (stemsState.isPlaying) {
      stemsState.isPlaying = false;
      if (dom.btnMasterStemPlay) dom.btnMasterStemPlay.textContent = '▶';
      for (const [, audio] of stemsState.audioElements) {
        audio.pause();
      }
    } else {
      stemsState.isPlaying = true;
      if (dom.btnMasterStemPlay) dom.btnMasterStemPlay.textContent = '❚❚';
      applyStemAudibility();
      for (const [, audio] of stemsState.audioElements) {
        audio.play().catch(() => {});
      }
      syncStemPlaybackLoop();
    }
  }

  function syncStemPlaybackLoop() {
    if (!stemsState.isPlaying) return;

    let maxTime = 0;
    for (const [, audio] of stemsState.audioElements) {
      if (audio.currentTime > maxTime) maxTime = audio.currentTime;
    }

    if (!stemsState.isUpdatingSeek && dom.stemsMasterSeek && stemsState.duration > 0) {
      dom.stemsMasterSeek.value = (maxTime / stemsState.duration) * 100;
      updateStemsMasterTimeDisplay(maxTime, stemsState.duration);
    }

    stemsState.animFrame = requestAnimationFrame(syncStemPlaybackLoop);
  }

  function updateStemsMasterTimeDisplay(curr, total) {
    if (!dom.stemsMasterTimeDisplay) return;
    const format = (s) => {
      const m = Math.floor(s / 60);
      const sec = Math.floor(s % 60);
      return `${m}:${sec.toString().padStart(2, '0')}`;
    };
    dom.stemsMasterTimeDisplay.textContent = `${format(curr)} / ${format(total)}`;
  }

  function handleStemsBatchFiles(files) {
    if (!files || files.length === 0) return;
    Array.from(files).forEach(file => {
      const detectedRole = autoDetectStemRoleFromFilename(file.name) || 'other';
      stemsState.stagedFiles.push({
        id: 'stg_' + Math.random().toString(36).substring(2, 9),
        file,
        name: file.name,
        size: file.size,
        role: detectedRole,
      });
    });
    renderStagedStemsList();
  }

  function renderStagedStemsList() {
    if (!dom.stemsStagingList || !dom.stemsStagingContainer) return;
    if (stemsState.stagedFiles.length === 0) {
      dom.stemsStagingContainer.classList.add('hidden');
      return;
    }

    dom.stemsStagingContainer.classList.remove('hidden');
    if (dom.stemsStagedCount) dom.stemsStagedCount.textContent = stemsState.stagedFiles.length;
    dom.stemsStagingList.innerHTML = '';

    const stemRolesList = [
      ['vocals', 'Vocals'],
      ['lead_vocals', 'Lead Vocals'],
      ['backing_vocals', 'Backing Vocals'],
      ['drums', 'Drums'],
      ['percussion', 'Percussion'],
      ['bass', 'Bass'],
      ['guitar', 'Guitar'],
      ['acoustic_guitar', 'Acoustic Guitar'],
      ['electric_guitar', 'Electric Guitar'],
      ['keyboard', 'Keyboard'],
      ['piano', 'Piano'],
      ['synth', 'Synth / Keys'],
      ['strings', 'Strings'],
      ['brass', 'Brass'],
      ['woodwinds', 'Woodwinds'],
      ['fx', 'FX / Ambience'],
      ['instrumental', 'Instrumental / Backing'],
      ['other', 'Other Stem'],
    ];

    stemsState.stagedFiles.forEach(stg => {
      const row = document.createElement('div');
      row.className = 'stems-staging-row';

      const info = document.createElement('div');
      info.className = 'stems-staging-file-info';

      const name = document.createElement('span');
      name.className = 'stems-staging-filename';
      name.textContent = stg.name;
      info.appendChild(name);

      const size = document.createElement('span');
      size.className = 'stems-staging-filesize';
      size.textContent = `${(stg.size / (1024 * 1024)).toFixed(1)} MB`;
      info.appendChild(size);

      row.appendChild(info);

      const right = document.createElement('div');
      right.style.display = 'flex';
      right.style.alignItems = 'center';
      right.style.gap = '0.5rem';

      const select = document.createElement('select');
      select.className = 'stems-staging-role-select';
      stemRolesList.forEach(([val, label]) => {
        const opt = document.createElement('option');
        opt.value = val;
        opt.textContent = label;
        if (stg.role === val) opt.selected = true;
        select.appendChild(opt);
      });
      select.addEventListener('change', (e) => {
        stg.role = e.target.value;
      });
      right.appendChild(select);

      const statusSpan = document.createElement('span');
      statusSpan.id = `stgStatus_${stg.id}`;
      statusSpan.className = 'stems-staging-row-status ready';
      statusSpan.textContent = 'Ready';
      right.appendChild(statusSpan);

      const remBtn = document.createElement('button');
      remBtn.type = 'button';
      remBtn.className = 'stems-staging-remove-btn';
      remBtn.textContent = '×';
      remBtn.title = 'Remove file';
      remBtn.addEventListener('click', () => {
        stemsState.stagedFiles = stemsState.stagedFiles.filter(item => item.id !== stg.id);
        renderStagedStemsList();
      });
      right.appendChild(remBtn);

      row.appendChild(right);
      dom.stemsStagingList.appendChild(row);
    });
  }

  async function uploadAllStagedStems() {
    const trackId = dom.stemsTrackSelect ? dom.stemsTrackSelect.value : null;
    const takeId = dom.stemsTakeSelect ? dom.stemsTakeSelect.value : null;

    if (!trackId || !takeId) {
      showToast('Please select a target track workspace and take first', 'error');
      return;
    }
    if (stemsState.stagedFiles.length === 0) {
      showToast('No stems staged for upload', 'error');
      return;
    }

    const total = stemsState.stagedFiles.length;
    if (dom.btnUploadAllStagedStems) {
      dom.btnUploadAllStagedStems.disabled = true;
      dom.btnUploadAllStagedStems.textContent = `Uploading (0/${total})...`;
    }
    if (dom.btnClearStagedStems) dom.btnClearStagedStems.disabled = true;
    if (dom.stemsUploadProgressBarContainer) dom.stemsUploadProgressBarContainer.classList.remove('hidden');
    if (dom.stemsUploadProgressBar) dom.stemsUploadProgressBar.style.width = '0%';

    let successCount = 0;
    for (let i = 0; i < stemsState.stagedFiles.length; i++) {
      const item = stemsState.stagedFiles[i];
      if (dom.btnUploadAllStagedStems) {
        dom.btnUploadAllStagedStems.textContent = `Uploading (${i + 1}/${total})...`;
      }
      const statusEl = document.getElementById(`stgStatus_${item.id}`);
      if (statusEl) {
        statusEl.className = 'stems-staging-row-status uploading';
        statusEl.textContent = '⏳ Uploading...';
      }

      const formData = new FormData();
      formData.append('file', item.file);
      formData.append('role', item.role);

      try {
        const res = await fetch(`/api/projects/tracks/${trackId}/takes/${takeId}/stems`, {
          method: 'POST',
          body: formData,
        });
        if (res.ok) {
          successCount++;
          if (statusEl) {
            statusEl.className = 'stems-staging-row-status success';
            statusEl.textContent = '✅ Uploaded';
          }
        } else {
          if (statusEl) {
            statusEl.className = 'stems-staging-row-status error';
            statusEl.textContent = '❌ Failed';
          }
        }
      } catch (e) {
        if (statusEl) {
          statusEl.className = 'stems-staging-row-status error';
          statusEl.textContent = '❌ Error';
        }
      }

      if (dom.stemsUploadProgressBar) {
        dom.stemsUploadProgressBar.style.width = `${Math.round(((i + 1) / total) * 100)}%`;
      }
    }

    if (successCount === total) {
      showToast(`✨ All ${total} stems uploaded successfully!`, 'success');
    } else {
      showToast(`Uploaded ${successCount} of ${total} stems`, 'info');
    }

    if (dom.btnUploadAllStagedStems) {
      dom.btnUploadAllStagedStems.disabled = false;
      dom.btnUploadAllStagedStems.textContent = 'Upload All Stems';
    }
    if (dom.btnClearStagedStems) dom.btnClearStagedStems.disabled = false;
    if (dom.stemsUploadProgressBarContainer) dom.stemsUploadProgressBarContainer.classList.add('hidden');
    if (dom.stemsUploadProgressBar) dom.stemsUploadProgressBar.style.width = '0%';

    stemsState.stagedFiles = [];
    renderStagedStemsList();
    if (dom.stemsBatchUploadSection) dom.stemsBatchUploadSection.classList.add('hidden');
    onStemsTrackChanged(takeId);
    fetchTracks();
    fetchQuota();
  }

  // --- Setup Events ---
  function setupEvents() {
    if (dom.btnNavTracks) dom.btnNavTracks.addEventListener('click', () => switchStudioView('tracks'));
    if (dom.btnNavAlbums) dom.btnNavAlbums.addEventListener('click', () => switchStudioView('albums'));
    if (dom.btnNavStems) dom.btnNavStems.addEventListener('click', () => switchStudioView('stems'));
    if (dom.btnNavEditor) dom.btnNavEditor.addEventListener('click', () => switchStudioView('editor'));

    // Stems Studio Controls
    if (dom.stemsTrackSelect) {
      dom.stemsTrackSelect.addEventListener('change', () => onStemsTrackChanged());
    }
    if (dom.stemsTakeSelect) {
      dom.stemsTakeSelect.addEventListener('change', () => {
        const takeId = dom.stemsTakeSelect.value;
        stemsState.selectedTakeId = takeId;
        const trackId = stemsState.selectedTrackId || (dom.stemsTrackSelect ? dom.stemsTrackSelect.value : null);
        if (trackId) {
          fetch(`/api/projects/tracks/${trackId}`)
            .then(res => res.json())
            .then(td => {
              const activeTake = (td.takes || []).find(t => t.id === takeId);
              renderStemsMixer(td.track || td, activeTake);
            });
        }
      });
    }

    if (dom.btnToggleBatchUpload) {
      dom.btnToggleBatchUpload.addEventListener('click', () => {
        if (dom.stemsBatchUploadSection) {
          dom.stemsBatchUploadSection.classList.toggle('hidden');
        }
      });
    }

    if (dom.btnDownloadStemPack) {
      dom.btnDownloadStemPack.addEventListener('click', () => {
        const trackId = stemsState.selectedTrackId;
        const takeId = stemsState.selectedTakeId;
        if (!trackId || !takeId) return;
        window.location.href = `/api/projects/tracks/${trackId}/takes/${takeId}/stems/download-pack`;
      });
    }

    // Batch Dropzone & File Input
    if (dom.stemsBatchDropzone) {
      dom.stemsBatchDropzone.addEventListener('click', () => {
        if (dom.stemsBatchFileInput) dom.stemsBatchFileInput.click();
      });
      dom.stemsBatchDropzone.addEventListener('dragover', (e) => {
        e.preventDefault();
        dom.stemsBatchDropzone.classList.add('dragover');
      });
      dom.stemsBatchDropzone.addEventListener('dragleave', () => {
        dom.stemsBatchDropzone.classList.remove('dragover');
      });
      dom.stemsBatchDropzone.addEventListener('drop', (e) => {
        e.preventDefault();
        dom.stemsBatchDropzone.classList.remove('dragover');
        if (e.dataTransfer && e.dataTransfer.files) {
          handleStemsBatchFiles(e.dataTransfer.files);
        }
      });
    }
    if (dom.stemsBatchFileInput) {
      dom.stemsBatchFileInput.addEventListener('change', (e) => {
        if (e.target.files) {
          handleStemsBatchFiles(e.target.files);
        }
      });
    }

    if (dom.btnClearStagedStems) {
      dom.btnClearStagedStems.addEventListener('click', () => {
        stemsState.stagedFiles = [];
        renderStagedStemsList();
      });
    }
    if (dom.btnUploadAllStagedStems) {
      dom.btnUploadAllStagedStems.addEventListener('click', uploadAllStagedStems);
    }

    // Master Stem Transport
    if (dom.btnMasterStemPlay) {
      dom.btnMasterStemPlay.addEventListener('click', toggleMasterStemPlay);
    }
    if (dom.stemsMasterSeek) {
      dom.stemsMasterSeek.addEventListener('input', (e) => {
        stemsState.isUpdatingSeek = true;
        const pct = parseFloat(e.target.value);
        const targetSec = (pct / 100) * stemsState.duration;
        updateStemsMasterTimeDisplay(targetSec, stemsState.duration);
      });
      dom.stemsMasterSeek.addEventListener('change', (e) => {
        stemsState.isUpdatingSeek = false;
        const pct = parseFloat(e.target.value);
        const targetSec = (pct / 100) * stemsState.duration;
        for (const [, audio] of stemsState.audioElements) {
          audio.currentTime = targetSec;
        }
      });
    }
    if (dom.stemsMasterVolume) {
      dom.stemsMasterVolume.addEventListener('input', (e) => {
        stemsState.masterVolume = parseFloat(e.target.value);
        applyStemAudibility();
      });
    }
    if (dom.btnResetStemMixer) {
      dom.btnResetStemMixer.addEventListener('click', () => {
        for (const [stemId, st] of stemsState.channelStates) {
          st.isMuted = false;
          st.isSolo = false;
          st.volume = 1.0;
          const card = document.getElementById(`stemCard_${stemId}`);
          if (card) {
            card.classList.remove('muted');
            card.querySelectorAll('.btn-stem-mute, .btn-stem-solo').forEach(b => b.classList.remove('active'));
            const vol = card.querySelector('.stem-channel-vol');
            if (vol) vol.value = '1';
          }
        }
        applyStemAudibility();
      });
    }

    if (dom.btnRefreshTracks) dom.btnRefreshTracks.addEventListener('click', () => {
      showToast('Refreshing track workspaces...', 'info', 1000);
      fetchTracks();
      fetchQuota();
    });

    if (dom.btnRefreshAlbums) dom.btnRefreshAlbums.addEventListener('click', () => {
      showToast('Refreshing album projects...', 'info', 1000);
      fetchAlbums();
      fetchQuota();
    });

    if (dom.btnNewTrack) dom.btnNewTrack.addEventListener('click', () => {
      if (dom.modalNewTrack) dom.modalNewTrack.classList.remove('hidden');
    });
    if (dom.btnEmptyNewTrack) dom.btnEmptyNewTrack.addEventListener('click', () => {
      if (dom.modalNewTrack) dom.modalNewTrack.classList.remove('hidden');
    });

    if (dom.btnNewAlbum) dom.btnNewAlbum.addEventListener('click', () => {
      if (dom.modalNewAlbum) dom.modalNewAlbum.classList.remove('hidden');
    });
    if (dom.btnEmptyNewAlbum) dom.btnEmptyNewAlbum.addEventListener('click', () => {
      if (dom.modalNewAlbum) dom.modalNewAlbum.classList.remove('hidden');
    });

    // Search inputs
    if (dom.tracksSearchInput) {
      dom.tracksSearchInput.addEventListener('input', (e) => {
        state.trackSearch = e.target.value;
        renderTracksList();
      });
    }
    if (dom.albumsSearchInput) {
      dom.albumsSearchInput.addEventListener('input', (e) => {
        state.albumSearch = e.target.value;
        renderAlbumsList();
      });
    }

    // Filter pills
    if (dom.tracksFilterPills) {
      dom.tracksFilterPills.forEach(pill => {
        pill.addEventListener('click', () => {
          dom.tracksFilterPills.forEach(p => p.classList.remove('active'));
          pill.classList.add('active');
          state.trackFilter = pill.dataset.filter || 'all';
          renderTracksList();
        });
      });
    }
    if (dom.albumsFilterPills) {
      dom.albumsFilterPills.forEach(pill => {
        pill.addEventListener('click', () => {
          dom.albumsFilterPills.forEach(p => p.classList.remove('active'));
          pill.classList.add('active');
          state.albumFilter = pill.dataset.filter || 'all';
          renderAlbumsList();
        });
      });
    }

    // Save to Project Trigger in Editor
    if (dom.btnSaveToProject) dom.btnSaveToProject.addEventListener('click', openSaveToProjectModal);
    if (dom.radioSaveUpdateTake) dom.radioSaveUpdateTake.addEventListener('change', updateSaveTargetModeUI);
    if (dom.radioSaveNewTrack) dom.radioSaveNewTrack.addEventListener('change', updateSaveTargetModeUI);
    if (dom.radioSaveExistingTrack) dom.radioSaveExistingTrack.addEventListener('change', updateSaveTargetModeUI);
    if (dom.formSaveToProject) dom.formSaveToProject.addEventListener('submit', handleSaveToProjectSubmit);

    // Forms
    if (dom.formNewTrack) dom.formNewTrack.addEventListener('submit', handleNewTrackSubmit);
    if (dom.formUploadTake) dom.formUploadTake.addEventListener('submit', handleUploadTakeSubmit);
    if (dom.formNewAlbum) dom.formNewAlbum.addEventListener('submit', handleNewAlbumSubmit);
    if (dom.formUploadStem) dom.formUploadStem.addEventListener('submit', handleUploadStemSubmit);
    if (dom.uploadStemFileInput) {
      dom.uploadStemFileInput.addEventListener('change', (e) => {
        if (e.target.files && e.target.files.length > 0) {
          const detectedRole = autoDetectStemRoleFromFilename(e.target.files[0].name);
          if (detectedRole && dom.uploadStemRole) {
            dom.uploadStemRole.value = detectedRole;
          }
        }
      });
    }

    // Album Sequencer & Artwork
    if (dom.btnAddTrackToSequencer) dom.btnAddTrackToSequencer.addEventListener('click', () => openPickTrackModal(null));
    if (dom.selectPickTrack) {
      dom.selectPickTrack.addEventListener('change', (e) => {
        loadTakesForPickTrackModal(e.target.value);
      });
    }
    if (dom.btnConfirmAddTrackToAlbum) dom.btnConfirmAddTrackToAlbum.addEventListener('click', confirmAddTrackToAlbum);
    if (dom.btnConfirmRefreshAlbumEntry) dom.btnConfirmRefreshAlbumEntry.addEventListener('click', confirmRefreshAlbumEntry);
    if (dom.btnFinalizeAlbum) dom.btnFinalizeAlbum.addEventListener('click', handleFinalizeAlbum);
    if (dom.btnSaveAlbumDetails) dom.btnSaveAlbumDetails.addEventListener('click', saveAlbumChanges);
    if (dom.inputAlbumCoverFile) {
      dom.inputAlbumCoverFile.addEventListener('change', (e) => {
        if (e.target.files && e.target.files.length > 0) {
          handleAlbumCoverUpload(e.target.files[0]);
        }
      });
    }

    // Close Modals
    document.querySelectorAll('[data-close-modal]').forEach(btn => {
      btn.addEventListener('click', () => {
        const modalId = btn.dataset.closeModal;
        if (modalId === 'modalPickTrackForAlbum') {
          closePickTrackModal();
          return;
        }
        if (modalId === 'modalRefreshAlbumEntry') {
          closeRefreshModal();
          return;
        }
        if (modalId === 'modalAlbumDetail') {
          closeRefreshModal();
        }
        const modal = document.getElementById(modalId);
        if (modal) modal.classList.add('hidden');
      });
    });

    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') {
        if (dom.modalPickTrackForAlbum && !dom.modalPickTrackForAlbum.classList.contains('hidden')) {
          closePickTrackModal();
        }
        if (dom.modalRefreshAlbumEntry && !dom.modalRefreshAlbumEntry.classList.contains('hidden')) {
          closeRefreshModal();
        }
        if (dom.modalAlbumDetail && !dom.modalAlbumDetail.classList.contains('hidden')) {
          closeRefreshModal();
        }
        [
          dom.modalNewTrack,
          dom.modalUploadTake,
          dom.modalUploadStem,
          dom.modalNewAlbum,
          dom.modalAlbumDetail,
          dom.modalRefreshAlbumEntry,
          dom.modalSaveToProject,
        ].forEach(m => {
          if (m && !m.classList.contains('hidden')) m.classList.add('hidden');
        });
      }
    });
  }

  // --- Init ---
  document.addEventListener('DOMContentLoaded', () => {
    initDom();
    setupEvents();
    fetchQuota();
    fetchTracks();
    fetchAlbums();
  });
})();
