# XcelerateDL API Reference

This document provides detailed information about XcelerateDL's REST API endpoints, WebSocket interface, and data models.

## API Base URL

When running locally, the base URL is:
```
http://localhost:8000/api
```

## Authentication

XcelerateDL API currently doesn't require authentication. However, CORS is enabled with the following configuration:
- Allow all origins (`*`)
- Allow credentials
- Allow all methods
- Allow all headers

## REST API Endpoints

### Download Management

#### Get All Downloads

```
GET /api/downloads
```

Retrieves a list of all downloads, with optional filtering.

Query Parameters:
- `category` (optional): Filter by file category
- `status` (optional): Filter by status, can be a comma-separated list

Response Example:
```json
{
  "downloads": [
    {
      "id": "d1a2b3c4",
      "name": "example.zip",
      "url": "https://example.com/file.zip",
      "size": 1048576,
      "size_downloaded": 524288,
      "status": "downloading",
      "speed": 102400,
      "time_left": 5,
      "date_added": "2023-05-01T10:30:00",
      "save_path": "/path/to/downloads/compressed/example.zip",
      "category": "compressed",
      "priority": 2,
      "progress": 50
    }
  ],
  "total": 1
}
```

#### Add New Download

```
POST /api/downloads
```

Adds a new download to the queue.

Request Body:
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

Response Example:
```json
{
  "download": {
    "id": "d1a2b3c4",
    "name": "example.zip",
    // Other download properties as in GET response
  }
}
```

#### Search Downloads

```
POST /api/downloads/search
```

Searches for downloads with advanced criteria.

Request Body:
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

Response Example:
```json
{
  "downloads": [
    {
      "id": "d1a2b3c4",
      "name": "ubuntu-22.04.iso",
      // Other download properties
    }
  ],
  "total": 1
}
```

#### Get Download Details

```
GET /api/downloads/{download_id}
```

Retrieves details for a specific download.

Response Example:
```json
{
  "download": {
    "id": "d1a2b3c4",
    "name": "example.zip",
    // Other download properties
  }
}
```

#### Pause Download

```
POST /api/downloads/{download_id}/pause
```

Pauses an active download.

Response Example:
```json
{
  "download": {
    "id": "d1a2b3c4",
    "status": "paused",
    // Other download properties
  }
}
```

#### Resume Download

```
POST /api/downloads/{download_id}/resume
```

Resumes a paused download.

Response Example:
```json
{
  "download": {
    "id": "d1a2b3c4",
    "status": "downloading",
    // Other download properties
  }
}
```

#### Delete Download

```
DELETE /api/downloads/{download_id}
```

Deletes a download, with option to delete the downloaded file as well.

Query Parameters:
- `delete_file` (optional, boolean): Whether to delete the file itself

Response Example:
```json
{
  "detail": "Download deleted successfully"
}
```

#### Pause All Downloads

```
POST /api/downloads/pause-all
```

Pauses all active downloads.

Response Example:
```json
{
  "detail": "All downloads paused",
  "count": 3
}
```

#### Resume All Downloads

```
POST /api/downloads/resume-all
```

Resumes all paused downloads.

Response Example:
```json
{
  "detail": "All downloads resumed",
  "count": 3
}
```

#### Open Downloaded File

```
POST /api/downloads/{download_id}/open
```

Opens the downloaded file with the default system application.

Response Example:
```json
{
  "detail": "File opened successfully"
}
```

#### Update Download Settings

```
POST /api/downloads/{download_id}/settings
```

Updates settings for a specific download.

Request Body:
```json
{
  "priority": 3,  // Optional: 1=low, 2=normal, 3=high
  "max_speed": 2097152,  // Optional: Limit speed in bytes/sec
  "max_retries": 5,  // Optional: Max retry attempts
  "bandwidth_allocation": 30,  // Optional: Percentage of bandwidth
  "tags": ["important", "work"]  // Optional: Tags for the download
}
```

Response Example:
```json
{
  "download": {
    "id": "d1a2b3c4",
    "priority": 3,
    // Other download properties with updated values
  }
}
```

#### Schedule Download

```
POST /api/downloads/{download_id}/schedule
```

Schedules a download to start at a specific time.

Request Body:
```json
{
  "scheduled_time": "2023-12-31T23:00:00",
  "recurrence": "weekly",  // "daily", "weekly", "monthly", or null
  "days_of_week": [0, 3],  // 0=Monday, 6=Sunday (for weekly)
  "day_of_month": 15,  // 1-31 (for monthly)
  "bandwidth_allocation": 30,  // Percentage of bandwidth to use
  "notify_on_start": true,  // Send notification when download starts
  "priority_boost": false,  // Boost priority when scheduled download starts
  "pause_on_peak_hours": true,  // Pause during peak hours
  "peak_hours_start": 18,  // Peak hours start (6 PM)
  "peak_hours_end": 23  // Peak hours end (11 PM)
}
```

Response Example:
```json
{
  "download": {
    "id": "d1a2b3c4",
    "status": "scheduled",
    // Other download properties
  }
}
```

### Bandwidth Management

#### Get Bandwidth Settings

```
GET /api/downloads/bandwidth/settings
```

Retrieves current bandwidth settings.

Response Example:
```json
{
  "total_bandwidth": 10485760,  // 10 MB/s
  "allocation_mode": "priority",
  "custom_allocations": {
    "d1a2b3c4": 60,
    "e5f6g7h8": 20
  },
  "max_concurrent_downloads": 5,
  "enable_scheduling": true,
  "peak_hours_throttling": false,
  "peak_hours_start": 18,
  "peak_hours_end": 23,
  "peak_hours_limit": 5242880,  // 5 MB/s
  "scheduler_check_interval": 60
}
```

#### Update Bandwidth Settings

```
POST /api/downloads/bandwidth/settings
```

Updates global bandwidth settings.

Request Body:
```json
{
  "total_bandwidth": 10485760,  // 10 MB/s
  "allocation_mode": "priority",  // "equal", "priority", or "custom"
  "custom_allocations": {  // Only used in custom mode
    "d1a2b3c4": 60,  // Percentage allocation
    "e5f6g7h8": 20
  },
  "max_concurrent_downloads": 5,
  "enable_scheduling": true,
  "peak_hours_throttling": false,
  "peak_hours_start": 18,
  "peak_hours_end": 23,
  "peak_hours_limit": 5242880,  // 5 MB/s
  "scheduler_check_interval": 60
}
```

Response Example:
```json
{
  "detail": "Bandwidth settings updated"
}
```

#### Update Scheduler Settings

```
POST /api/downloads/scheduler/settings
```

Updates the scheduler check interval.

Request Body:
```json
{
  "check_interval_seconds": 30
}
```

Response Example:
```json
{
  "detail": "Scheduler check interval updated to 30 seconds"
}
```

### Advanced Scheduling

#### Batch Schedule Downloads

```
POST /api/downloads/batch-schedule
```

Schedules multiple downloads at once with the same settings.

Request Body:
```json
{
  "download_ids": ["d1a2b3c4", "e5f6g7h8"],
  "schedule": {
    "scheduled_time": "2023-12-31T23:00:00",
    "recurrence": "daily"
  },
  "priority_boost": false
}
```

Response Example:
```json
{
  "detail": "Downloads scheduled successfully",
  "scheduled": [
    {
      "id": "d1a2b3c4",
      "status": "scheduled"
    },
    {
      "id": "e5f6g7h8",
      "status": "scheduled"
    }
  ]
}
```

#### Smart Schedule Downloads

```
POST /api/downloads/smart-schedule
```

Creates a smart schedule that spaces downloads out over time.

Request Body:
```json
{
  "base_time": "2023-12-31T23:00:00",
  "target_completion_time": "2024-01-01T06:00:00",
  "spacing_minutes": 5,
  "download_ids": ["d1a2b3c4", "e5f6g7h8"],
  "status_filter": ["queued", "paused"]
}
```

Response Example:
```json
{
  "success": true,
  "message": "Scheduled 2 downloads based on bandwidth settings",
  "scheduled": [
    {
      "id": "d1a2b3c4",
      "scheduled_time": "2023-12-31T23:00:00"
    },
    {
      "id": "e5f6g7h8",
      "scheduled_time": "2023-12-31T23:05:00"
    }
  ],
  "failed": []
}
```

## WebSocket API

XcelerateDL uses WebSockets to provide real-time updates about download progress.

### Connection

Connect to the WebSocket endpoint:
```
ws://localhost:8000/ws
```

### Message Format

Messages sent by the server are JSON objects with the following structure:

```json
{
  "type": "update",  // Type of message: "update", "add", "delete", "status"
  "download": {
    "id": "d1a2b3c4",
    "name": "example.zip",
    "url": "https://example.com/file.zip",
    "size": 1048576,
    "size_downloaded": 524288,
    "status": "downloading",
    "speed": 102400,
    "time_left": 5,
    "date_added": "2023-05-01T10:30:00",
    "save_path": "/path/to/downloads/compressed/example.zip",
    "category": "compressed",
    "priority": 2,
    "progress": 50
  }
}
```

### Message Types

- `update`: A download has been updated (progress, status, etc.)
- `add`: A new download has been added
- `delete`: A download has been deleted
- `status`: A download's status has changed

### Client Messages

The WebSocket server also accepts simple messages from clients:

- `ping`: Server will respond with `pong` to keep the connection alive

## Data Models

### Download Status

```
queued      : Download is waiting in queue
downloading : Download is in progress
paused      : Download has been paused manually
completed   : Download has finished successfully
failed      : Download encountered an error
scheduled   : Download is scheduled for future start
```

### File Categories

```
compressed  : ZIP, RAR, 7Z, etc.
programs    : EXE, MSI, DEB, etc.
videos      : MP4, MKV, AVI, etc.
music       : MP3, FLAC, WAV, etc.
pictures    : JPG, PNG, GIF, etc.
documents   : PDF, DOCX, XLSX, etc.
youtube     : YouTube videos or extracted audio
other       : Files not matching other categories
```

### Priority Levels

```
1 : Low priority
2 : Normal priority (default)
3 : High priority
```

### Bandwidth Allocation Modes

```
equal    : Each download gets equal share of available bandwidth
priority : Higher priority downloads get more bandwidth
custom   : Custom percentage allocations for individual downloads
```

### Recurrence Types

```
daily   : Repeats every day at the scheduled time
weekly  : Repeats on specified days of week at the scheduled time
monthly : Repeats on a specified day of month at the scheduled time
```

## Error Handling

The API uses standard HTTP status codes to indicate the success or failure of a request.

Common status codes:
- `200 OK`: Request succeeded
- `201 Created`: Resource created successfully
- `400 Bad Request`: Invalid request parameters
- `404 Not Found`: Resource not found
- `500 Internal Server Error`: Server-side error

Error responses include a JSON object with details:
```json
{
  "detail": "An error message describing the problem",
  "error_type": "ErrorTypeName",
  "message": "Technical details about the error"
}
```
