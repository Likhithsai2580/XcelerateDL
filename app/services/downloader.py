import asyncio
import contextlib
import json
import mimetypes
import os
import subprocess
import sys
import uuid
import re
from datetime import datetime
from urllib.parse import urlparse

import aiofiles
import aiohttp

from app.models.download import CreateDownloadRequest, DownloadItem, DownloadStatus, FileCategory, YoutubeDownloadType
from app.services.ws_manager import manager as ws_manager


class DownloadManager:
    def __init__(
        self,
        download_dir: str = "./downloads",
        storage_file: str = "./downloads/download_data.json",
    ):
        self.download_dir = download_dir
        self.storage_file = storage_file
        self.downloads: dict[str, DownloadItem] = {}
        self.tasks: dict[str, asyncio.Task] = {}

        # Ensure download directory exists
        os.makedirs(download_dir, exist_ok=True)

        # Create category directories
        for category in FileCategory:
            if category != FileCategory.ALL:
                os.makedirs(os.path.join(download_dir, category.value), exist_ok=True)

    async def initialize(self):
        """Load saved downloads and resume interrupted ones"""
        await self.load_downloads()

        # Check for partially downloaded files and resume them
        downloads_to_resume = []
        for download_id, download in self.downloads.items():
            # Only resume downloads that were in progress
            if download.status in [
                DownloadStatus.DOWNLOADING,
                DownloadStatus.QUEUED,
                DownloadStatus.PAUSED,
            ]:
                # Check if the file exists but is incomplete
                if os.path.exists(download.save_path):
                    current_size = os.path.getsize(download.save_path)
                    if download.size and current_size < download.size:
                        # Update size_downloaded to match what's on disk
                        download.size_downloaded = current_size
                        download.status = DownloadStatus.PAUSED
                        downloads_to_resume.append(download_id)
                    elif current_size > 0 and download.size is None:
                        # We don't know the full size, but there's partial data
                        download.size_downloaded = current_size
                        download.status = DownloadStatus.PAUSED
                        downloads_to_resume.append(download_id)
                    else:
                        # File doesn't exist or is empty, mark as queued
                        download.size_downloaded = 0
                        download.status = DownloadStatus.QUEUED
                        downloads_to_resume.append(download_id)
                else:
                    # File doesn't exist, mark as queued
                    download.size_downloaded = 0
                    download.status = DownloadStatus.QUEUED
                    downloads_to_resume.append(download_id)

        # Resume downloads that were interrupted
        for download_id in downloads_to_resume:
            if self.downloads[download_id].is_youtube:
                self.tasks[download_id] = asyncio.create_task(self._download_youtube(download_id))
            else:
                self.tasks[download_id] = asyncio.create_task(self._download_file(download_id))

        print(
            f"Restored {len(self.downloads)}"
            f" downloads, resumed {len(downloads_to_resume)} downloads"
        )

        # Start periodic saving
        self._start_autosave()

        return len(downloads_to_resume)

    def _start_autosave(self, interval_seconds: int = 30):
        """Start an automatic save task to run periodically"""

        async def autosave_task():
            while True:
                await asyncio.sleep(interval_seconds)
                await self.save_downloads()

        asyncio.create_task(autosave_task())

    async def save_downloads(self):
        """Save downloads to a JSON file"""
        # Create a directory for the storage file if it doesn't exist
        os.makedirs(os.path.dirname(self.storage_file), exist_ok=True)

        # Convert downloads to serializable format
        downloads_data = {}
        for download_id, download in self.downloads.items():
            # Convert model to dict and handle non-serializable types
            download_dict = download.model_dump()

            # Convert datetime to ISO format
            download_dict["date_added"] = download_dict["date_added"].isoformat()

            # Store URL as string
            download_dict["url"] = str(download_dict["url"])
            
            # Remove callback functions which are not serializable
            download_dict.pop("cancel_callback", None)
            download_dict.pop("pause_resume_callback", None)

            downloads_data[download_id] = download_dict

        try:
            async with aiofiles.open(self.storage_file, "w") as f:
                await f.write(json.dumps(downloads_data, indent=2))
            return True
        except Exception as e:
            print(f"Error saving downloads: {e}")
            return False

    async def load_downloads(self):
        """Load downloads from a JSON file"""
        if not os.path.exists(self.storage_file):
            return False

        try:
            async with aiofiles.open(self.storage_file) as f:
                content = await f.read()
                downloads_data = json.loads(content)

            # Convert JSON data back to download objects
            for download_id, download_dict in downloads_data.items():
                # Convert ISO datetime string back to datetime
                download_dict["date_added"] = datetime.fromisoformat(download_dict["date_added"])

                # Create DownloadItem from dict
                download = DownloadItem(**download_dict)
                self.downloads[download_id] = download

            return True
        except Exception as e:
            print(f"Error loading downloads: {e}")
            return False

    def _detect_category(self, filename: str) -> FileCategory:
        """Detect file category based on filename and extension"""
        ext = os.path.splitext(filename)[1].lower()

        # Simplified category detection
        if ext in [".zip", ".rar", ".7z", ".tar", ".gz"]:
            return FileCategory.COMPRESSED
        elif ext in [".exe", ".msi", ".deb", ".rpm", ".pkg"]:
            return FileCategory.PROGRAMS
        elif ext in [".mp4", ".avi", ".mkv", ".mov", ".wmv"]:
            return FileCategory.VIDEOS
        elif ext in [".mp3", ".wav", ".ogg", ".flac", ".m4a"]:
            return FileCategory.MUSIC
        elif ext in [".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp"]:
            return FileCategory.PICTURES
        elif ext in [".pdf", ".doc", ".docx", ".txt", ".xls", ".xlsx", ".ppt", ".pptx"]:
            return FileCategory.DOCUMENTS
        else:
            return FileCategory.OTHER

    def is_youtube_url(self, url: str) -> bool:
        """Check if the URL is a YouTube URL"""
        if not url:
            return False
            
        url_str = str(url).lower()  # Convert to string and lowercase for comparison
        
        # Simple pattern matching for common YouTube URL formats
        youtube_patterns = [
            r"^(https?://)?(www\.)?(youtube\.com|youtu\.be)",
            r"youtube\.com/watch\?v=",
            r"youtu\.be/",
            r"youtube\.com/shorts/"
        ]
        
        for pattern in youtube_patterns:
            if re.search(pattern, url_str):
                print(f"Detected YouTube URL: {url}")
                return True
                
        return False

    async def get_youtube_info(self, url: str) -> tuple[str | None, int | None, str | None]:
        """Get video info from YouTube URL using yt-dlp"""
        try:
            # First check if yt-dlp is available
            yt_dlp_cmd = self._find_yt_dlp_command()
            if not yt_dlp_cmd:
                print("Cannot get YouTube info: yt-dlp not found")
                return None, None, None
            
            # Import re here to ensure it's available
            import re
            
            # Run yt-dlp to get video info
            cmd = yt_dlp_cmd.copy() + [
                "--dump-json",
                "--no-playlist",
                "--no-warnings",
                url
            ]
            
            print(f"Getting YouTube info using command: {' '.join(cmd)}")
            
            # Handle Windows-specific limitations
            if sys.platform == 'win32':
                # Run the command in a thread to avoid asyncio limitations on Windows
                import subprocess
                import threading
                import queue
                
                result_queue = queue.Queue()
                
                def run_in_thread():
                    try:
                        result = subprocess.run(
                            cmd,
                            stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE,
                            text=True,
                            check=False
                        )
                        result_queue.put((result.returncode, result.stdout, result.stderr))
                    except Exception as e:
                        result_queue.put((1, "", str(e)))
                
                # Run the command in a separate thread
                thread = threading.Thread(target=run_in_thread)
                thread.daemon = True
                thread.start()
                
                # Wait for the thread to complete (with timeout)
                thread.join(timeout=30.0)
                
                if thread.is_alive():
                    print("Command timed out after 30 seconds")
                    return None, None, None
                
                # Get the result
                returncode, stdout, stderr = result_queue.get()
                
                if returncode != 0:
                    error = stderr.strip() if stderr else "Unknown error"
                    print(f"Error getting YouTube info: {error}")
                    
                    # Try a simpler fallback - just extract the video ID and make a filename
                    video_id = None
                    if "youtu.be/" in url:
                        video_id = url.split("youtu.be/")[1].split("?")[0].split("&")[0]
                    elif "youtube.com/watch" in url:
                        # Try to extract from v= parameter
                        match = re.search(r'v=([a-zA-Z0-9_-]+)', url)
                        if match:
                            video_id = match.group(1)
                    
                    if video_id:
                        print(f"Using fallback method with video ID: {video_id}")
                        return f"YouTube Video {video_id}.mp4", None, None
                    
                    return None, None, None
                
                info_json = stdout
            else:
                # Use asyncio to run the command on non-Windows platforms
                process = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE
                )
                stdout, stderr = await process.communicate()
                
                if process.returncode != 0:
                    error = stderr.decode().strip() if stderr else "Unknown error"
                    print(f"Error getting YouTube info: {error}")
                    
                    # Try a simpler fallback - just extract the video ID and make a filename
                    video_id = None
                    if "youtu.be/" in url:
                        video_id = url.split("youtu.be/")[1].split("?")[0].split("&")[0]
                    elif "youtube.com/watch" in url:
                        # Try to extract from v= parameter
                        match = re.search(r'v=([a-zA-Z0-9_-]+)', url)
                        if match:
                            video_id = match.group(1)
                    
                    if video_id:
                        print(f"Using fallback method with video ID: {video_id}")
                        return f"YouTube Video {video_id}.mp4", None, None
                    
                    return None, None, None
                
                info_json = stdout.decode()
            
            if not info_json.strip():
                print("No output from yt-dlp")
                return None, None, None
            
            # Parse the JSON output
            video_info = json.loads(info_json)
            
            # Extract relevant information
            title = video_info.get("title", "Unknown YouTube Video")
            # Remove characters that might cause filename issues
            title = re.sub(r'[\\/*?:"<>|]', "_", title)
            filename = f"{title}.mp4"  # Default to mp4 for videos
            
            # Get approximate file size if available
            filesize = video_info.get("filesize") or video_info.get("filesize_approx")
            
            # Get video description
            description = video_info.get("description", "")
            
            print(f"Found YouTube video: {title}, size: {filesize}")
            return filename, filesize, description
            
        except json.JSONDecodeError as e:
            print(f"Error parsing YouTube info JSON: {e}")
            print(f"Raw output was: {info_json if 'info_json' in locals() else 'No output'}")
            return None, None, None
        except Exception as e:
            print(f"Error getting YouTube info: {e}")
            import traceback
            traceback.print_exc()
            return None, None, None

    async def get_file_info(self, url: str) -> tuple[str | None, int | None]:
        """Get filename and size from the URL"""
        # Check if it's a YouTube URL
        if self.is_youtube_url(url):
            filename, size, _ = await self.get_youtube_info(url)
            return filename, size

        try:
            async with (
                aiohttp.ClientSession() as session,
                session.head(url, allow_redirects=True) as response,
            ):
                if response.status != 200:
                    return None, None

                # Extract filename from URL or Content-Disposition header
                content_disposition = response.headers.get("Content-Disposition")
                if content_disposition and "filename=" in content_disposition:
                    filename = content_disposition.split("filename=")[1].strip("\"'")
                else:
                    parsed_url = urlparse(url)
                    filename = os.path.basename(parsed_url.path)
                    if not filename:
                        # Generate a filename with the right extension
                        content_type = response.headers.get("Content-Type", "")
                        ext = mimetypes.guess_extension(content_type) or ".bin"
                        filename = f"download_{uuid.uuid4().hex[:8]}{ext}"

                # Get file size if available
                content_length = response.headers.get("Content-Length")
                size = int(content_length) if content_length else None

                return filename, size
        except Exception:
            return None, None

    async def save_and_broadcast_download(self, download_id: str, update_type: str = "update"):
        """Save downloads to a JSON file and broadcast the update to WebSocket clients"""
        # Save to file
        await self.save_downloads()

        # Get the download to broadcast
        download = self.downloads.get(download_id)
        if not download:
            return

        # Prepare download data for broadcast
        download_dict = self._prepare_download_for_api(download)

        # Broadcast the update
        await ws_manager.broadcast({"type": update_type, "download": download_dict})

    async def broadcast_all_downloads(self):
        """Broadcast all downloads to WebSocket clients"""
        downloads_data = []
        for _download_id, download in self.downloads.items():
            # Convert model to API-safe dict format
            download_dict = self._prepare_download_for_api(download)
            downloads_data.append(download_dict)

        # Broadcast the list of downloads
        await ws_manager.broadcast(
            {"type": "downloads_list", "downloads": downloads_data, "total": len(downloads_data)}
        )

    async def add_download(self, request: CreateDownloadRequest) -> DownloadItem:
        """Add a new download and start it"""
        # Generate a unique ID for the download
        download_id = str(uuid.uuid4())

        # Set the save directory based on category or a default location
        category = request.category
        if not category:
            if request.is_youtube:
                category = FileCategory.YOUTUBE
            else:
                # Try to detect category from filename
                filename = request.filename
                if not filename and request.url:
                    # Extract filename from URL
                    url_path = urlparse(str(request.url)).path
                    filename = os.path.basename(url_path)
                
                if filename:
                    category = self._detect_category(filename)
                else:
                    category = FileCategory.OTHER

        # Handle save path
        if request.save_path:
            # Use user-provided save path
            save_dir = request.save_path
            # Ensure directory exists
            os.makedirs(save_dir, exist_ok=True)
        else:
            # Use category-based directory
            save_dir = os.path.join(self.download_dir, category.value)
            os.makedirs(save_dir, exist_ok=True)

        # Get filename
        filename = request.filename
        if not filename:
            # Extract filename from URL or use a default name
            if request.is_youtube:
                # For YouTube, we'll get the title later
                filename = f"youtube_{download_id}.mp4"
                if request.youtube_type == YoutubeDownloadType.AUDIO:
                    filename = f"youtube_{download_id}.mp3"
            else:
                # For regular URLs, extract from path
                url_path = urlparse(str(request.url)).path
                filename = os.path.basename(url_path)
                if not filename:
                    filename = f"download_{download_id}"

        # Full save path
        save_path = os.path.join(save_dir, filename)

        # Create download object
        download = DownloadItem(
            id=download_id,
            name=filename,
            url=request.url,
            save_path=save_path,
            size=None,  # Will be determined when download starts
            size_downloaded=0,
            status=DownloadStatus.QUEUED,
            speed=0,
            time_left=None,
            date_added=datetime.now(),
            category=category,
            is_youtube=request.is_youtube,
            youtube_type=request.youtube_type,
            priority=request.priority,
            max_speed=request.max_speed,
            max_retries=request.max_retries
        )

        # Save to our dictionary of downloads
        self.downloads[download_id] = download

        # Start the download task
        if download.is_youtube:
            self.tasks[download_id] = asyncio.create_task(self._download_youtube(download_id))
        else:
            self.tasks[download_id] = asyncio.create_task(self._download_file(download_id))

        # Save the downloads data
        await self.save_downloads()

        # Return the download object
        return download

    async def _download_file(self, download_id: str) -> None:
        """Download a file from a URL"""
        download = self.downloads.get(download_id)
        if not download:
            print(f"Download {download_id} not found")
            return

        # Try to get file info (name and size) if not already set
        try:
            if not download.size:
                name, size = await self.get_file_info(str(download.url))
                if name and not download.name.startswith("download_"):
                    download.name = name
                    # Update save path with new filename if detected
                    download.save_path = os.path.join(
                        os.path.dirname(download.save_path), name
                    )
                if size:
                    download.size = size
        except Exception as e:
            print(f"Error getting file info: {e}")
            # Continue anyway, we'll handle file size dynamically

        # Check if the file already exists and we can resume
        initial_size = 0
        if os.path.exists(download.save_path):
            try:
                initial_size = os.path.getsize(download.save_path)
                if download.size and initial_size >= download.size:
                    # File is already complete
                    download.size_downloaded = download.size
                    download.status = DownloadStatus.COMPLETED
                    await self.save_and_broadcast_download(download_id)
                    return
                elif download.size and initial_size > 0:
                    # Partial download, can resume
                    download.size_downloaded = initial_size
                    await self.save_and_broadcast_download(download_id, "resume")
                elif initial_size > 0:
                    # Partial download but unknown total size
                    download.size_downloaded = initial_size
                    await self.save_and_broadcast_download(download_id, "resume")
            except (OSError, FileNotFoundError) as e:
                print(f"Error checking existing file: {e}")
                initial_size = 0

        # Create parent directory if it doesn't exist
        os.makedirs(os.path.dirname(download.save_path), exist_ok=True)

        # Retry loop for the download
        retry_count = 0
        max_retries = download.max_retries
        
        while retry_count <= max_retries:
            try:
                # Set up download
                download.status = DownloadStatus.DOWNLOADING
                download.retry_count = retry_count
                await self.save_and_broadcast_download(download_id)

                # Set up aiohttp session
                async with aiohttp.ClientSession() as session:
                    headers = {}
                    if initial_size > 0:
                        # Resume download from where we left off
                        headers["Range"] = f"bytes={initial_size}-"

                    async with session.get(str(download.url), headers=headers) as response:
                        # Check if the server supports resume
                        if initial_size > 0 and response.status != 206:
                            # Server doesn't support resuming, start from the beginning
                            initial_size = 0
                            # Truncate the file
                            with open(download.save_path, "wb"):
                                pass
                            download.size_downloaded = 0
                        
                        # Update file size if we got it from response headers
                        if "Content-Length" in response.headers:
                            content_length = int(response.headers["Content-Length"])
                            if initial_size > 0 and response.status == 206:
                                # For resumed downloads, the content-length is the remaining bytes
                                download.size = initial_size + content_length
                            else:
                                download.size = content_length
                        
                        # Set up file write mode (append if resuming, otherwise write)
                        mode = "ab" if initial_size > 0 and response.status == 206 else "wb"
                        
                        # Open the file and start downloading
                        async with aiofiles.open(download.save_path, mode) as f:
                            chunk_size = 65536  # 64KB chunks
                            downloaded_since_update = 0
                            last_update_time = datetime.now()
                            last_downloaded = download.size_downloaded
                            
                            # Create a pause event to handle pausing
                            pause_event = asyncio.Event()
                            pause_event.set()  # Not paused initially
                            
                            # Set up pause/resume callback function
                            async def pause_resume_callback(paused: bool = None):
                                if paused is None:
                                    # Toggle pause state
                                    if pause_event.is_set():
                                        pause_event.clear()
                                        download.status = DownloadStatus.PAUSED
                                        await self.save_and_broadcast_download(download_id, "pause")
                                        return True
                                    else:
                                        pause_event.set()
                                        download.status = DownloadStatus.DOWNLOADING
                                        await self.save_and_broadcast_download(download_id, "resume")
                                        return True
                                elif paused and pause_event.is_set():
                                    # Pause the download
                                    pause_event.clear()
                                    download.status = DownloadStatus.PAUSED
                                    await self.save_and_broadcast_download(download_id, "pause")
                                    return True
                                elif not paused and not pause_event.is_set():
                                    # Resume the download
                                    pause_event.set()
                                    download.status = DownloadStatus.DOWNLOADING
                                    await self.save_and_broadcast_download(download_id, "resume")
                                    return True
                                return False
                            
                            # Set up cancel callback
                            async def cancel_callback():
                                # Mark the download as failed so we stop the loop
                                download.status = DownloadStatus.FAILED
                                await self.save_and_broadcast_download(download_id, "cancel")
                                return True
                            
                            # Attach callbacks to the download object
                            download.pause_resume_callback = pause_resume_callback
                            download.cancel_callback = cancel_callback
                            
                            # Stream the download
                            async for chunk in response.content.iter_chunked(chunk_size):
                                # Check if we've been asked to cancel
                                if download.status == DownloadStatus.FAILED:
                                    # We've been cancelled, break out
                                    return
                                
                                # Wait if we're paused
                                await pause_event.wait()
                                
                                # Apply speed limit if configured
                                if download.max_speed:
                                    # Calculate how long this chunk should take
                                    chunk_size_bytes = len(chunk)
                                    target_time_seconds = chunk_size_bytes / download.max_speed
                                    
                                    # Sleep to limit speed
                                    await asyncio.sleep(target_time_seconds)
                                
                                # Write the chunk
                                await f.write(chunk)
                                
                                # Update download size and progress
                                download.size_downloaded += len(chunk)
                                downloaded_since_update += len(chunk)
                                
                                # Calculate speed and update UI periodically
                                now = datetime.now()
                                time_diff = (now - last_update_time).total_seconds()
                                if time_diff >= 1.0:  # Update every second
                                    speed = downloaded_since_update / time_diff
                                    download.speed = int(speed)
                                    
                                    # Calculate time left
                                    if download.size and download.speed > 0:
                                        remaining_bytes = download.size - download.size_downloaded
                                        download.time_left = int(remaining_bytes / download.speed)
                                    else:
                                        download.time_left = None
                                    
                                    # Broadcast progress update
                                    downloaded_since_update = 0
                                    last_update_time = now
                                    await self.save_and_broadcast_download(download_id, "progress")
                            
                            # Download completed successfully
                            download.status = DownloadStatus.COMPLETED
                            download.speed = 0
                            download.time_left = 0
                            
                            # If we didn't know the size before, set it now
                            if not download.size:
                                download.size = download.size_downloaded
                            
                            # Send notification of completed download
                            await self._send_notification(
                                download_id,
                                "Download completed",
                                f"{download.name} has been downloaded successfully",
                                "success"
                            )
                            
                            await self.save_and_broadcast_download(download_id, "complete")
                            return  # Exit retry loop on success
            
            except asyncio.CancelledError:
                # Task was cancelled
                download.status = DownloadStatus.PAUSED
                await self.save_and_broadcast_download(download_id)
                return
            
            except Exception as e:
                # Log the error
                print(f"Download error: {e}")
                retry_count += 1
                download.retry_count = retry_count
                download.status = DownloadStatus.FAILED if retry_count > max_retries else DownloadStatus.QUEUED
                
                # Send notification about retry
                if retry_count <= max_retries:
                    await self._send_notification(
                        download_id,
                        "Download failed - Retrying",
                        f"Retrying {download.name} (Attempt {retry_count}/{max_retries})",
                        "warning"
                    )
                    # Wait a bit before retrying
                    await asyncio.sleep(2 * retry_count)  # Progressive backoff
                else:
                    # Send notification about final failure
                    await self._send_notification(
                        download_id,
                        "Download failed",
                        f"Failed to download {download.name} after {max_retries} attempts",
                        "error"
                    )
                
                await self.save_and_broadcast_download(download_id)
                
                if retry_count > max_retries:
                    return  # Exit function after max retries exceeded

    async def _download_youtube(self, download_id: str) -> None:
        """Download a YouTube video using yt-dlp with pause/resume functionality"""
        download = self.downloads[download_id]
        download.status = DownloadStatus.DOWNLOADING
        download.speed = 0
        download.time_left = None
        download.size_downloaded = 0

        # Broadcast the initial status
        await self._broadcast_download_update(download_id)

        # Cancellation flag
        is_cancelled = False
        paused_event = asyncio.Event()
        paused_event.set()  # Start in non-paused state

        try:
            # Prepare the output file path
            save_dir = os.path.dirname(download.save_path)
            os.makedirs(save_dir, exist_ok=True)

            # Set output format based on YouTube download type
            output_template = os.path.join(save_dir, "%(title)s.%(ext)s")
            
            if download.youtube_type == YoutubeDownloadType.AUDIO:
                format_option = "bestaudio/best"
            else:  # Default to video
                format_option = "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best"

            # Check if yt-dlp is installed - try multiple possible paths
            yt_dlp_cmd = self._find_yt_dlp_command()
            if not yt_dlp_cmd:
                print("Error: yt-dlp not found. Please install it using 'pip install yt-dlp'")
                download.status = DownloadStatus.FAILED
                await self._broadcast_download_update(download_id)
                return
                
            print(f"Using yt-dlp command: {' '.join(yt_dlp_cmd)}")
            
            # Run yt-dlp
            cmd = yt_dlp_cmd.copy()
            cmd.extend([
                "-f", format_option,
                "--newline",  
                "--progress",
                "--no-playlist",
                "-o", output_template,
            ])
            
            if download.youtube_type == YoutubeDownloadType.AUDIO:
                cmd.extend(["--extract-audio", "--audio-format", "mp3", "--audio-quality", "192K"])
            
            # Add options for resuming if partial file exists
            cmd.append("--no-overwrites")
            
            # Add URL at the end
            cmd.append(str(download.url))
            
            print(f"Executing YouTube download command: {' '.join(cmd)}")

            # Different process handling for Windows vs other platforms
            if sys.platform == 'win32':
                # Use a different approach for Windows due to asyncio limitations
                import subprocess
                import threading
                import queue
                
                # Use standard queues for thread communication
                stdout_queue = queue.Queue()
                stderr_queue = queue.Queue()
                process_done = threading.Event()
                process = None
                process_returncode = None
                
                # Function to run in a separate thread
                def run_process():
                    nonlocal process, process_returncode
                    try:
                        # Create process with pipes
                        process = subprocess.Popen(
                            cmd,
                            stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE,
                            bufsize=0,  # Use unbuffered mode
                            text=False
                        )
                        
                        # Thread function to read from a stream
                        def read_stream(stream, output_queue):
                            try:
                                while True:
                                    data = stream.read(1024)
                                    if not data:
                                        break
                                    output_queue.put(data)
                            except Exception as e:
                                print(f"Error reading stream: {e}")
                        
                        # Create and start reader threads
                        stdout_reader = threading.Thread(
                            target=read_stream,
                            args=(process.stdout, stdout_queue)
                        )
                        stderr_reader = threading.Thread(
                            target=read_stream,
                            args=(process.stderr, stderr_queue)
                        )
                        
                        stdout_reader.daemon = True
                        stderr_reader.daemon = True
                        stdout_reader.start()
                        stderr_reader.start()
                        
                        # Wait for process to complete
                        process_returncode = process.wait()
                        
                        # Wait for reader threads to finish
                        stdout_reader.join(timeout=1)
                        stderr_reader.join(timeout=1)
                        
                        # Set completion event
                        process_done.set()
                        
                    except Exception as e:
                        print(f"Error in process thread: {e}")
                        import traceback
                        traceback.print_exc()
                        process_done.set()
                
                # Start the process in a separate thread
                process_thread = threading.Thread(target=run_process)
                process_thread.daemon = True
                process_thread.start()
                
                # Variables for progress tracking
                download_complete = False
                actual_file_path = None
                line_buffer = ""
                
                # Main monitoring loop
                while not is_cancelled:
                    # Check if we should pause
                    await paused_event.wait()
                    
                    # Check if process is done
                    if process_done.is_set():
                        if process_returncode == 0:
                            download_complete = True
                        break
                    
                    # Get output with a timeout
                    try:
                        chunk = stdout_queue.get(block=True, timeout=0.5)
                        if chunk:
                            # Process the chunk
                            line_buffer += chunk.decode('utf-8', errors='replace')
                            lines = line_buffer.split('\n')
                            line_buffer = lines.pop()  # Keep partial line
                            
                            # Process the complete lines
                            for line in lines:
                                if '[download]' in line:
                                    try:
                                        # Extract filename from destination line
                                        if 'Destination:' in line:
                                            filename = line.split('Destination:')[1].strip()
                                            actual_file_path = filename
                                            print(f"Detected output filename: {actual_file_path}")
                                        
                                        # Parse progress percentage and other stats
                                        elif "%" in line and ('ETA' in line or 'at' in line):
                                            self._parse_youtube_progress_line(download, line)
                                            # Update download status in database
                                            await self._broadcast_download_update(download_id)
                                    except Exception as e:
                                        print(f"Error parsing progress line: {e}")
                                        print(f"Line was: {line}")
                    except queue.Empty:
                        # No output available, pause briefly
                        await asyncio.sleep(0.1)
                
                # Process stderr output
                stderr_lines = []
                while not stderr_queue.empty():
                    try:
                        data = stderr_queue.get(block=False)
                        stderr_lines.append(data.decode('utf-8', errors='replace'))
                    except queue.Empty:
                        break
                
                if stderr_lines:
                    print(f"yt-dlp stderr: {''.join(stderr_lines)}")
                
                # Get final exit code
                exit_code = process_returncode
                
                # Define callbacks
                def cancel_callback():
                    nonlocal is_cancelled
                    is_cancelled = True
                    if process:
                        try:
                            process.terminate()
                        except Exception as e:
                            print(f"Error terminating process: {e}")
                
                def pause_callback():
                    if paused_event.is_set():
                        paused_event.clear()  # Pause
                        download.status = DownloadStatus.PAUSED
                        self._broadcast_download_update_sync(download_id)
                    else:
                        paused_event.set()  # Resume
                        download.status = DownloadStatus.DOWNLOADING
                        self._broadcast_download_update_sync(download_id)
            
            else:
                # For non-Windows platforms, use asyncio subprocess
                process = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE
                )

                # Variables for progress tracking
                download_complete = False
                actual_file_path = None
                line_buffer = ""
                
                # Main monitoring loop
                while not is_cancelled:
                    # Check if we should pause
                    await paused_event.wait()
                    
                    # Check if process has exited
                    if process.returncode is not None:
                        if process.returncode == 0:
                            download_complete = True
                        break
                    
                    # Read a chunk of output
                    chunk = await process.stdout.read(1024)
                    if not chunk:
                        # No more output to read
                        break
                    
                    # Process the output
                    line_buffer += chunk.decode('utf-8', errors='replace')
                    lines = line_buffer.split('\n')
                    line_buffer = lines.pop()  # Keep any partial line for next iteration
                    
                    for line in lines:
                        # Extract download progress information
                        if '[download]' in line:
                            try:
                                # Extract filename from destination line
                                if 'Destination:' in line:
                                    filename = line.split('Destination:')[1].strip()
                                    actual_file_path = filename
                                    print(f"Detected output filename: {actual_file_path}")
                                
                                # Parse progress percentage and other stats
                                elif "%" in line and ('ETA' in line or 'at' in line):
                                    self._parse_youtube_progress_line(download, line)
                                    # Update download status in database
                                    await self._broadcast_download_update(download_id)
                            except Exception as e:
                                print(f"Error parsing progress line: {e}")
                                print(f"Line was: {line}")
                
                # Process stderr output
                stderr_output = await process.stderr.read()
                if stderr_output:
                    print(f"yt-dlp stderr: {stderr_output.decode('utf-8', errors='replace')}")
                
                # Get the process return code
                exit_code = await process.wait()
                
                # Define callbacks
                def cancel_callback():
                    nonlocal is_cancelled
                    is_cancelled = True
                    if process and process.returncode is None:
                        process.terminate()
                
                def pause_callback():
                    if paused_event.is_set():
                        paused_event.clear()  # Pause
                        download.status = DownloadStatus.PAUSED
                        self._broadcast_download_update_sync(download_id)
                    else:
                        paused_event.set()  # Resume
                        download.status = DownloadStatus.DOWNLOADING
                        self._broadcast_download_update_sync(download_id)
            
            # Update the download status based on the results
            if download_complete and actual_file_path and os.path.exists(actual_file_path):
                print(f"YouTube download completed successfully: {actual_file_path}")
                download.save_path = actual_file_path
                download.size_downloaded = os.path.getsize(actual_file_path)
                download.status = DownloadStatus.COMPLETED
            elif is_cancelled:
                download.status = DownloadStatus.CANCELLED
                print(f"YouTube download was cancelled: {download.url}")
            else:
                # If the file path wasn't captured but the process exited successfully,
                # try to find the downloaded file in the output directory
                if exit_code == 0:
                    save_dir = os.path.dirname(download.save_path)
                    print(f"Looking for downloaded files in: {save_dir}")
                    
                    # Ensure the directory exists
                    os.makedirs(save_dir, exist_ok=True)
                    
                    if os.path.exists(save_dir):
                        # Look for recently created files
                        found = False
                        all_files = os.listdir(save_dir)
                        print(f"Found {len(all_files)} files in directory")
                        
                        # Try to find the file by normalizing special characters
                        expected_base = os.path.splitext(os.path.basename(download.save_path))[0]
                        expected_base_normalized = expected_base.replace("_", " ").lower()
                        print(f"Looking for file matching normalized name: {expected_base_normalized}")
                        
                        # First look for video files specifically
                        video_extensions = ['.mp4', '.mkv', '.webm', '.mp3', '.m4a']
                        
                        for file in all_files:
                            file_path = os.path.join(save_dir, file)
                            file_ext = os.path.splitext(file)[1].lower()
                            file_base = os.path.splitext(file)[0]
                            file_base_normalized = file_base.replace("_", " ").replace("：", " ").replace(":", " ").lower()
                            
                            # Check if the normalized names are similar
                            name_similarity = 0
                            for char in expected_base_normalized:
                                if char in file_base_normalized:
                                    name_similarity += 1
                            
                            name_match_percentage = name_similarity / len(expected_base_normalized) if expected_base_normalized else 0
                            
                            # Check for either a video file with similar name
                            if (os.path.isfile(file_path) and 
                                file_ext in video_extensions and
                                name_match_percentage > 0.7):  # 70% or more similarity
                                
                                # Use this file
                                download.save_path = file_path
                                download.name = os.path.basename(file_path)
                                download.size_downloaded = os.path.getsize(file_path)
                                download.size = download.size_downloaded
                                download.status = DownloadStatus.COMPLETED
                                found = True
                                print(f"Found completed YouTube download by name similarity: {file_path}")
                                break
                        
                        # If no video files found by name similarity, look for any recently created video files
                        if not found:
                            print("No video files found by name similarity, checking for any video files")
                            most_recent_file = None
                            most_recent_time = 0
                            
                            for file in all_files:
                                file_path = os.path.join(save_dir, file)
                                file_ext = os.path.splitext(file)[1].lower()
                                
                                # Skip temporary or hidden files
                                if file.startswith('.') or file.endswith('.part') or file.endswith('.temp'):
                                    continue
                                
                                # Check if it's a video file
                                if os.path.isfile(file_path) and file_ext in video_extensions:
                                    try:
                                        mtime = os.path.getmtime(file_path)
                                        print(f"Video file: {file}, Modified: {datetime.fromtimestamp(mtime)}")
                                        
                                        # Use the most recent video file
                                        if mtime > most_recent_time:
                                            most_recent_time = mtime
                                            most_recent_file = file_path
                                    except Exception as e:
                                        print(f"Error checking file {file}: {e}")
                            
                            # If we found a video file, use it
                            if most_recent_file and os.path.exists(most_recent_file):
                                download.save_path = most_recent_file
                                download.name = os.path.basename(most_recent_file)
                                download.size_downloaded = os.path.getsize(most_recent_file)
                                download.size = download.size_downloaded
                                download.status = DownloadStatus.COMPLETED
                                found = True
                                print(f"Found completed YouTube download (most recent video file): {most_recent_file}")
                        
                        if not found:
                            download.status = DownloadStatus.FAILED
                            print(f"YouTube download failed to find output file: {download.url}")
                            print(f"Files in directory: {all_files}")
                    else:
                        download.status = DownloadStatus.FAILED
                        print(f"YouTube download output directory not found: {save_dir}")
                else:
                    download.status = DownloadStatus.FAILED
                    print(f"YouTube download failed with exit code {exit_code}: {download.url}")
            
            # Final update to the frontend
            await self._broadcast_download_update(download_id)
            
        except Exception as e:
            print(f"Error in YouTube download: {str(e)}")
            import traceback
            traceback.print_exc()
            download.status = DownloadStatus.FAILED
            await self._broadcast_download_update(download_id)
            
        # Register callbacks
        download.cancel_callback = cancel_callback
        download.pause_resume_callback = pause_callback
        
    def _broadcast_download_update_sync(self, download_id: str):
        """Synchronous version of broadcast update for use in thread callbacks"""
        download = self.downloads.get(download_id)
        if not download:
            return

        # Schedule the asynchronous broadcast in the event loop
        asyncio.run_coroutine_threadsafe(
            self._broadcast_download_update(download_id), 
            asyncio.get_event_loop()
        )

    def _find_yt_dlp_command(self) -> list[str]:
        """Find the yt-dlp command on the system, returns a list of command parts ready for subprocess"""
        possible_commands = [
            ["yt-dlp"],
            ["yt-dlp.exe"],
            ["python", "-m", "yt_dlp"]
        ]
        
        # Check if yt-dlp is in the Python scripts directory
        if sys.platform == "win32":
            # Check common Python script directories on Windows
            python_paths = []
            
            # Current Python executable's directory
            if getattr(sys, 'executable', None):
                python_dir = os.path.dirname(sys.executable)
                python_paths.append(os.path.join(python_dir, 'Scripts', 'yt-dlp.exe'))
                python_paths.append(os.path.join(python_dir, 'yt-dlp.exe'))
            
            # User's directory - common pip install location
            user_profile = os.environ.get('USERPROFILE')
            if user_profile:
                python_paths.append(os.path.join(user_profile, 'AppData', 'Local', 'Programs', 'Python', 'Python*', 'Scripts', 'yt-dlp.exe'))
                python_paths.append(os.path.join(user_profile, 'AppData', 'Roaming', 'Python', 'Python*', 'Scripts', 'yt-dlp.exe'))
            
            # Add python -m yt_dlp as a fallback
            possible_commands.append([sys.executable, "-m", "yt_dlp"])
            
            # Expand glob patterns and add to possible commands
            for path in python_paths:
                if '*' in path:
                    import glob
                    for expanded_path in glob.glob(path):
                        if os.path.exists(expanded_path):
                            possible_commands.append([expanded_path])
                elif os.path.exists(path):
                    possible_commands.append([path])
        
        # Try each command
        for cmd in possible_commands:
            try:
                # Use subprocess to check if command exists
                if len(cmd) > 1:  # For commands like ["python", "-m", "yt_dlp"]
                    test_cmd = cmd.copy() + ["--version"]
                else:
                    test_cmd = cmd.copy() + ["--version"]
                    
                result = subprocess.run(test_cmd, 
                                     stdout=subprocess.PIPE, 
                                     stderr=subprocess.PIPE, 
                                     text=True, 
                                     check=False)
                    
                if result.returncode == 0:
                    version = result.stdout.strip()
                    print(f"Found yt-dlp: {' '.join(cmd)}, version: {version}")
                    return cmd
            except Exception as e:
                print(f"Error checking {' '.join(cmd)}: {e}")
                continue
        
        # If we get here, we didn't find yt-dlp
        print("WARNING: yt-dlp command not found. YouTube downloading will not work until yt-dlp is installed.")
        print("Please install yt-dlp using: pip install yt-dlp")
        return None

    def get_download(self, download_id: str) -> DownloadItem | None:
        """Get a download by ID"""
        return self.downloads.get(download_id)

    def _prepare_download_for_api(self, download: DownloadItem) -> dict:
        """Prepare a download for API response by removing non-serializable attributes"""
        download_dict = download.model_dump()
        
        # Convert datetime to ISO format
        download_dict["date_added"] = download_dict["date_added"].isoformat()
        
        # Store URL as string
        download_dict["url"] = str(download_dict["url"])
        
        # Remove callback functions which are not serializable
        download_dict.pop("cancel_callback", None)
        download_dict.pop("pause_resume_callback", None)
        
        # Calculate progress percentage
        if download.size and download.size > 0:
            progress = min(100.0, (download.size_downloaded / download.size) * 100)
        else:
            progress = 0
        download_dict["progress"] = progress
        
        return download_dict

    async def _broadcast_download_update(self, download_id: str):
        """Broadcast a download update to WebSocket clients"""
        download = self.downloads.get(download_id)
        if not download:
            return

        try:
            # Prepare download data for broadcast
            download_dict = self._prepare_download_for_api(download)
            
            # Broadcast the update
            await ws_manager.broadcast({"type": "download_update", "download": download_dict})
        except Exception as e:
            print(f"Error broadcasting download update: {str(e)}")
            # Don't let errors in broadcasting break the download process
            
    def get_downloads(
        self, category: FileCategory | None = None, status: DownloadStatus | None = None
    ) -> list[dict]:
        """Get all downloads, optionally filtered by category or status"""
        downloads = list(self.downloads.values())

        if category and category != FileCategory.ALL:
            downloads = [d for d in downloads if d.category == category]

        if status:
            downloads = [d for d in downloads if d.status == status]

        # Convert downloads to API-safe format
        return [self._prepare_download_for_api(d) for d in downloads]

    async def pause_download(self, download_id: str) -> DownloadItem | None:
        """Pause a download"""
        download = self.get_download(download_id)
        if not download or download.status != DownloadStatus.DOWNLOADING:
            return None

        task = self.tasks.get(download_id)
        if task:
            task.cancel()
            await asyncio.sleep(0.1)  # Give the task time to handle cancellation

        download.status = DownloadStatus.PAUSED
        await self._broadcast_download_update(download_id)
        return download

    async def resume_download(self, download_id: str) -> DownloadItem | None:
        """Resume a paused download"""
        download = self.get_download(download_id)
        if not download:
            return None

        # Allow resuming from failed or paused state
        if download.status not in [DownloadStatus.PAUSED, DownloadStatus.FAILED]:
            return None

        # Start a new download task
        if download.is_youtube:
            self.tasks[download_id] = asyncio.create_task(self._download_youtube(download_id))
        else:
            self.tasks[download_id] = asyncio.create_task(self._download_file(download_id))

        # Update status
        download.status = DownloadStatus.QUEUED
        await self._broadcast_download_update(download_id)
        return download

    async def delete_download(self, download_id: str, delete_file: bool = False) -> bool:
        """Delete a download and optionally the downloaded file"""
        download = self.downloads.get(download_id)
        if not download:
            return False

        # Cancel any running task
        if download_id in self.tasks and not self.tasks[download_id].done():
            self.tasks[download_id].cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.tasks[download_id]

        # Delete file if requested
        file_deleted = False
        file_error = None

        if delete_file and os.path.exists(download.save_path):
            try:
                os.remove(download.save_path)
                file_deleted = True
            except PermissionError as e:
                # File is being used by another process
                print(f"Warning: Cannot delete file {download.save_path} - it's in use: {e}")
                file_error = f"File is in use: {download.name}"
            except OSError as e:
                # Other file system errors
                print(f"Error deleting file {download.save_path}: {e}")
                file_error = f"Error deleting file: {str(e)}"

        # Create a copy of the download data for the delete notification
        download_data = {
            "id": download.id,
            "name": download.name,
            "file_deleted": file_deleted,
            "file_error": file_error,
        }

        # Remove download from dictionaries
        del self.downloads[download_id]
        if download_id in self.tasks:
            del self.tasks[download_id]

        # Save changes
        await self.save_downloads()

        # Send delete notification
        await ws_manager.broadcast(
            {"type": "delete_download", "download_id": download_id, "download": download_data}
        )

        return True

    async def pause_all(self) -> int:
        """Pause all active downloads"""
        count = 0
        for download_id, download in self.downloads.items():
            if download.status == DownloadStatus.DOWNLOADING:
                download.status = DownloadStatus.PAUSED
                count += 1
                await self.save_and_broadcast_download(download_id)

        return count

    async def open_file(self, download_id: str) -> bool:
        """Open a file with the system's default application"""
        download = self.downloads.get(download_id)
        if not download or not os.path.exists(download.save_path):
            return False

        try:
            # Different open commands based on the operating system
            if sys.platform == "win32":
                # Windows
                os.startfile(download.save_path)
            elif sys.platform == "darwin":
                # macOS
                subprocess.Popen(["open", download.save_path])
            else:
                # Linux
                subprocess.Popen(["xdg-open", download.save_path])

            return True
        except Exception as e:
            print(f"Error opening file: {e}")
            return False

    async def resume_all(self) -> int:
        """Resume all paused downloads"""
        count = 0
        for download_id, download in self.downloads.items():
            if download.status == DownloadStatus.PAUSED:
                await self.resume_download(download_id)
                count += 1

        # Broadcast all downloads after bulk operation
        await self.broadcast_all_downloads()

        return count

    def _parse_youtube_progress_line(self, download, line):
        """Parse a yt-dlp progress line and update download object
        
        Args:
            download: DownloadItem object to update
            line: Progress line to parse
            
        Returns:
            bool: True if successfully parsed, False otherwise
        """
        try:
            # First check if it's a progress line with percentage
            if "%" in line and ('ETA' in line or 'at' in line):
                parts = line.split()
                
                # Find the percentage value
                percent_parts = [p for p in parts if p.endswith('%')]
                if not percent_parts:
                    return False
                    
                percent_str = percent_parts[0].rstrip('%')
                try:
                    percent = float(percent_str)
                    # Update size_downloaded based on percentage instead of setting progress directly
                    if download.size:
                        download.size_downloaded = int(download.size * (percent / 100.0))
                    else:
                        # If size is unknown, try to parse it from the line
                        size_parts = [p for i, p in enumerate(parts) if i > 0 and 'of' in parts[i-1]]
                        if size_parts:
                            size_str = size_parts[0]
                            if '~' in size_str:
                                size_str = size_str.replace('~', '').strip()
                            
                            # Parse different size formats (MiB, KiB, etc.)
                            if 'MiB' in size_str:
                                size_mb = float(size_str.replace('MiB', '').strip())
                                download.size = int(size_mb * 1024 * 1024)
                            elif 'KiB' in size_str:
                                size_kb = float(size_str.replace('KiB', '').strip())
                                download.size = int(size_kb * 1024)
                            elif 'GiB' in size_str:
                                size_gb = float(size_str.replace('GiB', '').strip())
                                download.size = int(size_gb * 1024 * 1024 * 1024)
                            else:
                                # Try generic parsing
                                download.size = int(float(size_str))
                                
                            # Calculate download size from percentage
                            download.size_downloaded = int(download.size * (percent / 100.0))
                except (ValueError, TypeError) as e:
                    print(f"Error parsing percentage: {e}, value: {percent_str}")
                    return False
                
                # Parse speed if available
                try:
                    speed_index = [i for i, p in enumerate(parts) if '/s' in p]
                    if speed_index:
                        speed_str = parts[speed_index[0]]
                        # Extract the numeric part and unit part
                        # Handle formats like "68.60KiB/s", "1.45MiB/s", "68.60K/s", "1.45M/s"
                        if 'KiB/s' in speed_str or 'K/s' in speed_str:
                            speed_value = float(speed_str.replace('KiB/s', '').replace('K/s', '').strip())
                            download.speed = int(speed_value * 1024)
                        elif 'MiB/s' in speed_str or 'M/s' in speed_str:
                            speed_value = float(speed_str.replace('MiB/s', '').replace('M/s', '').strip())
                            download.speed = int(speed_value * 1024 * 1024)
                        elif 'GiB/s' in speed_str or 'G/s' in speed_str:
                            speed_value = float(speed_str.replace('GiB/s', '').replace('G/s', '').strip())
                            download.speed = int(speed_value * 1024 * 1024 * 1024)
                        elif 'B/s' in speed_str:
                            speed_value = float(speed_str.replace('B/s', '').strip())
                            download.speed = int(speed_value)
                except (ValueError, TypeError, IndexError) as e:
                    print(f"Error parsing speed: {e}, speed_str: {line}")
                
                # Parse ETA if available
                try:
                    eta_index = [i for i, p in enumerate(parts) if p == 'ETA']
                    if eta_index and eta_index[0] < len(parts) - 1:
                        eta_str = parts[eta_index[0] + 1]
                        if ':' in eta_str:
                            # Parse HH:MM:SS or MM:SS format
                            time_parts = eta_str.split(':')
                            seconds = 0
                            if len(time_parts) == 3:  # HH:MM:SS
                                seconds = int(time_parts[0]) * 3600 + int(time_parts[1]) * 60 + int(time_parts[2])
                            elif len(time_parts) == 2:  # MM:SS
                                seconds = int(time_parts[0]) * 60 + int(time_parts[1])
                            
                            download.time_left = seconds
                except (ValueError, TypeError, IndexError) as e:
                    print(f"Error parsing ETA: {e}")
                
                return True
                
            # Check if it's a destination line
            elif 'Destination:' in line:
                filename = line.split('Destination:')[1].strip()
                print(f"Detected output filename: {filename}")
                return True
                
            return False
        except Exception as e:
            print(f"Error parsing progress line: {e}")
            print(f"Line was: {line}")
            return False

    async def _send_notification(self, download_id: str, title: str, message: str, notification_type: str = "info"):
        """Send a notification to the client about a download event"""
        download = self.downloads.get(download_id)
        if not download:
            return
        
        # Create notification payload
        notification = {
            "type": "notification",
            "notification_type": notification_type,
            "title": title,
            "message": message,
            "download_id": download_id,
            "download_name": download.name,
            "timestamp": datetime.now().isoformat()
        }
        
        # Broadcast to all connected clients
        await ws_manager.broadcast(notification)


# Singleton instance
download_manager = DownloadManager()
