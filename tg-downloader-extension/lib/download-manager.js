// TG Downloader Pro - Download Manager
// Utility functions for managing downloads

const TGDownloadManager = {
  // Queue for batch downloads
  downloadQueue: [],
  selectedItems: new Set(),
  
  // Request download from background script
  async download(url, filename, type, quality = null) {
    return new Promise((resolve, reject) => {
      chrome.runtime.sendMessage({
        action: 'download',
        data: { url, filename, type, quality }
      }, (response) => {
        if (chrome.runtime.lastError) {
          reject(new Error(chrome.runtime.lastError.message));
        } else if (response?.success) {
          resolve(response);
        } else {
          reject(new Error(response?.error || 'Download failed'));
        }
      });
    });
  },
  
  // Batch download multiple items
  async batchDownload(items) {
    return new Promise((resolve, reject) => {
      chrome.runtime.sendMessage({
        action: 'batchDownload',
        data: items
      }, (response) => {
        if (chrome.runtime.lastError) {
          reject(new Error(chrome.runtime.lastError.message));
        } else if (response?.success) {
          resolve(response);
        } else {
          reject(new Error(response?.error || 'Batch download failed'));
        }
      });
    });
  },
  
  // Add item to selection for batch download
  selectItem(mediaInfo) {
    const key = mediaInfo.url || mediaInfo.id;
    if (!this.selectedItems.has(key)) {
      this.selectedItems.add(key);
      this.downloadQueue.push(mediaInfo);
    }
  },
  
  // Remove item from selection
  deselectItem(mediaInfo) {
    const key = mediaInfo.url || mediaInfo.id;
    this.selectedItems.delete(key);
    this.downloadQueue = this.downloadQueue.filter(item => 
      (item.url || item.id) !== key
    );
  },
  
  // Toggle item selection
  toggleSelection(mediaInfo) {
    const key = mediaInfo.url || mediaInfo.id;
    if (this.selectedItems.has(key)) {
      this.deselectItem(mediaInfo);
      return false;
    } else {
      this.selectItem(mediaInfo);
      return true;
    }
  },
  
  // Clear all selections
  clearSelection() {
    this.selectedItems.clear();
    this.downloadQueue = [];
  },
  
  // Get selection count
  getSelectionCount() {
    return this.selectedItems.size;
  },
  
  // Download all selected items
  async downloadSelected() {
    if (this.downloadQueue.length === 0) {
      return { success: false, error: 'No items selected' };
    }
    
    const result = await this.batchDownload(this.downloadQueue);
    this.clearSelection();
    return result;
  },
  
  // Extract filename from URL or generate one
  extractFilename(url, type) {
    try {
      const urlObj = new URL(url);
      const pathname = urlObj.pathname;
      const segments = pathname.split('/');
      const lastSegment = segments[segments.length - 1];
      
      if (lastSegment && lastSegment.includes('.')) {
        return decodeURIComponent(lastSegment);
      }
    } catch (e) {
      // URL parsing failed
    }
    
    // Generate filename
    const timestamp = new Date().toISOString().replace(/[:.]/g, '-');
    const extensions = {
      video: 'mp4',
      audio: 'mp3',
      voice: 'ogg',
      image: 'jpg',
      document: 'file'
    };
    
    return `telegram_${type}_${timestamp}.${extensions[type] || 'file'}`;
  },
  
  // Format bytes to human readable
  formatBytes(bytes) {
    if (bytes === 0) return '0 B';
    const k = 1024;
    const sizes = ['B', 'KB', 'MB', 'GB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
  },
  
  // Format duration to human readable
  formatDuration(seconds) {
    const mins = Math.floor(seconds / 60);
    const secs = Math.floor(seconds % 60);
    return `${mins}:${secs.toString().padStart(2, '0')}`;
  }
};

// Make available globally
window.TGDownloadManager = TGDownloadManager;
