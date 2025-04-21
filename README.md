# XcelerateDL Documentation

## Overview

XcelerateDL is a modern, high-performance download manager API built with FastAPI and Python. It provides a robust backend for managing downloads with features similar to Internet Download Manager (IDM). The application offers both a RESTful API for integration with other applications and a GUI interface for standalone usage.

## Core Features

- **Fast Asynchronous Downloads**: Uses Python's asyncio and aiohttp for efficient concurrent downloads
- **YouTube Integration**: Download YouTube videos or extract audio tracks as MP3 files
- **File Categorization**: Automatic organization of downloaded files by type
- **Download Control**: Pause, resume, or cancel individual downloads
- **Queue Management**: Control download queues with pause-all and resume-all functionality
- **Progress Tracking**: Monitor download progress, speed, and estimated time remaining
- **Persistent Storage**: Downloads state is automatically saved and can be resumed after application restart
- **WebSocket Updates**: Real-time download progress updates via WebSockets

## Architecture

XcelerateDL follows a modular architecture:

1. **FastAPI Backend**: Handles HTTP requests and serves the API endpoints
2. **Download Manager**: Core service responsible for managing downloads
3. **WebSocket Manager**: Handles real-time communication with clients
4. **Data Models**: Pydantic models for type validation and API documentation
5. **Web UI**: HTML/CSS/JS frontend for user interaction

### Key Components

#### Download Manager

The `DownloadManager` class in `app/services/downloader.py` is the heart of the application. It manages:
- Download queue processing
- File download operations
- YouTube video/audio downloading
- Download status tracking
- Persistence of download states

#### API Routes

API endpoints in `app/api/downloads.py` expose the download functionality through a RESTful interface:
- `/api/downloads`: Add and list downloads
- `/api/downloads/{id}`: Get, pause, resume, or delete a specific download
- `/api/downloads/pause-all`: Pause all active downloads
- `/api/downloads/resume-all`: Resume all paused downloads

#### Data Models

The models in `app/models/download.py` define the data structures:
- `DownloadItem`: Represents a download with all its metadata
- `DownloadStatus`: Enum of possible download states (queued, downloading, paused, etc.)
- `FileCategory`: Enum of file categories for organization
- `YoutubeDownloadType`: Enum for YouTube download options (video or audio)

## Installation

### Prerequisites

- Python 3.8 or higher
- For YouTube download functionality:
  - yt-dlp or youtube-dl
  - ffmpeg (for audio extraction)

### Step-by-Step Installation

1. Clone the repository:
   ```bash
   git clone https://github.com/yourusername/XcelerateDL.git
   cd XcelerateDL
   ```

2. Create and activate a virtual environment (optional but recommended):
   ```bash
   python -m venv venv
   # On Windows
   venv\Scripts\activate
   # On macOS/Linux
   source venv/bin/activate
   ```

3. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

4. Run the application:
   ```bash
   # Start with GUI
   python -m app.main --gui
   
   # Start API only
   python -m app.main --api-only
   
   # Start with custom host/port
   python -m app.main --api-only --host 127.0.0.1 --port 8080
   ```

## API Reference

### Add a New Download

```
POST /api/downloads
```

Request body:
```json
{
  "url": "https://example.com/file.zip",
  "filename": "myfile.zip",  // optional, detected from URL if not provided
  "save_path": "/path/to/save",  // optional, defaults to downloads folder
  "category": "compressed",  // optional, auto-detected if not provided
  "is_youtube": false,  // optional, auto-detected if not provided
  "youtube_type": "video"  // optional, used for YouTube downloads
}
```

Response:
```json
{
  "download": {
    "id": "550e8400-e29b-41d4-a716-446655440000",
    "name": "myfile.zip",
    "url": "https://example.com/file.zip",
    "size": 1024000,
    "size_downloaded": 0,
    "status": "queued",
    "speed": 0,
    "time_left": null,
    "date_added": "2023-04-01T12:00:00",
    "save_path": "/path/to/save/myfile.zip",
    "category": "compressed",
    "is_youtube": false,
    "youtube_type": null,
    "progress": 0
  }
}
```

### List All Downloads

```
GET /api/downloads
```

Query parameters:
- `category`: Filter by file category (optional)
- `status`: Filter by download status (optional)

Response:
```json
{
  "downloads": [
    {
      "id": "550e8400-e29b-41d4-a716-446655440000",
      "name": "myfile.zip",
      "url": "https://example.com/file.zip",
      "size": 1024000,
      "size_downloaded": 512000,
      "status": "downloading",
      "speed": 1048576,
      "time_left": 30,
      "date_added": "2023-04-01T12:00:00",
      "save_path": "/path/to/save/myfile.zip",
      "category": "compressed",
      "is_youtube": false,
      "youtube_type": null,
      "progress": 50
    }
  ],
  "total": 1
}
```

### Get Download Details

```
GET /api/downloads/{download_id}
```

Response: Same as add download response

### Pause a Download

```
POST /api/downloads/{download_id}/pause
```

Response: Download item with updated status

### Resume a Download

```
POST /api/downloads/{download_id}/resume
```

Response: Download item with updated status

### Delete a Download

```
DELETE /api/downloads/{download_id}?delete_file=false
```

Query parameters:
- `delete_file`: Whether to delete the downloaded file (default: false)

Response:
```json
{
  "success": true
}
```

### Pause All Downloads

```
POST /api/downloads/pause-all
```

Response:
```json
{
  "paused_count": 3
}
```

### Resume All Downloads

```
POST /api/downloads/resume-all
```

Response:
```json
{
  "resumed_count": 3
}
```

### Open a Downloaded File

```
POST /api/downloads/{download_id}/open
```

Response:
```json
{
  "success": true,
  "message": "Opening file: myfile.zip"
}
```

## WebSocket API

XcelerateDL provides real-time updates through WebSockets at:

```
ws://localhost:8000/ws
```

The WebSocket sends JSON messages with download updates:

```json
{
  "type": "update",
  "download": {
    "id": "550e8400-e29b-41d4-a716-446655440000",
    "name": "myfile.zip",
    "status": "downloading",
    "progress": 50,
    "speed": 1048576,
    "time_left": 30
    // ... other download properties
  }
}
```

## YouTube Download Features

XcelerateDL uses yt-dlp (or youtube-dl as fallback) to download content from YouTube. 

### Supported Features

- Video downloads in highest quality
- Audio extraction (MP3 format)
- Progress tracking
- Pause/resume functionality

### Example YouTube Download Request

```json
{
  "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
  "is_youtube": true,
  "youtube_type": "audio"
}
```

## File Categories

XcelerateDL automatically categorizes downloads based on file extensions:

- `compressed`: .zip, .rar, .7z, .tar, .gz
- `programs`: .exe, .msi, .deb, .rpm, .pkg
- `videos`: .mp4, .avi, .mkv, .mov, .wmv
- `music`: .mp3, .wav, .ogg, .flac, .m4a
- `pictures`: .jpg, .jpeg, .png, .gif, .bmp, .webp
- `documents`: .pdf, .doc, .docx, .txt, .xls, .xlsx, .ppt, .pptx
- `youtube`: Special category for YouTube downloads
- `other`: Any other file type

## Download Status Lifecycle

Downloads in XcelerateDL follow this lifecycle:

1. `queued`: Initial state after adding a download
2. `downloading`: Download is in progress
3. `paused`: Download has been manually paused
4. `completed`: Download has finished successfully
5. `failed`: Download encountered an error and could not complete

## Configuration

XcelerateDL has these configurable settings:

- Default download directory (default: `./downloads`)
- Storage file location (default: `./downloads/download_data.json`)
- Autosave interval (default: 30 seconds)

## Advanced Features

### Partial Download Recovery

XcelerateDL automatically detects partially downloaded files and resumes them from where they left off. This works by:

1. Comparing the downloaded file size with the expected total size
2. Sending a Range HTTP header to resume from the correct position
3. Updating progress tracking based on already downloaded content

### Download Speed and Time Left Calculation

The application tracks download speed and estimates remaining time by:
- Monitoring download progress over time
- Calculating a rolling average of download speed
- Estimating time left based on current speed and remaining bytes

### Error Handling

XcelerateDL handles various download errors:
- Network errors: Temporary failures are retried
- Permanent errors: Downloads are marked as failed
- Server errors: Appropriate HTTP status codes are handled

## Best Practices

1. **Recommended Folder Structure**:
   - Keep downloads organized in category subdirectories
   - Use a separate location for temporary/in-progress downloads

2. **Performance Optimization**:
   - Limit concurrent downloads to avoid network saturation
   - For YouTube downloads, audio-only is faster than video downloads

3. **Error Recovery**:
   - For failed downloads, try resuming before restarting from scratch
   - Check your internet connection before initiating large downloads

## Troubleshooting

### Common Issues

1. **Downloads fail to start**:
   - Check internet connectivity
   - Verify the URL is accessible from your network
   - Ensure you have write permissions to the download directory

2. **YouTube downloads fail**:
   - Make sure yt-dlp or youtube-dl is properly installed
   - For audio extraction, verify ffmpeg is installed

3. **Application crashes**:
   - Check logs for error messages
   - Ensure all dependencies are correctly installed

### Debugging

Enable detailed logging by setting the environment variable:
```
DEBUG=1
```

## Contributing

Contributions to XcelerateDL are welcome! Here's how you can contribute:

1. **Report bugs** by opening an issue
2. **Request features** through the issue tracker
3. **Submit pull requests** for bug fixes or features

Please follow the existing code style and add tests for new features.

## License

XcelerateDL is released under the MIT License. 