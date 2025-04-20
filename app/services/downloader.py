import asyncio
import contextlib
import json
import mimetypes
import os
import subprocess
import sys
import uuid
from datetime import datetime
from urllib.parse import urlparse

import aiofiles
import aiohttp

from app.models.download import CreateDownloadRequest, DownloadItem, DownloadStatus, FileCategory
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

    async def get_file_info(self, url: str) -> tuple[str | None, int | None]:
        """Get filename and size from the URL"""
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
        download_dict = download.model_dump()
        download_dict["date_added"] = download_dict["date_added"].isoformat()
        download_dict["url"] = str(download_dict["url"])

        # Calculate progress percentage
        if download.size and download.size > 0:
            progress = min(100.0, (download.size_downloaded / download.size) * 100)
        else:
            progress = 0
        download_dict["progress"] = progress

        # Broadcast the update
        await ws_manager.broadcast({"type": update_type, "download": download_dict})

    async def broadcast_all_downloads(self):
        """Broadcast all downloads to WebSocket clients"""
        downloads_data = []
        for _download_id, download in self.downloads.items():
            # Convert model to dict and handle non-serializable types
            download_dict = download.model_dump()

            # Convert datetime to ISO format
            download_dict["date_added"] = download_dict["date_added"].isoformat()

            # Store URL as string
            download_dict["url"] = str(download_dict["url"])

            # Calculate progress percentage
            if download.size and download.size > 0:
                progress = min(100.0, (download.size_downloaded / download.size) * 100)
            else:
                progress = 0
            download_dict["progress"] = progress

            downloads_data.append(download_dict)

        # Broadcast the list of downloads
        await ws_manager.broadcast(
            {"type": "downloads_list", "downloads": downloads_data, "total": len(downloads_data)}
        )

    async def add_download(self, request: CreateDownloadRequest) -> DownloadItem:
        """Add a new download and start it"""
        # Generate download ID
        download_id = str(uuid.uuid4())

        # Get file info if not provided
        filename, size = await self.get_file_info(str(request.url))
        filename = request.filename or filename or f"download_{download_id}"

        # Detect category if not provided
        category = request.category or self._detect_category(filename)

        # Determine save path
        category_dir = os.path.join(self.download_dir, category.value)
        save_path = request.save_path or os.path.join(category_dir, filename)

        # Create download object
        download = DownloadItem(
            id=download_id,
            name=filename,
            url=request.url,
            size=size,
            status=DownloadStatus.QUEUED,
            date_added=datetime.now(),
            save_path=save_path,
            category=category,
        )

        # Store download
        self.downloads[download_id] = download

        # Start the download
        self.tasks[download_id] = asyncio.create_task(self._download_file(download_id))

        # Save and broadcast the new download
        await self.save_and_broadcast_download(download_id, "new_download")

        return download

    async def _download_file(self, download_id: str) -> None:
        """Background task to download a file"""
        download = self.downloads[download_id]
        download.status = DownloadStatus.DOWNLOADING

        # Speed calculation variables
        speed_window = []  # Store recent chunk sizes for speed calculation
        speed_window_size = 5  # Number of recent chunks to consider for speed smoothing

        # Broadcast status change
        await self.save_and_broadcast_download(download_id)

        try:
            # Make sure the directory exists
            os.makedirs(os.path.dirname(download.save_path), exist_ok=True)

            # Check if file exists and is partially downloaded
            start_from = 0
            mode = "wb"
            if os.path.exists(download.save_path) and (
                download.size is not None and download.status == DownloadStatus.PAUSED
            ):
                current_size = os.path.getsize(download.save_path)
                if 0 < current_size < download.size:
                    # Resume download from the current file size
                    start_from = current_size
                    mode = "ab"  # Append to existing file
                    download.size_downloaded = current_size

            headers = {}
            if start_from > 0:
                # Add Range header to request only the remaining part
                headers["Range"] = f"bytes={start_from}-"
                print(f"Resuming download from byte {start_from}")

            async with (
                aiohttp.ClientSession() as session,
                session.get(str(download.url), headers=headers, allow_redirects=True) as response,
                aiofiles.open(download.save_path, mode) as f,
            ):
                if not (200 <= response.status < 300 or response.status == 206):
                    download.status = DownloadStatus.FAILED
                    await self.save_and_broadcast_download(download_id)
                    return

                # Update size if not known
                if download.size is None:
                    content_length = response.headers.get("Content-Length")
                    if content_length:
                        if start_from > 0 and response.status == 206:
                            # For partial downloads, content-length is the size of the\
                            #  remaining part
                            download.size = start_from + int(content_length)
                        else:
                            download.size = int(content_length)

                # Start time for speed calculation
                start_time = asyncio.get_event_loop().time()
                last_update = start_time
                last_save = start_time
                last_broadcast = start_time
                chunk_size = 8192
                bytes_since_last_update = 0

                # Download in chunks and track progress
                async for chunk in response.content.iter_chunked(chunk_size):
                    if download.status == DownloadStatus.PAUSED:
                        # Wait until unpaused
                        while download.status == DownloadStatus.PAUSED:
                            await asyncio.sleep(1)
                        # Reset time and speed when resuming
                        start_time = asyncio.get_event_loop().time()
                        last_update = start_time
                        last_save = start_time
                        last_broadcast = start_time
                        download.speed = 0
                        bytes_since_last_update = 0
                        speed_window = []

                    # Write chunk to file
                    await f.write(chunk)
                    download.size_downloaded += len(chunk)
                    bytes_since_last_update += len(chunk)

                    # Update speed and time left every second
                    current_time = asyncio.get_event_loop().time()
                    if current_time - last_update >= 1.0:
                        # Calculate instantaneous speed for this update interval
                        interval = current_time - last_update
                        instantaneous_speed = int(bytes_since_last_update / interval)

                        # Add to sliding window
                        speed_window.append(instantaneous_speed)

                        # Keep window at the desired size
                        if len(speed_window) > speed_window_size:
                            speed_window.pop(0)

                        # Calculate average speed from window
                        if speed_window:
                            # Use weighted average (more recent speeds have higher weight)
                            weights = [i + 1 for i in range(len(speed_window))]
                            total_weight = sum(weights)
                            weighted_speed = (
                                sum(s * w for s, w in zip(speed_window, weights, strict=False))
                                / total_weight
                            )
                            download.speed = int(weighted_speed)

                        # Calculate time left
                        if download.speed > 0 and download.size:
                            remaining_bytes = download.size - download.size_downloaded
                            download.time_left = int(remaining_bytes / download.speed)

                        # Reset counter for next interval
                        bytes_since_last_update = 0
                        last_update = current_time

                    # Broadcast progress every 2 seconds
                    if current_time - last_broadcast >= 2.0:
                        await self.save_and_broadcast_download(download_id)
                        last_broadcast = current_time

                    # Save progress to disk periodically (every 10 seconds)
                    if current_time - last_save >= 10.0:
                        await self.save_downloads()
                        last_save = current_time

            # Mark as completed
            download.status = DownloadStatus.COMPLETED
            download.time_left = 0
            download.speed = 0
            await self.save_and_broadcast_download(download_id)

        except asyncio.CancelledError:
            # Download was cancelled
            download.status = DownloadStatus.PAUSED
            await self.save_and_broadcast_download(download_id)
        except Exception as e:
            # Download failed
            print(f"Download failed: {e}")
            download.status = DownloadStatus.FAILED
            await self.save_and_broadcast_download(download_id)

    def get_download(self, download_id: str) -> DownloadItem | None:
        """Get a download by ID"""
        return self.downloads.get(download_id)

    def get_downloads(
        self, category: FileCategory | None = None, status: DownloadStatus | None = None
    ) -> list[DownloadItem]:
        """Get all downloads, optionally filtered by category or status"""
        downloads = list(self.downloads.values())

        if category and category != FileCategory.ALL:
            downloads = [d for d in downloads if d.category == category]

        if status:
            downloads = [d for d in downloads if d.status == status]

        return downloads

    async def pause_download(self, download_id: str) -> DownloadItem | None:
        """Pause a download"""
        download = self.downloads.get(download_id)
        if not download or download.status != DownloadStatus.DOWNLOADING:
            return None

        download.status = DownloadStatus.PAUSED
        await self.save_and_broadcast_download(download_id)
        return download

    async def resume_download(self, download_id: str) -> DownloadItem | None:
        """Resume a paused download"""
        download = self.downloads.get(download_id)
        if not download or download.status != DownloadStatus.PAUSED:
            return None

        download.status = DownloadStatus.DOWNLOADING
        download.speed = 0  # Reset speed when resuming
        download.time_left = None  # Reset time left until we have accurate calculations

        # If there's no existing task, create a new one
        if download_id not in self.tasks or self.tasks[download_id].done():
            self.tasks[download_id] = asyncio.create_task(self._download_file(download_id))

        # Broadcast the status change
        await self.save_and_broadcast_download(download_id)

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


# Singleton instance
download_manager = DownloadManager()
