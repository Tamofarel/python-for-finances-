# TG Downloader Pro

A Chrome browser extension for downloading media (videos, audio, images, documents) from Telegram Web without restrictions.

## Features

- 🎬 **Video Download** - Download videos with quality selection (1080p, 720p, 480p, 360p)
- 🎵 **Audio/Voice** - Download voice messages and audio files
- 🖼️ **Images** - Download photos and images in full resolution
- 📁 **Documents** - Download any document or file
- 📊 **Progress Tracking** - Real-time download progress indicator
- 📦 **Batch Download** - Download multiple files at once
- ⌨️ **Keyboard Shortcuts** - Ctrl+Shift+D to download all visible media

## Installation

1. Download or clone this repository
2. Open Chrome and go to `chrome://extensions`
3. Enable **Developer mode** (toggle in top right)
4. Click **Load unpacked**
5. Select the `tg-downloader-extension` folder
6. The extension icon should appear in your toolbar

## Usage

1. Navigate to [Telegram Web](https://web.telegram.org)
2. Open any chat with media content
3. Hover over any video, image, or audio - a download button will appear
4. Click the button to download

### Batch Download

- **Right-click** on download buttons to select multiple items
- A floating bar will appear showing selected count
- Click **Download All** to download all selected items
- Press **Escape** to clear selection

### Keyboard Shortcuts

- `Ctrl+Shift+D` - Select and download all visible media
- `Escape` - Clear batch selection

## Extension Popup

Click the extension icon to see:
- Connection status
- Media count (videos, audio, images, files)
- Active downloads with progress
- Quick scan and download buttons

## Permissions

- `downloads` - To download files
- `storage` - To save settings
- `activeTab` - To inject content scripts
- Host permissions for `*.telegram.org`

## Technical Details

- **Manifest Version**: 3 (latest Chrome extension standard)
- **Content Scripts**: Injected on Telegram Web pages
- **Service Worker**: Handles background download operations
- **Media Detection**: Uses MutationObserver for dynamic content

## Troubleshooting

**Download buttons not appearing?**
- Make sure you're on `web.telegram.org`
- Click the extension icon and hit "Scan for Media"
- Try refreshing the page

**Downloads failing?**
- Check if the media has finished loading in Telegram
- Some protected content may not be downloadable
- Try opening media in fullscreen first

## License

MIT License - Feel free to modify and distribute.

## Disclaimer

This extension is for personal use only. Please respect copyright and terms of service of Telegram and content creators.
