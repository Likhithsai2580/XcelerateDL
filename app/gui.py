import contextlib
import signal
import subprocess
import sys
import threading

import eel
import requests

# Initialize eel with your web files directory
# Use the parent directory of the templates folder to allow access to static files
eel.init("app", allowed_extensions=[".html", ".js", ".css"])

# API base URL - the /api prefix is already included in the router
API_BASE_URL = "http://localhost:8000/api/downloads"

# Default request timeout value (in seconds)
DEFAULT_REQUEST_TIMEOUT = 15.0

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
        "date_added": item["date_added"].timestamp()
        if isinstance(item["date_added"], str)
        else item["date_added"],
        "progress": progress,
        "is_youtube": item.get("is_youtube", False),
        "youtube_type": item.get("youtube_type"),
    }


@eel.expose
def add_download(download_data) -> dict:
    """Add a new download through the API."""
    try:
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
            url = download_data.get("url")
            filename = download_data.get("filename")
            save_path = download_data.get("save_path")
            category = download_data.get("category")
            is_youtube = download_data.get("is_youtube", False)
            youtube_type = download_data.get("youtube_type")

        payload = {"url": url, "filename": filename, "save_path": save_path, "category": category}

        # Add YouTube-specific parameters if needed
        if is_youtube:
            payload["is_youtube"] = True
            payload["youtube_type"] = youtube_type

        # Add scheduling parameters if provided
        schedule = download_data.get("schedule")
        if schedule:
            payload["schedule"] = schedule

        # Add other parameters if available
        if "priority" in download_data:
            payload["priority"] = download_data["priority"]
        if "max_speed" in download_data:
            payload["max_speed"] = download_data["max_speed"]
        if "max_retries" in download_data:
            payload["max_retries"] = download_data["max_retries"]

        # Remove None values
        payload = {k: v for k, v in payload.items() if v is not None}

        print(f"Sending download request with payload: {payload}")

        # Use a longer timeout for YouTube downloads
        timeout = DEFAULT_REQUEST_TIMEOUT * 2 if is_youtube else DEFAULT_REQUEST_TIMEOUT
        response = requests.post(f"{API_BASE_URL}", json=payload, timeout=timeout)

        response.raise_for_status()
        result = response.json()
        print(f"Add download response: {result}")

        if "error" in result:
            return {"error": result["error"]}

        return format_download_for_ui(result["download"])
    except requests.exceptions.Timeout:
        print("Request timed out while adding download")
        return {
            "error": "Request timed out. The server might be busy processing the download request. Check the downloads tab in a few moments to see if it was added successfully."
        }
    except requests.exceptions.RequestException as e:
        print(f"Request error adding download: {e}")
        return {"error": f"Network error: {str(e)}"}
    except Exception as e:
        print(f"Error adding download: {e}")
        return {"error": str(e)}


@eel.expose
def get_downloads() -> dict:
    """Get all downloads from the API."""
    try:
        # Add a timeout to prevent requests from hanging
        response = requests.get(f"{API_BASE_URL}", timeout=DEFAULT_REQUEST_TIMEOUT)
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
        # Return an empty dict instead of failing
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
        response = requests.post(
            f"{API_BASE_URL}/{download_id}/pause", timeout=DEFAULT_REQUEST_TIMEOUT
        )
        response.raise_for_status()
        result = response.json()
        print(f"Pause response: {result}")
        return format_download_for_ui(result["download"])
    except requests.Timeout:
        print(f"Request timeout while pausing download {download_id}")
        return {"error": "Request timed out. The download may still be paused. Please refresh."}
    except Exception as e:
        print(f"Error pausing download: {e}")
        return {"error": str(e)}


@eel.expose
def resume_download(download_id: str) -> dict:
    """Resume a specific download."""
    try:
        response = requests.post(
            f"{API_BASE_URL}/{download_id}/resume", timeout=DEFAULT_REQUEST_TIMEOUT
        )
        response.raise_for_status()
        result = response.json()
        print(f"Resume response: {result}")
        return format_download_for_ui(result["download"])
    except requests.Timeout:
        print(f"Request timeout while resuming download {download_id}")
        return {"error": "Request timed out. The download may still be resumed. Please refresh."}
    except Exception as e:
        print(f"Error resuming download: {e}")
        return {"error": str(e)}


@eel.expose
def delete_download(download_id: str, delete_file: bool = False) -> bool:
    """Delete a download."""
    try:
        response = requests.delete(
            f"{API_BASE_URL}/{download_id}",
            params={"delete_file": "true" if delete_file else "false"},
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
def update_download_settings(
    download_id: str, priority: int = None, max_speed: int = None, max_retries: int = None
) -> dict:
    """Update the settings for a specific download"""
    try:
        # Build params dict with non-None values
        params = {}
        if priority is not None:
            params["priority"] = priority

        if max_speed is not None:
            params["max_speed"] = max_speed

        if max_retries is not None:
            params["max_retries"] = max_retries

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
def schedule_download(download_id: str, schedule_data: dict) -> dict:
    """Schedule a download with the given parameters"""
    try:
        # Format schedule data for API
        schedule = {
            "scheduled_time": schedule_data.get("scheduled_time"),
            "recurrence": schedule_data.get("recurrence"),
            "days_of_week": schedule_data.get("days_of_week"),
            "bandwidth_allocation": schedule_data.get("bandwidth_allocation"),
        }

        # Remove None values
        schedule = {k: v for k, v in schedule.items() if v is not None}

        print(f"Scheduling download {download_id} with parameters: {schedule}")

        # Make API call
        response = requests.post(
            f"{API_BASE_URL}/{download_id}/schedule", json=schedule, timeout=10
        )
        response.raise_for_status()
        result = response.json()
        print(f"Schedule download response: {result}")

        if "error" in result:
            return {"error": result["error"]}

        return format_download_for_ui(result["download"])
    except requests.exceptions.Timeout:
        print(f"Request timed out while scheduling download {download_id}")
        return {"error": "Request timed out. The server might be busy."}
    except requests.exceptions.RequestException as e:
        print(f"Request error scheduling download: {e}")
        return {"error": f"Network error: {str(e)}"}
    except Exception as e:
        print(f"Error scheduling download: {e}")
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
            date_added = datetime.fromisoformat(date_added.replace("Z", "+00:00")).timestamp()
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
        "youtube_type": download.get("youtube_type"),
    }


def run_api_server():
    """Run the FastAPI server in a separate process."""
    global api_process
    import sys

    # Use the same Python executable that's running this script
    python_executable = sys.executable

    # Pass worker configurations to make the API more reliable
    api_process = subprocess.Popen(
        [
            python_executable,
            "-m",
            "uvicorn",
            "app.main:app",
            "--host",
            "localhost",
            "--port",
            "8000",
            "--workers",
            "2",  # Use multiple workers for better concurrency
        ]
    )

    return api_process


def shutdown_api_server():
    """Shutdown the API server properly."""
    global api_process
    if api_process:
        print("Shutting down API server...")
        with contextlib.suppress(Exception):
            # Try to send a clean shutdown request to the API
            requests.post("http://localhost:8000/shutdown", timeout=2)  # Increased timeout

        # Make sure the process is terminated
        try:
            api_process.terminate()
            api_process.wait(timeout=5)  # Increased timeout
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
    time.sleep(5)  # Increased wait time for API server to start

    # Check if the API server is responding
    api_ready = False
    retry_count = 0

    while not api_ready and retry_count < 5:
        try:
            response = requests.get("http://localhost:8000/api", timeout=1)
            if response.status_code == 200:
                api_ready = True
                print("API server is ready")
            else:
                retry_count += 1
                time.sleep(1)
        except:
            retry_count += 1
            time.sleep(1)

    if not api_ready:
        print("Warning: API server may not be fully ready yet")

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
        eel.start(
            "templates/index.html", size=(1200, 800), port=8888, close_callback=cleanup_on_exit
        )
    except (SystemExit, KeyboardInterrupt):
        # Handle any cleanup here
        print("Shutting down GUI...")
        shutdown_api_server()
