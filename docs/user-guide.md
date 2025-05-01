# XcelerateDL User Guide

This guide provides detailed information on how to use XcelerateDL's features.

## Basic Usage

### Adding Downloads

To add a new download:

1. **Via Web UI**:
   - Navigate to the main page
   - Enter a URL in the input field
   - Click "Add Download"
   - Optionally configure download settings in the dialog that appears

2. **Via GUI**:
   - Enter a URL in the input field
   - Click "Add Download" button
   - Configure download settings in the popup dialog

3. **Via API**:
   ```bash
   curl -X POST http://localhost:8000/api/downloads \
     -H "Content-Type: application/json" \
     -d '{"url": "https://example.com/file.zip"}'
   ```

### Managing Downloads

#### Viewing Downloads

All active, queued, paused, and completed downloads appear in the download list, organized by their status.

#### Download Controls

For each download, you can:

- **Pause**: Temporarily stop a download while retaining progress
- **Resume**: Continue a paused download from where it left off
- **Cancel**: Stop a download completely
- **Delete**: Remove a download from the list (with option to delete the file)
- **Open**: Open the downloaded file with the default application
- **Show in Folder**: Open the folder containing the downloaded file

#### Bulk Actions

You can also perform bulk actions:

- **Pause All**: Pause all active downloads
- **Resume All**: Resume all paused downloads
- **Clear Completed**: Remove completed downloads from the list

## YouTube Downloads

XcelerateDL provides specialized support for YouTube downloads.

### Adding a YouTube Download

1. Paste a YouTube URL into the download input field
2. Select whether you want to download the video or just extract audio
3. Choose quality settings (if available)

### YouTube Options

- **Video Download**: Download the video in its original format
- **Audio Extraction**: Extract audio as MP3
- **Quality Selection**: Choose from available quality options
- **Format Selection**: Select from available formats

### API Example

```bash
curl -X POST http://localhost:8000/api/downloads \
  -H "Content-Type: application/json" \
  -d '{
    "url": "https://youtube.com/watch?v=VIDEO_ID",
    "is_youtube": true,
    "youtube_type": "video"
  }'
```

## Bandwidth Management

XcelerateDL offers sophisticated bandwidth management features.

### Bandwidth Settings

You can configure:

- **Total Bandwidth**: Maximum bandwidth to use for all downloads
- **Allocation Mode**: How to distribute bandwidth among downloads
- **Concurrent Downloads**: Maximum number of downloads to process simultaneously
- **Peak Hours Settings**: Throttle downloads during specific hours

### Allocation Modes

- **Equal**: Each download gets the same share of available bandwidth
- **Priority**: Bandwidth allocated according to priority (Low, Normal, High)
- **Custom**: Manually assign percentage of bandwidth to specific downloads

### Priority Levels

Each download can be assigned a priority:

- **Low (1)**: Gets lower bandwidth share when using Priority allocation mode
- **Normal (2)**: Default priority for downloads
- **High (3)**: Gets higher bandwidth share when using Priority allocation mode

### Configuring Bandwidth

To configure bandwidth settings:

1. Go to Settings → Bandwidth
2. Adjust the total bandwidth value (in bytes/second)
3. Select allocation mode
4. Configure other settings as needed

## Download Scheduling

XcelerateDL allows you to schedule downloads for specific times.

### Scheduling Options

- **One-time Schedule**: Download starts at a specific date and time
- **Recurring Schedule**: Download occurs on a recurring basis
  - Daily: Runs every day at the specified time
  - Weekly: Runs on specific days of the week
  - Monthly: Runs on a specific day of the month

### Creating a Schedule

To schedule a download:

1. Add a new download or select an existing one
2. Click "Schedule"
3. Set the desired time, recurrence, and other options
4. Click "Save Schedule"

### Smart Scheduling

XcelerateDL offers smart scheduling to distribute downloads across time periods:

1. Select multiple downloads
2. Click "Smart Schedule"
3. Choose the time window and parameters
4. XcelerateDL will intelligently space out downloads

### API Example

```bash
curl -X POST http://localhost:8000/api/downloads \
  -H "Content-Type: application/json" \
  -d '{
    "url": "https://example.com/large-file.zip",
    "schedule": {
      "scheduled_time": "2023-12-31T23:00:00",
      "recurrence": "weekly",
      "days_of_week": [0, 3]
    }
  }'
```

## File Organization

XcelerateDL automatically organizes downloads by file type.

### File Categories

Downloads are organized into these categories:

- **Compressed**: ZIP, RAR, 7Z, etc.
- **Programs**: EXE, MSI, DEB, etc.
- **Videos**: MP4, MKV, AVI, etc.
- **Music**: MP3, FLAC, WAV, etc.
- **Pictures**: JPG, PNG, GIF, etc.
- **Documents**: PDF, DOCX, XLSX, etc.
- **YouTube**: YouTube videos or extracted audio
- **Other**: Files not matching other categories

### Custom Save Locations

You can specify a custom save location for any download:

1. When adding a download, click "Advanced Options"
2. Specify a custom save path
3. Optionally set this as the default for this file type

## Search and Filtering

XcelerateDL provides powerful search capabilities.

### Basic Search

Use the search box to filter downloads by:
- Filename
- URL
- Tags

### Advanced Search

For more complex queries, use the advanced search:

1. Click "Advanced Search"
2. Set filters for:
   - Text query
   - Category
   - Status
   - Date range
   - File size range
   - Tags

### Tags

You can add tags to downloads for better organization:

1. Select a download
2. Click "Edit Tags"
3. Add or remove tags
4. Click "Save"

### API Example

```bash
curl -X POST http://localhost:8000/api/downloads/search \
  -H "Content-Type: application/json" \
  -d '{
    "query": "ubuntu",
    "category": "compressed",
    "status": ["completed", "failed"],
    "date_from": "2023-01-01T00:00:00",
    "date_to": "2023-12-31T23:59:59",
    "min_size": 1048576,
    "max_size": 1073741824,
    "tags": ["linux", "iso"]
  }'
```

## Configuration Options

XcelerateDL can be configured through the Settings menu.

### General Settings

- Default download folder
- User interface theme
- Notification settings
- Auto-start behavior

### Advanced Settings

- Connection settings
- Proxy configuration
- Network timeout values
- Logger settings

## Keyboard Shortcuts

XcelerateDL provides keyboard shortcuts for common operations:

- **Ctrl+N**: Add new download
- **Ctrl+P**: Pause selected download
- **Ctrl+R**: Resume selected download
- **Ctrl+D**: Delete selected download
- **Ctrl+F**: Search downloads
- **Ctrl+S**: Open settings
- **F5**: Refresh download list
- **F1**: Open help
