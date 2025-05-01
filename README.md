# XcelerateDL

<div align="center">
  
![XcelerateDL Logo](docs/asset/image.png)

**A high-performance download manager with FastAPI backend and intuitive UI**

[![Python Version](https://img.shields.io/badge/python-3.8%2B-blue.svg)](https://www.python.org/downloads/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.68%2B-009688.svg)](https://fastapi.tiangolo.com/)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

</div>

## 🚀 Overview

XcelerateDL is a modern, high-performance download manager built with Python and FastAPI. It provides a powerful, asynchronous downloading engine capable of handling multiple concurrent downloads with intelligent bandwidth management. The application offers both a web interface and a desktop GUI mode, making it versatile for different use cases.

## ✨ Key Features

- **⚡ High-Performance Downloads**: Leverages asynchronous I/O and multi-threading for optimal download speeds
- **🎥 YouTube Integration**: Download videos or extract audio as MP3 using yt-dlp
- **⏯️ Download Control**: Pause, resume, or cancel downloads at any time
- **🔄 Real-time Updates**: Monitor download progress via WebSockets
- **📁 Automatic File Organization**: Auto-categorize downloads by file type (videos, music, documents, etc.)
- **📊 Queue Management**: Easily manage download queue with priority settings
- **💾 Persistent State**: Resume downloads after application restart
- **🖥️ Dual Interface**: Use either the web UI or standalone desktop GUI
- **🌐 Bandwidth Management**: Intelligently allocate bandwidth across multiple downloads
- **⏰ Advanced Scheduling**: Schedule downloads with recurrence options and smart scheduling
- **🔍 Powerful Search**: Find downloads with advanced filtering options

## 📋 Table of Contents

- [Quick Start](#-quick-start)
- [Installation](#-installation)
- [Usage](#-usage)
  - [GUI Mode](#gui-mode)
  - [API Mode](#api-mode)
- [API Documentation](#-api-documentation)
- [Configuration](#-configuration)
- [Architecture](#-architecture)
- [Development](#-development)
- [Contributing](#-contributing)
- [License](#-license)

## 🚀 Quick Start

### Prerequisites

- Python 3.8+ (3.13 recommended)
- ffmpeg (required for YouTube downloads)

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
pip install -e .

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

3. **Install the application and its dependencies**:
   ```bash
   pip install -e .
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

XcelerateDL offers a powerful scheduling system for downloads:

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

## ⚙️ Configuration

XcelerateDL can be configured through:

- **Command Line Arguments**: Override defaults for the current session
- **API Endpoints**: Programmatically update settings
- **Settings UI**: Configure through the application interface (in GUI mode)

### Command Line Arguments

```bash
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

### Core Components

#### FastAPI Backend
- RESTful API endpoints for download management
- WebSocket support for real-time updates
- Automatic API documentation (Swagger UI and ReDoc)

#### Download Manager
- Handles download operations using asyncio
- Manages the download queue based on priority
- Processes YouTube downloads with yt-dlp
- Implements bandwidth allocation logic
- Manages scheduling system for downloads

#### WebSocket Manager
- Manages client connections
- Broadcasts download updates in real-time
- Handles connection and disconnection events

#### Data Models
- Uses Pydantic for strict type validation
- Ensures data integrity throughout the application
- Provides clean serialization/deserialization

#### User Interfaces
- Web UI built with HTML, CSS, and JavaScript
- Desktop GUI using Eel for a native-like experience

## 🧪 Development

### Project Structure

```
XcelerateDL/
├─ app/                   # Main application code
│  ├─ api/                # API endpoints
│  │  └─ downloads.py     # Download-related endpoints
│  ├─ models/             # Data models
│  │  └─ download.py      # Download and related models
│  ├─ services/           # Business logic
│  │  ├─ downloader.py    # Download manager service
│  │  └─ ws_manager.py    # WebSocket manager
│  ├─ static/             # Static assets for web UI
│  │  ├─ css/             # CSS styles
│  │  ├─ js/              # JavaScript code
│  │  └─ images/          # UI images
│  ├─ templates/          # HTML templates
│  │  └─ index.html       # Main UI template
│  ├─ gui.py              # GUI implementation
│  ├─ main.py             # Application entry point
├─ docs/                  # Documentation
├─ downloads/             # Default download directory
├─ pyproject.toml         # Project metadata and dependencies
├─ README.md              # Project overview
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

## 🤝 Contributing

Contributions are welcome! Here's how you can contribute:

1. Fork the repository
2. Create a feature branch: `git checkout -b my-new-feature`
3. Make your changes
4. Add tests for your changes
5. Run the existing tests to ensure nothing breaks
6. Commit your changes: `git commit -am 'Add some feature'`
7. Push to the branch: `git push origin my-new-feature`
8. Submit a pull request

### Coding Standards

- Follow PEP 8 guidelines for Python code
- Use type hints wherever possible
- Document functions and classes with docstrings
- Format code with the project's formatter

## 📄 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## 🙏 Acknowledgements

- [FastAPI](https://fastapi.tiangolo.com/) - For the powerful API framework
- [yt-dlp](https://github.com/yt-dlp/yt-dlp) - For YouTube download functionality
- [Eel](https://github.com/ChrisKnott/Eel) - For the GUI framework
- [aiohttp](https://docs.aiohttp.org/) - For asynchronous HTTP requests
- [Pydantic](https://pydantic-docs.helpmanual.io/) - For data validation
