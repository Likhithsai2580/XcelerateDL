import contextlib
import signal
import subprocess
import sys
import threading
import time

import eel
import requests

# Initialize eel with your web files directory
# Use the parent directory of the templates folder to allow access to static files
eel.init("app", allowed_extensions=[".html", ".js", ".css"])

# API base URL - the /api prefix is already included in the router
API_BASE_URL = "http://localhost:8000/api/downloads"

# Default request timeout value (in seconds)
DEFAULT_REQUEST_TIMEOUT = 30.0  # Increase default timeout from 15 to 30 seconds
YOUTUBE_REQUEST_TIMEOUT = 60.0  # Set even longer timeout for YouTube

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

        if not url:
            return {"error": "URL is required"}

        # Import URL normalization function for consistency
        from app.services.downloader import normalize_url

        # Check for existing downloads with the same URL
        normalized_url = normalize_url(url)
        existing_downloads = get_downloads()
        
        if existing_downloads and not isinstance(existing_downloads, dict):
            # Handle unexpected response format
            print(f"Unexpected response from get_downloads: {existing_downloads}")
        elif existing_downloads and "error" not in existing_downloads:
            for download_id, download in existing_downloads.items():
                download_url = download.get("url", "")
                if normalize_url(download_url) == normalized_url:
                    # Found a potential duplicate
                    if download.get("status") in ["downloading", "queued", "completed", "paused", "scheduled"]:
                        print(f"GUI: Download already exists for URL: {normalized_url}, ID: {download_id}")
                        return download  # Return the existing download instead

        payload = {"url": url, "filename": filename, "save_path": save_path, "category": category}

        # Add YouTube-specific parameters if needed
        if is_youtube:
            payload["is_youtube"] = True
            payload["youtube_type"] = youtube_type
            print(f"Adding YouTube download: {url}")

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
        timeout = YOUTUBE_REQUEST_TIMEOUT if is_youtube else DEFAULT_REQUEST_TIMEOUT
        
        # For YouTube, use retry logic with backoff
        if is_youtube:
            from requests.adapters import HTTPAdapter
            from urllib3.util.retry import Retry
            
            retry_strategy = Retry(
                total=3,
                backoff_factor=1,
                status_forcelist=[429, 500, 502, 503, 504],
                allowed_methods=["POST"]
            )
            
            adapter = HTTPAdapter(max_retries=retry_strategy)
            session = requests.Session()
            session.mount("http://", adapter)
            session.mount("https://", adapter)
            
            print(f"Using retry strategy for YouTube download with timeout={timeout}s")
            response = session.post(f"{API_BASE_URL}", json=payload, timeout=timeout)
        else:
            # Standard request for non-YouTube
            response = requests.post(f"{API_BASE_URL}", json=payload, timeout=timeout)

        response.raise_for_status()
        result = response.json()
        print(f"Add download response: {result}")

        if "error" in result:
            return {"error": result["error"]}

        # For YouTube downloads, inform the user that processing might continue
        if is_youtube:
            # Enhance the download info with extra indicators
            download_info = format_download_for_ui(result["download"])
            download_info["youtube_processing"] = True
            download_info["message"] = "YouTube download started. Initial metadata extraction might take some time."
            return download_info
        else:
            return format_download_for_ui(result["download"])
    except requests.exceptions.Timeout:
        print(f"Request timed out while adding download (is_youtube={is_youtube})")
        if is_youtube:
            return {
                "error": "YouTube processing timed out. The download might still be processing in the background. Check the downloads tab in a few moments to see if it was added successfully."
            }
        else:
            return {
                "error": "Request timed out. The server might be busy processing the download request. Check the downloads tab in a few moments to see if it was added successfully."
            }
    except requests.exceptions.RequestException as e:
        print(f"Request error adding download: {e}")
        return {"error": f"Network error: {str(e)}"}
    except Exception as e:
        print(f"Error adding download: {e}")
        import traceback
        traceback.print_exc()
        return {"error": str(e)}


@eel.expose
def get_downloads() -> dict:
    """Get all downloads from the API.
    
    Note: This function may not be called as frequently when WebSocket updates are active.
    """
    try:
        # Use retry strategy for more robust API calls
        from requests.adapters import HTTPAdapter
        from urllib3.util.retry import Retry
        
        retry_strategy = Retry(
            total=3,  # Maximum number of retries
            backoff_factor=0.5,  # Exponential backoff factor
            status_forcelist=[429, 500, 502, 503, 504],  # HTTP status codes to retry on
            allowed_methods=["GET"]  # Only retry on GET requests
        )
        
        session = requests.Session()
        adapter = HTTPAdapter(max_retries=retry_strategy)
        session.mount("http://", adapter)
        session.mount("https://", adapter)
        
        # Use session with retry strategy
        response = session.get(f"{API_BASE_URL}", timeout=DEFAULT_REQUEST_TIMEOUT)
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
        import traceback
        traceback.print_exc()
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
    """Format a download object for the UI."""
    try:
        # Calculate progress
        progress = 0
        if download.get("size") and download["size"] > 0:
            progress = min(100, (download.get("size_downloaded", 0) / download["size"]) * 100)

        # Create a consistent structure for the UI
        return {
            "id": download["id"],
            "url": str(download["url"]),
            "filename": download["name"],
            "save_path": download["save_path"],
            "category": download["category"],
            "status": download["status"],
            "size": download.get("size", 0),
            "downloaded": download.get("size_downloaded", 0),
            "speed": download.get("speed", 0),
            "time_left": download.get("time_left", 0),
            "date_added": download["date_added"],
            "progress": progress,
            "priority": download.get("priority", 2),
            "max_speed": download.get("max_speed", None),
            "max_retries": download.get("max_retries", 3),
            "is_youtube": download.get("is_youtube", False),
            "youtube_type": download.get("youtube_type", None),
            "schedule": download.get("schedule", None),
        }
    except Exception as e:
        print(f"Error formatting download for UI: {e}")
        # Return a minimal download object to avoid breaking the UI
        return {
            "id": download.get("id", "unknown"),
            "filename": download.get("name", "Unknown file"),
            "status": "error",
            "error": str(e)
        }


def run_api_server():
    """Run the API server as a separate process."""
    global api_process
    
    try:
        # First check if the API server is already running
        try:
            print("Checking if API server is already running...")
            response = requests.get("http://localhost:8000/api", timeout=2.0)
            if response.status_code == 200:
                print("API server is already running. Skipping startup process.")
                return
        except requests.ConnectionError:
            # Most likely server not running
            print("API server not detected, starting new server...")
        except requests.RequestException as e:
            # Other request errors
            print(f"Error checking API server status: {e}. Will attempt to start new server.")
        except Exception as e:
            print(f"Unexpected error checking server status: {e}. Will attempt to start new server.")
        
        # Use Python executable to ensure we're using the right version
        executable = sys.executable
        
        # Build the command to run the main.py script
        command = [executable, "-m", "app.main", "--api-only"]
        
        # Start the process with appropriate options
        if sys.platform == "win32":
            # On Windows, use subprocess.DETACHED_PROCESS to allow the process to run independently
            # of the console window
            api_process = subprocess.Popen(
                command,
                # Don't capture stdout/stderr to allow them to be displayed in console
                stdin=subprocess.PIPE,
                # stdout=subprocess.PIPE,
                # stderr=subprocess.PIPE,
                creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
            )
        else:
            # On Unix-like systems, we need different settings
            api_process = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                # stdout=subprocess.PIPE,
                # stderr=subprocess.PIPE,
                start_new_session=True,
            )
        
        # Wait for server to start with exponential backoff
        max_attempts = 10
        base_wait_time = 1.0  # Start with 1 second
        for attempt in range(max_attempts):
            wait_time = base_wait_time * (1.5 ** attempt)  # Exponential backoff
            try:
                # Try to connect to the API
                print(f"Attempting to connect to API server (attempt {attempt + 1}/{max_attempts})")
                response = requests.get("http://localhost:8000/api", timeout=wait_time)
                if response.status_code == 200:
                    print(f"API server started successfully after {attempt + 1} attempts")
                    # Give a little extra time for all routes and services to initialize
                    time.sleep(1)
                    break
            except requests.RequestException:
                # Server not ready yet, wait with exponential backoff
                print(f"Waiting for API server to start (attempt {attempt + 1}/{max_attempts}, waiting {wait_time:.1f}s)...")
                time.sleep(wait_time)
                
                # Check if process is still running
                if api_process.poll() is not None:
                    print("API server process exited prematurely!")
                    # Try to read stdout/stderr for debugging if available
                    if hasattr(api_process, 'stdout') and api_process.stdout:
                        try:
                            stdout, stderr = api_process.communicate(timeout=1)
                            print(f"API server stdout: {stdout.decode() if stdout else 'N/A'}")
                            print(f"API server stderr: {stderr.decode() if stderr else 'N/A'}")
                        except Exception as comm_error:
                            print(f"Could not read API server output: {comm_error}")
                    # Break out of the loop
                    break
                
        # If we didn't connect successfully after max attempts
        if attempt == max_attempts - 1:
            print("Failed to connect to API server after multiple attempts!")
            
    except Exception as e:
        print(f"Error starting API server: {e}")
        import traceback
        traceback.print_exc()


def shutdown_scheduler():
    """Shutdown the scheduler service properly."""
    print("Shutting down scheduler...")
    success = False

    try:
        # Try to send a clean shutdown request to the scheduler API endpoint
        response = requests.post(
            "http://localhost:8000/api/downloads/scheduler/shutdown", timeout=5
        )
        if response.status_code == 200:
            print("Scheduler shutdown successful")
            success = True
        else:
            print(f"Scheduler shutdown returned status code: {response.status_code}")
    except Exception as e:
        print(f"Error shutting down scheduler: {e}")

    # Try a secondary approach if the first one fails
    if not success:
        try:
            print("Attempting alternative scheduler shutdown...")
            try:
                response = requests.post("http://localhost:8000/api/scheduler/shutdown", timeout=3)
                if response.status_code == 200:
                    print("Alternative scheduler shutdown successful")
                    success = True
            except:
                pass

            # Final attempt - terminate process
            if not success:
                print("Using final shutdown method - API server termination")
                # Shutdown API server will also handle scheduler
                try:
                    shutdown_api_server()
                    success = True
                except:
                    pass
        except Exception as inner_e:
            print(f"Alternative scheduler shutdown also failed: {inner_e}")

    print("Scheduler shutdown complete")
    return success


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


@eel.expose
def import_download_list(file_path: str) -> dict:
    """Import a download list from a file."""
    try:
        if not file_path:
            return {"error": "No file path provided"}

        # Send the file path to the API
        response = requests.post(
            f"{API_BASE_URL}/import",
            json={"file_path": file_path},
            timeout=DEFAULT_REQUEST_TIMEOUT * 2,  # Longer timeout for imports
        )
        response.raise_for_status()
        result = response.json()

        if "error" in result:
            return {"error": result["error"]}

        return {
            "success": True,
            "message": f"Successfully imported {result.get('imported_count', 0)} downloads",
            "imported_count": result.get("imported_count", 0),
        }
    except Exception as e:
        print(f"Error importing download list: {e}")
        return {"error": str(e)}


@eel.expose
def export_download_list(file_path: str) -> dict:
    """Export the download list to a file."""
    try:
        if not file_path:
            return {"error": "No file path provided"}

        # Send the file path to the API
        response = requests.post(
            f"{API_BASE_URL}/export", json={"file_path": file_path}, timeout=DEFAULT_REQUEST_TIMEOUT
        )
        response.raise_for_status()
        result = response.json()

        if "error" in result:
            return {"error": result["error"]}

        return {
            "success": True,
            "message": f"Successfully exported {result.get('exported_count', 0)} downloads to {file_path}",
        }
    except Exception as e:
        print(f"Error exporting download list: {e}")
        return {"error": str(e)}


@eel.expose
def show_file_open_dialog(title="Select a file", file_types=None, initial_dir=None):
    """Show a file open dialog and return the selected file path."""
    try:
        import tkinter as tk
        from tkinter import filedialog

        # If file_types is not provided, default to common formats
        if file_types is None:
            file_types = [
                ("JSON Files", "*.json"),
                ("CSV Files", "*.csv"),
                ("Text Files", "*.txt"),
                ("All Files", "*.*"),
            ]

        # Create a hidden root window
        root = tk.Tk()
        root.withdraw()

        # Show the dialog
        file_path = filedialog.askopenfilename(
            title=title, filetypes=file_types, initialdir=initial_dir
        )

        # Destroy the root window
        root.destroy()

        if file_path:
            return {"success": True, "file_path": file_path}
        else:
            return {"success": False, "error": "No file selected"}
    except Exception as e:
        print(f"Error showing file open dialog: {e}")
        return {"success": False, "error": str(e)}


@eel.expose
def show_file_save_dialog(
    title="Save as",
    default_extension=".json",
    file_types=None,
    initial_dir=None,
    initial_file="download_list.json",
):
    """Show a file save dialog and return the selected file path."""
    try:
        import tkinter as tk
        from tkinter import filedialog

        # If file_types is not provided, default to JSON
        if file_types is None:
            file_types = [("JSON Files", "*.json"), ("All Files", "*.*")]

        # Create a hidden root window
        root = tk.Tk()
        root.withdraw()

        # Show the dialog
        file_path = filedialog.asksaveasfilename(
            title=title,
            defaultextension=default_extension,
            filetypes=file_types,
            initialdir=initial_dir,
            initialfile=initial_file,
        )

        # Destroy the root window
        root.destroy()

        if file_path:
            return {"success": True, "file_path": file_path}
        else:
            return {"success": False, "error": "No file selected"}
    except Exception as e:
        print(f"Error showing file save dialog: {e}")
        return {"success": False, "error": str(e)}


@eel.expose
def open_file_location(file_path: str) -> dict:
    """Open the directory containing a file."""
    try:
        import os
        import platform
        import subprocess

        if not file_path:
            return {"success": False, "error": "No file path provided"}

        # Get the directory path
        dir_path = os.path.dirname(file_path)

        # Check if the directory exists
        if not os.path.exists(dir_path):
            return {"success": False, "error": f"Directory does not exist: {dir_path}"}

        # Open the directory based on the platform
        system = platform.system()

        if system == "Windows":
            # On Windows, use explorer to open the directory
            subprocess.Popen(["explorer", dir_path])
        elif system == "Darwin":
            # On macOS, use open command
            subprocess.Popen(["open", dir_path])
        else:
            # On Linux, use xdg-open
            subprocess.Popen(["xdg-open", dir_path])

        return {"success": True}
    except Exception as e:
        print(f"Error opening file location: {e}")
        return {"success": False, "error": str(e)}


def start_gui():
    """Start the GUI application."""
    print("Starting XcelerateDL GUI...")
    
    global api_thread, api_process # Ensure api_process is the global one being modified/accessed

    _cleanup_has_run = False # Flag to ensure cleanup runs only once
    
    try:
        # Start the API server
        thread = threading.Thread(target=run_api_server)
        thread.daemon = True  # Set as daemon so it gets killed when main thread exits
        thread.start()
        
        # Store the thread for later reference
        global api_thread
        api_thread = thread
        
        # Set up event handlers
        @eel.expose
        def on_load():
            """Called when the UI has loaded."""
            print("UI loaded!")
        
        # Register app shutdown callback
        def cleanup_on_exit(*args, **kwargs):
            """Handle graceful shutdown when the application exits."""
            nonlocal _cleanup_has_run
            # api_process is global, so it's accessible

            if _cleanup_has_run:
                print("GUI: Cleanup process already initiated or completed.")
                return
            _cleanup_has_run = True

            print("GUI: Initiating application shutdown sequence...")
            try:
                # --- Graceful API Server Shutdown ---
                if api_process and api_process.poll() is None: # Check if process exists and is running
                    print("GUI: API server process is active. Attempting graceful shutdown.")
                    
                    # Step 1: Send shutdown request to the API server
                    print("GUI: Sending /shutdown request to API server...")
                    try:
                        # The API's /shutdown endpoint (in main.py) is designed to return quickly.
                        # Uvicorn (in main.py) has timeout_graceful_shutdown=30.
                        response = requests.post("http://localhost:8000/shutdown", timeout=10) # Timeout for the request itself
                        print(f"GUI: API /shutdown request: Status {response.status_code}. Response: {response.text[:150]}...")
                    except requests.RequestException as e:
                        print(f"GUI: Failed to send /shutdown request to API server: {e}. Will proceed to terminate process.")
                    
                    # Step 2: Wait for the API server process to exit gracefully
                    api_graceful_wait_timeout = 35 # Should be > uvicorn's timeout_graceful_shutdown (30s)
                    print(f"GUI: Waiting up to {api_graceful_wait_timeout}s for API server process to self-terminate...")
                    try:
                        api_process.wait(timeout=api_graceful_wait_timeout)
                        print("GUI: API server process has exited.")
                    except subprocess.TimeoutExpired:
                        print(f"GUI: API server process did not exit within {api_graceful_wait_timeout}s. Escalating to SIGTERM.")
                        # Step 3: If wait times out, terminate (SIGTERM) the process
                        try:
                            api_process.terminate()
                            print("GUI: Sent SIGTERM to API server process. Waiting up to 10s for termination...")
                            api_process.wait(timeout=10) 
                            print("GUI: API server process terminated.")
                        except subprocess.TimeoutExpired:
                            print("GUI: API server process did not terminate after SIGTERM (10s). Escalating to SIGKILL.")
                            # Step 4: If terminate times out, kill (SIGKILL) the process
                            try:
                                api_process.kill()
                                # Wait a moment for kill to take effect, though it's usually immediate
                                api_process.wait(timeout=5) 
                                print("GUI: Sent SIGKILL to API server process.")
                            except Exception as e_kill:
                                print(f"GUI: Error during API process SIGKILL or subsequent wait: {e_kill}")
                        except Exception as e_term:
                            print(f"GUI: Error during API process SIGTERM or subsequent wait: {e_term}")
                    except Exception as e_wait: # Catches other errors during the initial api_process.wait()
                        print(f"GUI: Error while waiting for API process to self-terminate: {e_wait}")
                
                elif api_process and api_process.poll() is not None:
                    print(f"GUI: API server process was found but had already exited (Code: {api_process.returncode}).")
                else:
                    print("GUI: API server process not found or was not started by this GUI instance.")

                # --- Add any other GUI-specific cleanup here if needed ---
                # For example, explicitly close any open resources by the GUI itself.

                print("GUI: Application shutdown sequence finished.")
            except Exception as e:
                print(f"GUI: Unhandled error during the cleanup_on_exit process: {e}")
                import traceback
                traceback.print_exc()
            finally:
                # This ensures that even if an error occurs, subsequent calls know it has attempted to run.
                _cleanup_has_run = True
                
        # Set up signal handlers for graceful shutdown
        signal.signal(signal.SIGINT, cleanup_on_exit)
        signal.signal(signal.SIGTERM, cleanup_on_exit)
        
        # Start the Eel application
        # NOTE: We use web=False to use the default browser
        eel.start(
            "templates/index.html",
            mode="chrome",
            size=(1280, 800),
            port=0,  # Use a random port
            block=True,  # Block so that the API server stays alive
            suppress_error=False,
            close_callback=cleanup_on_exit,  # Add this to ensure cleanup happens when window is closed
        )
    except (SystemExit, KeyboardInterrupt):
        # This is expected when the application is closed normally
        print("Application exited normally.")
    except Exception as e:
        print(f"Error starting GUI: {e}")
        raise
