# XcelerateDL

<div align="center">
  
![XcelerateDL Logo](docs/asset/image.png)

**A high-performance download manager with FastAPI backend and intuitive UI**

[![Python Version](https://img.shields.io/badge/python-3.8%2B-blue.svg)](https://www.python.org/downloads/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.68%2B-009688.svg)](https://fastapi.tiangolo.com/)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

</div>

## 🚀 Features

- **⚡ Fast Downloads**: Multi-threaded, asynchronous downloading for maximum speed
- **🎥 YouTube Support**: Download videos or extract audio as MP3
- **⏯️ Flexible Control**: Pause, resume, or cancel downloads anytime
- **🔄 Real-time Updates**: Monitor download progress via WebSockets
- **📁 File Organization**: Auto-categorize downloads by file type
- **📊 Queue Management**: Prioritize downloads with pause/resume all functionality
- **💾 Persistent Storage**: Resume downloads after application restart
- **🖥️ Dual Interface**: Use the REST API or standalone GUI
- **🌐 Bandwidth Management**: Intelligent bandwidth allocation across multiple downloads
- **⏰ Download Scheduling**: Schedule downloads for specific times/dates
- **🔍 Advanced Search**: Powerful search functionality for finding downloads

## 📋 Table of Contents

- [Quick Start](#-quick-start)
- [Installation](#-installation)
- [Usage](#-usage)
  - [GUI Mode](#gui-mode)
  - [API Mode](#api-mode)
- [API Documentation](#-api-documentation)
- [Configuration](#-configuration)
- [Architecture](#-architecture)
- [Contributing](#-contributing)
- [Development](#-development)
- [License](#-license)

## 🚀 Quick Start

### Prerequisites

- Python 3.8+ (3.13 recommended)
- For YouTube downloads: ffmpeg

### Quick Install

```bash
# Clone the repository
git clone https://github.com/yourusername/XcelerateDL.git
cd XcelerateDL

# Create and activate virtual environment
python -m venv venv
# On Windows:
venv\Scripts\activate
# On macOS/Linux:
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Start the application
python -m app.main --gui
```

## 🔧 Installation

### Detailed Steps

1. **Clone the repository**:
   ```bash
   git clone https://github.com/yourusername/XcelerateDL.git
   cd XcelerateDL
   ```

2. **Create and activate a virtual environment**:
   ```bash
   python -m venv venv
   
   # On Windows:
   venv\Scripts\activate
   
   # On macOS/Linux:
   source venv/bin/activate
   ```

3. **Install dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

4. **Install ffmpeg** (required for YouTube downloads):
   - **Windows**: [Download ffmpeg](https://www.gyan.dev/ffmpeg/builds/) and add it to your PATH
   - **macOS**: `brew install ffmpeg`
   - **Linux**: `sudo apt install ffmpeg` (Debian/Ubuntu) or `sudo yum install ffmpeg` (CentOS/RHEL)

5. **Run the application**:
   ```bash
   python -m app.main --gui
   ```

## 🖥️ Usage

XcelerateDL offers two operation modes:

### GUI Mode

For a complete desktop experience with a user-friendly interface:

```bash
python -m app.main --gui
```

This launches a desktop application with all features accessible through an intuitive interface:

- Drag and drop URLs for quick downloads
- Monitor download progress in real-time
- Organize downloads by category
- Set bandwidth limits and priorities
- Schedule downloads for off-peak hours

### API Mode

For headless operation or integration with other applications:

```bash
# Default configuration (host: 0.0.0.0, port: 8000)
python -m app.main

# Custom host and port
python -m app.main --api-only --host 127.0.0.1 --port 8080
```

After starting in API mode, access the web interface at `http://localhost:8000` (or your custom host/port).

## 🔌 API Documentation

Once the server is running, access the interactive API documentation at:
- Swagger UI: `http://localhost:8000/docs`
- ReDoc: `http://localhost:8000/redoc`

### Basic API Examples

#### Adding a Download

```bash
curl -X POST http://localhost:8000/api/downloads \
  -H "Content-Type: application/json" \
  -d '{"url": "https://example.com/file.zip"}'
```

#### YouTube Download

```bash
curl -X POST http://localhost:8000/api/downloads \
  -H "Content-Type: application/json" \
  -d '{"url": "https://youtube.com/watch?v=VIDEO_ID", "is_youtube": true, "youtube_type": "video"}'
```

#### Scheduling a Download

```bash
curl -X POST http://localhost:8000/api/downloads \
  -H "Content-Type: application/json" \
  -d '{"url": "https://example.com/large-file.zip", "schedule": {"scheduled_time": "2023-12-31T23:00:00", "recurrence": "weekly", "days_of_week": [0, 3]}}'
```

#### Managing Downloads

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

# Search downloads
curl -X POST http://localhost:8000/api/downloads/search \
  -H "Content-Type: application/json" \
  -d '{"query": "ubuntu", "category": "compressed", "tags": ["linux"]}'

# Update bandwidth settings
curl -X POST http://localhost:8000/api/downloads/bandwidth/settings \
  -H "Content-Type: application/json" \
  -d '{"total_bandwidth": 10485760, "allocation_mode": "priority"}'
```

### Full API Reference

For a complete list of available endpoints, parameters, and responses, see our [API Reference Section](#-api-reference) below.

## 📘 API Reference

### Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/downloads` | GET | List all downloads with optional filtering |
| `/api/downloads` | POST | Add a new download |
| `/api/downloads/search` | POST | Search downloads with advanced criteria |
| `/api/downloads/{id}` | GET | Get details of a specific download |
| `/api/downloads/{id}/pause` | POST | Pause a specific download |
| `/api/downloads/{id}/resume` | POST | Resume a specific download |
| `/api/downloads/{id}` | DELETE | Delete a download, with option to delete file |
| `/api/downloads/pause-all` | POST | Pause all active downloads |
| `/api/downloads/resume-all` | POST | Resume all paused downloads |
| `/api/downloads/{id}/open` | POST | Open downloaded file with default application |
| `/api/downloads/{id}/settings` | POST | Update settings for a specific download |
| `/api/downloads/{id}/schedule` | POST | Schedule a download for a specific time |
| `/api/downloads/bandwidth/settings` | GET | Get current bandwidth settings |
| `/api/downloads/bandwidth/settings` | POST | Update bandwidth settings |
| `/api/downloads/scheduler/settings` | POST | Update scheduler check interval |
| `/api/downloads/{id}/tags` | POST | Update tags for a download |
| `/api/downloads/batch-schedule` | POST | Schedule multiple downloads at once |
| `/api/downloads/smart-schedule` | POST | Intelligently schedule multiple downloads |
| `/api/downloads/schedule-based-on-bandwidth` | POST | Schedule downloads based on available bandwidth |

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
  "max_retries": 3,  // Optional: Max retry attempts for failed downloads
  "schedule": {  // Optional: Schedule settings
    "scheduled_time": "2023-12-31T23:00:00",  // When to start the download
    "recurrence": "weekly",  // "daily", "weekly", "monthly", or null for one-time
    "days_of_week": [0, 3],  // For weekly: 0=Monday, 6=Sunday
    "day_of_month": 15,  // For monthly: 1-31
    "bandwidth_allocation": 30  // Optional: Percentage of bandwidth to use (0-100)
  },
  "bandwidth_allocation": 20,  // Optional: Percentage of bandwidth to allocate
  "tags": ["linux", "iso"]  // Optional: Tags for organization and search
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
| `scheduled` | Download is scheduled for future |

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

## 📊 Bandwidth Management

The bandwidth management system allows intelligent allocation of network resources:

### Allocation Modes
- **Equal**: Each download gets an equal share of available bandwidth
- **Priority**: Higher priority downloads get more bandwidth (Low=1x, Normal=2x, High=4x weight)
- **Custom**: Specify exact percentages for individual downloads

### Setting Bandwidth Limits
```json
{
  "total_bandwidth": 10485760,  // Total bandwidth in bytes/sec (10 MB/s)
  "allocation_mode": "priority",  // "equal", "priority", or "custom"
  "custom_allocations": {  // Only used in custom mode
    "download-id-1": 60,  // Percentage allocation (0-100)
    "download-id-2": 20
  },
  "max_concurrent_downloads": 5,  // Maximum number of concurrent downloads
  "enable_scheduling": true,  // Enable smart scheduling
  "peak_hours_throttling": false,  // Throttle during peak hours 
  "peak_hours_start": 18,  // Peak hours start (6 PM)
  "peak_hours_end": 23,  // Peak hours end (11 PM)
  "peak_hours_limit": 5242880,  // Bandwidth limit during peak hours (5 MB/s)
  "scheduler_check_interval": 60  // Seconds between scheduler checks
}
```

## ⏰ Download Scheduling

Schedule downloads for specific times to better manage bandwidth usage:

### Scheduling Options
- One-time schedules (specific date and time)
- Recurring schedules (daily, weekly, or monthly)
- Day-of-week selection for weekly schedules
- Day-of-month selection for monthly schedules
- Per-download bandwidth allocation during scheduled times
- Peak hours detection and throttling

### Advanced Scheduling Features
- Smart scheduling to distribute downloads across time periods
- Bandwidth-based scheduling to complete downloads by target time
- Auto-retry for failed scheduled downloads
- Custom schedule descriptions and notifications

## 🔍 Advanced Search

Use the powerful search functionality to find downloads with multiple criteria:

### Search Parameters
```json
{
  "query": "ubuntu",  // Text to search in name/url/tags
  "category": "compressed",  // Filter by category
  "status": ["completed", "failed"],  // Filter by one or more statuses
  "date_from": "2023-01-01T00:00:00",  // Filter by date range
  "date_to": "2023-12-31T23:59:59",
  "min_size": 1048576,  // Filter by size range (bytes)
  "max_size": 1073741824,
  "tags": ["linux", "iso"]  // Filter by tags
}
```

## ⚙️ Configuration

XcelerateDL's behavior can be configured through:

- **Environment Variables**: Set system-wide defaults
- **Command Line Arguments**: Override defaults for the current session
- **Settings UI**: Configure through the application interface
- **API Endpoints**: Programmatically update settings

### Command Line Arguments

```
--gui               Start the GUI version
--api-only          Start only the API server
--port PORT         API server port (default: 8000)
--host HOST         API server host (default: 0.0.0.0)
```

### Configurable Settings
- Download folder location
- Maximum concurrent downloads
- Default download priorities
- Speed limits
- Global bandwidth settings
- Scheduler check interval
- Peak hours configuration

## 🏗️ Architecture

XcelerateDL follows a modern, modular architecture:

- **FastAPI Backend**: Handles HTTP requests and WebSocket connections
- **Download Manager**: Core service that manages the download queue and operations
- **WebSocket Manager**: Provides real-time updates to connected clients
- **Pydantic Models**: Ensures type validation and data integrity
- **GUI**: Built with Eel for a seamless desktop experience

### Core Components

#### Download Manager

The heart of XcelerateDL is the `DownloadManager` class which:
- Processes the download queue based on priority
- Manages file download operations using async I/O
- Handles YouTube video/audio downloading through yt-dlp
- Maintains persistent download state
- Manages download scheduling and bandwidth allocation

#### API Layer

The FastAPI-based API provides:
- RESTful endpoints for managing downloads
- WebSocket connections for real-time updates
- Swagger documentation for easy integration
- Comprehensive error handling

#### User Interface

XcelerateDL offers dual interfaces:
- **Web UI**: Built with modern web technologies for browser access
- **Desktop App**: Wrapped with Eel for a native-like experience

## 🤝 Contributing

Contributions are welcome! Here's how you can contribute:

### Getting Started

1. Fork the repository on GitHub
2. Clone your fork to your local machine
3. Set up the development environment
4. Create a new branch for your feature or bugfix

### Making Changes

1. Make your changes following the coding style guidelines
2. Add tests for your changes
3. Run the existing tests to ensure nothing breaks
4. Update documentation as needed

### Submitting Changes

1. Commit your changes with clear, descriptive commit messages
2. Push your changes to your fork
3. Create a pull request against the main repository
4. Wait for review and address any feedback

### Coding Standards

- Follow PEP 8 guidelines for Python code
- Use type hints wherever possible
- Document functions and classes with docstrings
- Format code with the project's specified formatter

### Reporting Issues

- Use the GitHub issue tracker to report bugs
- Include detailed steps to reproduce the bug
- Specify your operating system and Python version
- Include logs or screenshots if applicable

## 🧪 Development

### Setting Up a Development Environment

```bash
# Clone the repository
git clone https://github.com/yourusername/XcelerateDL.git
cd XcelerateDL

# Create and activate virtual environment
python -m venv venv
# On Windows:
venv\Scripts\activate
# On macOS/Linux:
source venv/bin/activate

# Install development dependencies
pip install -r requirements.txt
```

### Project Structure

```
XcelerateDL/
├─ app/                   # Main application code
│  ├─ api/                # API endpoints
│  ├─ models/             # Data models
│  ├─ services/           # Business logic
│  ├─ static/             # Static assets for web UI
│  ├─ templates/          # HTML templates
│  ├─ gui.py              # GUI implementation
│  ├─ main.py             # Application entry point
├─ docs/                  # Documentation
├─ downloads/             # Default download directory
├─ requirements.txt       # Python dependencies
├─ pyproject.toml         # Project metadata and configuration
├─ README.md              # Project documentation
├─ FUTUREPLAN.md          # Future development roadmap
```

### Running in Development Mode

```bash
# Start with auto-reload for API development
python -m app.main --api-only --host 127.0.0.1 --port 8000

# Start GUI mode
python -m app.main --gui
```

### Cleaning Downloads

To clean all downloads (useful during development):

```bash
# On Windows
delete_downloads.bat

# On macOS/Linux
rm -rf downloads/*
```

### Future Development

For a detailed roadmap of planned features and improvements, see [FUTUREPLAN.md](FUTUREPLAN.md).

## 📄 License

This project is licensed under the MIT License - see the LICENSE file for details.

## 🙏 Acknowledgements

- [FastAPI](https://fastapi.tiangolo.com/) - For the powerful API framework
- [yt-dlp](https://github.com/yt-dlp/yt-dlp) - For YouTube download functionality
- [Eel](https://github.com/ChrisKnott/Eel) - For the GUI framework
- [aiohttp](https://docs.aiohttp.org/) - For asynchronous HTTP requests
- [Pydantic](https://pydantic-docs.helpmanual.io/) - For data validation
