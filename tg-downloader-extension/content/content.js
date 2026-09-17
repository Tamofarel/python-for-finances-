// TG Downloader Pro - Main Content Script
// Injects download buttons and manages UI interactions

(function() {
  'use strict';

  // Prevent multiple injections
  if (window.__TGDownloaderProLoaded) return;
  window.__TGDownloaderProLoaded = true;

  console.log('TG Downloader Pro: Content script loaded');

  // State
  const state = {
    initialized: false,
    observer: null,
    downloadButtons: new Map(),
    batchMode: false
  };

  // Initialize extension
  function init() {
    if (state.initialized) return;
    
    console.log('TG Downloader Pro: Initializing...');
    
    // Wait for page to be ready
    if (document.readyState === 'loading') {
      document.addEventListener('DOMContentLoaded', onReady);
    } else {
      onReady();
    }
  }

  function onReady() {
    // Start observing for media
    setupObserver();
    
    // Scan existing media
    scanAndInjectButtons();
    
    // Listen for messages from background
    setupMessageListener();
    
    // Add keyboard shortcuts
    setupKeyboardShortcuts();
    
    state.initialized = true;
    console.log('TG Downloader Pro: Ready!');
  }

  // Setup MutationObserver to watch for new media
  function setupObserver() {
    state.observer = new MutationObserver((mutations) => {
      let shouldScan = false;
      
      for (const mutation of mutations) {
        if (mutation.addedNodes.length > 0) {
          shouldScan = true;
          break;
        }
      }
      
      if (shouldScan) {
        // Debounce scanning
        clearTimeout(state.scanTimeout);
        state.scanTimeout = setTimeout(scanAndInjectButtons, 300);
      }
    });

    state.observer.observe(document.body, {
      childList: true,
      subtree: true
    });
  }

  // Scan page and inject download buttons
  function scanAndInjectButtons() {
    // Detect all media
    const media = window.TGMediaDetector.detectAllMedia();
    
    // Inject buttons for videos
    media.videos.forEach(video => {
      injectDownloadButton(video.element, video);
    });
    
    // Inject buttons for audios
    media.audios.forEach(audio => {
      injectDownloadButton(audio.element, audio);
    });
    
    // Inject buttons for images
    media.images.forEach(image => {
      injectDownloadButton(image.element, image);
    });
    
    // Inject buttons for documents
    media.documents.forEach(doc => {
      injectDownloadButton(doc.element, doc);
    });
    
    // Check media viewer
    const viewerMedia = window.TGMediaDetector.getMediaViewerContent();
    if (viewerMedia) {
      injectViewerButton(viewerMedia);
    }
  }

  // Inject download button on media element
  function injectDownloadButton(element, mediaInfo) {
    if (!element || state.downloadButtons.has(element)) return;
    
    // Create button container
    const container = document.createElement('div');
    container.className = 'tg-dl-btn-container';
    
    // Main download button
    const btn = document.createElement('button');
    btn.className = 'tg-dl-btn';
    btn.innerHTML = getDownloadIcon();
    btn.title = `Download ${mediaInfo.type}`;
    
    // Add quality badge for videos
    if (mediaInfo.type === 'video' && mediaInfo.quality) {
      const badge = document.createElement('span');
      badge.className = 'tg-dl-quality';
      badge.textContent = mediaInfo.quality;
      container.appendChild(badge);
    }
    
    // Click handler
    btn.addEventListener('click', async (e) => {
      e.preventDefault();
      e.stopPropagation();
      
      if (state.batchMode) {
        // Toggle selection in batch mode
        const selected = window.TGDownloadManager.toggleSelection(mediaInfo);
        container.classList.toggle('tg-dl-selected', selected);
        updateBatchCounter();
      } else {
        // Single download
        await downloadMedia(mediaInfo, btn);
      }
    });
    
    // Right-click for batch select
    btn.addEventListener('contextmenu', (e) => {
      e.preventDefault();
      const selected = window.TGDownloadManager.toggleSelection(mediaInfo);
      container.classList.toggle('tg-dl-selected', selected);
      state.batchMode = true;
      updateBatchCounter();
    });
    
    container.appendChild(btn);
    
    // Position the button
    positionButton(element, container);
    
    state.downloadButtons.set(element, container);
  }

  // Position button relative to media element
  function positionButton(element, button) {
    const parent = element.parentElement;
    if (!parent) return;
    
    // Make parent relative if not already positioned
    const parentPos = getComputedStyle(parent).position;
    if (parentPos === 'static') {
      parent.style.position = 'relative';
    }
    
    parent.appendChild(button);
  }

  // Inject button in media viewer
  function injectViewerButton(mediaInfo) {
    // Check if already exists
    if (document.querySelector('.tg-dl-viewer-btn')) return;
    
    const viewer = document.querySelector('.media-viewer, .MediaViewer, [class*="MediaViewer"]');
    if (!viewer) return;
    
    const btn = document.createElement('button');
    btn.className = 'tg-dl-viewer-btn';
    btn.innerHTML = `${getDownloadIcon()} Download`;
    
    if (mediaInfo.quality) {
      btn.innerHTML += ` <span class="tg-dl-quality">${mediaInfo.quality}</span>`;
    }
    
    btn.addEventListener('click', async (e) => {
      e.preventDefault();
      e.stopPropagation();
      await downloadMedia(mediaInfo, btn);
    });
    
    // Find controls area or append to viewer
    const controls = viewer.querySelector('.media-viewer-controls, [class*="controls"]');
    if (controls) {
      controls.appendChild(btn);
    } else {
      viewer.appendChild(btn);
    }
  }

  // Download media
  async function downloadMedia(mediaInfo, buttonElement) {
    if (!mediaInfo.url) {
      showNotification('Could not find download URL', 'error');
      return;
    }
    
    // Show loading state
    const originalContent = buttonElement.innerHTML;
    buttonElement.innerHTML = getLoadingIcon();
    buttonElement.disabled = true;
    buttonElement.classList.add('tg-dl-loading');
    
    try {
      const filename = window.TGDownloadManager.extractFilename(mediaInfo.url, mediaInfo.type);
      
      const result = await window.TGDownloadManager.download(
        mediaInfo.url,
        filename,
        mediaInfo.type,
        mediaInfo.quality
      );
      
      if (result.success) {
        buttonElement.innerHTML = getSuccessIcon();
        buttonElement.classList.remove('tg-dl-loading');
        buttonElement.classList.add('tg-dl-success');
        showNotification(`Downloading ${mediaInfo.type}...`, 'success');
        
        // Reset after delay
        setTimeout(() => {
          buttonElement.innerHTML = originalContent;
          buttonElement.disabled = false;
          buttonElement.classList.remove('tg-dl-success');
        }, 2000);
      } else {
        throw new Error(result.error);
      }
    } catch (error) {
      console.error('Download failed:', error);
      buttonElement.innerHTML = getErrorIcon();
      buttonElement.classList.remove('tg-dl-loading');
      buttonElement.classList.add('tg-dl-error');
      showNotification(`Download failed: ${error.message}`, 'error');
      
      setTimeout(() => {
        buttonElement.innerHTML = originalContent;
        buttonElement.disabled = false;
        buttonElement.classList.remove('tg-dl-error');
      }, 2000);
    }
  }

  // Show notification
  function showNotification(message, type = 'info') {
    // Remove existing
    const existing = document.querySelector('.tg-dl-notification');
    if (existing) existing.remove();
    
    const notification = document.createElement('div');
    notification.className = `tg-dl-notification tg-dl-notification-${type}`;
    notification.textContent = message;
    
    document.body.appendChild(notification);
    
    // Animate in
    setTimeout(() => notification.classList.add('tg-dl-notification-show'), 10);
    
    // Remove after delay
    setTimeout(() => {
      notification.classList.remove('tg-dl-notification-show');
      setTimeout(() => notification.remove(), 300);
    }, 3000);
  }

  // Update batch download counter
  function updateBatchCounter() {
    let counter = document.querySelector('.tg-dl-batch-counter');
    const count = window.TGDownloadManager.getSelectionCount();
    
    if (count === 0) {
      if (counter) counter.remove();
      state.batchMode = false;
      return;
    }
    
    if (!counter) {
      counter = document.createElement('div');
      counter.className = 'tg-dl-batch-counter';
      
      const downloadBtn = document.createElement('button');
      downloadBtn.className = 'tg-dl-batch-btn';
      downloadBtn.innerHTML = `${getDownloadIcon()} Download All`;
      downloadBtn.addEventListener('click', downloadAllSelected);
      
      const clearBtn = document.createElement('button');
      clearBtn.className = 'tg-dl-batch-clear';
      clearBtn.textContent = '✕';
      clearBtn.addEventListener('click', clearSelection);
      
      counter.appendChild(downloadBtn);
      counter.appendChild(clearBtn);
      document.body.appendChild(counter);
    }
    
    counter.querySelector('.tg-dl-batch-btn').innerHTML = 
      `${getDownloadIcon()} Download ${count} item${count > 1 ? 's' : ''}`;
  }

  // Download all selected items
  async function downloadAllSelected() {
    const btn = document.querySelector('.tg-dl-batch-btn');
    if (!btn) return;
    
    btn.disabled = true;
    btn.innerHTML = `${getLoadingIcon()} Downloading...`;
    
    try {
      await window.TGDownloadManager.downloadSelected();
      showNotification('Batch download started!', 'success');
      clearSelection();
    } catch (error) {
      showNotification(`Batch download failed: ${error.message}`, 'error');
    }
    
    btn.disabled = false;
  }

  // Clear selection
  function clearSelection() {
    window.TGDownloadManager.clearSelection();
    document.querySelectorAll('.tg-dl-selected').forEach(el => {
      el.classList.remove('tg-dl-selected');
    });
    const counter = document.querySelector('.tg-dl-batch-counter');
    if (counter) counter.remove();
    state.batchMode = false;
  }

  // Setup message listener
  function setupMessageListener() {
    chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
      switch (message.action) {
        case 'downloadStarted':
          // Could show inline progress on the button
          break;
          
        case 'downloadProgress':
          // Update progress indicator
          break;
          
        case 'downloadComplete':
          showNotification(`Downloaded: ${message.filename}`, 'success');
          break;
          
        case 'scanMedia':
          // Rescan for media
          scanAndInjectButtons();
          sendResponse({ success: true });
          break;
          
        case 'getMediaCount':
          const media = window.TGMediaDetector.detectAllMedia();
          sendResponse({
            videos: media.videos.length,
            audios: media.audios.length,
            images: media.images.length,
            documents: media.documents.length
          });
          break;
      }
    });
  }

  // Setup keyboard shortcuts
  function setupKeyboardShortcuts() {
    document.addEventListener('keydown', (e) => {
      // Ctrl+Shift+D - Download all visible media
      if (e.ctrlKey && e.shiftKey && e.key === 'D') {
        e.preventDefault();
        // Select all and download
        selectAllVisibleMedia();
      }
      
      // Escape - Clear selection
      if (e.key === 'Escape' && state.batchMode) {
        clearSelection();
      }
    });
  }

  // Select all visible media
  function selectAllVisibleMedia() {
    const media = window.TGMediaDetector.detectAllMedia();
    
    [...media.videos, ...media.audios, ...media.images, ...media.documents].forEach(item => {
      if (item.url) {
        window.TGDownloadManager.selectItem(item);
      }
    });
    
    document.querySelectorAll('.tg-dl-btn-container').forEach(container => {
      container.classList.add('tg-dl-selected');
    });
    
    state.batchMode = true;
    updateBatchCounter();
  }

  // Icons
  function getDownloadIcon() {
    return `<svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor">
      <path d="M19 9h-4V3H9v6H5l7 7 7-7zM5 18v2h14v-2H5z"/>
    </svg>`;
  }

  function getLoadingIcon() {
    return `<svg class="tg-dl-spin" viewBox="0 0 24 24" width="18" height="18" fill="currentColor">
      <path d="M12 4V2A10 10 0 0 0 2 12h2a8 8 0 0 1 8-8z"/>
    </svg>`;
  }

  function getSuccessIcon() {
    return `<svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor">
      <path d="M9 16.17L4.83 12l-1.42 1.41L9 19 21 7l-1.41-1.41L9 16.17z"/>
    </svg>`;
  }

  function getErrorIcon() {
    return `<svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor">
      <path d="M19 6.41L17.59 5 12 10.59 6.41 5 5 6.41 10.59 12 5 17.59 6.41 19 12 13.41 17.59 19 19 17.59 13.41 12 19 6.41z"/>
    </svg>`;
  }

  // Start
  init();
})();
