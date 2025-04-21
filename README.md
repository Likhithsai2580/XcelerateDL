# XcelerateDL

A high-performance download manager with FastAPI backend and intuitive UI.

## Features

- **Fast Downloads**: Multi-threaded, asynchronous downloading
- **YouTube Support**: Download videos or extract audio as MP3
- **Flexible Control**: Pause, resume, or cancel downloads
- **Real-time Updates**: Monitor progress via WebSockets 
- **File Organization**: Auto-categorize downloads by type
- **Queue Management**: Prioritize downloads with pause/resume all
- **Persistent Storage**: Resume downloads after application restart
- **Dual Interface**: Use the REST API or standalone GUI

## Quick Start

### Prerequisites

- Python 3.8+
- For YouTube downloads: ffmpeg

### Installation

```bash
# Clone the repository
git clone https://github.com/yourusername/XcelerateDL.git
cd XcelerateDL

# Create and activate virtual environment (recommended)
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Run the application
python -m app.main  # Default mode
python -m app.main --gui  # GUI mode
python -m app.main --api-only --host 127.0.0.1 --port 8080  # API only with custom host/port
```

## API Usage

### Add a Download

```bash
curl -X POST http://localhost:8000/api/downloads \
  -H "Content-Type: application/json" \
  -d '{"url": "https://example.com/file.zip"}'
```

### YouTube Download

```bash
curl -X POST http://localhost:8000/api/downloads \
  -H "Content-Type: application/json" \
  -d '{"url": "https://youtube.com/watch?v=VIDEO_ID", "is_youtube": true, "youtube_type": "video"}'
```

### Manage Downloads

```bash
# List all downloads
curl http://localhost:8000/api/downloads

# Pause a download
curl -X POST http://localhost:8000/api/downloads/{download_id}/pause

# Resume a download
curl -X POST http://localhost:8000/api/downloads/{download_id}/resume

# Delete a download
curl -X DELETE http://localhost:8000/api/downloads/{download_id}

# Pause all downloads
curl -X POST http://localhost:8000/api/downloads/pause-all

# Resume all downloads
curl -X POST http://localhost:8000/api/downloads/resume-all
```

## Comprehensive Documentation

### API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/downloads` | GET | List all downloads with optional filtering by category or status |
| `/api/downloads` | POST | Add a new download |
| `/api/downloads/{id}` | GET | Get details of a specific download |
| `/api/downloads/{id}/pause` | POST | Pause a specific download |
| `/api/downloads/{id}/resume` | POST | Resume a specific download |
| `/api/downloads/{id}` | DELETE | Delete a download, with option to delete the file |
| `/api/downloads/pause-all` | POST | Pause all active downloads |
| `/api/downloads/resume-all` | POST | Resume all paused downloads |
| `/api/downloads/{id}/open` | POST | Open downloaded file with system's default application |
| `/api/downloads/{id}/settings` | POST | Update settings for a specific download |

### Download Parameters

When creating a download, you can specify these parameters:

```json
{
  "url": "https://example.com/file.zip",
  "filename": "myfile.zip",  // Optional: Auto-detected if not provided
  "save_path": "/path/to/save",  // Optional: Uses default downloads folder if not specified
  "category": "compressed",  // Optional: Auto-detected based on file extension
  "is_youtube": false,  // Optional: Auto-detected from URL
  "youtube_type": "video",  // Optional: "video" or "audio"
  "priority": 2,  // Optional: 1=low, 2=normal, 3=high
  "max_speed": 1048576,  // Optional: Limit download speed in bytes/sec
  "max_retries": 3  // Optional: Max retry attempts for failed downloads
}
```

### Status Codes

| Status | Description |
|--------|-------------|
| `queued` | Download is waiting in queue |
| `downloading` | Download is in progress |
| `paused` | Download has been paused |
| `completed` | Download has finished successfully |
| `failed` | Download encountered an error |

### File Categories

| Category | Description |
|----------|-------------|
| `compressed` | ZIP, RAR, 7Z, etc. |
| `programs` | EXE, MSI, DEB, etc. |
| `videos` | MP4, MKV, AVI, etc. |
| `music` | MP3, FLAC, WAV, etc. |
| `pictures` | JPG, PNG, GIF, etc. |
| `documents` | PDF, DOCX, XLSX, etc. |
| `youtube` | YouTube videos or extracted audio |
| `other` | Files not matching other categories |

## Configuration

Configure default settings in the app:

- Download folder location
- Maximum concurrent downloads
- Default download priorities
- Speed limits

## Architecture

- **FastAPI Backend**: Handles HTTP requests and WebSocket connections
- **Download Manager**: Core service that manages the download queue and operations
- **WebSocket Manager**: Provides real-time updates to connected clients
- **Data Models**: Pydantic models for type validation
- **GUI**: Built with Eel for a seamless desktop experience

### Core Components

#### Download Manager

The heart of XcelerateDL is the `DownloadManager` class which:
- Processes the download queue based on priority
- Manages file download operations using async I/O
- Handles YouTube video/audio downloading through yt-dlp
- Tracks download status, speed, and progress
- Persists download states between application sessions

#### WebSocket Manager

The WebSocket manager enables real-time updates by:
- Managing client connections
- Broadcasting download status changes
- Providing progress updates during downloads
- Enabling responsive UI without polling

#### FastAPI Routes

The API routes provide RESTful access to all download functionality:
- CRUD operations for downloads
- Batch operations (pause-all, resume-all)
- File operations (open downloaded files)
- Filtering and searching downloads

## Development

### Project Structure

```
XcelerateDL/
├── app/
│   ├── api/           # API endpoints
│   ├── models/        # Data models
│   ├── services/      # Business logic
│   ├── static/        # CSS, JS, images
│   ├── templates/     # HTML templates
│   ├── gui.py         # Desktop GUI
│   └── main.py        # Application entry point
├── downloads/         # Default download location
├── docs/              # Documentation
├── requirements.txt   # Python dependencies
└── README.md          # This file
```

## License

MIT License

Copyright (c) 2023 XcelerateDL

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE. 