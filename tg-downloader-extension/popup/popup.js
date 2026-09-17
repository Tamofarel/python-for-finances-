// TG Downloader Pro - Popup Script

document.addEventListener('DOMContentLoaded', () => {
  // Elements
  const statusBar = document.getElementById('statusBar');
  const statusText = document.getElementById('statusText');
  const videoCount = document.getElementById('videoCount');
  const audioCount = document.getElementById('audioCount');
  const imageCount = document.getElementById('imageCount');
  const docCount = document.getElementById('docCount');
  const scanBtn = document.getElementById('scanBtn');
  const downloadAllBtn = document.getElementById('downloadAllBtn');
  const downloadsList = document.getElementById('downloadsList');

  // Initialize
  init();

  async function init() {
    // Check if we're on Telegram Web
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    
    if (tab?.url?.includes('telegram.org')) {
      statusBar.classList.add('connected');
      statusText.textContent = 'Connected to Telegram Web';
      
      // Get media counts
      await updateMediaCounts(tab.id);
    } else {
      statusBar.classList.add('disconnected');
      statusText.textContent = 'Not on Telegram Web';
      scanBtn.disabled = true;
    }

    // Get active downloads
    updateDownloadsList();
  }

  // Update media counts from content script
  async function updateMediaCounts(tabId) {
    try {
      const response = await chrome.tabs.sendMessage(tabId, { action: 'getMediaCount' });
      
      if (response) {
        videoCount.textContent = response.videos || 0;
        audioCount.textContent = response.audios || 0;
        imageCount.textContent = response.images || 0;
        docCount.textContent = response.documents || 0;
        
        const total = (response.videos || 0) + (response.audios || 0) + 
                     (response.images || 0) + (response.documents || 0);
        
        downloadAllBtn.disabled = total === 0;
      }
    } catch (error) {
      console.log('Content script not ready:', error);
      // Content script might not be injected yet
      videoCount.textContent = '-';
      audioCount.textContent = '-';
      imageCount.textContent = '-';
      docCount.textContent = '-';
    }
  }

  // Update downloads list
  async function updateDownloadsList() {
    try {
      const response = await chrome.runtime.sendMessage({ action: 'getDownloads' });
      
      if (response?.downloads?.length > 0) {
        downloadsList.innerHTML = '';
        
        response.downloads.forEach(download => {
          const item = createDownloadItem(download);
          downloadsList.appendChild(item);
        });
      } else {
        downloadsList.innerHTML = `
          <div class="empty-state">
            <svg viewBox="0 0 24 24" width="40" height="40" fill="currentColor" opacity="0.3">
              <path d="M19 9h-4V3H9v6H5l7 7 7-7zM5 18v2h14v-2H5z"/>
            </svg>
            <p>No active downloads</p>
          </div>
        `;
      }
    } catch (error) {
      console.error('Failed to get downloads:', error);
    }
  }

  // Create download item element
  function createDownloadItem(download) {
    const item = document.createElement('div');
    item.className = 'download-item';
    item.innerHTML = `
      <div class="download-item-icon">
        ${getTypeIcon(download.type)}
      </div>
      <div class="download-item-info">
        <div class="download-item-name">${escapeHtml(download.filename)}</div>
        <div class="download-item-progress">
          <div class="progress-bar">
            <div class="progress-bar-fill" style="width: ${download.progress}%"></div>
          </div>
          <span class="progress-text">${download.progress}%</span>
        </div>
      </div>
    `;
    return item;
  }

  // Get icon for media type
  function getTypeIcon(type) {
    const icons = {
      video: '<svg viewBox="0 0 24 24" width="16" height="16" fill="currentColor"><path d="M17 10.5V7c0-.55-.45-1-1-1H4c-.55 0-1 .45-1 1v10c0 .55.45 1 1 1h12c.55 0 1-.45 1-1v-3.5l4 4v-11l-4 4z"/></svg>',
      audio: '<svg viewBox="0 0 24 24" width="16" height="16" fill="currentColor"><path d="M12 3v10.55c-.59-.34-1.27-.55-2-.55-2.21 0-4 1.79-4 4s1.79 4 4 4 4-1.79 4-4V7h4V3h-6z"/></svg>',
      voice: '<svg viewBox="0 0 24 24" width="16" height="16" fill="currentColor"><path d="M12 14c1.66 0 3-1.34 3-3V5c0-1.66-1.34-3-3-3S9 3.34 9 5v6c0 1.66 1.34 3 3 3z"/></svg>',
      image: '<svg viewBox="0 0 24 24" width="16" height="16" fill="currentColor"><path d="M21 19V5c0-1.1-.9-2-2-2H5c-1.1 0-2 .9-2 2v14c0 1.1.9 2 2 2h14c1.1 0 2-.9 2-2zM8.5 13.5l2.5 3.01L14.5 12l4.5 6H5l3.5-4.5z"/></svg>',
      document: '<svg viewBox="0 0 24 24" width="16" height="16" fill="currentColor"><path d="M14 2H6c-1.1 0-1.99.9-1.99 2L4 20c0 1.1.89 2 1.99 2H18c1.1 0 2-.9 2-2V8l-6-6z"/></svg>'
    };
    return icons[type] || icons.document;
  }

  // Escape HTML
  function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
  }

  // Scan button click
  scanBtn.addEventListener('click', async () => {
    scanBtn.disabled = true;
    scanBtn.innerHTML = `
      <svg class="spin" viewBox="0 0 24 24" width="18" height="18" fill="currentColor">
        <path d="M12 4V2A10 10 0 0 0 2 12h2a8 8 0 0 1 8-8z"/>
      </svg>
      Scanning...
    `;

    try {
      const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
      await chrome.tabs.sendMessage(tab.id, { action: 'scanMedia' });
      
      // Wait a bit then update counts
      await new Promise(resolve => setTimeout(resolve, 500));
      await updateMediaCounts(tab.id);
      
      scanBtn.innerHTML = `
        <svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor">
          <path d="M9 16.17L4.83 12l-1.42 1.41L9 19 21 7l-1.41-1.41L9 16.17z"/>
        </svg>
        Done!
      `;
      
      setTimeout(() => {
        scanBtn.innerHTML = `
          <svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor">
            <path d="M17.65 6.35C16.2 4.9 14.21 4 12 4c-4.42 0-7.99 3.58-7.99 8s3.57 8 7.99 8c3.73 0 6.84-2.55 7.73-6h-2.08c-.82 2.33-3.04 4-5.65 4-3.31 0-6-2.69-6-6s2.69-6 6-6c1.66 0 3.14.69 4.22 1.78L13 11h7V4l-2.35 2.35z"/>
          </svg>
          Scan for Media
        `;
        scanBtn.disabled = false;
      }, 1500);
    } catch (error) {
      console.error('Scan failed:', error);
      scanBtn.textContent = 'Scan Failed';
      scanBtn.disabled = false;
    }
  });

  // Download all button click
  downloadAllBtn.addEventListener('click', async () => {
    downloadAllBtn.disabled = true;
    downloadAllBtn.innerHTML = `
      <svg class="spin" viewBox="0 0 24 24" width="18" height="18" fill="currentColor">
        <path d="M12 4V2A10 10 0 0 0 2 12h2a8 8 0 0 1 8-8z"/>
      </svg>
      Starting...
    `;

    try {
      const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
      
      // Tell content script to select all and download
      await chrome.tabs.sendMessage(tab.id, { action: 'downloadAllVisible' });
      
      downloadAllBtn.innerHTML = `
        <svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor">
          <path d="M9 16.17L4.83 12l-1.42 1.41L9 19 21 7l-1.41-1.41L9 16.17z"/>
        </svg>
        Downloads Started!
      `;
      
      // Refresh downloads list
      setTimeout(updateDownloadsList, 500);
      
      setTimeout(() => {
        downloadAllBtn.innerHTML = `
          <svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor">
            <path d="M19 9h-4V3H9v6H5l7 7 7-7zM5 18v2h14v-2H5z"/>
          </svg>
          Download All
        `;
        downloadAllBtn.disabled = false;
      }, 2000);
    } catch (error) {
      console.error('Download all failed:', error);
      downloadAllBtn.textContent = 'Failed';
      downloadAllBtn.disabled = false;
    }
  });

  // Stat card clicks
  document.querySelectorAll('.stat-card').forEach(card => {
    card.addEventListener('click', async () => {
      const type = card.dataset.type;
      // Could filter and show only that type
      console.log('Filter by:', type);
    });
  });

  // Add spin animation
  const style = document.createElement('style');
  style.textContent = `
    .spin {
      animation: spin 1s linear infinite;
    }
    @keyframes spin {
      from { transform: rotate(0deg); }
      to { transform: rotate(360deg); }
    }
  `;
  document.head.appendChild(style);

  // Refresh downloads periodically
  setInterval(updateDownloadsList, 2000);
});
