# XcelerateDL Development Guide

This guide is intended for developers who want to understand, modify, or contribute to the XcelerateDL codebase.

## Development Environment Setup

### Prerequisites

- Python 3.8+ (3.13 recommended)
- Git
- ffmpeg (for YouTube download functionality)
- A code editor (VS Code, PyCharm, etc.)

### Setting Up the Development Environment

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

3. **Install in development mode**:
   ```bash
   pip install -e .
   ```

4. **Install development dependencies**:
   ```bash
   pip install pytest pytest-asyncio flake8 black isort mypy
   ```

### Running the Application in Development Mode

```bash
# Start with auto-reload for API development
python -m app.main --api-only --host 127.0.0.1 --port 8000

# Start GUI mode
python -m app.main --gui
```

## Project Structure

```
XcelerateDL/
├─ app/                      # Main application code
│  ├─ api/                   # API endpoints
│  │  └─ downloads.py        # Download-related endpoints
│  ├─ models/                # Data models
│  │  ├─ __init__.py
│  │  └─ download.py         # Download and related models
│  ├─ services/              # Business logic
│  │  ├─ downloader.py       # Download manager service
│  │  └─ ws_manager.py       # WebSocket manager
│  ├─ static/                # Static assets for web UI
│  │  ├─ css/                # CSS styles
│  │  │  └─ style.css
│  │  ├─ js/                 # JavaScript code
│  │  │  └─ script.js
│  │  └─ images/             # UI images
│  ├─ templates/             # HTML templates
│  │  └─ index.html          # Main UI template
│  ├─ gui.py                 # GUI implementation
│  ├─ main.py                # Application entry point
├─ docs/                     # Documentation
├─ downloads/                # Default download directory
├─ tests/                    # Test files
├─ pyproject.toml            # Project metadata and dependencies
├─ README.md                 # Project overview
```

## Code Walkthrough

### Main Entry Point (`app/main.py`)

The application entry point handles:
- Command-line argument parsing
- FastAPI application setup
- WebSocket endpoint setup
- Server startup and shutdown events
- Global exception handling

### API Layer (`app/api/downloads.py`)

The API layer defines endpoints for:
- Creating, retrieving, updating, and deleting downloads
- Managing download state (pause, resume)
- Searching for downloads
- Configuring bandwidth and scheduler settings

### Download Manager (`app/services/downloader.py`)

The core component responsible for:
- Download queue management
- File downloading using aiohttp
- YouTube downloads using yt-dlp
- Bandwidth allocation
- Schedule processing
- Persistent state management

### WebSocket Manager (`app/services/ws_manager.py`)

Handles real-time communication:
- Client connection management
- Broadcasting download updates
- Connection and disconnection events

### Data Models (`app/models/download.py`)

Defines Pydantic models for:
- Download items
- Bandwidth settings
- Schedule settings
- API request/response schemas

### Web UI (`app/templates/index.html`, `app/static/js/script.js`)

The web interface provides:
- Download management UI
- WebSocket communication for real-time updates
- Responsive design for different screen sizes

### GUI Integration (`app/gui.py`)

Integrates the web UI into a desktop application using Eel.

## Development Workflow

### Adding a New Feature

1. **Create a feature branch**:
   ```bash
   git checkout -b feature/your-feature-name
   ```

2. **Implement the feature**:
   - Update models if needed
   - Add/modify service logic
   - Create/update API endpoints
   - Update UI components

3. **Test your changes**:
   - Write unit tests for new functionality
   - Run existing tests to ensure nothing breaks
   - Manually test the feature

4. **Format and lint your code**:
   ```bash
   black app/
   isort app/
   flake8 app/
   mypy app/
   ```

5. **Commit your changes**:
   ```bash
   git add .
   git commit -m "Add feature: your feature description"
   ```

6. **Create a pull request**:
   - Push your branch to GitHub
   - Create a pull request with a clear description

### Modifying the Download Engine

The download engine is the most complex part of XcelerateDL. When modifying it:

1. **Understand the current flow**:
   - Review the `_download_file` and `_download_youtube` methods
   - Understand how pause/resume/cancel work
   - Review bandwidth management logic

2. **Make incremental changes**:
   - Test each change thoroughly
   - Handle edge cases (network errors, corrupt files, etc.)

3. **Consider thread safety**:
   - Downloads run asynchronously
   - Shared state must be protected

4. **Update WebSocket notifications**:
   - Ensure progress updates are broadcasted correctly

### Extending the API

When adding new API endpoints:

1. **Define Pydantic models** for request/response bodies
2. **Add the endpoint** to the appropriate router
3. **Implement input validation**
4. **Add detailed documentation** using FastAPI's docstring format
5. **Update API reference documentation**

Example:
```python
@router.post("/new-endpoint", response_model=MyResponseModel)
async def my_new_endpoint(request: MyRequestModel):
    """
    Description of what this endpoint does.
    
    Additional details about parameters, behavior, etc.
    """
    # Implementation
    return {"result": "success"}
```

## Testing

### Unit Tests

Write unit tests for individual components:

```python
# Example test for a downloader method
import pytest
from app.services.downloader import download_manager

@pytest.mark.asyncio
async def test_get_file_info():
    # Setup
    url = "https://example.com/test.zip"
    
    # Execute
    filename, size = await download_manager.get_file_info(url)
    
    # Assert
    assert filename == "test.zip"
    assert isinstance(size, int)
```

### Integration Tests

Test interactions between components:

```python
@pytest.mark.asyncio
async def test_add_and_pause_download():
    # Setup
    request = CreateDownloadRequest(url="https://example.com/test.zip")
    
    # Execute
    download = await download_manager.add_download(request)
    await download_manager.pause_download(download.id)
    
    # Assert
    assert download_manager.get_download(download.id).status == DownloadStatus.PAUSED
```

### Running Tests

```bash
# Run all tests
pytest

# Run specific test file
pytest tests/test_downloader.py

# Run tests with coverage report
pytest --cov=app tests/
```

## Debugging Tips

### Common Issues

1. **WebSocket Disconnections**:
   - Check connection timeout settings
   - Verify the client reconnection logic

2. **Download Stalling**:
   - Enable debug logging to track progress
   - Check rate limiter functionality
   - Verify bandwidth allocation

3. **YouTube Downloads Failing**:
   - Check yt-dlp version compatibility
   - Verify ffmpeg installation
   - Look for YouTube API changes

### Logging

Enable debug logging for more detailed information:

```python
import logging
logging.basicConfig(level=logging.DEBUG)
```

### Debugging Asynchronous Code

Use these techniques for debugging async code:

1. **Add print statements with timestamps**:
   ```python
   print(f"{time.time()}: Starting download")
   ```

2. **Use asyncio debug mode**:
   ```python
   import asyncio
   asyncio.get_event_loop().set_debug(True)
   ```

3. **Monitor task status**:
   ```python
   task = asyncio.create_task(some_function())
   # Later
   print(f"Task done: {task.done()}, cancelled: {task.cancelled()}")
   ```

## Performance Optimization

### Bandwidth Optimization

Tips for optimizing bandwidth usage:

1. **Chunk size tuning**:
   - Default is 8192 bytes
   - Larger chunks reduce overhead but increase memory usage
   - Smaller chunks provide more granular progress updates

2. **Connection pooling**:
   - Reuse aiohttp sessions
   - Set appropriate connection limits

3. **Rate limiting**:
   - Balance between accurate limiting and overhead
   - Consider adaptive rate limiting based on network conditions

### Memory Management

Tips for managing memory usage:

1. **Stream processing for large files**:
   - Process downloads in chunks
   - Avoid loading entire files into memory

2. **Clean up resources**:
   - Close file handles immediately after use
   - Cancel and cleanup tasks when done

3. **Monitor memory usage**:
   - Add logging of memory consumption
   - Consider adding memory limits for processes

## Contribution Guidelines

1. **Follow coding standards**:
   - PEP 8 for Python code
   - Use type hints wherever possible
   - Document functions and classes with docstrings

2. **Write tests**:
   - Add tests for new functionality
   - Maintain or improve test coverage

3. **Update documentation**:
   - Update API reference for new endpoints
   - Document complex algorithms or patterns
   - Keep README and user guide updated

4. **Create clear pull requests**:
   - Reference issues being addressed
   - Provide clear descriptions of changes
   - Include any necessary migration steps

## Release Process

1. **Update version number** in `pyproject.toml`
2. **Update changelog** with notable changes
3. **Run full test suite**
4. **Create a release tag** in git
5. **Build and publish** package if applicable

## Additional Resources

- [FastAPI Documentation](https://fastapi.tiangolo.com/)
- [asyncio Documentation](https://docs.python.org/3/library/asyncio.html)
- [Pydantic Documentation](https://pydantic-docs.helpmanual.io/)
- [yt-dlp Documentation](https://github.com/yt-dlp/yt-dlp#readme)
- [Eel Documentation](https://github.com/ChrisKnott/Eel)
