/**
 * MP3MetaFix - Administrator Control Center Controller (/admin)
 * Manages live resource telemetry, system access policies, quotas, software updates, and credentials.
 */

(function () {
  'use strict';

  let telemetryTimer = null;
  let isCheckingUpdates = false;

  // Formatting helpers
  function formatUptime(seconds) {
    const d = Math.floor(seconds / (3600 * 24));
    const h = Math.floor((seconds % (3600 * 24)) / 3600);
    const m = Math.floor((seconds % 3600) / 60);
    const s = seconds % 60;
    if (d > 0) return `${d}d ${h}h ${m}m`;
    if (h > 0) return `${h}h ${m}m ${s}s`;
    if (m > 0) return `${m}m ${s}s`;
    return `${s}s`;
  }

  function notify(message, type = 'info') {
    if (typeof window.showToast === 'function') {
      window.showToast(message, type);
      return;
    }
    const container = document.getElementById('toastContainer');
    if (!container) return;
    const toast = document.createElement('div');
    toast.className = `toast toast-${type}`;
    const span = document.createElement('span');
    span.textContent = message;
    toast.appendChild(span);
    container.appendChild(toast);
    setTimeout(() => {
      toast.style.opacity = '0';
      toast.style.transform = 'translateY(10px) scale(0.95)';
      setTimeout(() => toast.remove(), 250);
    }, 3500);
  }

  // --- 1. Live System Diagnostics Telemetry ---
  async function fetchTelemetry() {
    try {
      const res = await fetch('/api/system/stats');
      if (res.status === 401 || res.status === 403) {
        stopTelemetryPolling();
        return;
      }
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();

      // Update version badge
      const verBadge = document.getElementById('adminVersionBadge');
      if (verBadge && data.version) verBadge.textContent = `v${data.version}`;

      const cardVerBadge = document.getElementById('updateCardVerBadge');
      if (cardVerBadge && data.version) cardVerBadge.textContent = `v${data.version}`;

      // CPU
      if (data.cpu) {
        document.getElementById('statCpuCores').textContent = `${data.cpu.cores} CPU Core${data.cpu.cores > 1 ? 's' : ''}`;
        document.getElementById('statCpuPercent').textContent = `${data.cpu.estimated_percent}%`;
        document.getElementById('statCpuMeter').style.width = `${Math.min(100, data.cpu.estimated_percent)}%`;
        document.getElementById('statCpuLoadAvg').textContent = `${data.cpu.load_1m} / ${data.cpu.load_5m} / ${data.cpu.load_15m}`;
      }

      // RAM
      if (data.memory) {
        document.getElementById('statRamTotal').textContent = `${data.memory.total_mb} MB Total`;
        document.getElementById('statRamPercent').textContent = `${data.memory.used_percent}%`;
        document.getElementById('statRamMeter').style.width = `${Math.min(100, data.memory.used_percent)}%`;
        document.getElementById('statRamDetails').textContent = `${data.memory.used_mb}MB / ${data.memory.available_mb}MB free`;
      }

      // Host Disk
      if (data.disk) {
        document.getElementById('statDiskTotal').textContent = `${data.disk.total_gb} GB Total`;
        document.getElementById('statDiskPercent').textContent = `${data.disk.used_percent}%`;
        document.getElementById('statDiskMeter').style.width = `${Math.min(100, data.disk.used_percent)}%`;
        document.getElementById('statDiskFree').textContent = `${data.disk.free_gb} GB Free`;
      }

      // App Storage
      if (data.app_storage) {
        document.getElementById('statActiveSessions').textContent = `${data.app_storage.active_sessions_count} Active Session${data.app_storage.active_sessions_count === 1 ? '' : 's'}`;
        document.getElementById('statStorageSize').textContent = `${data.app_storage.temp_storage_mb} MB`;
        document.getElementById('statStorageMeter').style.width = `${Math.min(100, data.app_storage.temp_storage_used_percent)}%`;
        document.getElementById('statStorageQuota').textContent = `${data.app_storage.temp_storage_used_percent}% of ${data.app_storage.max_temp_storage_mb}MB`;
      }

      // Network & Runtime
      if (data.network) {
        document.getElementById('statEnvBind').textContent = `${data.network.host}:${data.network.port}`;
        document.getElementById('statEnvProxy').textContent = data.network.trust_proxies ? 'Enabled' : 'Disabled';
        if (data.network.proxy_host) {
          const proto = data.network.proxy_host.startsWith('http') ? '' : 'https://';
          const domainUrl = `${proto}${data.network.proxy_host}`;
          const domainEl = document.getElementById('statEnvDomain');
          domainEl.textContent = '';
          const link = document.createElement('a');
          link.href = domainUrl;
          link.target = '_blank';
          link.rel = 'noopener noreferrer';
          link.className = 'env-link';
          link.textContent = data.network.proxy_host;
          domainEl.appendChild(link);
        } else {
          document.getElementById('statEnvDomain').textContent = 'Not Configured';
        }
      }

      if (typeof data.uptime_seconds === 'number') {
        document.getElementById('statEnvUptime').textContent = formatUptime(data.uptime_seconds);
      }

      const statusBadge = document.getElementById('adminStatusBadge');
      if (statusBadge) {
        statusBadge.innerHTML = '<span class="status-dot"></span> Online';
        statusBadge.classList.remove('status-offline');
      }
    } catch (err) {
      console.warn('Admin telemetry polling error:', err);
      const statusBadge = document.getElementById('adminStatusBadge');
      if (statusBadge) {
        statusBadge.innerHTML = '<span class="status-dot dot-offline"></span> Disconnected';
        statusBadge.classList.add('status-offline');
      }
    }
  }

  function startTelemetryPolling() {
    if (telemetryTimer) return;
    fetchTelemetry();
    telemetryTimer = setInterval(fetchTelemetry, 4000);
  }

  function stopTelemetryPolling() {
    if (telemetryTimer) {
      clearInterval(telemetryTimer);
      telemetryTimer = null;
    }
  }

  // --- 2. System Settings & Quota Management ---
  async function loadSettings() {
    try {
      const res = await fetch('/api/settings');
      if (!res.ok) return;
      const settings = await res.json();

      const toggleGuest = document.getElementById('adminGuestMode');
      const inputMaxSessions = document.getElementById('adminMaxSessions');
      const inputMaxStorage = document.getElementById('adminMaxStorage');

      if (toggleGuest) toggleGuest.checked = !!(settings.guest_mode ?? settings.guest_mode_enabled);
      if (inputMaxSessions) inputMaxSessions.value = settings.max_sessions || 10;
      if (inputMaxStorage) inputMaxStorage.value = settings.max_global_storage_mb || settings.max_temp_storage_mb || 2048;
    } catch (err) {
      console.warn('Could not load admin settings:', err);
    }
  }

  async function handleSaveSettings(e) {
    e.preventDefault();
    const toggleGuest = document.getElementById('adminGuestMode');
    const inputMaxSessions = document.getElementById('adminMaxSessions');
    const inputMaxStorage = document.getElementById('adminMaxStorage');
    const submitBtn = document.getElementById('btnSaveAdminSettings');

    const isGuest = toggleGuest ? toggleGuest.checked : false;
    const maxStorage = inputMaxStorage ? parseInt(inputMaxStorage.value, 10) : 2048;
    const maxSessions = inputMaxSessions ? parseInt(inputMaxSessions.value, 10) : 10;

    const payload = {
      guest_mode_enabled: isGuest,
      guest_mode: isGuest,
      max_global_storage_mb: maxStorage,
      max_temp_storage_mb: maxStorage,
      max_sessions: maxSessions,
    };

    if (submitBtn) submitBtn.disabled = true;

    try {
      const res = await fetch('/api/settings', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      const data = await res.json();

      if (res.ok) {
        notify('System policies and quotas updated successfully', 'success');
      } else {
        notify(data.detail || 'Failed to update system policies', 'error');
      }
    } catch (err) {
      notify('Network error saving settings', 'error');
    } finally {
      if (submitBtn) submitBtn.disabled = false;
    }
  }

  // --- 3. Software Updates & Release Inspector ---
  async function checkForUpdates(force = false) {
    if (isCheckingUpdates) return;
    isCheckingUpdates = true;
    const btn = document.getElementById('btnAdminCheckUpdates');
    const spinner = btn ? btn.querySelector('.spin-on-load') : null;
    const timeLabel = document.getElementById('adminUpdateCheckedAt');
    const cardOk = document.getElementById('adminUpdateUpToDateCard');
    const cardWarn = document.getElementById('adminUpdateAvailCard');
    const availHeading = document.getElementById('adminAvailHeading');
    const availDesc = document.getElementById('adminAvailDesc');
    const changelogBox = document.getElementById('adminChangelogBox');
    const releaseNotesLink = document.getElementById('adminReleaseNotesLink');

    if (btn) btn.disabled = true;
    if (spinner) spinner.classList.add('spinning');
    if (timeLabel) timeLabel.textContent = 'Checking GitHub repository...';

    try {
      const url = force ? '/api/updates/check?force=true' : '/api/updates/check';
      const res = await fetch(url);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();

      const nowStr = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
      if (timeLabel) timeLabel.textContent = `Last checked: ${nowStr}`;

      if (data.update_available) {
        if (cardOk) cardOk.classList.add('hidden');
        if (cardWarn) cardWarn.classList.remove('hidden');

        if (availHeading) availHeading.textContent = `New Release Available: v${data.latest_version}`;
        if (availDesc) availDesc.textContent = `Version v${data.latest_version} is available on GitHub (Current: v${data.current_version}).`;

        if (changelogBox && data.release_notes) {
          changelogBox.textContent = data.release_notes;
        }

        if (releaseNotesLink && data.release_url) {
          releaseNotesLink.href = data.release_url;
        }
      } else {
        if (cardOk) cardOk.classList.remove('hidden');
        if (cardWarn) cardWarn.classList.add('hidden');
      }
    } catch (err) {
      console.warn('Update check error:', err);
      if (timeLabel) timeLabel.textContent = 'Last check failed (Network or rate limit)';
    } finally {
      isCheckingUpdates = false;
      if (btn) btn.disabled = false;
      if (spinner) spinner.classList.remove('spinning');
    }
  }

  // --- 4. In-App Software Update Installer (SSE Stream) ---
  async function handleInstallUpdate() {
    const btn = document.getElementById('btnAdminInstallUpdate');
    const label = document.getElementById('btnAdminInstallUpdateLabel');
    const icon = document.getElementById('iconInstallUpdate');
    const logPanel = document.getElementById('adminUpdateLogPanel');
    const logBox = document.getElementById('adminUpdateLog');
    const statusEl = document.getElementById('adminUpdateStatus');

    if (!btn || btn.disabled) return;
    btn.disabled = true;
    if (label) label.textContent = 'Installing…';
    if (icon) icon.classList.add('spinning');

    if (logPanel) logPanel.classList.remove('hidden');
    if (logBox) logBox.textContent = '';
    if (statusEl) { statusEl.textContent = ''; statusEl.classList.add('hidden'); }

    try {
      const res = await fetch('/api/updates/apply', {
        method: 'POST',
        headers: { 'X-Requested-With': 'XMLHttpRequest' },
      });

      if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: `HTTP ${res.status}` }));
        const msg = document.createElement('span');
        msg.textContent = `Error: ${err.detail || 'Failed to start update.'}`;
        if (logBox) logBox.appendChild(msg);
        if (statusEl) {
          statusEl.textContent = '✗ Installation failed.';
          statusEl.style.color = 'var(--clr-error, #f87171)';
          statusEl.classList.remove('hidden');
        }
        return;
      }

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const parts = buffer.split('\n\n');
        buffer = parts.pop() ?? '';
        for (const part of parts) {
          const dataLine = part.split('\n').find(l => l.startsWith('data: '));
          if (!dataLine) continue;
          try {
            const payload = JSON.parse(dataLine.slice(6));
            if ((payload.type === 'log' || payload.type === 'step') && payload.message) {
              if (logBox) {
                const line = document.createElement('div');
                line.textContent = payload.message;
                logBox.appendChild(line);
                logBox.scrollTop = logBox.scrollHeight;
              }
            } else if (payload.type === 'complete') {
              if (statusEl) {
                statusEl.textContent = payload.message || '✓ Update installed. Server is restarting…';
                statusEl.style.color = 'var(--clr-success, #4ade80)';
                statusEl.classList.remove('hidden');
              }
            } else if (payload.type === 'error') {
              if (logBox) {
                const line = document.createElement('div');
                line.textContent = `[Error] ${payload.message || 'Unknown error.'}`;
                line.style.color = 'var(--clr-error, #f87171)';
                logBox.appendChild(line);
                logBox.scrollTop = logBox.scrollHeight;
              }
              if (statusEl) {
                statusEl.textContent = '✗ Installation failed. Check the log above.';
                statusEl.style.color = 'var(--clr-error, #f87171)';
                statusEl.classList.remove('hidden');
              }
            }
          } catch (_) { /* ignore malformed SSE lines */ }
        }
      }
    } catch (err) {
      notify('Network error during update installation', 'error');
    } finally {
      if (btn) btn.disabled = false;
      if (label) label.textContent = 'Install Update';
      if (icon) icon.classList.remove('spinning');
    }
  }

  // --- 5. Administrator Password Change ---

  async function handleChangePassword(e) {
    e.preventDefault();
    const form = e.target;
    const currentPassword = form.elements['adminPwdCurrent'].value;
    const newPassword = form.elements['adminPwdNew'].value;
    const confirmPassword = form.elements['adminPwdConfirm'].value;
    const submitBtn = document.getElementById('btnAdminChangePassword');

    if (newPassword !== confirmPassword) {
      notify('New passwords do not match', 'error');
      return;
    }

    if (newPassword.length < 8) {
      notify('New password must be at least 8 characters', 'error');
      return;
    }

    if (submitBtn) submitBtn.disabled = true;

    try {
      const res = await fetch('/api/auth/change-password', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          current_password: currentPassword,
          new_password: newPassword,
        }),
      });
      const data = await res.json();

      if (res.ok) {
        notify('Administrator password updated successfully', 'success');
        form.reset();
      } else {
        notify(data.detail || 'Failed to update password', 'error');
      }
    } catch (err) {
      notify('Network error updating password', 'error');
    } finally {
      if (submitBtn) submitBtn.disabled = false;
    }
  }

  // --- Initialization & Event Listeners ---
  document.addEventListener('DOMContentLoaded', () => {
    // Form handlers
    const formSettings = document.getElementById('formAdminSettings');
    if (formSettings) {
      formSettings.addEventListener('submit', handleSaveSettings);
    }

    const formPassword = document.getElementById('formAdminPassword');
    if (formPassword) {
      formPassword.addEventListener('submit', handleChangePassword);
    }

    const btnUpdates = document.getElementById('btnAdminCheckUpdates');
    if (btnUpdates) {
      btnUpdates.addEventListener('click', () => checkForUpdates(true));
    }

    const btnInstall = document.getElementById('btnAdminInstallUpdate');
    if (btnInstall) {
      btnInstall.addEventListener('click', handleInstallUpdate);
    }

    // React to auth lifecycle
    window.addEventListener('mp3metafix:auth-ready', (e) => {
      if (e.detail && e.detail.authenticated && e.detail.role === 'admin') {
        startTelemetryPolling();
        loadSettings();
        checkForUpdates(false);
      } else {
        stopTelemetryPolling();
      }
    });

    window.addEventListener('mp3metafix:auth-logout', () => {
      stopTelemetryPolling();
    });
  });
})();
