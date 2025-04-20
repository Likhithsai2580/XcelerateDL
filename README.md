# XcelerateDL - Fast Download Manager API

XcelerateDL is a modern download manager API built with FastAPI and Python. It provides a robust backend for managing downloads with features similar to Internet Download Manager (IDM).

## Features

- **Add and Manage Downloads**: Easily add new downloads and manage them through a REST API
- **File Categorization**: Automatic categorization of files (videos, music, documents, etc.)
- **Download Control**: Pause, resume, or cancel downloads
- **Download Queues**: Control download queues (pause all, resume all)
- **Progress Tracking**: Track download progress, speed, and estimated time left
- **Asynchronous Downloads**: Efficiently download files in the background using asynchronous I/O

## Installation

1. Clone the repository:
```bash
git clone https://github.com/yourusername/XcelerateDL.git
cd XcelerateDL
```

2. Install dependencies:
```bash
pip install -r requirements.txt
```

## Usage

1. Start the API server:
```bash
python -m app.main
```

2. The API will be available at `http://localhost:8000`
3. API documentation is available at `http://localhost:8000/docs`

## API Endpoints

### Add a New Download
```
POST /api/downloads
```
Request body:
```json
{
  "url": "https://example.com/file.zip",
  "filename": "myfile.zip",  // optional
  "save_path": "/path/to/save",  // optional
  "category": "compressed"  // optional
}
```

### List All Downloads
```
GET /api/downloads
```

### Get Download Details
```
GET /api/downloads/{download_id}
```

### Pause a Download
```
POST /api/downloads/{download_id}/pause
```

### Resume a Download
```
POST /api/downloads/{download_id}/resume
```

### Delete a Download
```
DELETE /api/downloads/{download_id}?delete_file=false
```

### Pause All Downloads
```
POST /api/downloads/pause-all
```

### Resume All Downloads
```
POST /api/downloads/resume-all
```

## Download Categories

- `all`: All downloads
- `compressed`: Compressed files (.zip, .rar, etc.)
- `programs`: Executable programs (.exe, .msi, etc.)
- `videos`: Video files (.mp4, .avi, etc.)
- `music`: Audio files (.mp3, .wav, etc.)
- `pictures`: Image files (.jpg, .png, etc.)
- `documents`: Document files (.pdf, .doc, etc.)
- `other`: Other file types

## Download Status

- `queued`: Download is queued but not started
- `downloading`: Download is in progress
- `paused`: Download is paused
- `completed`: Download has completed successfully
- `failed`: Download has failed

## License

MIT

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.
