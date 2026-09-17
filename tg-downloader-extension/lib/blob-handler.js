// TG Downloader Pro - Blob Handler
// Handles blob URL downloads for video content

const TGBlobHandler = {
  // Download blob URL content
  async downloadBlob(blobUrl, filename, type = 'video') {
    try {
      console.log('TG Downloader: Fetching blob URL...', blobUrl);
      
      // Check if it's a blob URL
      if (blobUrl.startsWith('blob:')) {
        return await this.fetchAndDownloadBlob(blobUrl, filename, type);
      }
      
      // If it's a regular URL, use the standard download
      return { success: false, useStandardDownload: true, url: blobUrl };
    } catch (error) {
      console.error('TG Downloader: Blob download failed:', error);
      return { success: false, error: error.message };
    }
  },
  
  // Fetch blob content and trigger download
  async fetchAndDownloadBlob(blobUrl, filename, type) {
    try {
      // Fetch the blob data
      const response = await fetch(blobUrl);
      if (!response.ok) {
        throw new Error(`Failed to fetch blob: ${response.status}`);
      }
      
      const blob = await response.blob();
      console.log('TG Downloader: Blob fetched, size:', blob.size, 'type:', blob.type);
      
      // Determine file extension from blob type or default
      const extension = this.getExtension(blob.type, type);
      const finalFilename = this.ensureExtension(filename, extension);
      
      // Create download link and trigger
      const downloadUrl = URL.createObjectURL(blob);
      
      const link = document.createElement('a');
      link.href = downloadUrl;
      link.download = finalFilename;
      link.style.display = 'none';
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
      
      // Cleanup after a delay
      setTimeout(() => URL.revokeObjectURL(downloadUrl), 1000);
      
      return { success: true, filename: finalFilename, size: blob.size };
    } catch (error) {
      console.error('TG Downloader: fetchAndDownloadBlob failed:', error);
      return { success: false, error: error.message };
    }
  },
  
  // Get file extension from MIME type
  getExtension(mimeType, fallbackType) {
    const mimeToExt = {
      'video/mp4': 'mp4',
      'video/webm': 'webm',
      'video/ogg': 'ogv',
      'video/quicktime': 'mov',
      'video/x-msvideo': 'avi',
      'video/x-matroska': 'mkv',
      'audio/mpeg': 'mp3',
      'audio/ogg': 'ogg',
      'audio/wav': 'wav',
      'audio/webm': 'weba',
      'image/jpeg': 'jpg',
      'image/png': 'png',
      'image/gif': 'gif',
      'image/webp': 'webp'
    };
    
    if (mimeType && mimeToExt[mimeType]) {
      return mimeToExt[mimeType];
    }
    
    // Fallback based on type
    const typeToExt = {
      'video': 'mp4',
      'audio': 'mp3',
      'voice': 'ogg',
      'image': 'jpg'
    };
    
    return typeToExt[fallbackType] || 'mp4';
  },
  
  // Ensure filename has correct extension
  ensureExtension(filename, extension) {
    if (!filename) {
      const timestamp = new Date().toISOString().replace(/[:.]/g, '-');
      return `telegram_video_${timestamp}.${extension}`;
    }
    
    // Remove any existing extension and add the correct one
    const baseName = filename.replace(/\.[^.]+$/, '');
    return `${baseName}.${extension}`;
  },
  
  // Try to get video source - enhanced for Telegram Web
  async getVideoSource(videoElement) {
    // First check if video has a source
    let src = videoElement.currentSrc || videoElement.src;
    
    if (src) {
      return src;
    }
    
    // Check source elements
    const sourceEl = videoElement.querySelector('source');
    if (sourceEl?.src) {
      return sourceEl.src;
    }
    
    // Wait for video to load if not ready
    if (videoElement.readyState < 2) {
      console.log('TG Downloader: Video not ready, waiting for load...');
      return await this.waitForVideoSource(videoElement);
    }
    
    return null;
  },
  
  // Wait for video source to become available
  waitForVideoSource(videoElement, timeout = 5000) {
    return new Promise((resolve) => {
      const startTime = Date.now();
      
      const checkSource = () => {
        const src = videoElement.currentSrc || videoElement.src;
        if (src) {
          resolve(src);
          return;
        }
        
        if (Date.now() - startTime > timeout) {
          resolve(null);
          return;
        }
        
        requestAnimationFrame(checkSource);
      };
      
      // Also listen for loadeddata event
      const handleLoad = () => {
        videoElement.removeEventListener('loadeddata', handleLoad);
        const src = videoElement.currentSrc || videoElement.src;
        resolve(src);
      };
      
      videoElement.addEventListener('loadeddata', handleLoad);
      checkSource();
    });
  },
  
  // Trigger video to start loading
  triggerVideoLoad(videoElement) {
    // Try to trigger video to start loading
    if (videoElement.paused && videoElement.readyState < 2) {
      // Click on the video container to trigger load
      const container = videoElement.closest('.media-container, [class*="media"]');
      if (container) {
        container.click();
      }
    }
  }
};

// Make available globally
window.TGBlobHandler = TGBlobHandler;
