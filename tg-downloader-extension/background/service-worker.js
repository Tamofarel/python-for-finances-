// TG Downloader Pro - Background Service Worker
// Handles download requests and progress tracking

// Store active downloads
const activeDownloads = new Map();

// Listen for messages from content script
chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  switch (message.action) {
    case 'download':
      handleDownload(message.data, sender.tab?.id)
        .then(result => sendResponse(result))
        .catch(error => sendResponse({ success: false, error: error.message }));
      return true; // Keep channel open for async response

    case 'batchDownload':
      handleBatchDownload(message.data, sender.tab?.id)
        .then(result => sendResponse(result))
        .catch(error => sendResponse({ success: false, error: error.message }));
      return true;

    case 'getDownloads':
      sendResponse({ downloads: Array.from(activeDownloads.values()) });
      return false;

    case 'cancelDownload':
      cancelDownload(message.downloadId)
        .then(result => sendResponse(result))
        .catch(error => sendResponse({ success: false, error: error.message }));
      return true;
  }
});

// Handle single download
async function handleDownload(data, tabId) {
  const { url, filename, type, quality } = data;
  
  try {
    // Generate unique filename
    const finalFilename = generateFilename(filename, type);
    
    // Start download
    const downloadId = await chrome.downloads.download({
      url: url,
      filename: finalFilename,
      saveAs: false
    });

    // Track download
    const downloadInfo = {
      id: downloadId,
      url,
      filename: finalFilename,
      type,
      quality,
      status: 'in_progress',
      progress: 0,
      startTime: Date.now(),
      tabId
    };
    
    activeDownloads.set(downloadId, downloadInfo);
    
    // Notify content script
    if (tabId) {
      chrome.tabs.sendMessage(tabId, {
        action: 'downloadStarted',
        downloadId,
        filename: finalFilename
      });
    }

    return { success: true, downloadId };
  } catch (error) {
    console.error('Download failed:', error);
    return { success: false, error: error.message };
  }
}

// Handle batch download
async function handleBatchDownload(items, tabId) {
  const results = [];
  
  for (const item of items) {
    try {
      const result = await handleDownload(item, tabId);
      results.push(result);
      // Small delay between downloads to avoid overwhelming
      await new Promise(resolve => setTimeout(resolve, 300));
    } catch (error) {
      results.push({ success: false, error: error.message, url: item.url });
    }
  }
  
  return { success: true, results };
}

// Cancel a download
async function cancelDownload(downloadId) {
  try {
    await chrome.downloads.cancel(downloadId);
    activeDownloads.delete(downloadId);
    return { success: true };
  } catch (error) {
    return { success: false, error: error.message };
  }
}

// Generate filename with proper extension
function generateFilename(originalName, type) {
  const timestamp = Date.now();
  const sanitized = sanitizeFilename(originalName);
  
  const extensions = {
    video: '.mp4',
    audio: '.mp3',
    voice: '.ogg',
    image: '.jpg',
    document: ''
  };
  
  const ext = extensions[type] || '';
  const baseName = sanitized || `telegram_${type}_${timestamp}`;
  
  // Ensure proper extension
  if (ext && !baseName.toLowerCase().endsWith(ext)) {
    return `TG_Downloads/${baseName}${ext}`;
  }
  
  return `TG_Downloads/${baseName}`;
}

// Sanitize filename for file system
function sanitizeFilename(name) {
  if (!name) return '';
  return name
    .replace(/[<>:"/\\|?*]/g, '_')
    .replace(/\s+/g, '_')
    .substring(0, 200);
}

// Monitor download progress
chrome.downloads.onChanged.addListener((delta) => {
  const downloadInfo = activeDownloads.get(delta.id);
  
  if (!downloadInfo) return;
  
  // Update status
  if (delta.state) {
    downloadInfo.status = delta.state.current;
    
    if (delta.state.current === 'complete') {
      downloadInfo.progress = 100;
      notifyDownloadComplete(downloadInfo);
    } else if (delta.state.current === 'interrupted') {
      downloadInfo.status = 'failed';
      downloadInfo.error = delta.error?.current || 'Download interrupted';
    }
  }
  
  // Update progress
  if (delta.bytesReceived && delta.totalBytes) {
    const progress = (delta.bytesReceived.current / delta.totalBytes.current) * 100;
    downloadInfo.progress = Math.round(progress);
    notifyProgress(downloadInfo);
  }
  
  activeDownloads.set(delta.id, downloadInfo);
});

// Notify content script of progress
function notifyProgress(downloadInfo) {
  if (downloadInfo.tabId) {
    chrome.tabs.sendMessage(downloadInfo.tabId, {
      action: 'downloadProgress',
      downloadId: downloadInfo.id,
      progress: downloadInfo.progress
    }).catch(() => {}); // Ignore errors if tab closed
  }
}

// Notify download complete
function notifyDownloadComplete(downloadInfo) {
  if (downloadInfo.tabId) {
    chrome.tabs.sendMessage(downloadInfo.tabId, {
      action: 'downloadComplete',
      downloadId: downloadInfo.id,
      filename: downloadInfo.filename
    }).catch(() => {});
  }
  
  // Clean up after delay
  setTimeout(() => {
    activeDownloads.delete(downloadInfo.id);
  }, 5000);
}

// Extension installed/updated
chrome.runtime.onInstalled.addListener((details) => {
  console.log('TG Downloader Pro installed:', details.reason);
});
