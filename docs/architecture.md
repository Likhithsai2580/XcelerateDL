# XcelerateDL Architecture

This document describes the architectural design of XcelerateDL, including its components, data flow, and design principles.

## System Overview

XcelerateDL is built on a modern, modular architecture designed for performance, scalability, and extensibility. The application follows a layered architecture pattern with clear separation of concerns.

![Architecture Diagram](asset/architecture-diagram.png)

## Core Components

### FastAPI Backend

The FastAPI backend serves as the foundation of XcelerateDL, providing:

- RESTful API endpoints for managing downloads
- WebSocket connections for real-time updates
- Automatic OpenAPI documentation
- Request validation and data serialization

Key files:
- `app/main.py`: Application entry point and server configuration
- `app/api/downloads.py`: API endpoint definitions for download management

### Download Manager

The Download Manager is the heart of XcelerateDL, responsible for:

- Managing the download queue and lifecycle
- Handling file download operations using async I/O
- YouTube video/audio downloading via yt-dlp
- Bandwidth allocation and scheduling
- Persistent state management

Key files:
- `app/services/downloader.py`: Contains the DownloadManager class
- The DownloadManager is implemented as a singleton instance

### WebSocket Manager

The WebSocket Manager handles real-time communication with clients:

- Manages WebSocket connections
- Broadcasts download updates to all connected clients
- Handles connection and disconnection events

Key files:
- `app/services/ws_manager.py`: Contains the ConnectionManager class

### Data Models

The data models provide strict typing and validation:

- Uses Pydantic for data validation
- Defines core entities like DownloadItem, BandwidthSettings, etc.
- Ensures data integrity throughout the application

Key files:
- `app/models/download.py`: Contains Pydantic models for all data structures

### User Interfaces

XcelerateDL provides two user interfaces:

1. **Web UI**:
   - Built with HTML, CSS, and JavaScript
   - Communicates with the backend via RESTful API and WebSockets
   - Provides full download management capabilities

2. **Desktop GUI**:
   - Uses Eel to wrap the web interface in a desktop application
   - Provides a native-like experience while reusing the web UI code

Key files:
- `app/templates/index.html`: Main UI template
- `app/static/js/script.js`: Frontend JavaScript logic
- `app/static/css/style.css`: CSS styling
- `app/gui.py`: Desktop GUI implementation

## Data Flow

### Download Process

1. User submits a URL for download through UI or API
2. API endpoint receives request and validates input
3. Download Manager creates a new download entry with 'queued' status
4. If slots are available, Download Manager starts downloading the file:
   a. For regular files: Uses aiohttp for asynchronous downloading
   b. For YouTube: Uses yt-dlp in a separate process
5. Progress updates are sent via WebSockets to connected clients
6. When download completes, status is updated and notifications are sent

### Bandwidth Management

1. Download Manager maintains global bandwidth settings
2. Based on allocation mode (equal, priority, custom):
   a. Calculates bandwidth limits for each active download
   b. Applies rate limiting during download
3. Periodically recalculates bandwidth allocation as downloads complete

### Scheduling System

1. Scheduler task runs at specified intervals
2. Checks for downloads scheduled to start:
   a. For one-time schedules: Starts at specific date/time
   b. For recurring schedules: Checks recurrence pattern
3. Handles peak hours throttling if configured
4. Processes failed scheduled downloads for retry

## Design Patterns

XcelerateDL implements several design patterns:

### Singleton Pattern

The Download Manager is implemented as a singleton to ensure a single instance manages all downloads.

```python
# Singleton instance
download_manager = DownloadManager()
```

### Observer Pattern

WebSockets implement the observer pattern, with clients as observers of download state changes.

### Factory Pattern

Download process selection acts as a factory pattern, creating the appropriate downloader (regular or YouTube) based on URL type.

### Repository Pattern

Download state persistence implements a repository pattern for saving and loading downloads.

## Asynchronous Processing

XcelerateDL heavily utilizes asyncio for non-blocking operations:

- API endpoints are asynchronous using FastAPI
- Downloads use aiohttp for asynchronous HTTP requests
- File I/O uses aiofiles for non-blocking operations
- WebSocket communication is asynchronous

## File Storage and Persistence

Downloaded files are organized by category:

```
downloads/
├─ compressed/
├─ programs/
├─ videos/
├─ music/
├─ pictures/
├─ documents/
├─ youtube/
└─ other/
```

Download state is persisted in JSON files:
- `downloads/download_data.json`: Contains download metadata
- `downloads/bandwidth_settings.json`: Contains bandwidth settings

## Error Handling and Recovery

XcelerateDL implements robust error handling:

- Global exception handler for API requests
- Automatic retry mechanism for failed downloads
- Recovery from interruptions with download state persistence
- Detailed error logging for troubleshooting

## Scalability Considerations

While designed primarily as a desktop/single-user application, XcelerateDL's architecture allows for potential scaling:

- Stateless API design allows for horizontal scaling
- Download state could be migrated to a database for multi-user support
- Task queue integration could distribute download processing

## Future Architecture Enhancements

Potential architectural improvements include:

- Database integration for download state persistence
- Microservices architecture for scaling specific components
- Plugin system for extending functionality
- Clustering support for distributed downloading
