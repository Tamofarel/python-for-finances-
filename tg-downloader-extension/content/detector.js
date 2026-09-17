// TG Downloader Pro - Media Detector
// Detects and extracts media from Telegram Web

const TGMediaDetector = {
  // Track processed elements to avoid duplicates
  processedVideos: new WeakSet(),
  processedImages: new WeakSet(),
  
  // Selectors for Telegram Web elements (updated for current TG Web)
  selectors: {
    // Message containers
    message: '.message, .Message, .bubble, [class*="message"]',
    messageMedia: '.media-container, .media-inner, [class*="media"]',
    
    // Video elements - be more specific
    video: 'video',
    videoContainer: '.media-viewer-content, .MediaViewerContent, [class*="video"], .VideoPlayer, .media-video',
    videoMessage: '.video-message, [class*="VideoMessage"], [class*="round-video"]',
    
    // Audio elements
    audio: 'audio, .audio-player, [class*="Audio"]',
    voice: '.voice-message, [class*="voice"], [class*="Voice"]',
    
    // Image elements - exclude video thumbnails
    image: '.media-photo img, .photo img',
    
    // Document elements
    document: '.document, .Document, [class*="document"]',
    
    // Media viewer (fullscreen)
    mediaViewer: '.media-viewer, .MediaViewer, [class*="MediaViewer"], .media-viewer-whole',
  },
  
  // Detect all media in current view
  detectAllMedia() {
    const media = {
      videos: this.detectVideos(),
      audios: this.detectAudios(),
      images: this.detectImages(),
      documents: this.detectDocuments()
    };
    
    return media;
  },
  
  // Detect videos - PRIORITY over images
  detectVideos() {
    const videos = [];
    const videoContainers = new Set();
    
    // Find all video elements - mark containers FIRST regardless of source availability
    document.querySelectorAll('video').forEach((video, index) => {
      // ALWAYS mark the container as having video, even without source
      // This prevents thumbnail images from being detected as standalone images
      const container = video.closest('.media-container, [class*="media"], .bubble-content, .message-content, .media-inner');
      if (container) videoContainers.add(container);
      
      // Also mark parent elements up to message level
      const messageContainer = video.closest('.message, .Message, .bubble, [class*="message"]');
      if (messageContainer) {
        const mediaInMessage = messageContainer.querySelector('.media-container, [class*="media"]');
        if (mediaInMessage) videoContainers.add(mediaInMessage);
      }
    });
    
    // Store video containers globally for image detection BEFORE processing videos
    window.__tgVideoContainers = videoContainers;
    
    // Now process videos with sources
    document.querySelectorAll('video').forEach((video, index) => {
      // Skip if already processed
      if (this.processedVideos.has(video)) return;
      
      const src = this.getVideoSource(video);
      if (src) {
        const container = video.closest('.media-container, [class*="media"], .bubble-content, .message-content');
        
        videos.push({
          type: 'video',
          element: video,
          container: container,
          url: src,
          id: `video_${index}_${Date.now()}`,
          thumbnail: this.getVideoThumbnail(video),
          duration: video.duration || 0,
          quality: this.detectVideoQuality(video)
        });
        
        this.processedVideos.add(video);
      }
    });
    
    return videos;
  },
  
  // Get video source URL - improved for Telegram Web
  getVideoSource(video) {
    // Telegram Web often uses blob URLs for streaming
    // We can still download blob URLs!
    
    // Check currentSrc first (actual playing source)
    if (video.currentSrc) {
      return video.currentSrc;
    }
    
    // Direct src attribute
    if (video.src) {
      return video.src;
    }
    
    // Source elements
    const source = video.querySelector('source');
    if (source?.src) {
      return source.src;
    }
    
    // Check for data attributes with URL
    const container = video.closest('[data-src], [data-url], [data-video-url]');
    if (container) {
      return container.dataset.videoUrl || container.dataset.src || container.dataset.url;
    }
    
    // Try to find video URL in parent's data attributes
    const parent = video.parentElement;
    if (parent?.dataset?.src) {
      return parent.dataset.src;
    }
    
    return null;
  },
  
  // Get video thumbnail
  getVideoThumbnail(video) {
    if (video.poster) return video.poster;
    
    // Try to get from container
    const container = video.closest('.media-container, [class*="media"]');
    if (container) {
      const img = container.querySelector('img.thumbnail, img.poster, img[class*="thumb"]');
      if (img?.src) return img.src;
    }
    
    return null;
  },
  
  // Detect video quality from element
  detectVideoQuality(video) {
    // Try to get from loaded video metadata
    const height = video.videoHeight || video.height || 0;
    if (height >= 1080) return '1080p';
    if (height >= 720) return '720p';
    if (height >= 480) return '480p';
    if (height >= 360) return '360p';
    if (height > 0) return 'SD';
    return 'HD'; // Default assumption
  },
  
  // Detect audio files
  detectAudios() {
    const audios = [];
    
    // Audio elements
    document.querySelectorAll('audio').forEach((audio, index) => {
      const src = audio.currentSrc || audio.src;
      if (src) {
        audios.push({
          type: 'audio',
          element: audio,
          url: src,
          id: `audio_${index}_${Date.now()}`,
          duration: audio.duration || 0
        });
      }
    });
    
    // Voice messages (often use different structure)
    document.querySelectorAll(this.selectors.voice).forEach((voice, index) => {
      const audio = voice.querySelector('audio');
      const dataUrl = voice.dataset.src || voice.dataset.url;
      const url = audio?.currentSrc || audio?.src || dataUrl;
      
      if (url) {
        audios.push({
          type: 'voice',
          element: voice,
          url: url,
          id: `voice_${index}_${Date.now()}`,
          duration: audio?.duration || 0
        });
      }
    });
    
    return audios;
  },
  
  // Detect images - SKIP elements that are in video containers
  detectImages() {
    const images = [];
    const seen = new Set();
    const videoContainers = window.__tgVideoContainers || new Set();
    
    // Find images in media containers
    document.querySelectorAll('img').forEach((img, index) => {
      // Skip if already processed
      if (this.processedImages.has(img)) return;
      
      // IMPORTANT: Skip images that are inside video containers (thumbnails)
      for (const container of videoContainers) {
        if (container.contains(img)) {
          return; // This is a video thumbnail, skip it
        }
      }
      
      // Skip if there's a video sibling or parent has video - check entire message bubble
      const messageBubble = img.closest('.message, .Message, .bubble, [class*="message"], .bubble-content');
      if (messageBubble?.querySelector('video')) {
        return; // Message contains video, this is likely a thumbnail
      }
      
      // Skip if parent media container has video
      const parent = img.closest('.media-container, [class*="media"], .bubble-content, .media-inner');
      if (parent?.querySelector('video')) {
        return; // Container has video, this is likely a thumbnail
      }
      
      // Skip if there's a play button overlay (indicates video)
      if (parent?.querySelector('.video-play, [class*="play"], .play-button, svg[class*="play"]')) {
        return; // Has play button, likely video thumbnail
      }
      
      // Skip images with video-related classes
      const imgClasses = img.className.toLowerCase();
      const parentClasses = parent?.className?.toLowerCase() || '';
      if (imgClasses.includes('video') || imgClasses.includes('thumb') ||
          parentClasses.includes('video') || parentClasses.includes('round-video')) {
        return; // Video-related element
      }
      
      let src = img.src;
      
      // Skip thumbnails, get full size if available
      if (img.dataset.src) src = img.dataset.src;
      if (img.dataset.fullSrc) src = img.dataset.fullSrc;
      
      // Skip data URLs (inline images)
      if (src?.startsWith('data:')) return;
      
      // Skip small images (likely icons/avatars) - increased threshold
      if (img.naturalWidth < 150 || img.naturalHeight < 150) return;
      
      // Skip known non-media images
      if (src?.includes('avatar') || 
          src?.includes('emoji') || 
          src?.includes('sticker') ||
          src?.includes('icon') ||
          src?.includes('logo')) return;
      
      // Skip duplicates
      if (seen.has(src)) return;
      seen.add(src);
      
      // Only include images in media containers
      const mediaContainer = img.closest('.media-photo, .photo, [class*="photo"], .media-container');
      if (!mediaContainer) return;
      
      // Final check: ensure no video element anywhere near this image
      if (mediaContainer.querySelector('video')) {
        return;
      }
      
      images.push({
        type: 'image',
        element: img,
        url: src,
        id: `image_${index}_${Date.now()}`,
        width: img.naturalWidth,
        height: img.naturalHeight
      });
      
      this.processedImages.add(img);
    });
    
    return images;
  },
  
  // Detect documents
  detectDocuments() {
    const documents = [];
    
    document.querySelectorAll(this.selectors.document).forEach((doc, index) => {
      // Try to find download link or data attribute
      const link = doc.querySelector('a[href], [data-src], [data-url]');
      const url = link?.href || link?.dataset.src || link?.dataset.url || doc.dataset.src;
      
      if (url) {
        const filename = this.extractDocumentName(doc);
        const size = this.extractDocumentSize(doc);
        
        documents.push({
          type: 'document',
          element: doc,
          url: url,
          id: `doc_${index}_${Date.now()}`,
          filename: filename,
          size: size
        });
      }
    });
    
    return documents;
  },
  
  // Extract document filename from element
  extractDocumentName(element) {
    const nameEl = element.querySelector('.document-name, .filename, [class*="name"]');
    return nameEl?.textContent?.trim() || 'document';
  },
  
  // Extract document size from element
  extractDocumentSize(element) {
    const sizeEl = element.querySelector('.document-size, .filesize, [class*="size"]');
    return sizeEl?.textContent?.trim() || '';
  },
  
  // Get media from media viewer (fullscreen view)
  getMediaViewerContent() {
    const viewer = document.querySelector(this.selectors.mediaViewer);
    if (!viewer) return null;
    
    // Check for video FIRST - priority over images
    const video = viewer.querySelector('video');
    if (video) {
      const src = this.getVideoSource(video);
      if (src) {
        return {
          type: 'video',
          element: video,
          url: src,
          quality: this.detectVideoQuality(video)
        };
      }
    }
    
    // Check for image only if no video
    const img = viewer.querySelector('img:not([class*="thumb"]):not([class*="avatar"])');
    if (img?.src && !img.src.includes('thumb')) {
      return {
        type: 'image',
        element: img,
        url: img.src
      };
    }
    
    return null;
  },
  
  // Check if element is a downloadable media
  isDownloadableMedia(element) {
    if (!element) return false;
    
    // Direct media elements
    if (element.tagName === 'VIDEO' || element.tagName === 'AUDIO') {
      return true;
    }
    
    // Images in media containers (but not in video containers)
    if (element.tagName === 'IMG') {
      // Check if parent has video
      const parent = element.closest('.media-container, [class*="media"], .media-inner');
      if (parent?.querySelector('video')) return false; // It's a video thumbnail
      
      // Check if message bubble has video
      const messageBubble = element.closest('.message, .Message, .bubble, [class*="message"]');
      if (messageBubble?.querySelector('video')) return false; // It's a video thumbnail
      
      const container = element.closest('.media-photo, .media-container, [class*="media"]');
      return !!container;
    }
    
    // Document containers
    if (element.closest(this.selectors.document)) {
      return true;
    }
    
    return false;
  },
  
  // Get media info from element
  getMediaInfo(element) {
    if (element.tagName === 'VIDEO') {
      return {
        type: 'video',
        url: this.getVideoSource(element),
        quality: this.detectVideoQuality(element)
      };
    }
    
    if (element.tagName === 'AUDIO') {
      return {
        type: 'audio',
        url: element.currentSrc || element.src
      };
    }
    
    if (element.tagName === 'IMG') {
      // Double check this isn't a video thumbnail - check multiple parent levels
      const parent = element.closest('.media-container, [class*="media"], .media-inner');
      const messageBubble = element.closest('.message, .Message, .bubble, [class*="message"]');
      
      // Check immediate parent for video
      if (parent?.querySelector('video')) {
        // Return the video info instead
        const video = parent.querySelector('video');
        return {
          type: 'video',
          url: this.getVideoSource(video),
          quality: this.detectVideoQuality(video)
        };
      }
      
      // Check message bubble for video
      if (messageBubble?.querySelector('video')) {
        const video = messageBubble.querySelector('video');
        return {
          type: 'video',
          url: this.getVideoSource(video),
          quality: this.detectVideoQuality(video)
        };
      }
      
      return {
        type: 'image',
        url: element.dataset.fullSrc || element.dataset.src || element.src
      };
    }
    
    const docContainer = element.closest(this.selectors.document);
    if (docContainer) {
      return {
        type: 'document',
        url: docContainer.querySelector('a')?.href || docContainer.dataset.src,
        filename: this.extractDocumentName(docContainer)
      };
    }
    
    return null;
  },
  
  // Reset processed tracking (call when page changes significantly)
  reset() {
    this.processedVideos = new WeakSet();
    this.processedImages = new WeakSet();
    window.__tgVideoContainers = new Set();
  }
};

// Make available globally
window.TGMediaDetector = TGMediaDetector;
