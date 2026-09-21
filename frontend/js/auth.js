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

    // Settings Item
    const settingsItem = document.createElement('button');
    settingsItem.className = 'dropdown-item';
    settingsItem.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="3"></circle><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z"></path></svg><span>Settings & Security</span>';
    settingsItem.addEventListener('click', () => {
      dropdown.classList.add('hidden');
      userPill.classList.remove('active');
      openSettingsModal();
    });
    dropdown.appendChild(settingsItem);

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
        const inputMaxSessions = document.getElementById('settingMaxSessions');
        const inputMaxStorage = document.getElementById('settingMaxStorage');
        const adminSection = document.getElementById('settingsAdminSection');

        if (toggleGuest) toggleGuest.checked = !!(settings.guest_mode ?? settings.guest_mode_enabled);
        if (inputMaxSessions) inputMaxSessions.value = settings.max_sessions || 10;
        if (inputMaxStorage) inputMaxStorage.value = settings.max_global_storage_mb || settings.max_temp_storage_mb || 2048;

        if (adminSection) {
          if (AuthState.role === 'admin') {
            adminSection.classList.remove('hidden');
          } else {
            adminSection.classList.add('hidden');
          }
        }
      }
    } catch (err) {
      console.warn('Could not load current settings:', err);
    }

    openModal('settingsModal');
  }

  async function handleSaveSettings(e) {
    e.preventDefault();
    if (AuthState.role !== 'admin') {
      notify('Only administrators can modify system settings', 'error');
      return;
    }

    const toggleGuest = document.getElementById('settingGuestMode');
    const inputMaxSessions = document.getElementById('settingMaxSessions');
    const inputMaxStorage = document.getElementById('settingMaxStorage');
    const submitBtn = document.getElementById('btnSaveSettings');

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
        notify('System settings updated successfully', 'success');
        AuthState.guestMode = isGuest;
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

  // Expose public API
  window.MP3MetaFixAuth = {
    state: AuthState,
    checkAuthStatus,
    openSettingsModal,
    openModal,
    closeModal,
  };
})();
