/**
 * MP3MetaFix - Unified Authentication & Settings Controller
 * Handles first-time setup wizard, login/logout, guest mode, and administrator settings across all pages.
 */

(function () {
  'use strict';

  const AuthState = {
    initialized: true,
    authenticated: false,
    username: '',
    role: '',
    guestMode: false,
    isGuest: false,
    sunoIntegrationEnabled: false,
  };

  // Safe toast notifier fallback
  function notify(message, type = 'info') {
    if (typeof window.showToast === 'function') {
      window.showToast(message, type);
      return;
    }
    const container = document.getElementById('toastContainer');
    if (!container) {
      console.log(`[${type.toUpperCase()}] ${message}`);
      return;
    }
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

  // Helper to open/close modals
  function openModal(modalId) {
    const el = document.getElementById(modalId);
    if (el) {
      el.classList.remove('hidden');
    }
  }

  function closeModal(modalId) {
    const el = document.getElementById(modalId);
    if (el) {
      el.classList.add('hidden');
    }
  }

  // --- Header Account Pill & Dropdown Management ---
  function renderHeaderAuth() {
    const container = document.getElementById('headerAuthContainer');
    if (!container) return;

    // Clear existing
    container.innerHTML = '';

    if (!AuthState.authenticated) {
      // Guest or Unauthenticated State
      if (AuthState.guestMode) {
        const guestPill = document.createElement('div');
        guestPill.className = 'auth-user-pill';
        guestPill.title = 'You are currently browsing in Guest Mode';

        const avatar = document.createElement('div');
        avatar.className = 'auth-avatar avatar-guest';
        avatar.textContent = 'G';

        const nameSpan = document.createElement('span');
        nameSpan.className = 'auth-username';
        nameSpan.textContent = 'Guest';

        const roleSpan = document.createElement('span');
        roleSpan.className = 'auth-role-tag role-guest';
        roleSpan.textContent = 'Guest';

        const helpBtn = document.createElement('button');
        helpBtn.type = 'button';
        helpBtn.className = 'btn btn-secondary btn-xs';
        helpBtn.style.marginLeft = '0.35rem';
        helpBtn.title = 'Documentation & Help';
        helpBtn.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="13" height="13"><circle cx="12" cy="12" r="10"></circle><path d="M9.09 9a3 3 0 0 1 5.83 1c0 2-3 3-3 3"></path><line x1="12" y1="17" x2="12.01" y2="17"></line></svg>';
        helpBtn.addEventListener('click', (e) => {
          e.stopPropagation();
          openHelpModal();
        });

        const loginBtn = document.createElement('button');
        loginBtn.className = 'btn btn-secondary btn-xs';
        loginBtn.style.marginLeft = '0.35rem';
        loginBtn.textContent = 'Log In';
        loginBtn.addEventListener('click', (e) => {
          e.stopPropagation();
          openModal('loginModal');
        });

        guestPill.appendChild(avatar);
        guestPill.appendChild(nameSpan);
        guestPill.appendChild(roleSpan);
        guestPill.appendChild(helpBtn);
        guestPill.appendChild(loginBtn);
        container.appendChild(guestPill);
      } else {
        const loginBtn = document.createElement('button');
        loginBtn.className = 'btn btn-primary btn-sm';
        loginBtn.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="15" height="15"><path d="M15 3h4a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2h-4"></path><polyline points="10 17 15 12 10 7"></polyline><line x1="15" y1="12" x2="3" y2="12"></line></svg><span>Sign In</span>';
        loginBtn.addEventListener('click', () => openModal('loginModal'));
        container.appendChild(loginBtn);
      }
      return;
    }

    // Authenticated User Pill
    const userPill = document.createElement('button');
    userPill.className = 'auth-user-pill';
    userPill.id = 'authUserPill';
    userPill.setAttribute('aria-haspopup', 'true');
    userPill.setAttribute('aria-expanded', 'false');

    const avatar = document.createElement('div');
    avatar.className = 'auth-avatar';
    avatar.textContent = (AuthState.username || 'U').charAt(0).toUpperCase();

    const nameSpan = document.createElement('span');
    nameSpan.className = 'auth-username';
    nameSpan.textContent = AuthState.username;

    const roleSpan = document.createElement('span');
    roleSpan.className = `auth-role-tag role-${AuthState.role === 'admin' ? 'admin' : 'user'}`;
    roleSpan.textContent = AuthState.role === 'admin' ? 'Admin' : 'User';

    const chevronSvg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    chevronSvg.setAttribute('viewBox', '0 0 24 24');
    chevronSvg.setAttribute('fill', 'none');
    chevronSvg.setAttribute('stroke', 'currentColor');
    chevronSvg.setAttribute('stroke-width', '2');
    chevronSvg.setAttribute('class', 'auth-chevron');
    const polyline = document.createElementNS('http://www.w3.org/2000/svg', 'polyline');
    polyline.setAttribute('points', '6 9 12 15 18 9');
    chevronSvg.appendChild(polyline);

    userPill.appendChild(avatar);
    userPill.appendChild(nameSpan);
    userPill.appendChild(roleSpan);
    userPill.appendChild(chevronSvg);

    // Dropdown Menu
    const dropdown = document.createElement('div');
    dropdown.className = 'auth-dropdown-menu hidden';
    dropdown.id = 'authDropdownMenu';

    const userInfo = document.createElement('div');
    userInfo.className = 'dropdown-user-info';
    const dName = document.createElement('div');
    dName.className = 'dropdown-name';
    dName.textContent = AuthState.username;
    const dRole = document.createElement('div');
    dRole.className = 'dropdown-role';
    dRole.textContent = `Signed in as ${AuthState.role === 'admin' ? 'Administrator' : 'User'}`;
    userInfo.appendChild(dName);
    userInfo.appendChild(dRole);
    dropdown.appendChild(userInfo);

    // If admin, add Admin Dashboard link
    if (AuthState.role === 'admin') {
      const adminLink = document.createElement('a');
      adminLink.href = '/admin';
      adminLink.className = 'dropdown-item';
      adminLink.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 2L2 7l10 5 10-5-10-5zM2 17l10 5 10-5M2 12l10 5 10-5"></path></svg><span>Admin Dashboard</span>';
      dropdown.appendChild(adminLink);
    }

    // Quick Settings Item
    const settingsItem = document.createElement('button');
    settingsItem.className = 'dropdown-item';
    settingsItem.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="3"></circle><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z"></path></svg><span>Quick Settings</span>';
    settingsItem.addEventListener('click', () => {
      dropdown.classList.add('hidden');
      userPill.classList.remove('active');
      openSettingsModal();
    });
    dropdown.appendChild(settingsItem);

    // Help & Documentation Item
    const helpItem = document.createElement('button');
    helpItem.type = 'button';
    helpItem.className = 'dropdown-item';
    helpItem.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M2 3h6a4 4 0 0 1 4 4v14a3 3 0 0 0-3-3H2z"></path><path d="M22 3h-6a4 4 0 0 0-4 4v14a3 3 0 0 1 3-3h7z"></path></svg><span>Help & Documentation</span>';
    helpItem.addEventListener('click', () => {
      dropdown.classList.add('hidden');
      userPill.classList.remove('active');
      openHelpModal();
    });
    dropdown.appendChild(helpItem);

    // Logout Item
    const logoutItem = document.createElement('button');
    logoutItem.className = 'dropdown-item dropdown-item-danger';
    logoutItem.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"></path><polyline points="16 17 21 12 16 7"></polyline><line x1="21" y1="12" x2="9" y2="12"></line></svg><span>Sign Out</span>';
    logoutItem.addEventListener('click', handleLogout);
    dropdown.appendChild(logoutItem);

    // Toggle dropdown
    userPill.addEventListener('click', (e) => {
      e.stopPropagation();
      const isClosed = dropdown.classList.contains('hidden');
      if (isClosed) {
        dropdown.classList.remove('hidden');
        userPill.classList.add('active');
        userPill.setAttribute('aria-expanded', 'true');
      } else {
        dropdown.classList.add('hidden');
        userPill.classList.remove('active');
        userPill.setAttribute('aria-expanded', 'false');
      }
    });

    // Close dropdown on outside click
    document.addEventListener('click', (e) => {
      if (!container.contains(e.target)) {
        dropdown.classList.add('hidden');
        userPill.classList.remove('active');
        userPill.setAttribute('aria-expanded', 'false');
      }
    });

    container.appendChild(userPill);
    container.appendChild(dropdown);

    // Dynamic Admin-only DOM elements visibility
    document.querySelectorAll('.admin-only').forEach((el) => {
      if (AuthState.role === 'admin') {
        el.classList.remove('hidden');
      } else {
        el.classList.add('hidden');
      }
    });
  }

  // Check if current route is permitted for unauthenticated guests when guestMode is enabled
  function isGuestAllowedPath() {
    const path = window.location.pathname;
    const isHub = path === '/' || path === '/index.html' || path === '';
    const isApp = path === '/app' || path.startsWith('/app/');
    return isHub || isApp;
  }

  // --- API Authentication Handlers ---
  async function checkAuthStatus() {
    try {
      const res = await fetch('/api/auth/status');
      if (!res.ok) throw new Error('Status check failed');
      const data = await res.json();

      AuthState.initialized = data.initialized;
      AuthState.authenticated = data.authenticated;
      AuthState.username = data.username || '';
      AuthState.role = data.role || '';
      AuthState.guestMode = !!data.guest_mode;
      AuthState.isGuest = !!data.is_guest;
      AuthState.sunoIntegrationEnabled = !!data.suno_integration_enabled;

      renderHeaderAuth();

      // Setup / Login gating rules
      if (!AuthState.initialized) {
        // System not initialized: Force First-Run Setup Wizard
        openModal('setupModal');
        return;
      }

      const path = window.location.pathname;
      const isAdminPage = path === '/admin' || path.startsWith('/admin/');

      if (!AuthState.authenticated) {
        if (AuthState.guestMode && isGuestAllowedPath()) {
          // Allowed as Guest on / and /app
          closeModal('loginModal');
          closeModal('setupModal');
          window.dispatchEvent(new CustomEvent('mp3metafix:auth-ready', { detail: { ...AuthState } }));
        } else {
          // Protected page (/manager, /admin) or Guest mode disabled -> show login modal
          openModal('loginModal');
        }
      } else {
        // Authenticated user
        if (isAdminPage && AuthState.role !== 'admin') {
          notify('Administrator privileges required to access the Admin Control Center', 'error');
          setTimeout(() => { window.location.href = '/'; }, 1500);
          return;
        }
        closeModal('loginModal');
        closeModal('setupModal');
        window.dispatchEvent(new CustomEvent('mp3metafix:auth-ready', { detail: { ...AuthState } }));
      }
    } catch (err) {
      console.warn('Auth status check error:', err);
    }
  }

  async function handleSetup(e) {
    e.preventDefault();
    const form = e.target;
    const username = form.elements['setupUsername'].value.trim();
    const password = form.elements['setupPassword'].value;
    const confirm = form.elements['setupConfirm'].value;
    const errorEl = document.getElementById('setupErrorBanner');
    const errorText = document.getElementById('setupErrorText');
    const submitBtn = form.querySelector('button[type="submit"]');

    if (errorEl) errorEl.classList.add('hidden');

    if (password !== confirm) {
      if (errorEl && errorText) {
        errorText.textContent = 'Passwords do not match.';
        errorEl.classList.remove('hidden');
      } else {
        notify('Passwords do not match', 'error');
      }
      return;
    }

    if (password.length < 8) {
      if (errorEl && errorText) {
        errorText.textContent = 'Password must be at least 8 characters long.';
        errorEl.classList.remove('hidden');
      } else {
        notify('Password must be at least 8 characters long', 'error');
      }
      return;
    }

    if (submitBtn) submitBtn.disabled = true;

    try {
      const res = await fetch('/api/auth/setup', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username, password }),
      });
      const data = await res.json();

      if (res.ok) {
        notify('Administrator setup complete! Welcome.', 'success');
        closeModal('setupModal');
        await checkAuthStatus();
      } else {
        if (errorEl && errorText) {
          errorText.textContent = data.detail || 'Setup failed. Please try again.';
          errorEl.classList.remove('hidden');
        } else {
          notify(data.detail || 'Setup failed', 'error');
        }
      }
    } catch (err) {
      notify('Network error during setup', 'error');
    } finally {
      if (submitBtn) submitBtn.disabled = false;
    }
  }

  async function handleLogin(e) {
    e.preventDefault();
    const form = e.target;
    const username = form.elements['loginUsername'].value.trim();
    const password = form.elements['loginPassword'].value;
    const errorEl = document.getElementById('loginErrorBanner');
    const errorText = document.getElementById('loginErrorText');
    const submitBtn = form.querySelector('button[type="submit"]');

    if (errorEl) errorEl.classList.add('hidden');
    if (submitBtn) submitBtn.disabled = true;

    try {
      const res = await fetch('/api/auth/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username, password }),
      });
      const data = await res.json();

      if (res.ok) {
        notify(`Welcome back, ${data.username}!`, 'success');
        form.reset();
        closeModal('loginModal');
        await checkAuthStatus();
        // If on Hub or Editor, refresh content / session
        if (typeof window.fetchTelemetry === 'function') {
          window.fetchTelemetry();
        }
      } else {
        if (errorEl && errorText) {
          errorText.textContent = data.detail || 'Invalid username or password.';
          errorEl.classList.remove('hidden');
        } else {
          notify(data.detail || 'Login failed', 'error');
        }
      }
    } catch (err) {
      notify('Network error during login', 'error');
    } finally {
      if (submitBtn) submitBtn.disabled = false;
    }
  }

  async function handleLogout() {
    try {
      await fetch('/api/auth/logout', { method: 'POST' });
      notify('You have been signed out', 'info');
      window.dispatchEvent(new CustomEvent('mp3metafix:auth-logout'));
      await checkAuthStatus();
      // If current page requires auth and guest mode is off, modal will re-open
    } catch (err) {
      notify('Error during logout', 'error');
    }
  }

  // --- Settings Modal & API Handlers ---
  async function openSettingsModal() {
    if (!AuthState.authenticated) {
      openModal('loginModal');
      return;
    }

    try {
      const res = await fetch('/api/settings');
      if (res.ok) {
        const settings = await res.json();
        const toggleGuest = document.getElementById('settingGuestMode');
        const toggleSuno = document.getElementById('settingSunoIntegration');
        const inputMaxSessions = document.getElementById('settingMaxSessions');
        const inputMaxStorage = document.getElementById('settingMaxStorage');
        const inputSessionTtl = document.getElementById('settingSessionTtl');
        const inputMaxUploadSize = document.getElementById('settingMaxUploadSize');
        const adminSection = document.getElementById('settingsAdminSection');
        const formSystemSettings = document.getElementById('formSystemSettings');
        const emptyNotice = document.getElementById('quickSettingsEmptyNotice');
        const adminLink = document.getElementById('quickSettingsAdminLink');

        if (toggleGuest) {
          toggleGuest.checked = !!(settings.guest_mode_enabled ?? settings.guest_mode);
          if (!toggleGuest.dataset.bound) {
            toggleGuest.dataset.bound = 'true';
            toggleGuest.addEventListener('change', (e) => handleQuickToggle('guest_mode_enabled', e.target.checked, e.target));
          }
        }
        if (toggleSuno) {
          toggleSuno.checked = !!settings.suno_integration_enabled;
          if (!toggleSuno.dataset.bound) {
            toggleSuno.dataset.bound = 'true';
            toggleSuno.addEventListener('change', (e) => handleQuickToggle('suno_integration_enabled', e.target.checked, e.target));
          }
        }
        if (inputMaxSessions) inputMaxSessions.value = settings.max_sessions || 10;
        if (inputMaxStorage) inputMaxStorage.value = settings.max_global_storage_mb || settings.max_temp_storage_mb || 2048;
        if (inputSessionTtl) inputSessionTtl.value = settings.session_ttl_minutes || 60;
        if (inputMaxUploadSize) inputMaxUploadSize.value = settings.max_upload_size_mb || 150;

        const pinned = Array.isArray(settings.quick_settings_pinned)
          ? settings.quick_settings_pinned
          : ['guest_mode_enabled', 'max_sessions', 'max_global_storage_mb'];

        const guestBlock = document.getElementById('settingBlock_guest_mode_enabled');
        const limitsBlock = document.getElementById('settingBlock_limits');
        const maxSessionsBlock = document.getElementById('settingBlock_max_sessions');
        const maxStorageBlock = document.getElementById('settingBlock_max_global_storage_mb');
        const sessionTtlBlock = document.getElementById('settingBlock_session_ttl_minutes');
        const maxUploadBlock = document.getElementById('settingBlock_max_upload_size_mb');
        const sunoBlock = document.getElementById('settingBlock_suno_integration_enabled');
        const updatesBlock = document.getElementById('settingBlock_software_updates');

        const hasGuest = pinned.includes('guest_mode_enabled') || pinned.includes('guest_mode');
        const hasSessions = pinned.includes('max_sessions');
        const hasStorage = pinned.includes('max_global_storage_mb') || pinned.includes('max_temp_storage_mb');
        const hasTtl = pinned.includes('session_ttl_minutes');
        const hasUpload = pinned.includes('max_upload_size_mb');
        const hasSuno = pinned.includes('suno_integration_enabled') || pinned.includes('suno_enabled');
        const hasUpdates = pinned.includes('software_updates');
        const hasAnyLimit = hasSessions || hasStorage || hasTtl || hasUpload;

        const setVisibility = (el, isVisible) => {
          if (!el) return;
          el.classList.toggle('hidden', !isVisible);
          el.style.display = isVisible ? '' : 'none';
        };

        setVisibility(guestBlock, hasGuest);
        setVisibility(maxSessionsBlock, hasSessions);
        setVisibility(maxStorageBlock, hasStorage);
        setVisibility(sessionTtlBlock, hasTtl);
        setVisibility(maxUploadBlock, hasUpload);
        setVisibility(limitsBlock, hasAnyLimit);
        setVisibility(sunoBlock, hasSuno);
        setVisibility(updatesBlock, hasUpdates);

        const hasAnyPinned = hasGuest || hasAnyLimit || hasSuno || hasUpdates;
        setVisibility(emptyNotice, !hasAnyPinned);
        setVisibility(formSystemSettings, hasAnyPinned);

        if (adminSection) {
          if (AuthState.role === 'admin') {
            adminSection.classList.remove('hidden');
          } else {
            adminSection.classList.add('hidden');
          }
        }

        if (adminLink) {
          if (AuthState.role === 'admin') {
            adminLink.classList.remove('hidden');
          } else {
            adminLink.classList.add('hidden');
          }
        }
      }
    } catch (err) {
      console.warn('Could not load current settings:', err);
    }

    openModal('settingsModal');
  }

  async function handleQuickToggle(settingKey, isEnabled, checkboxEl) {
    if (AuthState.role !== 'admin') {
      notify('Only administrators can modify system settings', 'error');
      if (checkboxEl) checkboxEl.checked = !isEnabled;
      return;
    }
    const payload = {};
    payload[settingKey] = isEnabled;
    if (settingKey === 'guest_mode_enabled') {
      payload.guest_mode = isEnabled;
    }
    try {
      const res = await fetch('/api/settings', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      const data = await res.json();
      if (res.ok) {
        if (settingKey === 'suno_integration_enabled') {
          AuthState.sunoIntegrationEnabled = isEnabled;
          notify(isEnabled ? 'Suno metadata detection and sync enabled' : 'Suno metadata detection and sync disabled', 'success');
        } else if (settingKey === 'guest_mode_enabled') {
          AuthState.guestMode = isEnabled;
          notify(isEnabled ? 'Guest mode enabled' : 'Guest mode disabled', 'success');
        } else {
          notify('Setting updated', 'success');
        }
        window.dispatchEvent(new CustomEvent('mp3metafix:auth-ready', { detail: { ...AuthState } }));
        renderHeaderAuth();
      } else {
        notify(data.detail || 'Failed to update setting', 'error');
        if (checkboxEl) checkboxEl.checked = !isEnabled;
      }
    } catch (err) {
      notify('Network error saving setting', 'error');
      if (checkboxEl) checkboxEl.checked = !isEnabled;
    }
  }

  async function handleSaveSettings(e) {
    e.preventDefault();
    if (AuthState.role !== 'admin') {
      notify('Only administrators can modify system settings', 'error');
      return;
    }

    const toggleGuest = document.getElementById('settingGuestMode');
    const toggleSuno = document.getElementById('settingSunoIntegration');
    const inputMaxSessions = document.getElementById('settingMaxSessions');
    const inputMaxStorage = document.getElementById('settingMaxStorage');
    const inputSessionTtl = document.getElementById('settingSessionTtl');
    const inputMaxUpload = document.getElementById('settingMaxUploadSize');
    const submitBtn = document.getElementById('btnSaveSettings');

    const isGuest = toggleGuest ? toggleGuest.checked : false;
    const isSuno = toggleSuno ? toggleSuno.checked : false;
    const maxStorage = inputMaxStorage ? parseInt(inputMaxStorage.value, 10) : 2048;
    const maxSessions = inputMaxSessions ? parseInt(inputMaxSessions.value, 10) : 10;
    const sessionTtl = inputSessionTtl ? parseInt(inputSessionTtl.value, 10) : 60;
    const maxUpload = inputMaxUpload ? parseInt(inputMaxUpload.value, 10) : 150;

    const payload = {
      guest_mode_enabled: isGuest,
      guest_mode: isGuest,
      suno_integration_enabled: isSuno,
      max_global_storage_mb: maxStorage,
      max_temp_storage_mb: maxStorage,
      max_sessions: maxSessions,
      session_ttl_minutes: sessionTtl,
      max_upload_size_mb: maxUpload,
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
        notify('Quick Settings updated successfully', 'success');
        AuthState.guestMode = isGuest;
        AuthState.sunoIntegrationEnabled = isSuno;
        window.dispatchEvent(new CustomEvent('mp3metafix:auth-ready', { detail: { ...AuthState } }));
        renderHeaderAuth();
      } else {
        notify(data.detail || 'Failed to save settings', 'error');
      }
    } catch (err) {
      notify('Network error saving settings', 'error');
    } finally {
      if (submitBtn) submitBtn.disabled = false;
    }
  }

  async function handleChangePassword(e) {
    e.preventDefault();
    const form = e.target;
    const currentPassword = form.elements['pwdCurrent'].value;
    const newPassword = form.elements['pwdNew'].value;
    const confirmPassword = form.elements['pwdConfirm'].value;
    const errorEl = document.getElementById('pwdErrorBanner');
    const errorText = document.getElementById('pwdErrorText');
    const submitBtn = form.querySelector('button[type="submit"]');

    if (errorEl) errorEl.classList.add('hidden');

    if (newPassword !== confirmPassword) {
      if (errorEl && errorText) {
        errorText.textContent = 'New passwords do not match.';
        errorEl.classList.remove('hidden');
      } else {
        notify('New passwords do not match', 'error');
      }
      return;
    }

    if (newPassword.length < 8) {
      if (errorEl && errorText) {
        errorText.textContent = 'New password must be at least 8 characters long.';
        errorEl.classList.remove('hidden');
      } else {
        notify('New password must be at least 8 characters long', 'error');
      }
      return;
    }

    if (submitBtn) submitBtn.disabled = true;

    try {
      const res = await fetch('/api/auth/change-password', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ current_password: currentPassword, new_password: newPassword }),
      });
      const data = await res.json();

      if (res.ok) {
        notify('Password updated. Sign in again on each device.', 'success');
        form.reset();
        closeModal('settingsModal');
        await checkAuthStatus();
      } else {
        if (errorEl && errorText) {
          errorText.textContent = data.detail || 'Could not change password.';
          errorEl.classList.remove('hidden');
        } else {
          notify(data.detail || 'Could not change password', 'error');
        }
      }
    } catch (err) {
      notify('Network error changing password', 'error');
    } finally {
      if (submitBtn) submitBtn.disabled = false;
    }
  }

  async function handleQuickCheckUpdates() {
    const btn = document.getElementById('btnQuickCheckUpdates');
    const statusText = document.getElementById('quickUpdateStatusText');
    if (btn) btn.disabled = true;
    if (statusText) statusText.textContent = 'Checking GitHub releases...';
    try {
      const res = await fetch('/api/updates/check?force=true');
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      if (data.update_available) {
        if (statusText) statusText.textContent = `New update available: v${data.latest_version}`;
        notify(`New MP3MetaFix version available: v${data.latest_version}`, 'info');
      } else {
        if (statusText) statusText.textContent = `MP3MetaFix is up to date (v${data.current_version || '0.5.2-dev'})`;
        notify('MP3MetaFix is up to date', 'success');
      }
    } catch (err) {
      if (statusText) statusText.textContent = 'Could not check for updates.';
      notify('Failed to check for updates', 'error');
    } finally {
      if (btn) btn.disabled = false;
    }
  }

  // --- Initialize Event Listeners on DOM Ready ---
  document.addEventListener('DOMContentLoaded', () => {
    // Setup Form
    const formSetup = document.getElementById('formAuthSetup');
    if (formSetup) {
      formSetup.addEventListener('submit', handleSetup);
    }

    // Login Form
    const formLogin = document.getElementById('formAuthLogin');
    if (formLogin) {
      formLogin.addEventListener('submit', handleLogin);
    }

    // Settings Modal Forms & Buttons
    const formSettings = document.getElementById('formSystemSettings');
    if (formSettings) {
      formSettings.addEventListener('submit', handleSaveSettings);
    }

    const formPassword = document.getElementById('formChangePassword');
    if (formPassword) {
      formPassword.addEventListener('submit', handleChangePassword);
    }

    const btnQuickUpdates = document.getElementById('btnQuickCheckUpdates');
    if (btnQuickUpdates) {
      btnQuickUpdates.addEventListener('click', handleQuickCheckUpdates);
    }

    const btnCloseSettings = document.getElementById('btnCloseSettingsModal');
    if (btnCloseSettings) {
      btnCloseSettings.addEventListener('click', () => closeModal('settingsModal'));
    }

    const btnCloseLogin = document.getElementById('btnCloseLoginModal');
    if (btnCloseLogin) {
      btnCloseLogin.addEventListener('click', () => {
        if (AuthState.guestMode && isGuestAllowedPath()) {
          closeModal('loginModal');
        } else {
          notify('Please sign in to access this workspace', 'info');
        }
      });
    }

    // Global Modal Escape & Backdrop handling for Settings/Login
    window.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') {
        const settingsModal = document.getElementById('settingsModal');
        if (settingsModal && !settingsModal.classList.contains('hidden')) {
          closeModal('settingsModal');
        }
        const loginModal = document.getElementById('loginModal');
        if (loginModal && !loginModal.classList.contains('hidden') && AuthState.guestMode && isGuestAllowedPath()) {
          closeModal('loginModal');
        }
      }
    });

    const settingsModal = document.getElementById('settingsModal');
    if (settingsModal) {
      settingsModal.addEventListener('click', (e) => {
        if (e.target === settingsModal) {
          closeModal('settingsModal');
        }
      });
    }

    const loginModal = document.getElementById('loginModal');
    if (loginModal) {
      loginModal.addEventListener('click', (e) => {
        if (e.target === loginModal && AuthState.guestMode && isGuestAllowedPath()) {
          closeModal('loginModal');
        }
      });
    }

    // Initial Status Handshake
    checkAuthStatus();
  });

  // --- In-App Help Modal Controller ---
  let cachedDocsList = null;

  function renderHelpInline(text, container) {
    const regex = /(`[^`]+`|\*\*[^*]+\*\*|\*[^*]+\*|\[[^\]]+\]\([^)]+\)|<kbd>[^<]+<\/kbd>)/g;
    let lastIndex = 0;
    let match;
    while ((match = regex.exec(text)) !== null) {
      if (match.index > lastIndex) {
        container.appendChild(document.createTextNode(text.substring(lastIndex, match.index)));
      }
      const raw = match[0];
      if (raw.startsWith('`') && raw.endsWith('`')) {
        const code = document.createElement('code');
        code.textContent = raw.slice(1, -1);
        container.appendChild(code);
      } else if (raw.startsWith('**') && raw.endsWith('**')) {
        const strong = document.createElement('strong');
        renderHelpInline(raw.slice(2, -2), strong);
        container.appendChild(strong);
      } else if (raw.startsWith('*') && raw.endsWith('*')) {
        const em = document.createElement('em');
        renderHelpInline(raw.slice(1, -1), em);
        container.appendChild(em);
      } else if (raw.startsWith('<kbd>') && raw.endsWith('</kbd>')) {
        const kbd = document.createElement('kbd');
        kbd.textContent = raw.slice(5, -6);
        container.appendChild(kbd);
      } else if (raw.startsWith('[') && raw.includes('](') && raw.endsWith(')')) {
        const splitIdx = raw.indexOf('](');
        const linkText = raw.substring(1, splitIdx);
        let linkUrl = raw.substring(splitIdx + 2, raw.length - 1);
        const a = document.createElement('a');
        a.textContent = linkText;
        a.href = linkUrl;
        a.target = '_blank';
        a.rel = 'noopener noreferrer';
        container.appendChild(a);
      }
      lastIndex = regex.lastIndex;
    }
    if (lastIndex < text.length) {
      container.appendChild(document.createTextNode(text.substring(lastIndex)));
    }
  }

  function renderHelpMarkdownToDOM(markdownText, container) {
    container.innerHTML = '';
    const lines = markdownText.split('\n');
    let i = 0;
    while (i < lines.length) {
      let line = lines[i];
      if (!line.trim()) { i++; continue; }

      // Code Block
      if (line.trim().startsWith('```')) {
        const codeLines = [];
        i++;
        while (i < lines.length && !lines[i].trim().startsWith('```')) {
          codeLines.push(lines[i]);
          i++;
        }
        i++;
        const pre = document.createElement('pre');
        const code = document.createElement('code');
        code.textContent = codeLines.join('\n');
        pre.appendChild(code);
        container.appendChild(pre);
        continue;
      }

      // Alerts & Quotes
      if (line.trim().startsWith('>')) {
        const quoteLines = [];
        let alertType = 'note';
        while (i < lines.length && lines[i].trim().startsWith('>')) {
          let clean = lines[i].trim().replace(/^>\s?/, '');
          if (clean.startsWith('[!NOTE]')) { alertType = 'note'; clean = clean.replace('[!NOTE]', '').trim(); }
          else if (clean.startsWith('[!TIP]')) { alertType = 'tip'; clean = clean.replace('[!TIP]', '').trim(); }
          else if (clean.startsWith('[!WARNING]')) { alertType = 'warning'; clean = clean.replace('[!WARNING]', '').trim(); }
          else if (clean.startsWith('[!IMPORTANT]')) { alertType = 'important'; clean = clean.replace('[!IMPORTANT]', '').trim(); }
          else if (clean.startsWith('[!CAUTION]')) { alertType = 'caution'; clean = clean.replace('[!CAUTION]', '').trim(); }
          if (clean) quoteLines.push(clean);
          i++;
        }
        const alertDiv = document.createElement('div');
        alertDiv.className = `docs-alert docs-alert-${alertType}`;
        const contentDiv = document.createElement('div');
        contentDiv.className = 'docs-alert-content';
        quoteLines.forEach(ql => {
          const p = document.createElement('p');
          renderHelpInline(ql, p);
          contentDiv.appendChild(p);
        });
        alertDiv.appendChild(contentDiv);
        container.appendChild(alertDiv);
        continue;
      }

      // Headings
      if (line.startsWith('#')) {
        const level = line.match(/^#+/)[0].length;
        const headingText = line.replace(/^#+\s*/, '').trim();
        const heading = document.createElement(`h${Math.min(level, 6)}`);
        renderHelpInline(headingText, heading);
        container.appendChild(heading);
        i++;
        continue;
      }

      // Tables
      if (line.includes('|') && line.trim().startsWith('|')) {
        const tableLines = [];
        while (i < lines.length && lines[i].includes('|') && lines[i].trim().startsWith('|')) {
          tableLines.push(lines[i].trim());
          i++;
        }
        if (tableLines.length >= 2) {
          const table = document.createElement('table');
          const thead = document.createElement('thead');
          const headerRow = document.createElement('tr');
          tableLines[0].split('|').slice(1, -1).forEach(h => {
            const th = document.createElement('th');
            renderHelpInline(h.trim(), th);
            headerRow.appendChild(th);
          });
          thead.appendChild(headerRow);
          table.appendChild(thead);
          const tbody = document.createElement('tbody');
          for (let r = 2; r < tableLines.length; r++) {
            const row = document.createElement('tr');
            tableLines[r].split('|').slice(1, -1).forEach(c => {
              const td = document.createElement('td');
              renderHelpInline(c.trim(), td);
              row.appendChild(td);
            });
            tbody.appendChild(row);
          }
          table.appendChild(tbody);
          const tableWrapper = document.createElement('div');
          tableWrapper.className = 'help-table-wrapper';
          tableWrapper.appendChild(table);
          container.appendChild(tableWrapper);
          continue;
        }
      }

      // Unordered Lists
      if (line.trim().startsWith('- ') || line.trim().startsWith('* ')) {
        const ul = document.createElement('ul');
        while (i < lines.length && (lines[i].trim().startsWith('- ') || lines[i].trim().startsWith('* '))) {
          const li = document.createElement('li');
          renderHelpInline(lines[i].trim().replace(/^[-*]\s+/, ''), li);
          ul.appendChild(li);
          i++;
        }
        container.appendChild(ul);
        continue;
      }

      // Paragraph
      const p = document.createElement('p');
      renderHelpInline(line, p);
      container.appendChild(p);
      i++;
    }
  }

  function ensureHelpModal() {
    let modal = document.getElementById('helpModal');
    if (modal) return modal;

    modal = document.createElement('div');
    modal.id = 'helpModal';
    modal.className = 'modal-overlay hidden';
    modal.setAttribute('role', 'dialog');
    modal.setAttribute('aria-modal', 'true');
    modal.innerHTML = `
      <div class="modal-card modal-xl">
        <div class="modal-header">
          <div class="modal-title-group">
            <div class="modal-icon-badge">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="20" height="20">
                <path d="M2 3h6a4 4 0 0 1 4 4v14a3 3 0 0 0-3-3H2z"></path>
                <path d="M22 3h-6a4 4 0 0 0-4 4v14a3 3 0 0 1 3-3h7z"></path>
              </svg>
            </div>
            <div>
              <h2 id="helpModalTitle" class="modal-title">Help & Documentation</h2>
              <p class="modal-subtitle" id="helpModalSubtitle">Quick guides and workflow reference</p>
            </div>
          </div>
          <button class="btn-close" id="btnCloseHelpModal" aria-label="Close modal">&times;</button>
        </div>
        <div class="help-modal-body">
          <div class="help-tabs-wrapper" id="helpTabsContainer"></div>
          <div class="help-content-container" id="helpContentContainer">
            <div class="docs-loading-state"><div class="spinner"></div><span>Loading documentation...</span></div>
          </div>
        </div>
        <div class="help-modal-footer">
          <a href="/docs" target="_blank" class="help-portal-link" id="helpPortalLink">
            <span>Open Full Documentation Portal</span>
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="14" height="14">
              <line x1="7" y1="17" x2="17" y2="7"></line>
              <polyline points="7 7 17 7 17 17"></polyline>
            </svg>
          </a>
          <button type="button" class="btn btn-secondary btn-sm" id="btnDismissHelp">Close</button>
        </div>
      </div>
    `;

    document.body.appendChild(modal);

    modal.querySelector('#btnCloseHelpModal').addEventListener('click', () => closeModal('helpModal'));
    modal.querySelector('#btnDismissHelp').addEventListener('click', () => closeModal('helpModal'));
    modal.addEventListener('click', (e) => {
      if (e.target === modal) closeModal('helpModal');
    });

    return modal;
  }

  async function openHelpModal(topic) {
    const modal = ensureHelpModal();
    const tabsContainer = modal.querySelector('#helpTabsContainer');
    const contentContainer = modal.querySelector('#helpContentContainer');
    const portalLink = modal.querySelector('#helpPortalLink');

    // Context determination
    if (!topic) {
      const p = window.location.pathname;
      if (p.startsWith('/app')) topic = 'app';
      else if (p.startsWith('/manager')) topic = 'manager';
      else if (p.startsWith('/admin')) topic = 'admin';
      else if (p.startsWith('/projects')) topic = 'projects';
      else topic = 'app';
    }

    openModal('helpModal');

    // Load tabs if not cached
    if (!cachedDocsList) {
      try {
        const res = await fetch('/api/docs/list');
        const data = await res.json();
        cachedDocsList = data.sections || [];
      } catch (err) {
        cachedDocsList = [
          { id: 'app', title: 'MP3MetaFix Editor' },
          { id: 'manager', title: 'MP3MetaManager' },
          { id: 'projects', title: 'MP3Projects Studio' },
          { id: 'admin', title: 'Admin Control Center' },
          { id: 'deployment', title: 'Deployment' },
          { id: 'architecture', title: 'Architecture' },
        ];
      }
    }

    // Render tab buttons
    tabsContainer.innerHTML = '';
    cachedDocsList.forEach(item => {
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = `help-tab-btn ${item.id === topic ? 'active' : ''}`;
      btn.textContent = item.title;
      btn.addEventListener('click', () => {
        tabsContainer.querySelectorAll('.help-tab-btn').forEach(b => b.classList.remove('active'));
        btn.classList.add('active');
        fetchAndRenderHelpContent(item.id, contentContainer, portalLink);
      });
      tabsContainer.appendChild(btn);
    });

    fetchAndRenderHelpContent(topic, contentContainer, portalLink);
  }

  async function fetchAndRenderHelpContent(docId, container, portalLink) {
    if (portalLink) portalLink.href = `/docs#${docId}`;
    container.innerHTML = '<div class="docs-loading-state"><div class="spinner"></div><span>Loading guide...</span></div>';

    try {
      const res = await fetch(`/api/docs/${docId}`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      renderHelpMarkdownToDOM(data.content, container);
    } catch (err) {
      container.innerHTML = `<div class="toast toast-error" style="position:static; margin:1rem 0;"><span>Could not load documentation for "${docId}".</span></div>`;
    }
  }

  // Expose public API
  window.openHelpModal = openHelpModal;
  window.MP3MetaFixAuth = {
    state: AuthState,
    checkAuthStatus,
    openSettingsModal,
    openHelpModal,
    openModal,
    closeModal,
  };
})();
