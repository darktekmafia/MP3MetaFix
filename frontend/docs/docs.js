/**
 * MP3MetaFix - Documentation Portal Controller
 * Fetches Markdown documentation from backend and safely renders to DOM.
 */

(function () {
  'use strict';

  let docsList = [];
  let currentDocId = '';

  // Safe SVG icon provider
  function getCategoryIcon(iconName) {
    const icons = {
      music: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M9 18V5l12-2v13"></path><circle cx="6" cy="18" r="3"></circle><circle cx="18" cy="16" r="3"></circle></svg>',
      folder: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"></path></svg>',
      disc: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"></circle><circle cx="12" cy="12" r="3"></circle></svg>',
      shield: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"></path></svg>',
      server: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="2" y="2" width="20" height="8" rx="2" ry="2"></rect><rect x="2" y="14" width="20" height="8" rx="2" ry="2"></rect><line x1="6" y1="6" x2="6.01" y2="6"></line><line x1="6" y1="18" x2="6.01" y2="18"></line></svg>',
      lock: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="11" width="18" height="11" rx="2" ry="2"></rect><path d="M7 11V7a5 5 0 0 1 10 0v4"></path></svg>',
      cpu: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="4" y="4" width="16" height="16" rx="2"></rect><rect x="9" y="9" width="6" height="6"></rect><line x1="9" y1="1" x2="9" y2="4"></line><line x1="15" y1="1" x2="15" y2="4"></line><line x1="9" y1="20" x2="9" y2="23"></line><line x1="15" y1="20" x2="15" y2="23"></line><line x1="20" y1="9" x2="23" y2="9"></line><line x1="20" y1="14" x2="23" y2="14"></line><line x1="1" y1="9" x2="4" y2="9"></line><line x1="1" y1="14" x2="4" y2="14"></line></svg>',
      'shield-check': '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"></path><polyline points="9 12 11 14 15 10"></polyline></svg>',
      code: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="16 18 22 12 16 6"></polyline><polyline points="8 6 2 12 8 18"></polyline></svg>',
      'file-text': '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path><polyline points="14 2 14 8 20 8"></polyline><line x1="16" y1="13" x2="8" y2="13"></line><line x1="16" y1="17" x2="8" y2="17"></line><polyline points="10 9 9 9 8 9"></polyline></svg>',
    };
    return icons[iconName] || icons['file-text'];
  }

  // Safe in-line text parser: parses bold, code, links, kbd without raw innerHTML
  function parseInlineFormatting(text, container) {
    // Matches: `code`, **bold**, *italic*, [link](url), <kbd>key</kbd>
    const regex = /(`[^`]+`|\*\*[^*]+\*\*|\*[^*]+\*|\[[^\]]+\]\([^)]+\)|<kbd>[^<]+<\/kbd>)/g;
    let lastIndex = 0;
    let match;

    while ((match = regex.exec(text)) !== null) {
      // Text before match
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
        parseInlineFormatting(raw.slice(2, -2), strong);
        container.appendChild(strong);
      } else if (raw.startsWith('*') && raw.endsWith('*')) {
        const em = document.createElement('em');
        parseInlineFormatting(raw.slice(1, -1), em);
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
        
        // Handle internal doc routing (e.g. docs/app/README.md -> #app)
        if (linkUrl.includes('docs/app') || linkUrl.endsWith('/app.md')) linkUrl = '#app';
        else if (linkUrl.includes('docs/manager') || linkUrl.endsWith('/manager.md')) linkUrl = '#manager';
        else if (linkUrl.includes('docs/projects') || linkUrl.endsWith('/projects.md')) linkUrl = '#projects';
        else if (linkUrl.includes('docs/admin') || linkUrl.endsWith('/admin.md')) linkUrl = '#admin';
        else if (linkUrl.includes('DEPLOYMENT.md')) linkUrl = '#deployment';
        else if (linkUrl.includes('ACCOUNT_MIGRATION.md')) linkUrl = '#account_migration';
        else if (linkUrl.includes('ARCHITECTURE.md')) linkUrl = '#architecture';
        else if (linkUrl.includes('SECURITY_HARDENING.md')) linkUrl = '#security';
        else if (linkUrl.includes('development_workflow.md')) linkUrl = '#workflow';
        
        a.href = linkUrl;
        if (linkUrl.startsWith('http') || linkUrl.startsWith('https')) {
          a.target = '_blank';
          a.rel = 'noopener noreferrer';
        }
        container.appendChild(a);
      }

      lastIndex = regex.lastIndex;
    }

    if (lastIndex < text.length) {
      container.appendChild(document.createTextNode(text.substring(lastIndex)));
    }
  }

  // Safe Markdown Parser that directly creates DOM nodes
  function renderMarkdownToDOM(markdownText, targetContainer) {
    targetContainer.innerHTML = '';
    const lines = markdownText.split('\n');
    let i = 0;

    while (i < lines.length) {
      let line = lines[i];

      // Empty line
      if (!line.trim()) {
        i++;
        continue;
      }

      // Code Block
      if (line.trim().startsWith('```')) {
        const lang = line.trim().slice(3).trim() || 'text';
        const codeLines = [];
        i++;
        while (i < lines.length && !lines[i].trim().startsWith('```')) {
          codeLines.push(lines[i]);
          i++;
        }
        i++; // skip closing ```

        const wrapper = document.createElement('div');
        wrapper.className = 'docs-code-block-wrapper';

        const header = document.createElement('div');
        header.className = 'docs-code-header';
        const langSpan = document.createElement('span');
        langSpan.textContent = lang;
        const copyBtn = document.createElement('button');
        copyBtn.className = 'docs-code-copy-btn';
        copyBtn.textContent = 'Copy';
        const fullCode = codeLines.join('\n');
        copyBtn.addEventListener('click', () => {
          navigator.clipboard.writeText(fullCode).then(() => {
            copyBtn.textContent = 'Copied!';
            setTimeout(() => { copyBtn.textContent = 'Copy'; }, 2000);
          });
        });
        header.appendChild(langSpan);
        header.appendChild(copyBtn);
        wrapper.appendChild(header);

        const pre = document.createElement('pre');
        const code = document.createElement('code');
        code.textContent = fullCode;
        pre.appendChild(code);
        wrapper.appendChild(pre);
        targetContainer.appendChild(wrapper);
        continue;
      }

      // Blockquotes & Alerts (e.g. > [!NOTE], > [!WARNING], > [!TIP])
      if (line.trim().startsWith('>')) {
        const quoteLines = [];
        let alertType = 'note';
        while (i < lines.length && lines[i].trim().startsWith('>')) {
          let cleanLine = lines[i].trim().replace(/^>\s?/, '');
          if (cleanLine.startsWith('[!NOTE]')) {
            alertType = 'note';
            cleanLine = cleanLine.replace('[!NOTE]', '').trim();
          } else if (cleanLine.startsWith('[!TIP]')) {
            alertType = 'tip';
            cleanLine = cleanLine.replace('[!TIP]', '').trim();
          } else if (cleanLine.startsWith('[!WARNING]')) {
            alertType = 'warning';
            cleanLine = cleanLine.replace('[!WARNING]', '').trim();
          } else if (cleanLine.startsWith('[!IMPORTANT]')) {
            alertType = 'important';
            cleanLine = cleanLine.replace('[!IMPORTANT]', '').trim();
          } else if (cleanLine.startsWith('[!CAUTION]')) {
            alertType = 'caution';
            cleanLine = cleanLine.replace('[!CAUTION]', '').trim();
          }
          if (cleanLine) quoteLines.push(cleanLine);
          i++;
        }

        const alertDiv = document.createElement('div');
        alertDiv.className = `docs-alert docs-alert-${alertType}`;
        
        const contentDiv = document.createElement('div');
        contentDiv.className = 'docs-alert-content';
        
        const titleDiv = document.createElement('div');
        titleDiv.className = 'docs-alert-title';
        titleDiv.textContent = alertType.toUpperCase();
        contentDiv.appendChild(titleDiv);

        quoteLines.forEach(ql => {
          const p = document.createElement('p');
          parseInlineFormatting(ql, p);
          contentDiv.appendChild(p);
        });

        alertDiv.appendChild(contentDiv);
        targetContainer.appendChild(alertDiv);
        continue;
      }

      // Headings
      if (line.startsWith('#')) {
        const level = line.match(/^#+/)[0].length;
        const headingText = line.replace(/^#+\s*/, '').trim();
        const heading = document.createElement(`h${Math.min(level, 6)}`);
        parseInlineFormatting(headingText, heading);
        targetContainer.appendChild(heading);
        i++;
        continue;
      }

      // Markdown Tables
      if (line.includes('|') && line.trim().startsWith('|')) {
        const tableLines = [];
        while (i < lines.length && lines[i].includes('|') && lines[i].trim().startsWith('|')) {
          tableLines.push(lines[i].trim());
          i++;
        }

        if (tableLines.length >= 2) {
          const tableWrapper = document.createElement('div');
          tableWrapper.className = 'docs-table-wrapper';
          const table = document.createElement('table');

          // Header
          const headers = tableLines[0].split('|').slice(1, -1).map(h => h.trim());
          const thead = document.createElement('thead');
          const headerRow = document.createElement('tr');
          headers.forEach(hText => {
            const th = document.createElement('th');
            parseInlineFormatting(hText, th);
            headerRow.appendChild(th);
          });
          thead.appendChild(headerRow);
          table.appendChild(thead);

          // Body (skip line 1 which is separator |---|---|)
          const tbody = document.createElement('tbody');
          for (let r = 2; r < tableLines.length; r++) {
            const cells = tableLines[r].split('|').slice(1, -1).map(c => c.trim());
            const row = document.createElement('tr');
            cells.forEach(cText => {
              const td = document.createElement('td');
              parseInlineFormatting(cText, td);
              row.appendChild(td);
            });
            tbody.appendChild(row);
          }
          table.appendChild(tbody);
          tableWrapper.appendChild(table);
          targetContainer.appendChild(tableWrapper);
          continue;
        }
      }

      // Unordered Lists
      if (line.trim().startsWith('- ') || line.trim().startsWith('* ')) {
        const ul = document.createElement('ul');
        while (i < lines.length && (lines[i].trim().startsWith('- ') || lines[i].trim().startsWith('* '))) {
          const li = document.createElement('li');
          const itemText = lines[i].trim().replace(/^[-*]\s+/, '');
          parseInlineFormatting(itemText, li);
          ul.appendChild(li);
          i++;
        }
        targetContainer.appendChild(ul);
        continue;
      }

      // Ordered Lists
      if (/^\d+\.\s/.test(line.trim())) {
        const ol = document.createElement('ol');
        while (i < lines.length && /^\d+\.\s/.test(lines[i].trim())) {
          const li = document.createElement('li');
          const itemText = lines[i].trim().replace(/^\d+\.\s+/, '');
          parseInlineFormatting(itemText, li);
          ol.appendChild(li);
          i++;
        }
        targetContainer.appendChild(ol);
        continue;
      }

      // Horizontal Rule
      if (line.trim() === '---' || line.trim() === '***') {
        const hr = document.createElement('hr');
        hr.style.borderColor = 'var(--border-subtle)';
        hr.style.margin = '1.5rem 0';
        targetContainer.appendChild(hr);
        i++;
        continue;
      }

      // Normal Paragraph
      const p = document.createElement('p');
      parseInlineFormatting(line, p);
      targetContainer.appendChild(p);
      i++;
    }
  }

  // Load article content from API
  async function loadDoc(docId) {
    currentDocId = docId;
    window.location.hash = docId;

    const titleEl = document.getElementById('docsArticleTitle');
    const categoryEl = document.getElementById('docsCategoryPill');
    const bodyEl = document.getElementById('docsArticleBody');
    const readTimeEl = document.getElementById('docsReadTime');
    const mobileTitleEl = document.getElementById('docsMobileCurrentTitle');

    // Update active nav links
    document.querySelectorAll('.docs-nav-link').forEach(link => {
      if (link.dataset.id === docId) {
        link.classList.add('active');
      } else {
        link.classList.remove('active');
      }
    });

    bodyEl.innerHTML = '<div class="docs-loading-state"><div class="spinner"></div><span>Loading article...</span></div>';

    try {
      const res = await fetch(`/api/docs/${docId}`);
      if (!res.ok) {
        throw new Error(`Failed to load document (HTTP ${res.status})`);
      }
      const data = await res.json();

      titleEl.textContent = data.title;
      categoryEl.textContent = data.category;
      if (mobileTitleEl) mobileTitleEl.textContent = data.title;

      // Estimate read time
      const wordCount = data.content.split(/\s+/).length;
      const minutes = Math.max(1, Math.round(wordCount / 200));
      readTimeEl.textContent = `• ~${minutes} min read`;

      // Render Markdown safely into DOM
      renderMarkdownToDOM(data.content, bodyEl);

      // Update Pagination controls
      updatePagination(docId);

      // Scroll smoothly to top of article
      window.scrollTo({ top: 0, behavior: 'smooth' });

      // Close mobile sidebar if open
      const sidebar = document.getElementById('docsSidebar');
      if (sidebar) sidebar.classList.remove('mobile-open');

    } catch (err) {
      console.error(err);
      titleEl.textContent = 'Document Not Found';
      bodyEl.innerHTML = `<div class="toast toast-error" style="position:static; margin: 1rem 0;"><span>Could not load documentation for "${docId}".</span></div>`;
    }
  }

  // Update Next/Previous Pager
  function updatePagination(docId) {
    const prevBtn = document.getElementById('docsPrevBtn');
    const nextBtn = document.getElementById('docsNextBtn');
    const prevTitle = document.getElementById('docsPrevTitle');
    const nextTitle = document.getElementById('docsNextTitle');

    const currentIndex = docsList.findIndex(d => d.id === docId);
    if (currentIndex > 0) {
      const prevDoc = docsList[currentIndex - 1];
      prevBtn.disabled = false;
      prevTitle.textContent = prevDoc.title;
      prevBtn.onclick = () => loadDoc(prevDoc.id);
    } else {
      prevBtn.disabled = true;
      prevTitle.textContent = 'None';
      prevBtn.onclick = null;
    }

    if (currentIndex >= 0 && currentIndex < docsList.length - 1) {
      const nextDoc = docsList[currentIndex + 1];
      nextBtn.disabled = false;
      nextTitle.textContent = nextDoc.title;
      nextBtn.onclick = () => loadDoc(nextDoc.id);
    } else {
      nextBtn.disabled = true;
      nextTitle.textContent = 'None';
      nextBtn.onclick = null;
    }
  }

  // Populate Sidebar Navigation
  function renderNav(sections) {
    const container = document.getElementById('docsNavContainer');
    container.innerHTML = '';

    // Group by category
    const categories = {};
    sections.forEach(s => {
      const cat = s.category || 'General';
      if (!categories[cat]) categories[cat] = [];
      categories[cat].push(s);
    });

    Object.entries(categories).forEach(([categoryName, items]) => {
      const group = document.createElement('div');
      group.className = 'docs-nav-group';

      const catTitle = document.createElement('div');
      catTitle.className = 'docs-nav-category-title';
      catTitle.textContent = categoryName;
      group.appendChild(catTitle);

      items.forEach(item => {
        const a = document.createElement('a');
        a.href = `#${item.id}`;
        a.className = `docs-nav-link ${item.id === currentDocId ? 'active' : ''}`;
        a.dataset.id = item.id;
        a.innerHTML = `<span class="docs-nav-icon">${getCategoryIcon(item.icon)}</span><span>${item.title}</span>`;
        a.addEventListener('click', (e) => {
          e.preventDefault();
          loadDoc(item.id);
        });
        group.appendChild(a);
      });

      container.appendChild(group);
    });
  }

  // Setup Search Filter
  function setupSearch() {
    const searchInput = document.getElementById('docsSearchInput');
    const searchClear = document.getElementById('docsSearchClear');

    searchInput.addEventListener('input', () => {
      const query = searchInput.value.trim().toLowerCase();
      searchClear.classList.toggle('hidden', query.length === 0);

      const filtered = docsList.filter(d => 
        d.title.toLowerCase().includes(query) ||
        d.summary.toLowerCase().includes(query) ||
        d.category.toLowerCase().includes(query)
      );
      renderNav(filtered);
    });

    searchClear.addEventListener('click', () => {
      searchInput.value = '';
      searchClear.classList.add('hidden');
      renderNav(docsList);
      searchInput.focus();
    });
  }

  // Initialize
  async function init() {
    setupSearch();

    // Toggle Mobile Navigation
    const mobileToggle = document.getElementById('docsMobileMenuToggle');
    const sidebar = document.getElementById('docsSidebar');
    if (mobileToggle && sidebar) {
      mobileToggle.addEventListener('click', () => {
        sidebar.classList.toggle('mobile-open');
      });
    }

    // Fetch Sections
    try {
      const res = await fetch('/api/docs/list');
      if (!res.ok) throw new Error('Could not fetch documentation list');
      const data = await res.json();
      docsList = data.sections || [];

      renderNav(docsList);

      // Determine initial document from URL hash or default to 'app'
      const hash = window.location.hash.replace('#', '').trim();
      const initialDoc = docsList.some(d => d.id === hash) ? hash : (docsList[0]?.id || 'app');
      loadDoc(initialDoc);

    } catch (err) {
      console.error(err);
      document.getElementById('docsNavContainer').innerHTML = '<div class="toast toast-error" style="position:static;"><span>Failed to load documentation catalog.</span></div>';
    }

    // Listen for hash changes
    window.addEventListener('hashchange', () => {
      const hash = window.location.hash.replace('#', '').trim();
      if (hash && hash !== currentDocId && docsList.some(d => d.id === hash)) {
        loadDoc(hash);
      }
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
