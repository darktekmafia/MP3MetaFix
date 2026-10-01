/**
 * MP3MetaFix - Synced Lyrics & Tap-to-Sync Karaoke Studio Controller
 */

(function () {
  'use strict';

  // --- State ---
  const lyricsState = {
    lines: [],             // [{ id: number, text: string, time_ms: number | null, isHeader: boolean, isInstrumental: boolean }]
    currentIndex: 0,       // Line index currently waiting to be stamped
    activeMode: 'sync',    // 'sync' | 'karaoke' | 'raw'
    stripTags: false,      // Hide/omit bracketed structural tags
    playbackSpeed: 1.0,
    audioElement: null,
    isPlaying: false,
    rawText: '',
  };

  // Section / Header Regex - matches [Verse 1], (Chorus), [Outro], (Bridge), etc.
  const SECTION_TAG_REGEX = /^[\[(](.*?)[\])]$/;
  const INSTRUMENTAL_TAGS = ['intro', 'guitar solo', 'solo', 'instrumental', 'drop', 'interlude', 'outro', 'break'];

  // DOM Elements cache
  let dom = {};

  function initDOM() {
    dom = {
      panelAppView: document.getElementById('panelAppView'),
      panelLyricsView: document.getElementById('panelLyricsView'),
      navItemApp: document.getElementById('navItemApp'),
      navItemLyrics: document.getElementById('navItemLyrics'),
      btnNavApp: document.getElementById('btnNavApp'),
      btnNavLyrics: document.getElementById('btnNavLyrics'),

      // Lyrics Toolbar
      lyricsTrackTitle: document.getElementById('lyricsTrackTitle'),
      lyricsAudioSpecs: document.getElementById('lyricsAudioSpecs'),
      btnLyricsPlayPause: document.getElementById('btnLyricsPlayPause'),
      lyricsPlayIcon: document.getElementById('lyricsPlayIcon'),
      lyricsPauseIcon: document.getElementById('lyricsPauseIcon'),
      lyricsTimeCurrent: document.getElementById('lyricsTimeCurrent'),
      lyricsTimeTotal: document.getElementById('lyricsTimeTotal'),
      lyricsScrubber: document.getElementById('lyricsScrubber'),
      selectLyricsSpeed: document.getElementById('selectLyricsSpeed'),

      // View Mode Buttons
      btnModeSync: document.getElementById('btnModeSync'),
      btnModeKaraoke: document.getElementById('btnModeKaraoke'),
      btnModeRaw: document.getElementById('btnModeRaw'),
      modeSyncView: document.getElementById('modeSyncView'),
      modeKaraokeView: document.getElementById('modeKaraokeView'),
      modeRawView: document.getElementById('modeRawView'),

      // Actions
      btnSaveSylt: document.getElementById('btnSaveSylt'),
      btnExportLrc: document.getElementById('btnExportLrc'),
      btnImportLrc: document.getElementById('btnImportLrc'),
      lrcFileInput: document.getElementById('lrcFileInput'),
      btnPullTrackLyrics: document.getElementById('btnPullTrackLyrics'),
      btnRawApply: document.getElementById('btnRawApply'),
      rawLyricsTextarea: document.getElementById('rawLyricsTextarea'),

      // Stamping Controls
      btnStampHero: document.getElementById('btnStampHero'),
      stampHeroLineText: document.getElementById('stampHeroLineText'),
      stampHeroLineCounter: document.getElementById('stampHeroLineCounter'),
      btnStepBack: document.getElementById('btnStepBack'),
      btnNudgeBack: document.getElementById('btnNudgeBack'),
      btnNudgeForward: document.getElementById('btnNudgeForward'),
      btnClearAllStamps: document.getElementById('btnClearAllStamps'),
      toggleStripTags: document.getElementById('toggleStripTags'),

      // Stamping Queue Table
      lyricsQueueContainer: document.getElementById('lyricsQueueContainer'),
      lyricsEmptyState: document.getElementById('lyricsEmptyState'),

      // Karaoke Visualizer
      karaokeSectionBadge: document.getElementById('karaokeSectionBadge'),
      karaokePrevLine: document.getElementById('karaokePrevLine'),
      karaokeActiveLine: document.getElementById('karaokeActiveLine'),
      karaokeNextLine: document.getElementById('karaokeNextLine'),
      karaokeCueBox: document.getElementById('karaokeCueBox'),
      karaokeCueText: document.getElementById('karaokeCueText'),
    };
  }

  // --- Classification & Parsing ---
  function classifyLine(rawText) {
    const trimmed = rawText.trim();
    const match = trimmed.match(SECTION_TAG_REGEX);
    if (match) {
      const tagContent = match[1].trim().toLowerCase();
      const isInst = INSTRUMENTAL_TAGS.some(t => tagContent.includes(t));
      return {
        text: trimmed,
        isHeader: true,
        isInstrumental: isInst,
        headerLabel: match[1].trim(),
      };
    }
    return {
      text: trimmed,
      isHeader: false,
      isInstrumental: false,
      headerLabel: '',
    };
  }

  function parseLyricsText(text, existingSynced = []) {
    const rawLines = text.split(/\r?\n/).map(l => l.trim()).filter(Boolean);
    const parsed = [];
    let idx = 0;

    // Map existing timestamps in sequential queues per lowercase text key to prevent repeated lyrics collisions
    const occurrencesMap = new Map();
    if (Array.isArray(existingSynced)) {
      existingSynced.forEach(item => {
        if (item && item.text) {
          const key = item.text.trim().toLowerCase();
          if (!occurrencesMap.has(key)) {
            occurrencesMap.set(key, []);
          }
          occurrencesMap.get(key).push(item.time_ms);
        }
      });
    }

    rawLines.forEach(lineText => {
      const info = classifyLine(lineText);
      let time_ms = null;

      const lower = lineText.toLowerCase();
      if (occurrencesMap.has(lower) && occurrencesMap.get(lower).length > 0) {
        time_ms = occurrencesMap.get(lower).shift();
      }

      parsed.push({
        id: idx++,
        text: info.text,
        time_ms: time_ms,
        isHeader: info.isHeader,
        isInstrumental: info.isInstrumental,
        headerLabel: info.headerLabel,
      });
    });

    return parsed;
  }

  // --- Helpers ---
  function escapeHtml(str) {
    if (!str) return '';
    return String(str)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#039;');
  }

  function getActiveLines() {
    if (lyricsState.stripTags) {
      return lyricsState.lines.filter(l => !l.isHeader);
    }
    return lyricsState.lines;
  }

  function getExportableLines() {
    if (lyricsState.stripTags) {
      return lyricsState.lines.filter(l => !l.isHeader && l.time_ms !== null);
    }
    return lyricsState.lines.filter(l => l.time_ms !== null);
  }

  function formatMs(ms) {
    if (ms === null || typeof ms === 'undefined') return '--:--.--';
    const totalSecs = Math.max(0, ms / 1000);
    const mins = Math.floor(totalSecs / 60);
    const secs = Math.floor(totalSecs % 60);
    const hundredths = Math.floor((totalSecs % 1) * 100);
    return `${String(mins).padStart(2, '0')}:${String(secs).padStart(2, '0')}.${String(hundredths).padStart(2, '0')}`;
  }

  function getAudio() {
    if (!lyricsState.audioElement) {
      lyricsState.audioElement = document.getElementById('audioElement');
    }
    return lyricsState.audioElement;
  }

  // --- View Switching ---
  function switchSidebarView(viewName) {
    if (!dom.panelAppView || !dom.panelLyricsView) return;

    if (viewName === 'lyrics') {
      dom.panelAppView.classList.add('hidden');
      dom.panelLyricsView.classList.remove('hidden');
      if (dom.navItemApp) dom.navItemApp.classList.remove('active');
      if (dom.navItemLyrics) dom.navItemLyrics.classList.add('active');
      syncTrackState();
      renderQueue();
    } else {
      dom.panelLyricsView.classList.add('hidden');
      dom.panelAppView.classList.remove('hidden');
      if (dom.navItemLyrics) dom.navItemLyrics.classList.remove('active');
      if (dom.navItemApp) dom.navItemApp.classList.add('active');
    }
  }

  function switchLyricsMode(mode) {
    lyricsState.activeMode = mode;
    [dom.btnModeSync, dom.btnModeKaraoke, dom.btnModeRaw].forEach(btn => btn && btn.classList.remove('active'));
    [dom.modeSyncView, dom.modeKaraokeView, dom.modeRawView].forEach(v => v && v.classList.add('hidden'));

    if (mode === 'sync') {
      if (dom.btnModeSync) dom.btnModeSync.classList.add('active');
      if (dom.modeSyncView) dom.modeSyncView.classList.remove('hidden');
      renderQueue();
    } else if (mode === 'karaoke') {
      if (dom.btnModeKaraoke) dom.btnModeKaraoke.classList.add('active');
      if (dom.modeKaraokeView) dom.modeKaraokeView.classList.remove('hidden');
      updateKaraokeVisualizer();
    } else if (mode === 'raw') {
      if (dom.btnModeRaw) dom.btnModeRaw.classList.add('active');
      if (dom.modeRawView) dom.modeRawView.classList.remove('hidden');
      if (dom.rawLyricsTextarea) {
        dom.rawLyricsTextarea.value = lyricsState.lines.map(l => l.text).join('\n');
      }
    }
  }

  // --- Sync Track State with Main Session ---
  function syncTrackState() {
    const appState = window.MP3MetaFixAppState;
    if (appState && appState.hasSession) {
      if (dom.lyricsTrackTitle) {
        const title = appState.metadata.title || appState.originalFilename || 'Untitled Track';
        const artist = appState.metadata.artist ? ` — ${appState.metadata.artist}` : '';
        dom.lyricsTrackTitle.textContent = `${title}${artist}`;
      }
      if (dom.lyricsAudioSpecs) {
        dom.lyricsAudioSpecs.textContent = `${(appState.format || 'MP3').toUpperCase()} Audio Session Loaded`;
      }

      // Check if we need to load lyrics from track
      if (lyricsState.lines.length === 0) {
        const raw = appState.metadata.lyrics || '';
        const sylt = appState.metadata.synced_lyrics || [];
        if (raw || sylt.length > 0) {
          lyricsState.lines = parseLyricsText(raw || sylt.map(s => s.text).join('\n'), sylt);
          lyricsState.currentIndex = findNextTargetIndex(0);
        }
      }
    } else {
      if (dom.lyricsTrackTitle) dom.lyricsTrackTitle.textContent = 'No Audio Track Loaded';
      if (dom.lyricsAudioSpecs) dom.lyricsAudioSpecs.textContent = 'Upload or load an audio file in MP3MetaFix';
    }
  }

  function findNextTargetIndex(fromIdx = 0) {
    if (lyricsState.lines.length === 0) return 0;

    // If stripTags is enabled, strictly search for next unstamped vocal line
    if (lyricsState.stripTags) {
      for (let i = fromIdx; i < lyricsState.lines.length; i++) {
        if (!lyricsState.lines[i].isHeader && lyricsState.lines[i].time_ms === null) {
          return i;
        }
      }
      for (let i = 0; i < lyricsState.lines.length; i++) {
        if (!lyricsState.lines[i].isHeader && lyricsState.lines[i].time_ms === null) {
          return i;
        }
      }
      for (let i = lyricsState.lines.length - 1; i >= 0; i--) {
        if (!lyricsState.lines[i].isHeader) return i;
      }
      return 0;
    }

    // Normal mode (tags preserved):
    // Auto-stamp Intro at 0ms if at start
    if (lyricsState.lines.length > 0 && lyricsState.lines[0].isHeader && lyricsState.lines[0].time_ms === null) {
      lyricsState.lines[0].time_ms = 0;
    }

    // Look for next unstamped line. If it's a section header preceding vocals, target the vocal line directly
    for (let i = fromIdx; i < lyricsState.lines.length; i++) {
      const line = lyricsState.lines[i];
      if (line.time_ms === null) {
        if (line.isHeader && i + 1 < lyricsState.lines.length && !lyricsState.lines[i + 1].isHeader) {
          return i + 1;
        }
        return i;
      }
    }

    for (let i = 0; i < lyricsState.lines.length; i++) {
      const line = lyricsState.lines[i];
      if (line.time_ms === null) {
        if (line.isHeader && i + 1 < lyricsState.lines.length && !lyricsState.lines[i + 1].isHeader) {
          return i + 1;
        }
        return i;
      }
    }

    return Math.max(0, lyricsState.lines.length - 1);
  }

  // --- Stamping Logic ---
  function stampCurrentLine() {
    const audio = getAudio();
    if (!audio || !audio.src) {
      showToast('Please load an audio track first', 'info');
      return;
    }

    if (lyricsState.lines.length === 0) {
      showToast('No lyrics loaded. Paste lyrics or pull from track.', 'info');
      return;
    }

    const currentMs = Math.round(audio.currentTime * 1000);
    const targetIdx = lyricsState.currentIndex;

    if (targetIdx >= 0 && targetIdx < lyricsState.lines.length) {
      const targetLine = lyricsState.lines[targetIdx];

      // Auto-stamp preceding header tags with lead-in reduction if not stripped
      if (!lyricsState.stripTags && !targetLine.isHeader) {
        const LEAD_IN_MS = 500; // 500ms time reduction / lead-in for section tags
        for (let j = targetIdx - 1; j >= 0; j--) {
          const prevLine = lyricsState.lines[j];
          if (prevLine.isHeader && prevLine.time_ms === null) {
            if (j === 0 && (prevLine.isInstrumental || prevLine.headerLabel.toLowerCase().includes('intro'))) {
              prevLine.time_ms = 0;
            } else {
              let prevStamped = 0;
              for (let k = j - 1; k >= 0; k--) {
                if (lyricsState.lines[k].time_ms !== null) {
                  prevStamped = lyricsState.lines[k].time_ms;
                  break;
                }
              }
              prevLine.time_ms = Math.max(prevStamped + 50, currentMs - LEAD_IN_MS);
            }
          } else if (!prevLine.isHeader) {
            break;
          }
        }
      }

      // Stamp target line
      targetLine.time_ms = currentMs;

      // Advance to next target line
      lyricsState.currentIndex = findNextTargetIndex(targetIdx + 1);

      renderQueue();
      updateHeroBanner();
      scrollRowIntoView(lyricsState.currentIndex);
    }
  }

  function stepBackOneLine() {
    const activeLines = getActiveLines();
    if (activeLines.length === 0) return;

    let lastStampedIdx = -1;
    for (let i = lyricsState.lines.length - 1; i >= 0; i--) {
      if (lyricsState.lines[i].time_ms !== null) {
        if (lyricsState.stripTags && lyricsState.lines[i].isHeader) continue;
        lastStampedIdx = i;
        break;
      }
    }

    if (lastStampedIdx >= 0) {
      const line = lyricsState.lines[lastStampedIdx];
      line.time_ms = null;

      // If this was a vocal line with auto-stamped preceding headers, clear them too
      if (!lyricsState.stripTags && !line.isHeader) {
        for (let j = lastStampedIdx - 1; j >= 0; j--) {
          const prev = lyricsState.lines[j];
          if (prev.isHeader) {
            if (j !== 0 || prev.time_ms !== 0) {
              prev.time_ms = null;
            }
          } else {
            break;
          }
        }
      }

      lyricsState.currentIndex = lastStampedIdx;
      renderQueue();
      updateHeroBanner();
      scrollRowIntoView(lyricsState.currentIndex);
    }
  }

  function nudgeSelectedOrLast(deltaMs) {
    if (lyricsState.lines.length === 0) return;

    let targetIdx = lyricsState.currentIndex;

    // 1. If currently selected line has a timestamp, nudge it directly
    if (targetIdx >= 0 && targetIdx < lyricsState.lines.length && lyricsState.lines[targetIdx].time_ms !== null) {
      const oldTime = lyricsState.lines[targetIdx].time_ms;
      const newTime = Math.max(0, oldTime + deltaMs);
      lyricsState.lines[targetIdx].time_ms = newTime;
      renderQueue();
      updateHeroBanner();
      showToast(`Nudged line #${targetIdx + 1} (${deltaMs > 0 ? '+' : ''}${deltaMs}ms) → ${formatMs(newTime)}`, 'info', 1500);
      return;
    }

    // 2. Otherwise find the most recent stamped line before or at currentIndex
    let foundIdx = -1;
    for (let i = targetIdx; i >= 0; i--) {
      if (lyricsState.lines[i].time_ms !== null) {
        if (lyricsState.stripTags && lyricsState.lines[i].isHeader) continue;
        foundIdx = i;
        break;
      }
    }

    // 3. Fallback: search backwards from the end
    if (foundIdx === -1) {
      for (let i = lyricsState.lines.length - 1; i >= 0; i--) {
        if (lyricsState.lines[i].time_ms !== null) {
          if (lyricsState.stripTags && lyricsState.lines[i].isHeader) continue;
          foundIdx = i;
          break;
        }
      }
    }

    if (foundIdx >= 0 && foundIdx < lyricsState.lines.length) {
      const oldTime = lyricsState.lines[foundIdx].time_ms;
      const newTime = Math.max(0, oldTime + deltaMs);
      lyricsState.lines[foundIdx].time_ms = newTime;
      lyricsState.currentIndex = foundIdx;
      renderQueue();
      updateHeroBanner();
      showToast(`Nudged line #${foundIdx + 1} (${deltaMs > 0 ? '+' : ''}${deltaMs}ms) → ${formatMs(newTime)}`, 'info', 1500);
    } else {
      showToast('No timestamped line selected to nudge.', 'info', 2000);
    }
  }

  function navigateSelectedLine(delta) {
    const activeLines = getActiveLines();
    if (activeLines.length === 0) return;

    const curLine = lyricsState.lines[lyricsState.currentIndex];
    let curRank = activeLines.indexOf(curLine);
    if (curRank === -1) curRank = 0;

    const nextRank = Math.max(0, Math.min(activeLines.length - 1, curRank + delta));
    const targetLine = activeLines[nextRank];
    const targetIdx = lyricsState.lines.indexOf(targetLine);

    if (targetIdx !== -1) {
      lyricsState.currentIndex = targetIdx;
      renderQueue();
      updateHeroBanner();
      scrollRowIntoView(targetIdx);
    }
  }

  function clearAllStamps() {
    if (confirm('Clear all timestamp recordings? The lyrics text will remain intact.')) {
      lyricsState.lines.forEach(l => l.time_ms = null);
      lyricsState.currentIndex = findNextTargetIndex(0);
      renderQueue();
      updateHeroBanner();
      showToast('All timestamps cleared', 'info');
    }
  }

  // --- Render Functions ---
  function renderQueue() {
    if (!dom.lyricsQueueContainer) return;

    const activeLines = getActiveLines();
    if (activeLines.length === 0) {
      if (dom.lyricsEmptyState) dom.lyricsEmptyState.classList.remove('hidden');
      dom.lyricsQueueContainer.innerHTML = '';
      updateHeroBanner();
      return;
    }

    if (dom.lyricsEmptyState) dom.lyricsEmptyState.classList.add('hidden');
    dom.lyricsQueueContainer.innerHTML = '';

    let displayRank = 1;
    lyricsState.lines.forEach((line, idx) => {
      // If stripTags is true and line is a header, omit it completely from the DOM
      if (lyricsState.stripTags && line.isHeader) return;

      const isCurrent = idx === lyricsState.currentIndex;
      const isStamped = line.time_ms !== null;

      const row = document.createElement('div');
      row.className = `lyric-queue-row ${isCurrent ? 'active' : ''} ${isStamped ? 'stamped' : ''} ${line.isHeader ? 'row-header' : ''}`;
      row.dataset.index = idx;

      // Index column
      const idxCell = document.createElement('div');
      idxCell.className = 'queue-col-idx';
      idxCell.textContent = `#${displayRank++}`;

      // Status indicator
      const statusCell = document.createElement('div');
      statusCell.className = 'queue-col-status';
      if (isStamped) {
        statusCell.innerHTML = '<span class="status-dot-stamped" title="Stamped">✓</span>';
      } else if (isCurrent) {
        statusCell.innerHTML = '<span class="status-dot-active" title="Next to stamp">▶</span>';
      } else {
        statusCell.innerHTML = '<span class="status-dot-pending" title="Pending">○</span>';
      }

      // Timestamp Pill
      const timeCell = document.createElement('div');
      timeCell.className = 'queue-col-time';
      const timeBtn = document.createElement('button');
      timeBtn.type = 'button';
      timeBtn.className = `lyric-time-pill ${isStamped ? 'has-time' : ''}`;
      timeBtn.textContent = formatMs(line.time_ms);
      timeBtn.title = isStamped ? 'Click to jump audio to this timestamp' : 'Not stamped yet';
      timeBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        if (line.time_ms !== null) {
          const audio = getAudio();
          if (audio) {
            audio.currentTime = line.time_ms / 1000;
            if (audio.paused) audio.play();
          }
        } else {
          lyricsState.currentIndex = idx;
          renderQueue();
          updateHeroBanner();
        }
      });
      timeCell.appendChild(timeBtn);

      // Lyric Text
      const textCell = document.createElement('div');
      textCell.className = 'queue-col-text';
      if (line.isHeader) {
        const badge = document.createElement('span');
        badge.className = `section-tag-badge ${line.isInstrumental ? 'badge-instrumental' : 'badge-vocal'}`;
        badge.textContent = line.text;
        textCell.appendChild(badge);
      } else {
        textCell.textContent = line.text;
      }

      // Actions (Clear/Restamp)
      const actionCell = document.createElement('div');
      actionCell.className = 'queue-col-actions';
      
      const restampBtn = document.createElement('button');
      restampBtn.type = 'button';
      restampBtn.className = 'btn-icon-xs';
      restampBtn.title = 'Set as next line to stamp';
      restampBtn.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="13" height="13"><polygon points="5 3 19 12 5 21 5 3"></polygon></svg>';
      restampBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        lyricsState.currentIndex = idx;
        renderQueue();
        updateHeroBanner();
      });

      const clearBtn = document.createElement('button');
      clearBtn.type = 'button';
      clearBtn.className = 'btn-icon-xs text-danger';
      clearBtn.title = 'Clear timestamp for this line';
      clearBtn.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="13" height="13"><line x1="18" y1="6" x2="6" y2="18"></line><line x1="6" y1="6" x2="18" y2="18"></line></svg>';
      clearBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        line.time_ms = null;
        renderQueue();
        updateHeroBanner();
      });

      actionCell.appendChild(restampBtn);
      if (isStamped) actionCell.appendChild(clearBtn);

      // Row click selects line
      row.addEventListener('click', () => {
        lyricsState.currentIndex = idx;
        renderQueue();
        updateHeroBanner();
      });

      row.appendChild(idxCell);
      row.appendChild(statusCell);
      row.appendChild(timeCell);
      row.appendChild(textCell);
      row.appendChild(actionCell);

      dom.lyricsQueueContainer.appendChild(row);
    });

    updateHeroBanner();
  }

  function updateHeroBanner() {
    if (!dom.stampHeroLineText || !dom.stampHeroLineCounter) return;

    const activeLines = getActiveLines();
    const total = activeLines.length;
    if (total === 0) {
      dom.stampHeroLineText.textContent = 'No Lyrics Loaded';
      dom.stampHeroLineCounter.textContent = '0 / 0';
      return;
    }

    const current = lyricsState.lines[lyricsState.currentIndex];
    if (current && (!lyricsState.stripTags || !current.isHeader)) {
      const activeRank = activeLines.indexOf(current) + 1;
      dom.stampHeroLineCounter.textContent = lyricsState.stripTags
        ? `Vocal Line ${activeRank} of ${total}`
        : `Line ${activeRank} of ${total}`;

      let parentHeader = '';
      if (!current.isHeader) {
        for (let j = lyricsState.currentIndex - 1; j >= 0; j--) {
          if (lyricsState.lines[j].isHeader) {
            parentHeader = lyricsState.lines[j].text;
            break;
          }
        }
      }

      if (current.isHeader) {
        dom.stampHeroLineText.innerHTML = `<span class="section-tag-badge ${current.isInstrumental ? 'badge-instrumental' : 'badge-vocal'}">${escapeHtml(current.text)}</span>`;
      } else if (parentHeader && !lyricsState.stripTags) {
        dom.stampHeroLineText.innerHTML = `<span class="section-tag-badge badge-vocal" style="font-size:0.75rem; vertical-align:middle; margin-right:8px;">${escapeHtml(parentHeader)}</span><span>${escapeHtml(current.text)}</span>`;
      } else {
        dom.stampHeroLineText.textContent = current.text;
      }
    } else {
      const allStamped = activeLines.every(l => l.time_ms !== null);
      if (allStamped) {
        dom.stampHeroLineText.textContent = 'All Lines Stamped! 🎉';
        dom.stampHeroLineCounter.textContent = `${total} / ${total}`;
      } else {
        lyricsState.currentIndex = findNextTargetIndex(0);
        updateHeroBanner();
      }
    }
  }

  function scrollRowIntoView(idx) {
    if (!dom.lyricsQueueContainer) return;
    const row = dom.lyricsQueueContainer.querySelector(`[data-index="${idx}"]`);
    if (row) {
      row.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    }
  }

  // --- Karaoke Live Visualizer ---
  function updateKaraokeVisualizer() {
    if (lyricsState.activeMode !== 'karaoke') return;
    const audio = getAudio();
    if (!audio) return;

    const currentMs = Math.round(audio.currentTime * 1000);
    const lines = getExportableLines().sort((a, b) => a.time_ms - b.time_ms);

    if (lines.length === 0) {
      if (dom.karaokeActiveLine) dom.karaokeActiveLine.textContent = 'No stamped lyrics yet. Use Tap-to-Sync to record timestamps.';
      if (dom.karaokePrevLine) dom.karaokePrevLine.textContent = '';
      if (dom.karaokeNextLine) dom.karaokeNextLine.textContent = '';
      if (dom.karaokeSectionBadge) dom.karaokeSectionBadge.textContent = '';
      return;
    }

    let activeIdx = -1;
    for (let i = 0; i < lines.length; i++) {
      if (lines[i].time_ms <= currentMs) {
        activeIdx = i;
      } else {
        break;
      }
    }

    if (activeIdx >= 0) {
      const activeLine = lines[activeIdx];
      const prevLine = activeIdx > 0 ? lines[activeIdx - 1] : null;
      const nextLine = activeIdx + 1 < lines.length ? lines[activeIdx + 1] : null;

      // Check section header context
      if (activeLine.isHeader) {
        if (dom.karaokeSectionBadge) dom.karaokeSectionBadge.textContent = activeLine.headerLabel || activeLine.text;
        if (activeLine.isInstrumental && dom.karaokeCueBox) {
          dom.karaokeCueBox.classList.remove('hidden');
          if (dom.karaokeCueText) dom.karaokeCueText.textContent = `🎸 ${activeLine.text}`;
        } else if (dom.karaokeCueBox) {
          dom.karaokeCueBox.classList.add('hidden');
        }
      } else {
        if (dom.karaokeCueBox) dom.karaokeCueBox.classList.add('hidden');
      }

      if (dom.karaokeActiveLine) dom.karaokeActiveLine.textContent = activeLine.text;
      if (dom.karaokePrevLine) dom.karaokePrevLine.textContent = prevLine ? prevLine.text : '';
      if (dom.karaokeNextLine) dom.karaokeNextLine.textContent = nextLine ? nextLine.text : '';
    } else {
      // Intro before first line
      const firstLine = lines[0];
      if (dom.karaokeActiveLine) dom.karaokeActiveLine.textContent = `Intro... (Starts in ${(firstLine.time_ms / 1000).toFixed(1)}s)`;
      if (dom.karaokePrevLine) dom.karaokePrevLine.textContent = '';
      if (dom.karaokeNextLine) dom.karaokeNextLine.textContent = firstLine.text;
      if (dom.karaokeCueBox) dom.karaokeCueBox.classList.remove('hidden');
      if (dom.karaokeCueText) dom.karaokeCueText.textContent = '🎵 Musical Intro';
    }
  }

  // --- Save SYLT to Session File ---
  async function saveSyltToSession() {
    const appState = window.MP3MetaFixAppState;
    if (!appState || !appState.hasSession) {
      showToast('No active audio session to save lyrics to.', 'error');
      return;
    }

    const stampedEntries = getExportableLines().map(l => ({ text: l.text, time_ms: l.time_ms }));

    if (stampedEntries.length === 0) {
      showToast('No timestamped lyrics to save.', 'info');
      return;
    }

    if (dom.btnSaveSylt) dom.btnSaveSylt.disabled = true;
    showToast('Embedding SYLT synchronized lyrics into audio...', 'info', 2000);

    try {
      const payload = {
        title: appState.metadata.title || '',
        artist: appState.metadata.artist || '',
        album: appState.metadata.album || '',
        album_artist: appState.metadata.album_artist || '',
        genre: appState.metadata.genre || '',
        year: appState.metadata.year || '',
        track_number: appState.metadata.track_number || '',
        total_tracks: appState.metadata.total_tracks || '',
        disc_number: appState.metadata.disc_number || '',
        total_discs: appState.metadata.total_discs || '',
        composer: appState.metadata.composer || '',
        bpm: appState.metadata.bpm || '',
        comment: appState.metadata.comment || '',
        lyrics: lyricsState.lines.map(l => l.text).join('\n'),
        synced_lyrics: stampedEntries,
        remove_artwork: false,
      };

      const res = await fetch('/api/save', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });

      const data = await res.json();
      if (res.ok && data.success) {
        showToast('✨ Synced lyrics (SYLT) saved directly into audio file!', 'success');
        if (appState.metadata) {
          appState.metadata.synced_lyrics = stampedEntries;
          appState.metadata.lyrics = payload.lyrics;
        }
      } else {
        showToast(data.detail || 'Failed to save synced lyrics', 'error');
      }
    } catch (e) {
      showToast('Error connecting to server', 'error');
    } finally {
      if (dom.btnSaveSylt) dom.btnSaveSylt.disabled = false;
    }
  }

  // --- Export & Import LRC ---
  async function exportLrcFile() {
    const stampedEntries = getExportableLines().map(l => ({ text: l.text, time_ms: l.time_ms }));

    if (stampedEntries.length === 0) {
      showToast('Please stamp at least one line before exporting .LRC', 'info');
      return;
    }

    const appState = window.MP3MetaFixAppState || {};
    const artist = appState.metadata?.artist || '';
    const title = appState.metadata?.title || 'lyrics';

    try {
      const res = await fetch('/api/lyrics/export-lrc', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          entries: stampedEntries,
          artist: artist,
          title: title,
          album: appState.metadata?.album || '',
        }),
      });

      if (!res.ok) throw new Error('Failed to export LRC');
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `${artist ? `${artist} - ` : ''}${title}.lrc`.replace(/[\\/*?:"<>|]/g, '_');
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
      showToast('Downloaded .LRC synced lyrics file', 'success');
    } catch (e) {
      showToast('Could not export .LRC file', 'error');
    }
  }

  async function importLrcFile(file) {
    if (!file) return;
    const text = await file.text();
    try {
      const res = await fetch('/api/lyrics/parse-lrc', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ lrc_text: text }),
      });
      const data = await res.json();
      if (res.ok && data.success && data.entries) {
        lyricsState.lines = parseLyricsText(data.entries.map(e => e.text).join('\n'), data.entries);
        lyricsState.currentIndex = findNextTargetIndex(0);
        renderQueue();
        showToast(`Imported ${data.count} timestamped lines from .LRC`, 'success');
      } else {
        showToast('Invalid or empty .LRC file', 'error');
      }
    } catch (e) {
      showToast('Error parsing .LRC file', 'error');
    }
  }

  // --- Audio Event Listeners ---
  function setupAudioListeners() {
    const audio = getAudio();
    if (!audio) return;

    audio.addEventListener('timeupdate', () => {
      const cur = audio.currentTime;
      const dur = audio.duration || 0;
      if (dom.lyricsTimeCurrent) dom.lyricsTimeCurrent.textContent = formatMs(cur * 1000);
      if (dom.lyricsTimeTotal) dom.lyricsTimeTotal.textContent = formatMs(dur * 1000);
      if (dom.lyricsScrubber && dur > 0) {
        dom.lyricsScrubber.value = (cur / dur) * 100;
      }
      if (lyricsState.activeMode === 'karaoke') {
        updateKaraokeVisualizer();
      }
    });

    audio.addEventListener('play', () => {
      lyricsState.isPlaying = true;
      if (dom.lyricsPlayIcon) dom.lyricsPlayIcon.classList.add('hidden');
      if (dom.lyricsPauseIcon) dom.lyricsPauseIcon.classList.remove('hidden');
    });

    audio.addEventListener('pause', () => {
      lyricsState.isPlaying = false;
      if (dom.lyricsPlayIcon) dom.lyricsPlayIcon.classList.remove('hidden');
      if (dom.lyricsPauseIcon) dom.lyricsPauseIcon.classList.add('hidden');
    });
  }

  // --- Event Wireup ---
  function setupEventListeners() {
    // Sidebar navigation
    if (dom.btnNavApp) {
      dom.btnNavApp.addEventListener('click', () => switchSidebarView('app'));
    }
    if (dom.btnNavLyrics) {
      dom.btnNavLyrics.addEventListener('click', () => switchSidebarView('lyrics'));
    }

    // Mode tabs
    if (dom.btnModeSync) dom.btnModeSync.addEventListener('click', () => switchLyricsMode('sync'));
    if (dom.btnModeKaraoke) dom.btnModeKaraoke.addEventListener('click', () => switchLyricsMode('karaoke'));
    if (dom.btnModeRaw) dom.btnModeRaw.addEventListener('click', () => switchLyricsMode('raw'));

    // Stamping buttons
    if (dom.btnStampHero) dom.btnStampHero.addEventListener('click', stampCurrentLine);
    if (dom.btnStepBack) dom.btnStepBack.addEventListener('click', stepBackOneLine);
    if (dom.btnNudgeBack) dom.btnNudgeBack.addEventListener('click', () => nudgeSelectedOrLast(-100));
    if (dom.btnNudgeForward) dom.btnNudgeForward.addEventListener('click', () => nudgeSelectedOrLast(100));
    if (dom.btnClearAllStamps) dom.btnClearAllStamps.addEventListener('click', clearAllStamps);

    // Toggle strip tags
    if (dom.toggleStripTags) {
      dom.toggleStripTags.addEventListener('change', (e) => {
        lyricsState.stripTags = e.target.checked;
        lyricsState.currentIndex = findNextTargetIndex(0);
        renderQueue();
        updateHeroBanner();
      });
    }

    // Audio Play/Pause
    if (dom.btnLyricsPlayPause) {
      dom.btnLyricsPlayPause.addEventListener('click', () => {
        const audio = getAudio();
        if (!audio || !audio.src) {
          showToast('No audio track loaded', 'info');
          return;
        }
        if (audio.paused) {
          audio.play();
        } else {
          audio.pause();
        }
      });
    }

    // Scrubber
    if (dom.lyricsScrubber) {
      dom.lyricsScrubber.addEventListener('input', (e) => {
        const audio = getAudio();
        if (audio && audio.duration) {
          audio.currentTime = (e.target.value / 100) * audio.duration;
        }
      });
    }

    // Playback Speed
    if (dom.selectLyricsSpeed) {
      dom.selectLyricsSpeed.addEventListener('change', (e) => {
        const speed = parseFloat(e.target.value) || 1.0;
        lyricsState.playbackSpeed = speed;
        const audio = getAudio();
        if (audio) audio.playbackRate = speed;
      });
    }

    // Save & Export
    if (dom.btnSaveSylt) dom.btnSaveSylt.addEventListener('click', saveSyltToSession);
    if (dom.btnExportLrc) dom.btnExportLrc.addEventListener('click', exportLrcFile);

    // Import LRC
    if (dom.btnImportLrc && dom.lrcFileInput) {
      dom.btnImportLrc.addEventListener('click', () => dom.lrcFileInput.click());
      dom.lrcFileInput.addEventListener('change', (e) => {
        if (e.target.files && e.target.files[0]) {
          importLrcFile(e.target.files[0]);
          e.target.value = '';
        }
      });
    }

    // Pull from Track USLT
    if (dom.btnPullTrackLyrics) {
      dom.btnPullTrackLyrics.addEventListener('click', () => {
        const appState = window.MP3MetaFixAppState;
        if (!appState || !appState.hasSession) {
          showToast('No active audio session found', 'info');
          return;
        }
        const text = appState.metadata.lyrics || '';
        if (!text) {
          showToast('No lyrics found in loaded audio track', 'info');
          return;
        }
        lyricsState.lines = parseLyricsText(text, appState.metadata.synced_lyrics || []);
        lyricsState.currentIndex = findNextTargetIndex(0);
        renderQueue();
        showToast('Pulled lyrics from audio track', 'success');
      });
    }

    // Raw Lyrics Apply
    if (dom.btnRawApply && dom.rawLyricsTextarea) {
      dom.btnRawApply.addEventListener('click', () => {
        const text = dom.rawLyricsTextarea.value.trim();
        if (!text) {
          showToast('Please enter lyrics text', 'info');
          return;
        }
        lyricsState.lines = parseLyricsText(text, lyricsState.lines.filter(l => l.time_ms !== null));
        lyricsState.currentIndex = findNextTargetIndex(0);
        switchLyricsMode('sync');
        showToast('Updated lyrics queue', 'success');
      });
    }

    // Keyboard Shortcuts (when in sync mode & not focusing an input)
    window.addEventListener('keydown', (e) => {
      // Only handle when lyrics panel is visible and activeMode is 'sync'
      if (dom.panelLyricsView && !dom.panelLyricsView.classList.contains('hidden') && lyricsState.activeMode === 'sync') {
        const activeTag = document.activeElement ? document.activeElement.tagName.toLowerCase() : '';
        if (activeTag === 'input' || activeTag === 'textarea') return;

        if (e.code === 'Space') {
          e.preventDefault();
          stampCurrentLine();
        } else if (e.key === 'Backspace') {
          e.preventDefault();
          stepBackOneLine();
        } else if (e.key === '[' || e.key === '-') {
          e.preventDefault();
          nudgeSelectedOrLast(-100);
        } else if (e.key === ']' || e.key === '=' || e.key === '+') {
          e.preventDefault();
          nudgeSelectedOrLast(100);
        } else if (e.key === 'ArrowUp') {
          e.preventDefault();
          navigateSelectedLine(-1);
        } else if (e.key === 'ArrowDown') {
          e.preventDefault();
          navigateSelectedLine(1);
        }
      }
    });

    // Listen for session load events from app.js
    window.addEventListener('mp3metafix:session-loaded', () => {
      syncTrackState();
    });
  }

  // Toast Helper
  function showToast(message, type = 'info', duration = 3000) {
    const container = document.getElementById('toastContainer');
    if (!container) return;
    const toast = document.createElement('div');
    toast.className = `toast toast-${type}`;
    toast.textContent = message;
    container.appendChild(toast);
    setTimeout(() => {
      toast.style.animation = 'fadeOut 0.3s forwards';
      setTimeout(() => toast.remove(), 300);
    }, duration);
  }

  // Startup
  document.addEventListener('DOMContentLoaded', () => {
    initDOM();
    setupAudioListeners();
    setupEventListeners();
  });

})();
