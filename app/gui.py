import asyncio
import contextlib
import json
import os
import signal
import subprocess
import sys
import threading
from typing import Any, List, Optional

import eel
import requests

# Initialize eel with your web files directory
# Use the parent directory of the templates folder to allow access to static files
eel.init('app', allowed_extensions=['.html', '.js', '.css'])

# API base URL - the /api prefix is already included in the router
API_BASE_URL = "http://localhost:8000/api/downloads"

# Thread to run the API server
api_thread = None
# Process for the API server
api_process = None

# Convert DownloadItem to a dictionary
def download_item_to_dict(item: dict) -> dict:
    """Convert a DownloadItem to a dictionary for JSON serialization."""
    # Calculate progress
    progress = 0
    if item.get("size") and item["size"] > 0:
        progress = min(100, (item.get("size_downloaded", 0) / item["size"]) * 100)

    # Map API fields to UI fields
    return {
        "id": item["id"],
        "url": str(item["url"]),
        "filename": item["name"],
        "save_path": item["save_path"],
        "category": item["category"],
        "status": item["status"],
        "size": item.get("size", 0),
        "downloaded": item.get("size_downloaded", 0),
        "speed": item.get("speed", 0),
        "time_left": item.get("time_left", 0),
        "date_added": item["date_added"].timestamp() if isinstance(item["date_added"], str) else item["date_added"],
        "progress": progress,
        "is_youtube": item.get("is_youtube", False),
        "youtube_type": item.get("youtube_type")
    }

@eel.expose
def add_download(download_data) -> dict:
    """Add a new download through the API."""
    if isinstance(download_data, str):
        # Legacy support for old function signature
        url = download_data
        filename = None
        save_path = None
        category = None
        is_youtube = False
        youtube_type = None
    else:
        # New structured parameter format
        url = download_data.get('url')
        filename = download_data.get('filename')
        save_path = download_data.get('save_path')
        category = download_data.get('category')
        is_youtube = download_data.get('is_youtube', False)
        youtube_type = download_data.get('youtube_type')

    payload = {
        "url": url,
        "filename": filename,
        "save_path": save_path,
        "category": category
    }

    # Add YouTube-specific parameters if needed
    if is_youtube:
        payload["is_youtube"] = True
        payload["youtube_type"] = youtube_type

    # Remove None values
    payload = {k: v for k, v in payload.items() if v is not None}

    try:
        response = requests.post(f"{API_BASE_URL}", json=payload)
        response.raise_for_status()
        result = response.json()
        print(f"Add download response: {result}")
        return format_download_for_ui(result["download"])
    except Exception as e:
        print(f"Error adding download: {e}")
        return {"error": str(e)}

@eel.expose
def get_downloads() -> dict:
    """Get all downloads from the API."""
    try:
        # Add a timeout to prevent requests from hanging
        response = requests.get(f"{API_BASE_URL}", timeout=3.0)
        response.raise_for_status()
        downloads = response.json()["downloads"]

        # Convert to the expected format (id -> download object)
        result = {}
        for download in downloads:
            try:
                result[str(download["id"])] = format_download_for_ui(download)
            except Exception as e:
                print(f"Error formatting download {download.get('id', 'unknown')}: {e}")
                # Skip this download if it can't be formatted
                continue

        return result
    except requests.Timeout:
        print("Request timeout while getting downloads")
        return {}
    except requests.ConnectionError:
        print("Connection error while getting downloads")
        return {}
    except Exception as e:
        print(f"Error getting downloads: {e}")
        return {}

@eel.expose
def pause_download(download_id: str) -> dict:
    """Pause a specific download."""
    try:
        response = requests.post(f"{API_BASE_URL}/{download_id}/pause")
        response.raise_for_status()
        result = response.json()
        print(f"Pause response: {result}")
        return format_download_for_ui(result["download"])
    except Exception as e:
        print(f"Error pausing download: {e}")
        return {"error": str(e)}

@eel.expose
def resume_download(download_id: str) -> dict:
    """Resume a specific download."""
    try:
        response = requests.post(f"{API_BASE_URL}/{download_id}/resume")
        response.raise_for_status()
        result = response.json()
        print(f"Resume response: {result}")
        return format_download_for_ui(result["download"])
    except Exception as e:
        print(f"Error resuming download: {e}")
        return {"error": str(e)}

@eel.expose
def delete_download(download_id: str, delete_file: bool = False) -> bool:
    """Delete a download."""
    try:
        response = requests.delete(
            f"{API_BASE_URL}/{download_id}",
            params={"delete_file": "true" if delete_file else "false"}
        )
        response.raise_for_status()
        return True
    except Exception as e:
        print(f"Error deleting download: {e}")
        return False

@eel.expose
def pause_all() -> bool:
    """Pause all downloads."""
    try:
        response = requests.post(f"{API_BASE_URL}/pause-all")
        response.raise_for_status()
        return True
    except Exception as e:
        print(f"Error pausing all downloads: {e}")
        return False

@eel.expose
def resume_all() -> bool:
    """Resume all downloads."""
    try:
        response = requests.post(f"{API_BASE_URL}/resume-all")
        response.raise_for_status()
        return True
    except Exception as e:
        print(f"Error resuming all downloads: {e}")
        return False

@eel.expose
def open_download(download_id: str) -> dict:
    """Open a downloaded file with default system application."""
    try:
        response = requests.post(f"{API_BASE_URL}/{download_id}/open")
        response.raise_for_status()
        result = response.json()
        print(f"Open file response: {result}")
        return result
    except Exception as e:
        print(f"Error opening file: {e}")
        return {"error": str(e), "success": False}

@eel.expose
def update_download_settings(download_id: str, priority: int = None, max_speed: int = None, max_retries: int = None) -> dict:
    """Update the settings for a specific download"""
    try:
        # Build params dict with non-None values
        params = {}
        if priority is not None:
            params['priority'] = priority
        
        if max_speed is not None:
            params['max_speed'] = max_speed
        
        if max_retries is not None:
            params['max_retries'] = max_retries
        
        # Make API call if we have parameters
        if params:
            response = requests.post(f"{API_BASE_URL}/{download_id}/settings", json=params)
            response.raise_for_status()
            result = response.json()
            print(f"Update settings response: {result}")
            return format_download_for_ui(result["download"])
        else:
            return {"error": "No settings provided"}
    except Exception as e:
        print(f"Error updating download settings: {e}")
        return {"error": str(e)}

@eel.expose
def receive_notification(notification_data: dict) -> None:
    """Forward notification to JavaScript"""
    try:
        eel.receiveNotification(notification_data)
    except Exception as e:
        print(f"Error sending notification to UI: {e}")

def format_download_for_ui(download: dict) -> dict:
    """Format the download data for the UI."""
    # Calculate progress if not already provided
    progress = download.get("progress")
    if progress is None and download.get("size") and download["size"] > 0:
        progress = min(100, (download.get("size_downloaded", 0) / download["size"]) * 100)

    # Handle datetime conversion for date_added
    date_added = download.get("date_added")
    if isinstance(date_added, str):
        try:
            from datetime import datetime
            # Try to parse ISO format first
            date_added = datetime.fromisoformat(date_added.replace('Z', '+00:00')).timestamp()
        except (ValueError, TypeError):
            # If parsing fails, use current timestamp
            date_added = datetime.now().timestamp()

    # Map API fields to UI fields
    return {
        "id": download["id"],
        "url": str(download["url"]),
        "filename": download["name"],
        "save_path": download.get("save_path", ""),
        "category": download.get("category", "other"),
        "status": download.get("status", "queued"),
        "size": download.get("size", 0),
        "downloaded": download.get("size_downloaded", 0),
        "speed": download.get("speed", 0),
        "time_left": download.get("time_left", 0),
        "date_added": date_added or 0,
        "progress": progress or 0,
        "is_youtube": download.get("is_youtube", False),
        "youtube_type": download.get("youtube_type")
    }

def run_api_server():
    """Run the FastAPI server in a separate process."""
    global api_process
    import subprocess
    import sys

    # Use the same Python executable that's running this script
    python_executable = sys.executable
    api_process = subprocess.Popen([python_executable, "-m", "app.main"])
    return api_process

def shutdown_api_server():
    """Shutdown the API server properly."""
    global api_process
    if api_process:
        print("Shutting down API server...")
        with contextlib.suppress(Exception):
            # Try to send a clean shutdown request to the API
            requests.post("http://localhost:8000/shutdown", timeout=1)

        # Make sure the process is terminated
        try:
            api_process.terminate()
            api_process.wait(timeout=3)
        except Exception:
            # Force kill if terminate doesn't work
            with contextlib.suppress(Exception):
                api_process.kill()

        api_process = None
        print("API server shutdown complete")

def start_gui():
    """Start the Eel GUI."""
    global api_thread, api_process

    # Start the API server in a separate thread
    api_thread = threading.Thread(target=run_api_server)
    api_thread.daemon = True  # This ensures the thread will exit when the main program exits
    api_thread.start()

    # Give the API server time to start
    import time
    print("Starting API server...")
    time.sleep(2)

    # Register shutdown handlers
    def cleanup_on_exit(*args, **kwargs):
        print("Shutting down GUI and API...")
        shutdown_api_server()
        sys.exit(0)

    # Register for common exit signals
    signal.signal(signal.SIGINT, cleanup_on_exit)
    signal.signal(signal.SIGTERM, cleanup_on_exit)

    # Start the application
    try:
        # Use templates/index.html as the entry point
        eel.start('templates/index.html', size=(1200, 800), port=8888, close_callback=cleanup_on_exit)
    except (SystemExit, KeyboardInterrupt):
        # Handle any cleanup here
        print("Shutting down GUI...")
        shutdown_api_server() 