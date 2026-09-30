/**
 * MP3MetaManager Storage & Track Workspace Subsystem
 * Handles persistent Track Workspaces, Takes, Stems, and Quota Telemetry.
 */

(function () {
  'use strict';

  // --- State ---
  const storageState = {
    tracks: [],
    currentFilter: 'all',
    searchQuery: '',
    quota: null,
    activeTrackId: null,
    activeTakeId: null,
  };

  // --- DOM Elements Cache ---
  const dom = {};

  function initDom() {
    dom.panelAppView = document.getElementById('panelAppView');
    dom.panelLyricsView = document.getElementById('panelLyricsView');
    dom.panelStorageView = document.getElementById('panelStorageView');
    dom.panelStemsView = document.getElementById('panelStemsView');

    dom.navItemApp = document.getElementById('navItemApp');
    dom.navItemLyrics = document.getElementById('navItemLyrics');
    dom.navItemStorage = document.getElementById('navItemStorage');
    dom.navItemStems = document.getElementById('navItemStems');
    dom.navStorageCount = document.getElementById('navStorageCount');

    dom.btnNavApp = document.getElementById('btnNavApp');
    dom.btnNavLyrics = document.getElementById('btnNavLyrics');
    dom.btnNavStorage = document.getElementById('btnNavStorage');
    dom.btnNavStems = document.getElementById('btnNavStems');

    // Quota indicator in sidebar
    dom.sidebarQuotaContainer = document.getElementById('sidebarQuotaContainer');
    dom.sidebarQuotaText = document.getElementById('sidebarQuotaText');
    dom.sidebarQuotaBar = document.getElementById('sidebarQuotaBar');

    // Stems Manager Panel Controls
    dom.stemsTrackSelect = document.getElementById('stemsTrackSelect');
    dom.stemsTakeSelect = document.getElementById('stemsTakeSelect');
    dom.btnToggleBatchUpload = document.getElementById('btnToggleBatchUpload');
    dom.btnDownloadStemPack = document.getElementById('btnDownloadStemPack');
    dom.stemsBatchUploadSection = document.getElementById('stemsBatchUploadSection');
    dom.stemsBatchDropzone = document.getElementById('stemsBatchDropzone');
    dom.stemsBatchFileInput = document.getElementById('stemsBatchFileInput');
    dom.stemsStagingContainer = document.getElementById('stemsStagingContainer');
    dom.stemsStagedCount = document.getElementById('stemsStagedCount');
    dom.stemsStagingList = document.getElementById('stemsStagingList');
    dom.btnClearStagedStems = document.getElementById('btnClearStagedStems');
    dom.btnUploadAllStagedStems = document.getElementById('btnUploadAllStagedStems');
    dom.stemsMixerSection = document.getElementById('stemsMixerSection');
    dom.btnMasterStemPlay = document.getElementById('btnMasterStemPlay');
    dom.stemsMasterSeek = document.getElementById('stemsMasterSeek');
    dom.stemsMasterTimeDisplay = document.getElementById('stemsMasterTimeDisplay');
    dom.stemsMasterVolume = document.getElementById('stemsMasterVolume');
    dom.stemsChannelsList = document.getElementById('stemsChannelsList');
    dom.stemsEmptyState = document.getElementById('stemsEmptyState');

    // Storage Panel Controls
    dom.storageSearchInput = document.getElementById('storageSearchInput');
    dom.storageFilterPills = document.querySelectorAll('.storage-filter-pill');
    dom.tracksListContainer = document.getElementById('storageTracksContainer');
    dom.storageEmptyState = document.getElementById('storageEmptyState');
    dom.btnNewTrack = document.getElementById('btnNewTrack');
    dom.btnRefreshStorage = document.getElementById('btnRefreshStorage');

    // Save To Project Modal
    dom.modalSaveToProject = document.getElementById('modalSaveToProject');
    dom.btnSaveToProject = document.getElementById('btnSaveToProject');
    dom.formSaveToProject = document.getElementById('formSaveToProject');
    dom.selectSaveTargetTrack = document.getElementById('selectSaveTargetTrack');
    dom.saveProjectNewTrackFields = document.getElementById('saveProjectNewTrackFields');
    dom.radioSaveNewTrack = document.getElementById('radioSaveNewTrack');
    dom.radioSaveExistingTrack = document.getElementById('radioSaveExistingTrack');
    dom.radioSaveUpdateTake = document.getElementById('radioSaveUpdateTake');
    dom.labelRadioSaveUpdateTake = document.getElementById('labelRadioSaveUpdateTake');
    dom.saveProjectUpdateGroup = document.getElementById('saveProjectUpdateGroup');
    dom.saveProjectUpdateNoticeText = document.getElementById('saveProjectUpdateNoticeText');

    // New Track Modal
    dom.modalNewTrack = document.getElementById('modalNewTrack');
    dom.formNewTrack = document.getElementById('formNewTrack');

    // Upload Take Modal
    dom.modalUploadTake = document.getElementById('modalUploadTake');
    dom.formUploadTake = document.getElementById('formUploadTake');
    dom.uploadTakeTrackId = document.getElementById('uploadTakeTrackId');
    dom.uploadTakeTitleDisplay = document.getElementById('uploadTakeTitleDisplay');

    // Upload Stem Modal
    dom.modalUploadStem = document.getElementById('modalUploadStem');
    dom.formUploadStem = document.getElementById('formUploadStem');
    dom.uploadStemTrackId = document.getElementById('uploadStemTrackId');
    dom.uploadStemTakeId = document.getElementById('uploadStemTakeId');
    dom.uploadStemFileInput = document.getElementById('uploadStemFileInput');
    dom.uploadStemRole = document.getElementById('uploadStemRole');
  }

  // --- View Switcher ---
  function switchView(viewName, optTrackId, optTakeId) {
    if (!dom.panelAppView || !dom.panelLyricsView || !dom.panelStorageView) return;

    // Remove active class from all sidebar nav items
    [dom.navItemApp, dom.navItemLyrics, dom.navItemStorage, dom.navItemStems].forEach(item => {
      if (item) item.classList.remove('active');
    });

    // Hide all main panels
    [dom.panelAppView, dom.panelLyricsView, dom.panelStorageView, dom.panelStemsView].forEach(panel => {
      if (panel) panel.classList.add('hidden');
    });

    if (viewName === 'storage') {
      dom.panelStorageView.classList.remove('hidden');
      if (dom.navItemStorage) dom.navItemStorage.classList.add('active');
      fetchStorageData();
    } else if (viewName === 'stems') {
      if (dom.panelStemsView) dom.panelStemsView.classList.remove('hidden');
      if (dom.navItemStems) dom.navItemStems.classList.add('active');
      initStemsManagerView(optTrackId, optTakeId);
    } else if (viewName === 'lyrics') {
      dom.panelLyricsView.classList.remove('hidden');
      if (dom.navItemLyrics) dom.navItemLyrics.classList.add('active');
      if (typeof window.syncTrackState === 'function') window.syncTrackState();
      if (typeof window.renderLyricsQueue === 'function') window.renderLyricsQueue();
    } else {
      dom.panelAppView.classList.remove('hidden');
      if (dom.navItemApp) dom.navItemApp.classList.add('active');
    }
  }

  // Make globally available so lyrics.js and app.js can switch seamlessly
  window.switchManagerView = switchView;

  // --- Toast Helper ---
  function showToast(msg, type = 'info', duration = 3000) {
    if (typeof window.showToast === 'function') {
      window.showToast(msg, type, duration);
    }
  }

  // --- API Calls ---

  async function fetchQuota() {
    try {
      const res = await fetch('/api/storage/quota');
      if (res.ok) {
        const data = await res.json();
        storageState.quota = data;
        renderQuota(data);
      }
    } catch (e) {
      console.warn('Could not fetch storage quota:', e);
    }
  }

  async function fetchStorageData() {
    await fetchQuota();
    try {
      const res = await fetch('/api/projects/tracks');
      if (res.ok) {
        const tracks = await res.json();
        storageState.tracks = tracks;
        if (dom.navStorageCount) {
          dom.navStorageCount.textContent = String(tracks.length);
        }
        renderTrackList();
      } else if (res.status === 401) {
        showToast('Please log in to view project storage', 'error');
      }
    } catch (e) {
      console.error('Error fetching tracks:', e);
      showToast('Network error loading track workspaces', 'error');
    }
  }

  // --- Render Quota Indicator ---
  function renderQuota(quota) {
    if (!dom.sidebarQuotaContainer || !quota) return;
    dom.sidebarQuotaContainer.classList.remove('hidden');

    const usedMb = quota.used_mb || 0;
    const maxMb = quota.max_quota_mb || 2048;
    const pct = Math.min(100, Math.max(0, quota.used_percent || 0));

    if (dom.sidebarQuotaText) {
      dom.sidebarQuotaText.textContent = `${usedMb.toFixed(1)} MB / ${maxMb} MB`;
    }
    if (dom.sidebarQuotaBar) {
      dom.sidebarQuotaBar.style.width = `${pct}%`;
      dom.sidebarQuotaBar.className = 'quota-bar-fill';
      if (pct > 90) {
        dom.sidebarQuotaBar.classList.add('quota-danger');
      } else if (pct > 75) {
        dom.sidebarQuotaBar.classList.add('quota-warning');
      } else {
        dom.sidebarQuotaBar.classList.add('quota-normal');
      }
    }
  }

  // --- Render Track List ---
  function renderTrackList() {
    if (!dom.tracksListContainer) return;
    dom.tracksListContainer.innerHTML = '';

    const query = storageState.searchQuery.toLowerCase().trim();
    const filter = storageState.currentFilter;

    const filtered = storageState.tracks.filter(t => {
      if (filter !== 'all' && t.status !== filter) return false;
      if (query) {
        const matchTitle = (t.title || '').toLowerCase().includes(query);
        const matchArtist = (t.artist || '').toLowerCase().includes(query);
        const matchNotes = (t.notes || '').toLowerCase().includes(query);
        return matchTitle || matchArtist || matchNotes;
      }
      return true;
    });

    if (filtered.length === 0) {
      if (dom.storageEmptyState) dom.storageEmptyState.classList.remove('hidden');
      return;
    }

    if (dom.storageEmptyState) dom.storageEmptyState.classList.add('hidden');

    filtered.forEach(track => {
      const card = createTrackCardElement(track);
      dom.tracksListContainer.appendChild(card);
    });
  }

  // --- Safe DOM Construction for Track Cards ---
  function createTrackCardElement(track) {
    const card = document.createElement('div');
    card.className = 'track-workspace-card';
    card.dataset.trackId = track.id;

    // Card Header Row
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

    // Status Badge
    const statusBadge = document.createElement('span');
    statusBadge.className = `status-pill status-${track.status || 'draft'}`;
    statusBadge.textContent = formatStatusLabel(track.status);
    titleRow.appendChild(statusBadge);

    infoStack.appendChild(titleRow);

    // Meta details (Artist, BPM, Key, Takes count)
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

    // Header Actions
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

    // Notes Row (if any)
    if (track.notes) {
      const notesEl = document.createElement('div');
      notesEl.className = 'track-card-notes';
      notesEl.textContent = `📝 ${track.notes}`;
      card.appendChild(notesEl);
    }

    // Expandable Takes Drawer
    const takesDrawer = document.createElement('div');
    takesDrawer.className = 'takes-drawer hidden';
    card.appendChild(takesDrawer);

    btnExpand.addEventListener('click', async () => {
      const isHidden = takesDrawer.classList.contains('hidden');
      if (isHidden) {
        takesDrawer.classList.remove('hidden');
        btnExpand.classList.add('expanded');
        takesDrawer.innerHTML = '<div class="takes-loading-spinner">Loading takes & stems...</div>';
        await loadAndRenderTakes(track.id, takesDrawer);
      } else {
        takesDrawer.classList.add('hidden');
        btnExpand.classList.remove('expanded');
      }
    });

    return card;
  }

  function formatStatusLabel(status) {
    switch (status) {
      case 'in_progress': return 'In Progress';
      case 'completed': return 'Completed';
      case 'archived': return 'Archived';
      case 'draft':
      default: return 'Draft';
    }
  }

  // --- Load and Render Takes for a Track ---
  async function loadAndRenderTakes(trackId, drawerEl) {
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
        emptyTakes.textContent = 'No audio takes uploaded yet. Click "Add Take" above to upload your first take or recording.';
        drawerEl.appendChild(emptyTakes);
        return;
      }

      const takesList = document.createElement('div');
      takesList.className = 'takes-list-grid';

      takes.forEach(take => {
        const takeRow = createTakeRowElement(data.track, take);
        takesList.appendChild(takeRow);
      });

      drawerEl.appendChild(takesList);
    } catch (e) {
      drawerEl.innerHTML = '<div class="takes-error">Network error loading takes.</div>';
    }
  }

  // --- Safe DOM Construction for Take Rows ---
  function createTakeRowElement(track, take) {
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

    // Take Action Buttons
    const actions = document.createElement('div');
    actions.className = 'take-actions';

    // Open in MP3MetaFix Editor
    const btnOpenEditor = document.createElement('button');
    btnOpenEditor.className = 'btn btn-primary btn-sm';
    btnOpenEditor.title = 'Open this take in MP3MetaFix Editor';
    btnOpenEditor.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="13" height="13"><polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"></polygon></svg><span>Open Editor</span>';
    btnOpenEditor.addEventListener('click', () => loadTakeIntoSession(track.id, take.id, 'app'));
    actions.appendChild(btnOpenEditor);

    // Sync Lyrics in Tap-to-Sync Studio
    const btnOpenLyrics = document.createElement('button');
    btnOpenLyrics.className = 'btn btn-secondary btn-sm';
    btnOpenLyrics.title = 'Open this take in Synced Lyrics Karaoke Studio';
    btnOpenLyrics.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="13" height="13"><path d="M12 20h9"></path><path d="M16.5 3.5a2.121 2.121 0 0 1 3 3L7 19l-4 1 1-4L16.5 3.5z"></path></svg><span>Sync Lyrics</span>';
    btnOpenLyrics.addEventListener('click', () => loadTakeIntoSession(track.id, take.id, 'lyrics'));
    actions.appendChild(btnOpenLyrics);

    // Add Stem button
    const btnAddStem = document.createElement('button');
    btnAddStem.className = 'btn btn-secondary btn-sm';
    btnAddStem.title = 'Add separated stem file (Vocals, Inst, Bass, Drums)';
    btnAddStem.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="13" height="13"><line x1="12" y1="5" x2="12" y2="19"></line><line x1="5" y1="12" x2="19" y2="12"></line></svg><span>+ Stem</span>';
    btnAddStem.addEventListener('click', () => openUploadStemModal(track.id, take.id, take.label));
    actions.appendChild(btnAddStem);

    // Set as Master (if not master)
    if (!take.is_master) {
      const btnMakeMaster = document.createElement('button');
      btnMakeMaster.className = 'btn btn-secondary btn-sm';
      btnMakeMaster.title = 'Designate as master take';
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

    // Production Notes / Prompt Snippets (if any)
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

    // Stems Rack (if take has stems)
    const stems = take.stems ? Object.values(take.stems) : [];
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
      btnOpenInStems.addEventListener('click', () => {
        switchView('stems', track.id, take.id);
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
        roleSpan.textContent = stem.role.toUpperCase();
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

        // Stem Delete Button
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

  // --- Actions ---

  async function loadTakeIntoSession(trackId, takeId, targetView = 'app') {
    showToast('Loading take into editor...', 'info', 1500);
    try {
      const res = await fetch(`/api/projects/tracks/${trackId}/takes/${takeId}/load-session`, {
        method: 'POST',
      });
      if (res.ok) {
        const data = await res.json();
        const track = storageState.tracks.find(t => t.id === trackId);
        storageState.loadedProjectTake = {
          track_id: trackId,
          take_id: takeId,
          track_title: track ? track.title : 'Workspace Track',
          take_label: data.label || 'Master Take',
          filename: data.filename || 'track.mp3',
        };
        sessionStorage.setItem('mp3metafix_loaded_project_track', JSON.stringify(storageState.loadedProjectTake));
        showToast('✨ Track loaded into editor session!', 'success');
        // Restore session in frontend app.js
        if (typeof window.restoreSession === 'function') {
          await window.restoreSession();
        }
        switchView(targetView);
      } else {
        const err = await res.json();
        showToast(err.detail || 'Failed to load take into editor', 'error');
      }
    } catch (e) {
      showToast('Error connecting to server', 'error');
    }
  }

  async function setTakeMaster(trackId, takeId) {
    try {
      const res = await fetch(`/api/projects/tracks/${trackId}/takes/${takeId}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ is_master: true }),
      });
      if (res.ok) {
        showToast('★ Master take updated!', 'success');
        fetchStorageData();
      } else {
        showToast('Failed to set master take', 'error');
      }
    } catch (e) {
      showToast('Network error updating master take', 'error');
    }
  }

  async function confirmDeleteTrack(trackId, trackTitle) {
    if (!confirm(`Are you sure you want to delete "${trackTitle}" and all its takes/stems? This cannot be undone.`)) {
      return;
    }
    try {
      const res = await fetch(`/api/projects/tracks/${trackId}`, { method: 'DELETE' });
      if (res.ok) {
        showToast('Track workspace deleted', 'info');
        fetchStorageData();
      } else {
        showToast('Failed to delete track', 'error');
      }
    } catch (e) {
      showToast('Network error deleting track', 'error');
    }
  }

  async function confirmDeleteTake(trackId, takeId, takeLabel) {
    if (!confirm(`Delete take "${takeLabel}"?`)) return;
    try {
      const res = await fetch(`/api/projects/tracks/${trackId}/takes/${takeId}`, { method: 'DELETE' });
      if (res.ok) {
        showToast('Take deleted', 'info');
        fetchStorageData();
      } else {
        showToast('Failed to delete take', 'error');
      }
    } catch (e) {
      showToast('Network error deleting take', 'error');
    }
  }

  async function deleteStem(trackId, takeId, stemId) {
    if (!confirm('Delete this stem?')) return;
    try {
      const res = await fetch(`/api/projects/tracks/${trackId}/takes/${takeId}/stems/${stemId}`, { method: 'DELETE' });
      if (res.ok) {
        showToast('Stem deleted', 'info');
        fetchStorageData();
      } else {
        showToast('Failed to delete stem', 'error');
      }
    } catch (e) {
      showToast('Network error deleting stem', 'error');
    }
  }

  // --- Modals Management ---

  function openNewTrackModal() {
    if (!dom.modalNewTrack) return;
    if (dom.formNewTrack) dom.formNewTrack.reset();
    dom.modalNewTrack.classList.remove('hidden');
  }

  function closeNewTrackModal() {
    if (dom.modalNewTrack) dom.modalNewTrack.classList.add('hidden');
  }

  function openUploadTakeModal(trackId, trackTitle) {
    if (!dom.modalUploadTake) return;
    if (dom.formUploadTake) dom.formUploadTake.reset();
    if (dom.uploadTakeTrackId) dom.uploadTakeTrackId.value = trackId;
    if (dom.uploadTakeTitleDisplay) dom.uploadTakeTitleDisplay.textContent = trackTitle;
    dom.modalUploadTake.classList.remove('hidden');
  }

  function closeUploadTakeModal() {
    if (dom.modalUploadTake) dom.modalUploadTake.classList.add('hidden');
  }

  function openUploadStemModal(trackId, takeId, takeLabel) {
    if (!dom.modalUploadStem) return;
    if (dom.formUploadStem) dom.formUploadStem.reset();
    if (dom.uploadStemTrackId) dom.uploadStemTrackId.value = trackId;
    if (dom.uploadStemTakeId) dom.uploadStemTakeId.value = takeId;
    const labelEl = document.getElementById('uploadStemTakeDisplay');
    if (labelEl) labelEl.textContent = takeLabel;
    dom.modalUploadStem.classList.remove('hidden');
  }

  function closeUploadStemModal() {
    if (dom.modalUploadStem) dom.modalUploadStem.classList.add('hidden');
  }

  // Open Save to Project Modal from the Editor
  async function openSaveToProjectModal() {
    if (!dom.modalSaveToProject) return;
    if (dom.formSaveToProject) dom.formSaveToProject.reset();

    // Check if we loaded a project take previously
    let loadedTake = storageState.loadedProjectTake;
    if (!loadedTake) {
      try {
        const stored = sessionStorage.getItem('mp3metafix_loaded_project_track');
        if (stored) loadedTake = JSON.parse(stored);
      } catch (e) {}
    }

    // Populate existing tracks dropdown
    if (dom.selectSaveTargetTrack) {
      dom.selectSaveTargetTrack.innerHTML = '';
      storageState.tracks.forEach(track => {
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

      if (storageState.tracks.length === 0) {
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
    const loadedTitleTag = document.getElementById('tagTitle');
    const loadedFilename = document.getElementById('loadedFilename');
    if (titleInput) {
      titleInput.value = (loadedTitleTag && loadedTitleTag.value) || (loadedFilename && loadedFilename.textContent.replace(/\.[^.]+$/, '')) || '';
    }

    dom.modalSaveToProject.classList.remove('hidden');
  }

  function closeSaveToProjectModal() {
    if (dom.modalSaveToProject) dom.modalSaveToProject.classList.add('hidden');
  }

  // --- Form Handlers ---

  async function handleNewTrackSubmit(e) {
    e.preventDefault();
    const title = (document.getElementById('newTrackTitle').value || '').trim();
    const artist = (document.getElementById('newTrackArtist').value || '').trim();
    const bpmVal = document.getElementById('newTrackBpm').value;
    const musicalKey = (document.getElementById('newTrackKey').value || '').trim();
    const notes = (document.getElementById('newTrackNotes').value || '').trim();
    const statusVal = document.getElementById('newTrackStatus').value || 'draft';

    if (!title) {
      showToast('Please enter a track title', 'error');
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
        closeNewTrackModal();
        fetchStorageData();
      } else {
        const err = await res.json();
        showToast(err.detail || 'Failed to create track workspace', 'error');
      }
    } catch (err) {
      showToast('Network error creating track workspace', 'error');
    }
  }

  async function handleUploadTakeSubmit(e) {
    e.preventDefault();
    const trackId = dom.uploadTakeTrackId.value;
    const fileInput = document.getElementById('uploadTakeFileInput');
    const label = (document.getElementById('uploadTakeLabel').value || '').trim();
    const prompt = (document.getElementById('uploadTakePrompt').value || '').trim();
    const notes = (document.getElementById('uploadTakeNotes').value || '').trim();
    const isMaster = document.getElementById('uploadTakeIsMaster').checked;

    if (!fileInput.files || fileInput.files.length === 0) {
      showToast('Please select an audio file', 'error');
      return;
    }

    const formData = new FormData();
    formData.append('file', fileInput.files[0]);
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
        showToast('✨ Audio take uploaded successfully!', 'success');
        closeUploadTakeModal();
        fetchStorageData();
      } else {
        const err = await res.json();
        showToast(err.detail || 'Failed to upload take', 'error');
      }
    } catch (err) {
      showToast('Network error uploading take', 'error');
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
        showToast('✨ Stem uploaded successfully!', 'success');
        closeUploadStemModal();
        fetchStorageData();
      } else {
        const err = await res.json();
        showToast(err.detail || 'Failed to upload stem', 'error');
      }
    } catch (err) {
      showToast('Network error uploading stem', 'error');
    }
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
      let loadedTake = storageState.loadedProjectTake;
      if (!loadedTake) {
        try {
          const stored = sessionStorage.getItem('mp3metafix_loaded_project_track');
          if (stored) loadedTake = JSON.parse(stored);
        } catch (err) {}
      }
      if (!loadedTake || !loadedTake.track_id || !loadedTake.take_id) {
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
        const savedTake = await res.json();
        showToast(mode === 'update' ? '✨ Track take updated in workspace!' : '✨ Audio file saved into Project Storage!', 'success');
        closeSaveToProjectModal();
        fetchStorageData();
      } else {
        const err = await res.json();
        showToast(err.detail || 'Failed to save into project storage', 'error');
      }
    } catch (err) {
      showToast('Network error saving to project', 'error');
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
    if (storageState.tracks.length === 0) {
      await fetchStorageData();
    }
    populateStemsTrackSelect(targetTrackId, targetTakeId);
  }

  function populateStemsTrackSelect(preferredTrackId, preferredTakeId) {
    if (!dom.stemsTrackSelect) return;
    dom.stemsTrackSelect.innerHTML = '<option value="">Select Track Workspace...</option>';

    storageState.tracks.forEach(t => {
      const opt = document.createElement('option');
      opt.value = t.id;
      opt.textContent = `${t.title || 'Untitled Track'} (${t.takes_count || 0} takes)`;
      dom.stemsTrackSelect.appendChild(opt);
    });

    if (preferredTrackId && storageState.tracks.some(t => t.id === preferredTrackId)) {
      dom.stemsTrackSelect.value = preferredTrackId;
    } else if (storageState.tracks.length > 0) {
      dom.stemsTrackSelect.value = storageState.tracks[0].id;
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
        const stemCount = Object.keys(take.stems || {}).length;
        opt.textContent = `${take.label || 'Take'} ${take.is_master ? '★ MASTER' : ''} (${stemCount} stem${stemCount === 1 ? '' : 's'})`;
        dom.stemsTakeSelect.appendChild(opt);
      });

      let chosenTakeId = null;
      if (preferredTakeId && takes.some(t => t.id === preferredTakeId)) {
        chosenTakeId = preferredTakeId;
      } else if (takes.length > 0) {
        const masterTake = takes.find(t => t.is_master);
        chosenTakeId = masterTake ? masterTake.id : takes[0].id;
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
    const stems = take && take.stems ? Object.values(take.stems) : [];

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
        try {
          const res = await fetch(`/api/projects/tracks/${trackId}/takes/${take.id}/stems/${stem.id}`, { method: 'DELETE' });
          if (res.ok) {
            showToast('Stem deleted', 'info');
            onStemsTrackChanged(take.id);
            fetchStorageData();
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
      audio.currentTime = 0;
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
    showToast(`Uploading ${total} separated stem${total === 1 ? '' : 's'}...`, 'info', 3000);

    let successCount = 0;
    for (let i = 0; i < stemsState.stagedFiles.length; i++) {
      const item = stemsState.stagedFiles[i];
      const formData = new FormData();
      formData.append('file', item.file);
      formData.append('role', item.role);

      try {
        const res = await fetch(`/api/projects/tracks/${trackId}/takes/${takeId}/stems`, {
          method: 'POST',
          body: formData,
        });
        if (res.ok) successCount++;
      } catch (e) {}
    }

    if (successCount === total) {
      showToast(`✨ All ${total} stems uploaded successfully!`, 'success');
    } else {
      showToast(`Uploaded ${successCount} of ${total} stems`, 'info');
    }

    stemsState.stagedFiles = [];
    renderStagedStemsList();
    if (dom.stemsBatchUploadSection) dom.stemsBatchUploadSection.classList.add('hidden');
    onStemsTrackChanged(takeId);
    fetchStorageData();
  }

  // --- Setup Event Listeners ---
  function setupEvents() {
    // Sidebar Navigation
    if (dom.btnNavApp) {
      dom.btnNavApp.addEventListener('click', () => switchView('app'));
    }
    if (dom.btnNavLyrics) {
      dom.btnNavLyrics.addEventListener('click', () => switchView('lyrics'));
    }
    if (dom.btnNavStorage) {
      dom.btnNavStorage.addEventListener('click', () => switchView('storage'));
    }
    if (dom.btnNavStems) {
      dom.btnNavStems.addEventListener('click', () => switchView('stems'));
    }

    // Refresh & New Buttons
    if (dom.btnRefreshStorage) {
      dom.btnRefreshStorage.addEventListener('click', () => {
        showToast('Refreshing storage library...', 'info', 1000);
        fetchStorageData();
      });
    }
    if (dom.btnNewTrack) {
      dom.btnNewTrack.addEventListener('click', openNewTrackModal);
    }
    const btnEmptyNewTrack = document.getElementById('btnEmptyNewTrack');
    if (btnEmptyNewTrack) {
      btnEmptyNewTrack.addEventListener('click', openNewTrackModal);
    }

    // Search & Filter
    if (dom.storageSearchInput) {
      dom.storageSearchInput.addEventListener('input', (e) => {
        storageState.searchQuery = e.target.value;
        renderTrackList();
      });
    }

    if (dom.storageFilterPills) {
      dom.storageFilterPills.forEach(pill => {
        pill.addEventListener('click', () => {
          dom.storageFilterPills.forEach(p => p.classList.remove('active'));
          pill.classList.add('active');
          storageState.currentFilter = pill.dataset.filter || 'all';
          renderTrackList();
        });
      });
    }

    // Save to Project Button in Editor
    if (dom.btnSaveToProject) {
      dom.btnSaveToProject.addEventListener('click', openSaveToProjectModal);
    }

    // Radio toggle in Save to Project modal
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

    if (dom.radioSaveUpdateTake) dom.radioSaveUpdateTake.addEventListener('change', updateSaveTargetModeUI);
    if (dom.radioSaveNewTrack) dom.radioSaveNewTrack.addEventListener('change', updateSaveTargetModeUI);
    if (dom.radioSaveExistingTrack) dom.radioSaveExistingTrack.addEventListener('change', updateSaveTargetModeUI);

    // Forms Submissions
    if (dom.formNewTrack) {
      dom.formNewTrack.addEventListener('submit', handleNewTrackSubmit);
    }
    if (dom.formUploadTake) {
      dom.formUploadTake.addEventListener('submit', handleUploadTakeSubmit);
    }
    if (dom.formUploadStem) {
      dom.formUploadStem.addEventListener('submit', handleUploadStemSubmit);
    }
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
    if (dom.formSaveToProject) {
      dom.formSaveToProject.addEventListener('submit', handleSaveToProjectSubmit);
    }

    // Stems Panel Events
    if (dom.btnNavStems) {
      dom.btnNavStems.addEventListener('click', () => switchView('stems'));
    }
    if (dom.stemsTrackSelect) {
      dom.stemsTrackSelect.addEventListener('change', () => onStemsTrackChanged());
    }
    if (dom.stemsTakeSelect) {
      dom.stemsTakeSelect.addEventListener('change', () => {
        const takeId = dom.stemsTakeSelect.value;
        stemsState.selectedTakeId = takeId;
        const track = storageState.tracks.find(t => t.id === stemsState.selectedTrackId);
        if (track) {
          fetch(`/api/projects/tracks/${track.id}`)
            .then(res => res.json())
            .then(td => {
              const activeTake = (td.takes || []).find(t => t.id === takeId);
              renderStemsMixer(td.track || td || track, activeTake);
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

    // Modal Close Buttons
    document.querySelectorAll('[data-close-modal]').forEach(btn => {
      btn.addEventListener('click', () => {
        const modalId = btn.dataset.closeModal;
        const modal = document.getElementById(modalId);
        if (modal) modal.classList.add('hidden');
      });
    });

    // Close modals on escape key
    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') {
        [dom.modalNewTrack, dom.modalUploadTake, dom.modalUploadStem, dom.modalSaveToProject].forEach(m => {
          if (m && !m.classList.contains('hidden')) m.classList.add('hidden');
        });
      }
    });
  }

  // --- Initialization ---
  document.addEventListener('DOMContentLoaded', () => {
    initDom();
    setupEvents();
    // Initial fetch of quota & track counts if logged in
    fetchQuota();
    fetchStorageData();
  });
})();
