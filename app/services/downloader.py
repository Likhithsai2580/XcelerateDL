import asyncio
import calendar
import contextlib
import json
import mimetypes
import os
import re
import subprocess
import sys
import uuid
from datetime import UTC, datetime, timedelta
from urllib.parse import urlparse

import aiofiles
import aiohttp

from app.models.download import (
    BandwidthAllocationMode,
    BandwidthSettings,
    CreateDownloadRequest,
    DownloadItem,
    DownloadPriority,
    DownloadStatus,
    FileCategory,
    RecurrenceType,
    ScheduleSettings,
    YoutubeDownloadType,
)
from app.services.ws_manager import manager as ws_manager


class DownloadManager:
    def __init__(
        self,
        download_dir: str = "./downloads",
        storage_file: str = "./downloads/download_data.json",
        bandwidth_file: str = "./downloads/bandwidth_settings.json",
    ):
        """Initialize the download manager"""
        self.downloads = {}
        self.tasks = {}
        self.download_dir = os.path.abspath(download_dir)
        self.storage_file = os.path.abspath(storage_file)
        self.bandwidth_file = os.path.abspath(bandwidth_file)
        self.bandwidth_settings = BandwidthSettings(
            total_bandwidth=10 * 1024 * 1024,  # Default: 10 MB/s
            allocation_mode=BandwidthAllocationMode.EQUAL,
        )

        # Scheduler settings
        self.scheduler_check_interval = 60  # Default check interval in seconds
        self.scheduler_task = None
        self.scheduler_failed_downloads = {}  # track failed scheduled downloads for retry

        # Create the download directory if it doesn't exist
        os.makedirs(self.download_dir, exist_ok=True)

        # Create category directories
        for category in FileCategory:
            if category != FileCategory.ALL:
                os.makedirs(os.path.join(self.download_dir, category.value), exist_ok=True)

    async def initialize(self):
        """Load saved downloads and resume interrupted ones"""
        await self.load_downloads()
        await self.load_bandwidth_settings()

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
            if (
                download_id in self.downloads
                and self.downloads[download_id].status != DownloadStatus.SCHEDULED
            ):
                if self.downloads[download_id].is_youtube:
                    self.tasks[download_id] = asyncio.create_task(
                        self._download_youtube(download_id)
                    )
                else:
                    self.tasks[download_id] = asyncio.create_task(self._download_file(download_id))

        print(
            f"Restored {len(self.downloads)} downloads, resumed {len(downloads_to_resume)} downloads"
        )

        # Start periodic saving
        self._start_autosave()

        # Start scheduler for scheduled downloads
        self._start_scheduler()

        return len(downloads_to_resume)

    def _start_autosave(self, interval_seconds: int = 30):
        """Start an automatic save task to run periodically"""

        async def autosave_task():
            while True:
                await asyncio.sleep(interval_seconds)
                await self.save_downloads()
                await self.save_bandwidth_settings()

        asyncio.create_task(autosave_task())

    def _start_scheduler(self, check_interval_seconds: int = 60):
        """Start the scheduler for scheduled downloads"""

        async def scheduler_task():
            while True:
                try:
                    await asyncio.sleep(check_interval_seconds)
                    print(
                        f"Running scheduler check at {datetime.now().isoformat()} with interval {check_interval_seconds}s"
                    )
                    await self._process_scheduled_downloads()
                    await self._process_failed_scheduled_downloads()
                except Exception as e:
                    print(f"ERROR in scheduler task: {str(e)}")
                    # Don't let exceptions stop the scheduler - log and continue
                    import traceback

                    traceback.print_exc()
                    await asyncio.sleep(5)  # Wait a bit before trying again after error

        if self.scheduler_task is None or self.scheduler_task.done():
            self.scheduler_task = asyncio.create_task(scheduler_task())
            print(f"Scheduler task started with interval {check_interval_seconds}s")

    async def _process_failed_scheduled_downloads(self):
        """Process any scheduled downloads that failed and need retry"""
        current_time = datetime.now()

        # Process each failed scheduled download
        for download_id in list(self.scheduler_failed_downloads.keys()):
            retry_info = self.scheduler_failed_downloads[download_id]
            retry_time = retry_info.get("retry_time")

            # Skip if not time to retry yet
            if not retry_time or retry_time > current_time:
                continue

            # Get the download
            download = self.downloads.get(download_id)
            if not download or not download.schedule:
                # Download no longer exists or doesn't have schedule, remove from retry list
                self.scheduler_failed_downloads.pop(download_id, None)
                continue

            # Check if we've exceeded max retries
            if download.schedule.current_schedule_retries >= download.schedule.max_schedule_retries:
                # Max retries exceeded, clear from retry list
                self.scheduler_failed_downloads.pop(download_id, None)

                # Send notification about failed scheduled download
                await self._send_notification(
                    download_id,
                    "Scheduled Download Failed",
                    f"The scheduled download '{download.name}' has failed after {download.schedule.max_schedule_retries} retry attempts.",
                    "error",
                )
                continue

            # Increment retry counter
            download.schedule.current_schedule_retries += 1

            # Start the download
            download.status = DownloadStatus.QUEUED
            if download.is_youtube:
                self.tasks[download_id] = asyncio.create_task(self._download_youtube(download_id))
            else:
                self.tasks[download_id] = asyncio.create_task(self._download_file(download_id))

            # Remove from retry list
            self.scheduler_failed_downloads.pop(download_id, None)

            # Send notification
            await self._send_notification(
                download_id,
                "Scheduled Download Retry",
                f"Retrying scheduled download '{download.name}' (Attempt {download.schedule.current_schedule_retries}/{download.schedule.max_schedule_retries})",
                "info",
            )

            # Broadcast the update
            await self._broadcast_download_update(download_id)

    async def _process_scheduled_downloads(self):
        """Process any downloads that are scheduled to start now"""
        try:
            # Ensure current_time is timezone-aware (UTC)
            current_time = datetime.now(UTC)
            print(f"Processing scheduled downloads at {current_time.isoformat()}")

            # Track ongoing downloads to avoid starting too many at once
            active_download_count = sum(
                1 for d in self.downloads.values() if d.status == DownloadStatus.DOWNLOADING
            )
            print(f"Current active downloads: {active_download_count}")

            # Get all scheduled downloads
            scheduled_downloads = [
                (download_id, download)
                for download_id, download in self.downloads.items()
                if (
                    download.status == DownloadStatus.SCHEDULED
                    and download.schedule
                    and download.schedule.scheduled_time
                )
            ]
            print(f"Found {len(scheduled_downloads)} scheduled downloads")

            # Sort scheduled downloads by priority, scheduled time, and type
            # This ensures higher priority downloads start first when multiple are scheduled
            scheduled_downloads.sort(
                key=lambda item: (
                    # First by scheduled time (earlier first)
                    item[1].schedule.scheduled_time,
                    # Then by priority (HIGH = 0, NORMAL = 1, LOW = 2)
                    0
                    if item[1].priority == DownloadPriority.HIGH
                    else (1 if item[1].priority == DownloadPriority.NORMAL else 2),
                    # Then by type (non-YouTube first as they're usually simpler)
                    0 if not item[1].is_youtube else 1,
                )
            )

            # Process each scheduled download
            for download_id, download in scheduled_downloads:
                try:
                    # Skip if bandwidth settings disallow more downloads
                    if (
                        active_download_count >= self.bandwidth_settings.max_concurrent_downloads
                        and not download.schedule.priority_boost  # Allow priority boost to bypass limit
                    ):
                        print(
                            f"Skipping scheduled download {download_id} - too many active downloads"
                        )
                        continue

                    # Get scheduled time (ensure it's properly compared using timezone)
                    scheduled_time = download.schedule.scheduled_time
                    print(
                        f"Processing scheduled download {download_id} - Scheduled: {scheduled_time.isoformat()}, Current: {current_time.isoformat()}"
                    )

                    # Proper timezone handling for comparison
                    should_start_now = False
                    if hasattr(scheduled_time, "tzinfo") and scheduled_time.tzinfo is not None:
                        # Convert both times to UTC for comparison
                        utc_scheduled_time = scheduled_time.astimezone(UTC)

                        # Only start if current time is AFTER or EQUAL TO scheduled time
                        should_start_now = current_time >= utc_scheduled_time

                        print(
                            f"UTC comparison - Current: {current_time.isoformat()}, Scheduled: {utc_scheduled_time.isoformat()}, Should start: {should_start_now}"
                        )
                    else:
                        # Make naive time timezone-aware by assuming it's in UTC
                        utc_scheduled_time = scheduled_time.replace(tzinfo=UTC)

                        # Only start if current time is AFTER or EQUAL TO scheduled time
                        should_start_now = current_time >= utc_scheduled_time

                        print(
                            f"Converted naive time to UTC: {utc_scheduled_time.isoformat()}, Should start: {should_start_now}"
                        )

                    # Check if it's time to start this download
                    if should_start_now:
                        # Handle recurrence if set
                        if download.schedule.recurrence:
                            # For recurrence, check if we should process based on recurrence type
                            if download.schedule.recurrence == RecurrenceType.DAILY:
                                # For daily, we always process and calculate next run
                                pass  # Process normally and update time later
                            elif download.schedule.recurrence == RecurrenceType.WEEKLY:
                                # For weekly, only process if current day is in days_of_week
                                if (
                                    not download.schedule.days_of_week
                                    or current_time.weekday() not in download.schedule.days_of_week
                                ):
                                    print(
                                        f"Skipping weekly download {download_id} - not scheduled for today"
                                    )
                                    continue
                            elif download.schedule.recurrence == RecurrenceType.MONTHLY:
                                # For monthly, only process if current day matches day_of_month
                                day_of_month = download.schedule.day_of_month or scheduled_time.day
                                if current_time.day != day_of_month:
                                    print(
                                        f"Skipping monthly download {download_id} - not scheduled for today"
                                    )
                                    continue

                        # It's time to start this download
                        print(f"Starting scheduled download: {download.name} (ID: {download_id})")

                        # Update status and apply priority boost if enabled
                        download.status = DownloadStatus.QUEUED
                        if (
                            download.schedule.priority_boost
                            and download.priority != DownloadPriority.HIGH
                        ):
                            download.priority = DownloadPriority.HIGH

                        # Start the download task
                        try:
                            if download.is_youtube:
                                self.tasks[download_id] = asyncio.create_task(
                                    self._download_youtube(download_id)
                                )
                            else:
                                self.tasks[download_id] = asyncio.create_task(
                                    self._download_file(download_id)
                                )
                        except Exception as e:
                            print(f"Error starting download task for {download_id}: {e}")

                        # Handle recurring downloads by updating the next scheduled time
                        if download.schedule.recurrence:
                            try:
                                # Calculate next scheduled time based on recurrence type
                                next_time = self._calculate_next_scheduled_time(
                                    download.schedule, current_time
                                )

                                # Clone the download for the next occurrence
                                next_download = self._clone_download_for_next_occurrence(
                                    download, next_time
                                )

                                # Add the cloned download
                                self.downloads[next_download.id] = next_download
                                print(
                                    f"Created next occurrence of recurring download: {next_download.id} at {next_time.isoformat()}"
                                )
                            except Exception as e:
                                print(f"Error handling recurrence for download {download_id}: {e}")

                        # Increment active download count
                        active_download_count += 1

                        # Broadcast the update
                        await self._broadcast_download_update(download_id)

                        # Send notification if enabled
                        if download.schedule.notify_on_start:
                            await self._send_notification(
                                download_id,
                                "Scheduled Download Started",
                                f"The scheduled download '{download.name}' has started.",
                                "info",
                            )
                except Exception as e:
                    print(f"Error processing scheduled download {download_id}: {e}")
                    import traceback

                    traceback.print_exc()
        except Exception as e:
            print(f"Critical error in _process_scheduled_downloads: {e}")
            import traceback

            traceback.print_exc()

    async def save_downloads(self):
        """Save downloads to a JSON file"""
        # Create a directory for the storage file if it doesn't exist
        os.makedirs(os.path.dirname(self.storage_file), exist_ok=True)

        # Convert downloads to serializable format
        downloads_data = {}
        for download_id, download in self.downloads.items():
            try:
                # Convert model to dict and handle non-serializable types
                download_dict = download.model_dump()

                # Convert datetime to ISO format
                download_dict["date_added"] = download_dict["date_added"].isoformat()

                # Convert scheduled_time to ISO format if it exists
                if download_dict.get("schedule") and download_dict["schedule"].get(
                    "scheduled_time"
                ):
                    download_dict["schedule"]["scheduled_time"] = download_dict["schedule"][
                        "scheduled_time"
                    ].isoformat()

                # Store URL as string
                download_dict["url"] = str(download_dict["url"])

                # Remove callback functions which are not serializable
                download_dict.pop("cancel_callback", None)
                download_dict.pop("pause_resume_callback", None)

                downloads_data[download_id] = download_dict
            except Exception as e:
                print(f"Error serializing download {download_id}: {e}")
                continue

        try:
            async with aiofiles.open(self.storage_file, "w") as f:
                await f.write(json.dumps(downloads_data, indent=2))
            return True
        except Exception as e:
            print(f"Error saving downloads: {e}")
            return False

    async def save_bandwidth_settings(self):
        """Save bandwidth settings to a JSON file"""
        # Create a directory for the storage file if it doesn't exist
        os.makedirs(os.path.dirname(self.bandwidth_file), exist_ok=True)

        try:
            # Convert model to dict
            settings_dict = self.bandwidth_settings.model_dump()

            async with aiofiles.open(self.bandwidth_file, "w") as f:
                await f.write(json.dumps(settings_dict, indent=2))
            return True
        except Exception as e:
            print(f"Error saving bandwidth settings: {e}")
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

                # Convert scheduled_time if it exists
                if download_dict.get("schedule") and download_dict["schedule"].get(
                    "scheduled_time"
                ):
                    download_dict["schedule"]["scheduled_time"] = datetime.fromisoformat(
                        download_dict["schedule"]["scheduled_time"]
                    )

                # Create DownloadItem from dict
                download = DownloadItem(**download_dict)
                self.downloads[download_id] = download

            return True
        except Exception as e:
            print(f"Error loading downloads: {e}")
            return False

    async def load_bandwidth_settings(self):
        """Load bandwidth settings from a JSON file"""
        if not os.path.exists(self.bandwidth_file):
            # Default settings already set in __init__
            return False

        try:
            async with aiofiles.open(self.bandwidth_file) as f:
                content = await f.read()
                settings_data = json.loads(content)

            # Create BandwidthSettings from dict
            self.bandwidth_settings = BandwidthSettings(**settings_data)
            return True
        except Exception as e:
            print(f"Error loading bandwidth settings: {e}")
            return False

    async def update_bandwidth_settings(self, settings: BandwidthSettings) -> bool:
        """Update the bandwidth settings"""
        self.bandwidth_settings = settings

        # Update max_speed for all active downloads based on new settings
        await self._recalculate_bandwidth_allocation()

        # Save the new settings
        return await self.save_bandwidth_settings()

    async def _recalculate_bandwidth_allocation(self):
        """Recalculate the bandwidth allocation for all active downloads"""
        # Get list of active downloads
        active_downloads = [
            download_id
            for download_id, download in self.downloads.items()
            if download.status == DownloadStatus.DOWNLOADING
        ]

        if not active_downloads:
            return

        # Calculate bandwidth allocation based on the allocation mode
        if self.bandwidth_settings.allocation_mode == BandwidthAllocationMode.EQUAL:
            # Equal share for all active downloads
            per_download_bandwidth = self.bandwidth_settings.total_bandwidth // len(
                active_downloads
            )

            for download_id in active_downloads:
                download = self.downloads[download_id]
                if download.bandwidth_allocation is not None:
                    # User has specified a custom allocation for this download
                    download.max_speed = int(
                        self.bandwidth_settings.total_bandwidth
                        * download.bandwidth_allocation
                        / 100
                    )
                else:
                    download.max_speed = per_download_bandwidth

        elif self.bandwidth_settings.allocation_mode == BandwidthAllocationMode.PRIORITY:
            # Allocate based on priority levels
            # Calculate total priority weight
            priority_counts = {
                DownloadPriority.LOW: 0,
                DownloadPriority.NORMAL: 0,
                DownloadPriority.HIGH: 0,
            }

            for download_id in active_downloads:
                priority = self.downloads[download_id].priority
                priority_counts[priority] += 1

            # Assign weights: High=4, Normal=2, Low=1
            priority_weights = {
                DownloadPriority.LOW: 1,
                DownloadPriority.NORMAL: 2,
                DownloadPriority.HIGH: 4,
            }

            # Calculate total weight
            total_weight = sum(priority_weights[p] * count for p, count in priority_counts.items())

            if total_weight > 0:
                # Calculate bandwidth per weight unit
                bandwidth_per_weight = self.bandwidth_settings.total_bandwidth / total_weight

                # Set speed limits based on priority
                for download_id in active_downloads:
                    download = self.downloads[download_id]
                    if download.bandwidth_allocation is not None:
                        # User has specified a custom allocation for this download
                        download.max_speed = int(
                            self.bandwidth_settings.total_bandwidth
                            * download.bandwidth_allocation
                            / 100
                        )
                    else:
                        weight = priority_weights[download.priority]
                        download.max_speed = int(bandwidth_per_weight * weight)

        elif self.bandwidth_settings.allocation_mode == BandwidthAllocationMode.CUSTOM:
            # Use custom percentages from settings
            for download_id in active_downloads:
                if download_id in self.bandwidth_settings.custom_allocations:
                    percentage = self.bandwidth_settings.custom_allocations[download_id]
                    self.downloads[download_id].max_speed = int(
                        self.bandwidth_settings.total_bandwidth * percentage / 100
                    )
                else:
                    # Default to equal share for downloads without specific allocation
                    default_percentage = (
                        100 - sum(self.bandwidth_settings.custom_allocations.values())
                    ) / (len(active_downloads) - len(self.bandwidth_settings.custom_allocations))
                    if default_percentage > 0:
                        self.downloads[download_id].max_speed = int(
                            self.bandwidth_settings.total_bandwidth * default_percentage / 100
                        )
                    else:
                        # Fallback if we can't allocate bandwidth evenly
                        self.downloads[download_id].max_speed = 1024 * 1024  # 1 MB/s default

        # Apply the new speed limits
        for download_id in active_downloads:
            download = self.downloads[download_id]
            if download.pause_resume_callback:
                # Let the download know its speed has changed
                await download.pause_resume_callback(paused=False)

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

        # Comprehensive pattern matching for YouTube URL formats
        youtube_patterns = [
            r"^(https?://)?(www\.)?(youtube\.com|youtu\.be)",
            r"youtube\.com/watch\?v=",
            r"youtu\.be/",
            r"youtube\.com/shorts/",
            r"youtube\.com/v/",
            r"youtube\.com/embed/",
            r"youtube\.com/playlist\?list=",
            r"youtube\.com/channel/",
            r"youtube\.com/user/",
            r"youtube\.com/c/",
        ]

        for pattern in youtube_patterns:
            if re.search(pattern, url_str):
                print(f"Detected YouTube URL: {url}")
                return True

        # Check for youtube.com domain with any parameters
        parsed_url = urlparse(url_str)
        if parsed_url.netloc in ["youtube.com", "www.youtube.com", "youtu.be", "www.youtu.be"]:
            print(f"Detected YouTube URL (by domain): {url}")
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

            # Try a simpler fallback first - just extract the video ID from URL
            video_id = None
            simple_title = None

            if "youtu.be/" in url:
                video_id = url.split("youtu.be/")[1].split("?")[0].split("&")[0]
                simple_title = f"YouTube Video {video_id}"
            elif "youtube.com/watch" in url:
                # Try to extract from v= parameter
                match = re.search(r"v=([a-zA-Z0-9_-]+)", url)
                if match:
                    video_id = match.group(1)
                    simple_title = f"YouTube Video {video_id}"

            # If we got a video ID, we have a fallback title
            if video_id:
                print(f"Extracted YouTube video ID: {video_id}")

            # Run yt-dlp with a timeout to get complete info
            cmd = yt_dlp_cmd.copy() + ["--dump-json", "--no-playlist", "--no-warnings", url]

            print(f"Getting YouTube info using command: {' '.join(cmd)}")

            # Handle Windows-specific limitations
            if sys.platform == "win32":
                # Run the command in a thread to avoid asyncio limitations on Windows
                import queue
                import threading

                result_queue = queue.Queue()

                def run_in_thread():
                    try:
                        result = subprocess.run(
                            cmd,
                            stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE,
                            text=True,
                            check=False,
                            timeout=15,  # Add a timeout here
                        )
                        result_queue.put((result.returncode, result.stdout, result.stderr))
                    except subprocess.TimeoutExpired:
                        result_queue.put((1, "", "Command timed out"))
                    except Exception as e:
                        result_queue.put((1, "", str(e)))

                # Run the command in a separate thread
                thread = threading.Thread(target=run_in_thread)
                thread.daemon = True
                thread.start()

                # Wait for the thread to complete (with timeout)
                thread.join(timeout=20.0)  # Increased timeout

                if thread.is_alive():
                    print("Command timed out after 20 seconds")
                    if simple_title:
                        return f"{simple_title}.mp4", None, None
                    return None, None, None

                # Get the result
                returncode, stdout, stderr = result_queue.get()

                if returncode != 0:
                    error = stderr.strip() if stderr else "Unknown error"
                    print(f"Error getting YouTube info: {error}")

                    # Return the simple title if we have it
                    if simple_title:
                        return f"{simple_title}.mp4", None, None
                    return None, None, None

                info_json = stdout
            else:
                # Use asyncio to run the command on non-Windows platforms
                try:
                    # Use asyncio.wait_for to set a timeout
                    process = await asyncio.create_subprocess_exec(
                        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
                    )

                    # Set a timeout for the process
                    try:
                        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=15.0)
                    except TimeoutError:
                        print("YouTube info extraction timed out")
                        # Terminate the process
                        process.terminate()
                        try:
                            await asyncio.wait_for(process.wait(), timeout=2.0)
                        except TimeoutError:
                            # Force kill if it doesn't terminate
                            process.kill()

                        # Return the simple title if we have it
                        if simple_title:
                            return f"{simple_title}.mp4", None, None
                        return None, None, None

                    if process.returncode != 0:
                        error = stderr.decode().strip() if stderr else "Unknown error"
                        print(f"Error getting YouTube info: {error}")

                        # Return the simple title if we have it
                        if simple_title:
                            return f"{simple_title}.mp4", None, None
                        return None, None, None

                    info_json = stdout.decode()
                except Exception as e:
                    print(f"Error running YouTube info command: {e}")
                    if simple_title:
                        return f"{simple_title}.mp4", None, None
                    return None, None, None

            if not info_json or not info_json.strip():
                print("No output from yt-dlp")
                if simple_title:
                    return f"{simple_title}.mp4", None, None
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
            if simple_title:
                return f"{simple_title}.mp4", None, None
            return None, None, None
        except Exception as e:
            print(f"Error getting YouTube info: {e}")
            import traceback

            traceback.print_exc()
            if simple_title:
                return f"{simple_title}.mp4", None, None
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
        try:
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
        except RecursionError:
            print(f"Warning: Recursion detected when saving download {download_id}")
        except Exception as e:
            print(f"Error in save_and_broadcast_download: {e}")

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
        # Handle scheduling
        initial_status = DownloadStatus.QUEUED
        if request.schedule and request.schedule.scheduled_time:
            # If scheduled for future, mark as SCHEDULED
            scheduled_time = request.schedule.scheduled_time
            now = datetime.now(UTC)  # Make current time timezone-aware (UTC)

            print(
                f"Current time (UTC): {now.isoformat()}, Scheduled time: {scheduled_time.isoformat()}"
            )

            # Convert scheduled_time to UTC if it has timezone info
            if hasattr(scheduled_time, "tzinfo") and scheduled_time.tzinfo is not None:
                # Convert scheduled time to UTC
                utc_scheduled_time = scheduled_time.astimezone(UTC)

                print(
                    f"UTC comparison - Now: {now.isoformat()}, Scheduled: {utc_scheduled_time.isoformat()}"
                )

                # Only queue immediately if current time is AFTER or EQUAL TO scheduled time
                if now >= utc_scheduled_time:
                    print(
                        f"Keeping status as QUEUED: Current time {now} is after or equal to scheduled time {utc_scheduled_time}"
                    )
                else:
                    print(
                        f"Setting status to SCHEDULED: Current time {now} is before scheduled time {utc_scheduled_time}"
                    )
                    initial_status = DownloadStatus.SCHEDULED
            else:
                # Make naive time timezone-aware by assuming it's in UTC
                utc_scheduled_time = scheduled_time.replace(tzinfo=UTC)

                # Only queue immediately if current time is AFTER or EQUAL TO scheduled time
                if now >= utc_scheduled_time:
                    print(
                        f"Keeping status as QUEUED: Current time {now} is after or equal to scheduled time {utc_scheduled_time}"
                    )
                else:
                    print(
                        f"Setting status to SCHEDULED: Current time {now} is before scheduled time {utc_scheduled_time}"
                    )
                    initial_status = DownloadStatus.SCHEDULED

        print(f"Initial status for download: {initial_status.value}")

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
            status=initial_status,
            speed=0,
            time_left=None,
            date_added=datetime.now(),
            category=category,
            is_youtube=request.is_youtube,
            youtube_type=request.youtube_type,
            priority=request.priority,
            max_speed=request.max_speed,
            max_retries=request.max_retries,
            schedule=request.schedule,
            bandwidth_allocation=request.bandwidth_allocation,
            tags=request.tags if request.tags else [],
        )

        # Save to our dictionary of downloads
        self.downloads[download_id] = download

        # Start the download task if not scheduled for future
        if download.status != DownloadStatus.SCHEDULED:
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
                    download.save_path = os.path.join(os.path.dirname(download.save_path), name)
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

                # Recalculate bandwidth allocation now that this download is active
                await self._recalculate_bandwidth_allocation()

                # Set up aiohttp session with timeout
                timeout = aiohttp.ClientTimeout(total=None, sock_connect=60, sock_read=60)
                async with aiohttp.ClientSession(timeout=timeout) as session:
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
                            chunk_size = 1048576  # 1MB
                            downloaded_since_update = 0
                            last_update_time = datetime.now()
                            last_downloaded = download.size_downloaded

                            # Create a pause event to handle pausing
                            pause_event = asyncio.Event()
                            pause_event.set()  # Not paused initially

                            # Variable for rate limiting
                            rate_limit = None
                            last_chunk_time = datetime.now()

                            # Set up pause/resume callback function
                            async def pause_resume_callback(paused: bool = None):
                                nonlocal rate_limit, last_chunk_time

                                if paused is None:
                                    # Toggle pause state
                                    if pause_event.is_set():
                                        pause_event.clear()
                                        download.status = DownloadStatus.PAUSED
                                        await self.save_and_broadcast_download(download_id, "pause")
                                        # Recalculate bandwidth allocation now that this download is paused
                                        await self._recalculate_bandwidth_allocation()
                                        return True
                                    else:
                                        pause_event.set()
                                        download.status = DownloadStatus.DOWNLOADING
                                        await self.save_and_broadcast_download(
                                            download_id, "resume"
                                        )
                                        # Recalculate bandwidth allocation now that this download is active
                                        await self._recalculate_bandwidth_allocation()
                                        # Reset rate limiting
                                        rate_limit = (
                                            download.max_speed if download.max_speed else None
                                        )
                                        last_chunk_time = datetime.now()
                                        return True
                                else:
                                    # Explicit pause/unpause
                                    if paused:
                                        pause_event.clear()
                                        download.status = DownloadStatus.PAUSED
                                        await self.save_and_broadcast_download(download_id, "pause")
                                        # Recalculate bandwidth allocation
                                        await self._recalculate_bandwidth_allocation()
                                        return True
                                    else:
                                        pause_event.set()
                                        download.status = DownloadStatus.DOWNLOADING
                                        await self.save_and_broadcast_download(
                                            download_id, "resume"
                                        )
                                        # Recalculate bandwidth allocation
                                        await self._recalculate_bandwidth_allocation()
                                        # Reset rate limiting
                                        rate_limit = (
                                            download.max_speed if download.max_speed else None
                                        )
                                        last_chunk_time = datetime.now()
                                        return True

                            # Set up cancel callback function
                            async def cancel_callback():
                                # Mark the download as failed so we stop the loop
                                download.status = DownloadStatus.FAILED
                                await self.save_and_broadcast_download(download_id, "cancel")
                                # Recalculate bandwidth allocation now that this download is stopped
                                await self._recalculate_bandwidth_allocation()
                                return True

                            # Set the callbacks in the download object
                            download.pause_resume_callback = pause_resume_callback
                            download.cancel_callback = cancel_callback

                            # Initial rate limit setup
                            rate_limit = download.max_speed if download.max_speed else None

                            # Read the file in chunks
                            try:
                                async for chunk in response.content.iter_chunked(chunk_size):
                                    # Check if we should pause
                                    await pause_event.wait()

                                    # Check if we've been cancelled
                                    if download.status == DownloadStatus.FAILED:
                                        raise asyncio.CancelledError("Download cancelled")

                                    # Apply rate limiting if needed
                                    if rate_limit:
                                        now = datetime.now()
                                        expected_time = len(chunk) / rate_limit  # in seconds
                                        elapsed_time = (now - last_chunk_time).total_seconds()
                                        sleep_time = max(0, expected_time - elapsed_time)

                                        if sleep_time > 0:
                                            await asyncio.sleep(sleep_time)

                                        last_chunk_time = datetime.now()

                                    # Write the chunk to file
                                    await f.write(chunk)

                                    # Update download progress
                                    download.size_downloaded += len(chunk)
                                    downloaded_since_update += len(chunk)

                                    # Update download speed and time left every few chunks
                                    if downloaded_since_update > 1048576:  # 1MB
                                        now = datetime.now()
                                        time_diff = (now - last_update_time).total_seconds()
                                        if time_diff > 0:
                                            # Calculate speed in bytes/sec
                                            download.speed = int(
                                                (download.size_downloaded - last_downloaded)
                                                / time_diff
                                            )

                                            # Update the time left estimate
                                            if (
                                                download.size
                                                and download.size > download.size_downloaded
                                                and download.speed > 0
                                            ):
                                                download.time_left = int(
                                                    (download.size - download.size_downloaded)
                                                    / download.speed
                                                )
                                            else:
                                                download.time_left = None

                                            # Reset counters
                                            last_update_time = now
                                            last_downloaded = download.size_downloaded
                                            downloaded_since_update = 0

                                            # Broadcast progress update
                                            await self._broadcast_download_update(download_id)

                                        # Periodically check if max_speed has changed
                                        if rate_limit != download.max_speed:
                                            rate_limit = (
                                                download.max_speed if download.max_speed else None
                                            )

                            except asyncio.CancelledError:
                                print(f"Download {download_id} was cancelled")
                                # Just exit the function, the download status is already set
                                download.pause_resume_callback = None
                                download.cancel_callback = None
                                # Recalculate bandwidth allocation
                                await self._recalculate_bandwidth_allocation()
                                return
                            except TimeoutError as e:
                                print(f"Download {download_id} timed out: {e}")
                                retry_count += 1
                                if retry_count <= max_retries:
                                    print(
                                        f"Retrying download {download_id} (attempt {retry_count}/{max_retries})"
                                    )
                                    await asyncio.sleep(2**retry_count)  # Exponential backoff
                                    break
                                else:
                                    # All retries failed
                                    download.status = DownloadStatus.FAILED
                                    download.pause_resume_callback = None
                                    download.cancel_callback = None
                                    await self.save_and_broadcast_download(download_id, "error")

                                    # Check if this was a scheduled download that should be retried
                                    if (
                                        download.schedule
                                        and download.schedule.retry_on_failure
                                        and download.schedule.current_schedule_retries
                                        < download.schedule.max_schedule_retries
                                    ):
                                        # Add to failed scheduled downloads for retry
                                        retry_time = datetime.now() + timedelta(
                                            minutes=download.schedule.retry_delay_minutes
                                        )
                                        self.scheduler_failed_downloads[download_id] = {
                                            "retry_time": retry_time,
                                            "attempts": download.schedule.current_schedule_retries,
                                        }

                                        # Send notification about retry
                                        await self._send_notification(
                                            download_id,
                                            "Scheduled Download Failed",
                                            f"The download '{download.name}' timed out. Retrying in {download.schedule.retry_delay_minutes} minutes.",
                                            "warning",
                                        )
                                    else:
                                        # Send standard error notification
                                        await self._send_notification(
                                            download_id,
                                            "Download Failed",
                                            f"The download '{download.name}' timed out after {max_retries} retries.",
                                            "error",
                                        )

                                    # Recalculate bandwidth allocation
                                    await self._recalculate_bandwidth_allocation()
                                    return

                            # Download completed successfully
                            download.status = DownloadStatus.COMPLETED
                            download.speed = 0
                            download.time_left = None
                            download.pause_resume_callback = None
                            download.cancel_callback = None

                            # Final progress update
                            await self.save_and_broadcast_download(download_id, "complete")

                            # Recalculate bandwidth allocation now that this download is complete
                            await self._recalculate_bandwidth_allocation()

                            # Send completion notification
                            await self._send_notification(
                                download_id,
                                "Download Complete",
                                f"The download '{download.name}' has completed successfully.",
                                "success",
                            )
                            return

            except aiohttp.ClientError as e:
                print(f"Download error: {e}")
                retry_count += 1
                if retry_count <= max_retries:
                    print(f"Retrying download {download_id} (attempt {retry_count}/{max_retries})")
                    await asyncio.sleep(2**retry_count)  # Exponential backoff
                else:
                    # All retries failed
                    download.status = DownloadStatus.FAILED
                    download.pause_resume_callback = None
                    download.cancel_callback = None
                    await self.save_and_broadcast_download(download_id, "error")

                    # Check if this was a scheduled download that should be retried
                    if (
                        download.schedule
                        and download.schedule.retry_on_failure
                        and download.schedule.current_schedule_retries
                        < download.schedule.max_schedule_retries
                    ):
                        # Add to failed scheduled downloads for retry
                        retry_time = datetime.now() + timedelta(
                            minutes=download.schedule.retry_delay_minutes
                        )
                        self.scheduler_failed_downloads[download_id] = {
                            "retry_time": retry_time,
                            "attempts": download.schedule.current_schedule_retries,
                        }

                        # Send notification about retry
                        await self._send_notification(
                            download_id,
                            "Scheduled Download Failed",
                            f"The download '{download.name}' failed. Retrying in {download.schedule.retry_delay_minutes} minutes.",
                            "warning",
                        )
                    else:
                        # Send standard error notification
                        await self._send_notification(
                            download_id,
                            "Download Failed",
                            f"The download '{download.name}' failed after {max_retries} retries: {str(e)}",
                            "error",
                        )

                    # Recalculate bandwidth allocation
                    await self._recalculate_bandwidth_allocation()
                    return

        # This should not be reached in normal operation
        print(f"Download {download_id} exited unexpectedly")

    async def _download_youtube(self, download_id: str) -> None:
        """Download a YouTube video using yt-dlp with pause/resume functionality"""
        download = self.downloads.get(download_id)
        if not download:
            print(f"Download {download_id} not found")
            return

        if not download.is_youtube:
            print(f"Download {download_id} is not a YouTube download")
            return

        # Check if we have required data
        if not download.url:
            print(f"Download {download_id} has no URL")
            download.status = DownloadStatus.FAILED
            await self._broadcast_download_update(download_id)
            return

        # Set the download status to downloading
        download.status = DownloadStatus.DOWNLOADING
        await self._broadcast_download_update(download_id)

        # Recalculate bandwidth allocation now that this download is active
        await self._recalculate_bandwidth_allocation()

        # Create output directory if it doesn't exist
        os.makedirs(os.path.dirname(download.save_path), exist_ok=True)

        # Determine the output path
        output_path = download.save_path

        # Get basic info about the video to display
        try:
            title, size, description = await self.get_youtube_info(str(download.url))
            if title:
                download.name = title

                # Update save path with proper name
                if os.path.basename(download.save_path).startswith("youtube_"):
                    extension = ".mp4"
                    if download.youtube_type == YoutubeDownloadType.AUDIO:
                        extension = ".mp3"

                    # Sanitize filename for filesystem
                    safe_title = re.sub(r'[\\/*?:"<>|]', "", title)  # Remove illegal characters
                    safe_title = re.sub(r"\s+", " ", safe_title).strip()  # Normalize whitespace
                    safe_title = safe_title[:100]  # Truncate if too long

                    output_path = os.path.join(
                        os.path.dirname(download.save_path), f"{safe_title}{extension}"
                    )
                    download.save_path = output_path
            if size:
                download.size = size
        except Exception as e:
            print(f"Error getting YouTube info: {e}")
            # Continue anyway, yt-dlp will handle it

        # Set up flags for tracking state
        is_cancelled = False
        is_paused = False
        paused_event = asyncio.Event()
        paused_event.set()  # Start in unpaused state

        # Set up callback functions
        if sys.platform == "win32":
            # For Windows, we need to run yt-dlp in a separate thread to avoid blocking
            def cancel_callback():
                nonlocal is_cancelled
                is_cancelled = True
                download.status = DownloadStatus.FAILED
                # Post to event loop to broadcast the update
                asyncio.run_coroutine_threadsafe(
                    self._broadcast_download_update(download_id), asyncio.get_event_loop()
                )
                # Recalculate bandwidth allocation
                asyncio.run_coroutine_threadsafe(
                    self._recalculate_bandwidth_allocation(), asyncio.get_event_loop()
                )
                return True

            def pause_callback(paused=None):
                nonlocal is_paused
                # Handle explicit pause/unpause if specified
                if paused is not None:
                    is_paused = paused
                else:
                    # Toggle pause state
                    is_paused = not is_paused

                if is_paused:
                    paused_event.clear()
                    download.status = DownloadStatus.PAUSED
                else:
                    paused_event.set()
                    download.status = DownloadStatus.DOWNLOADING

                # Post to event loop to broadcast the update
                asyncio.run_coroutine_threadsafe(
                    self._broadcast_download_update(download_id), asyncio.get_event_loop()
                )
                # Recalculate bandwidth allocation
                asyncio.run_coroutine_threadsafe(
                    self._recalculate_bandwidth_allocation(), asyncio.get_event_loop()
                )
                return True
        else:
            # For Unix-like systems, the process is run via asyncio
            async def cancel_callback():
                nonlocal is_cancelled
                is_cancelled = True
                download.status = DownloadStatus.FAILED
                await self._broadcast_download_update(download_id)
                # Recalculate bandwidth allocation
                await self._recalculate_bandwidth_allocation()
                return True

            async def pause_callback(paused=None):
                nonlocal is_paused
                # Handle explicit pause/unpause if specified
                if paused is not None:
                    is_paused = paused
                else:
                    # Toggle pause state
                    is_paused = not is_paused

                if is_paused:
                    paused_event.clear()
                    download.status = DownloadStatus.PAUSED
                else:
                    paused_event.set()
                    download.status = DownloadStatus.DOWNLOADING

                # Post to event loop to broadcast the update
                asyncio.run_coroutine_threadsafe(
                    self._broadcast_download_update(download_id), asyncio.get_event_loop()
                )
                # Recalculate bandwidth allocation
                asyncio.run_coroutine_threadsafe(
                    self._recalculate_bandwidth_allocation(), asyncio.get_event_loop()
                )
                return True

        # Set up the callbacks
        download.cancel_callback = cancel_callback
        download.pause_resume_callback = pause_callback

        # Set up yt-dlp command
        cmd = self._find_yt_dlp_command()

        # Apply bandwidth limit if necessary
        if download.max_speed:
            cmd.extend(["--limit-rate", f"{download.max_speed}"])

        if download.youtube_type == YoutubeDownloadType.AUDIO:
            # Extract audio only
            cmd.extend(
                [
                    "-f",
                    "bestaudio/best",
                    "-x",
                    "--audio-format",
                    "mp3",
                    "--audio-quality",
                    "0",  # 0 is best
                    "-o",
                    output_path,
                    "--no-mtime",
                    "--newline",
                    str(download.url),
                ]
            )
        else:
            # Download video (default)
            cmd.extend(
                [
                    "-f",
                    "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
                    "-o",
                    output_path,
                    "--no-mtime",
                    "--newline",
                    str(download.url),
                ]
            )

        # Start the download process
        if sys.platform == "win32":
            # Create a queue for reading output from the process
            import threading
            from queue import Queue

            output_queue = Queue()

            # Update info about the download
            def run_process():
                # Create a subprocess and capture output
                try:
                    process = subprocess.Popen(
                        cmd,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        bufsize=1,
                        universal_newlines=True,
                        text=True,
                    )

                    # Define helper function to read output stream
                    def read_stream(stream, output_queue):
                        for line in stream:
                            output_queue.put(line)
                        stream.close()

                    # Create thread to read output
                    thread = threading.Thread(
                        target=read_stream, args=(process.stdout, output_queue)
                    )
                    thread.daemon = True
                    thread.start()

                    # Wait for process to complete
                    returncode = process.wait()
                    output_queue.put(f"YTDL_RC:{returncode}")
                except Exception as e:
                    output_queue.put(f"YTDL_ERROR:{str(e)}")

            # Start process in a separate thread
            process_thread = threading.Thread(target=run_process)
            process_thread.daemon = True
            process_thread.start()

            # Process output and update progress
            line_buffer = ""
            download_complete = False
            return_code = None
            error_message = None

            while not download_complete and return_code is None and error_message is None:
                # Check if cancelled
                if is_cancelled:
                    break

                # If paused, wait
                if is_paused:
                    await asyncio.sleep(0.5)
                    continue

                # Get output from queue
                try:
                    # Non-blocking with timeout
                    line = output_queue.get(timeout=1)
                    line = line.strip()

                    # Check for process completion
                    if line.startswith("YTDL_RC:"):
                        return_code = int(line.split(":", 1)[1])
                        if return_code == 0:
                            download_complete = True
                        break
                    elif line.startswith("YTDL_ERROR:"):
                        error_message = line.split(":", 1)[1]
                        break

                    # Parse progress information
                    self._parse_youtube_progress_line(download, line)

                    # Update download state periodically
                    await self._broadcast_download_update(download_id)

                except Exception:
                    # Timeout or other errors, continue
                    await asyncio.sleep(0.1)

            # Handle results
            if download_complete or (return_code is not None and return_code == 0):
                if download.youtube_type == YoutubeDownloadType.AUDIO:
                    print(f"Audio extracted successfully: {download.name}")
                else:
                    print(f"Video downloaded successfully: {download.name}")

                download.status = DownloadStatus.COMPLETED
                download.speed = 0
                download.time_left = None
                download.size_downloaded = download.size if download.size else 0

                # Ensure progress is 100%
                if download.size is None:
                    # If we don't have the size but download is complete,
                    # set downloaded size as the size
                    download.size = os.path.getsize(download.save_path)
                    download.size_downloaded = download.size

                # File is ready
                await self._broadcast_download_update(download_id)
                await self._send_notification(
                    download_id,
                    "Download Complete",
                    f"'{download.name}' has been downloaded successfully.",
                    "success",
                )

                # Recalculate bandwidth allocation now that this download is complete
                await self._recalculate_bandwidth_allocation()
            elif is_cancelled:
                print(f"Download cancelled: {download.name}")
                download.status = DownloadStatus.FAILED
                await self._broadcast_download_update(download_id)

                # Recalculate bandwidth allocation now that this download is cancelled
                await self._recalculate_bandwidth_allocation()
            else:
                print(
                    f"Download failed: {download.name} - Return code: {return_code}, Error: {error_message}"
                )
                download.status = DownloadStatus.FAILED
                await self._broadcast_download_update(download_id)

                # Check if this was a scheduled download that should be retried
                if (
                    download.schedule
                    and download.schedule.retry_on_failure
                    and download.schedule.current_schedule_retries
                    < download.schedule.max_schedule_retries
                ):
                    # Add to failed scheduled downloads for retry
                    retry_time = datetime.now() + timedelta(
                        minutes=download.schedule.retry_delay_minutes
                    )
                    self.scheduler_failed_downloads[download_id] = {
                        "retry_time": retry_time,
                        "attempts": download.schedule.current_schedule_retries,
                    }

                    # Send notification about retry
                    await self._send_notification(
                        download_id,
                        "Scheduled Download Failed",
                        f"The scheduled download '{download.name}' has failed. Retrying in {download.schedule.retry_delay_minutes} minutes.",
                        "warning",
                    )
                else:
                    # Send standard error notification
                    await self._send_notification(
                        download_id,
                        "Download Failed",
                        f"Failed to download '{download.name}': {error_message or f'Error code {return_code}'}",
                        "error",
                    )

                await self._broadcast_download_update(download_id)

                # Recalculate bandwidth allocation now that this download is failed
                await self._recalculate_bandwidth_allocation()
        else:
            # For non-Windows platforms, use asyncio subprocess
            process = await asyncio.create_subprocess_exec(
                *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
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
                line_buffer += chunk.decode("utf-8", errors="replace")
                lines = line_buffer.split("\n")
                line_buffer = lines.pop()

                # Parse each line
                for line in lines:
                    # Parse progress information
                    self._parse_youtube_progress_line(download, line)

                # Update UI
                await self._broadcast_download_update(download_id)

                # Short sleep to prevent CPU hammering
                await asyncio.sleep(0.1)

            # Handle completion or cancellation
            if download_complete:
                if download.youtube_type == YoutubeDownloadType.AUDIO:
                    print(f"Audio extracted successfully: {download.name}")
                else:
                    print(f"Video downloaded successfully: {download.name}")

                download.status = DownloadStatus.COMPLETED
                download.speed = 0
                download.time_left = None

                # Ensure progress is 100%
                if download.size is None:
                    # If we don't have the size but download is complete,
                    # set downloaded size as the size
                    download.size = os.path.getsize(download.save_path)
                    download.size_downloaded = download.size

                # File is ready
                await self._broadcast_download_update(download_id)
                await self._send_notification(
                    download_id,
                    "Download Complete",
                    f"'{download.name}' has been downloaded successfully.",
                    "success",
                )

                # Recalculate bandwidth allocation
                await self._recalculate_bandwidth_allocation()
            else:
                # Check if process was killed or exited with error
                stderr_output = await process.stderr.read()
                stderr_text = stderr_output.decode("utf-8", errors="replace")

                print(f"Download failed or cancelled: {download.name}")
                print(f"Error output: {stderr_text}")

                download.status = DownloadStatus.FAILED

                # Check if this was a scheduled download that should be retried
                if (
                    download.schedule
                    and download.schedule.retry_on_failure
                    and download.schedule.current_schedule_retries
                    < download.schedule.max_schedule_retries
                ):
                    # Add to failed scheduled downloads for retry
                    retry_time = datetime.now() + timedelta(
                        minutes=download.schedule.retry_delay_minutes
                    )
                    self.scheduler_failed_downloads[download_id] = {
                        "retry_time": retry_time,
                        "attempts": download.schedule.current_schedule_retries,
                    }

                    # Send notification about retry
                    await self._send_notification(
                        download_id,
                        "Scheduled Download Failed",
                        f"The scheduled download '{download.name}' has failed. Retrying in {download.schedule.retry_delay_minutes} minutes.",
                        "warning",
                    )
                else:
                    # Send standard error notification
                    await self._send_notification(
                        download_id,
                        "Download Failed",
                        f"Failed to download '{download.name}': {stderr_text}",
                        "error",
                    )

                await self._broadcast_download_update(download_id)

                # Recalculate bandwidth allocation
                await self._recalculate_bandwidth_allocation()

        # Clean up
        download.pause_resume_callback = None
        download.cancel_callback = None

    def _broadcast_download_update_sync(self, download_id: str):
        """Synchronous version of broadcast update for use in thread callbacks"""
        download = self.downloads.get(download_id)
        if not download:
            return

        # Schedule the asynchronous broadcast in the event loop
        asyncio.run_coroutine_threadsafe(
            self._broadcast_download_update(download_id), asyncio.get_event_loop()
        )

    def _find_yt_dlp_command(self) -> list[str]:
        """Find the yt-dlp command on the system, returns a list of command parts ready for subprocess"""
        possible_commands = [["yt-dlp"], ["yt-dlp.exe"], ["python", "-m", "yt_dlp"]]

        # Check if yt-dlp is in the Python scripts directory
        if sys.platform == "win32":
            # Check common Python script directories on Windows
            python_paths = []

            # Current Python executable's directory
            if getattr(sys, "executable", None):
                python_dir = os.path.dirname(sys.executable)
                python_paths.append(os.path.join(python_dir, "Scripts", "yt-dlp.exe"))
                python_paths.append(os.path.join(python_dir, "yt-dlp.exe"))

            # User's directory - common pip install location
            user_profile = os.environ.get("USERPROFILE")
            if user_profile:
                python_paths.append(
                    os.path.join(
                        user_profile,
                        "AppData",
                        "Local",
                        "Programs",
                        "Python",
                        "Python*",
                        "Scripts",
                        "yt-dlp.exe",
                    )
                )
                python_paths.append(
                    os.path.join(
                        user_profile,
                        "AppData",
                        "Roaming",
                        "Python",
                        "Python*",
                        "Scripts",
                        "yt-dlp.exe",
                    )
                )

            # Add python -m yt_dlp as a fallback
            possible_commands.append([sys.executable, "-m", "yt_dlp"])

            # Expand glob patterns and add to possible commands
            for path in python_paths:
                if "*" in path:
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

                result = subprocess.run(
                    test_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False
                )

                if result.returncode == 0:
                    version = result.stdout.strip()
                    print(f"Found yt-dlp: {' '.join(cmd)}, version: {version}")
                    return cmd
            except Exception as e:
                print(f"Error checking {' '.join(cmd)}: {e}")
                continue

        # If we get here, we didn't find yt-dlp
        print(
            "WARNING: yt-dlp command not found. YouTube downloading will not work until yt-dlp is installed."
        )
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

        if delete_file:
            if os.path.exists(download.save_path):
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
            else:
                # File doesn't exist on disk, but we'll still proceed with deleting the download
                print(
                    f"Warning: File {download.save_path} not found on disk, but proceeding with download deletion"
                )
                file_error = "File not found on disk"

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
            if "%" in line and ("ETA" in line or "at" in line):
                parts = line.split()

                # Find the percentage value
                percent_parts = [p for p in parts if p.endswith("%")]
                if not percent_parts:
                    return False

                percent_str = percent_parts[0].rstrip("%")
                try:
                    percent = float(percent_str)
                    # Update size_downloaded based on percentage instead of setting progress directly
                    if download.size:
                        download.size_downloaded = int(download.size * (percent / 100.0))
                    else:
                        # If size is unknown, try to parse it from the line
                        size_parts = [
                            p for i, p in enumerate(parts) if i > 0 and "of" in parts[i - 1]
                        ]
                        if size_parts:
                            size_str = size_parts[0]
                            if "~" in size_str:
                                size_str = size_str.replace("~", "").strip()

                            # Parse different size formats (MiB, KiB, etc.)
                            if "MiB" in size_str:
                                size_mb = float(size_str.replace("MiB", "").strip())
                                download.size = int(size_mb * 1024 * 1024)
                            elif "KiB" in size_str:
                                size_kb = float(size_str.replace("KiB", "").strip())
                                download.size = int(size_kb * 1024)
                            elif "GiB" in size_str:
                                size_gb = float(size_str.replace("GiB", "").strip())
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
                    speed_index = [i for i, p in enumerate(parts) if "/s" in p]
                    if speed_index:
                        speed_str = parts[speed_index[0]]
                        # Extract the numeric part and unit part
                        # Handle formats like "68.60KiB/s", "1.45MiB/s", "68.60K/s", "1.45M/s"
                        if "KiB/s" in speed_str or "K/s" in speed_str:
                            speed_value = float(
                                speed_str.replace("KiB/s", "").replace("K/s", "").strip()
                            )
                            download.speed = int(speed_value * 1024)
                        elif "MiB/s" in speed_str or "M/s" in speed_str:
                            speed_value = float(
                                speed_str.replace("MiB/s", "").replace("M/s", "").strip()
                            )
                            download.speed = int(speed_value * 1024 * 1024)
                        elif "GiB/s" in speed_str or "G/s" in speed_str:
                            speed_value = float(
                                speed_str.replace("GiB/s", "").replace("G/s", "").strip()
                            )
                            download.speed = int(speed_value * 1024 * 1024 * 1024)
                        elif "B/s" in speed_str:
                            speed_value = float(speed_str.replace("B/s", "").strip())
                            download.speed = int(speed_value)
                except (ValueError, TypeError, IndexError) as e:
                    print(f"Error parsing speed: {e}, speed_str: {line}")

                # Parse ETA if available
                try:
                    eta_index = [i for i, p in enumerate(parts) if p == "ETA"]
                    if eta_index and eta_index[0] < len(parts) - 1:
                        eta_str = parts[eta_index[0] + 1]
                        if ":" in eta_str:
                            # Parse HH:MM:SS or MM:SS format
                            time_parts = eta_str.split(":")
                            seconds = 0
                            if len(time_parts) == 3:  # HH:MM:SS
                                seconds = (
                                    int(time_parts[0]) * 3600
                                    + int(time_parts[1]) * 60
                                    + int(time_parts[2])
                                )
                            elif len(time_parts) == 2:  # MM:SS
                                seconds = int(time_parts[0]) * 60 + int(time_parts[1])

                            download.time_left = seconds
                except (ValueError, TypeError, IndexError) as e:
                    print(f"Error parsing ETA: {e}")

                return True

            # Check if it's a destination line
            elif "Destination:" in line:
                filename = line.split("Destination:")[1].strip()
                print(f"Detected output filename: {filename}")
                return True

            return False
        except Exception as e:
            print(f"Error parsing progress line: {e}")
            print(f"Line was: {line}")
            return False

    async def _send_notification(
        self, download_id: str, title: str, message: str, notification_type: str = "info"
    ):
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
            "timestamp": datetime.now().isoformat(),
        }

        # Broadcast to all connected clients
        await ws_manager.broadcast(notification)

    async def search_downloads(self, search_query) -> list[dict]:
        """
        Search downloads based on advanced criteria

        Args:
            search_query: A SearchQuery object with search parameters

        Returns:
            A list of matching downloads in API-friendly format
        """
        downloads = list(self.downloads.values())
        filtered_downloads = []

        for download in downloads:
            # Match all conditions to include the download
            include = True

            # Text search in name and URL
            if search_query.query:
                query_lower = search_query.query.lower()
                name_match = query_lower in download.name.lower()
                url_match = query_lower in str(download.url).lower()
                tags_match = any(query_lower in tag.lower() for tag in download.tags)

                if not (name_match or url_match or tags_match):
                    include = False

            # Category filter
            if search_query.category and download.category != search_query.category:
                include = False

            # Status filter
            if search_query.status and download.status not in search_query.status:
                include = False

            # Date range filter
            if search_query.date_from and download.date_added < search_query.date_from:
                include = False

            if search_query.date_to and download.date_added > search_query.date_to:
                include = False

            # Size range filter
            if search_query.min_size is not None and (
                download.size is None or download.size < search_query.min_size
            ):
                include = False

            if search_query.max_size is not None and (
                download.size is not None and download.size > search_query.max_size
            ):
                include = False

            # Tags filter
            if search_query.tags:
                # All specified tags must be present
                if not all(tag in download.tags for tag in search_query.tags):
                    include = False

            # Add download to results if it matches all criteria
            if include:
                filtered_downloads.append(download)

        # Convert downloads to API-safe format
        return [self._prepare_download_for_api(d) for d in filtered_downloads]

    async def update_scheduler_settings(self, check_interval_seconds: int):
        """Update the scheduler check interval and restart the scheduler"""
        if check_interval_seconds < 5:
            # Don't allow intervals that are too small
            check_interval_seconds = 5

        self.scheduler_check_interval = check_interval_seconds

        # Restart the scheduler with the new interval
        if self.scheduler_task:
            self.scheduler_task.cancel()
            self.scheduler_task = None

        self._start_scheduler(self.scheduler_check_interval)
        return True

    async def schedule_download(self, download_id: str, schedule: dict) -> DownloadItem | None:
        """Schedule a download for a specific time"""
        download = self.downloads.get(download_id)
        if not download:
            return None

        # Convert string datetime to datetime object if needed
        if isinstance(schedule.get("scheduled_time"), str):
            try:
                # Parse ISO format string (will preserve timezone info if present)
                schedule["scheduled_time"] = datetime.fromisoformat(
                    schedule["scheduled_time"].replace("Z", "+00:00")
                )
            except ValueError:
                return None

        # Create ScheduleSettings object
        schedule_settings = ScheduleSettings(**schedule)

        # Make sure day_of_month is set for monthly recurrence
        if (
            schedule_settings.recurrence == RecurrenceType.MONTHLY
            and not schedule_settings.day_of_month
        ):
            schedule_settings.day_of_month = schedule_settings.scheduled_time.day

        # Make sure days_of_week is set for weekly recurrence
        if (
            schedule_settings.recurrence == RecurrenceType.WEEKLY
            and not schedule_settings.days_of_week
        ):
            schedule_settings.days_of_week = [schedule_settings.scheduled_time.weekday()]

        # Update the download's schedule
        download.schedule = schedule_settings

        # Check if the scheduled time is in the future
        now = datetime.now(UTC)  # Make current time timezone-aware (UTC)
        scheduled_time = schedule_settings.scheduled_time

        print(
            f"Schedule_download - Current time (UTC): {now.isoformat()}, Scheduled time: {scheduled_time.isoformat()}"
        )

        # Convert scheduled_time to UTC if it has timezone info
        if hasattr(scheduled_time, "tzinfo") and scheduled_time.tzinfo is not None:
            # Convert to UTC for comparison
            utc_scheduled_time = scheduled_time.astimezone(UTC)

            print(
                f"UTC comparison - Now: {now.isoformat()}, Scheduled: {utc_scheduled_time.isoformat()}"
            )

            # Only start immediately if current time is AFTER OR EQUAL TO scheduled time
            should_start_now = now >= utc_scheduled_time
        else:
            # Make naive time timezone-aware by assuming it's in UTC
            utc_scheduled_time = scheduled_time.replace(tzinfo=UTC)
            # Only start immediately if current time is AFTER OR EQUAL TO scheduled time
            should_start_now = now >= utc_scheduled_time
            print(f"Converted naive time to UTC: {utc_scheduled_time.isoformat()}")

        print(f"Should start now: {should_start_now}")

        if should_start_now:
            # Immediate scheduling - start the download right away
            download.status = DownloadStatus.QUEUED

            # Apply priority boost if enabled
            if download.schedule.priority_boost and download.priority != DownloadPriority.HIGH:
                download.priority = DownloadPriority.HIGH

            # Start the download task
            if download.is_youtube:
                self.tasks[download_id] = asyncio.create_task(self._download_youtube(download_id))
            else:
                self.tasks[download_id] = asyncio.create_task(self._download_file(download_id))
        else:
            # Future scheduling - mark as scheduled
            download.status = DownloadStatus.SCHEDULED

        # Save and broadcast the update
        await self.save_and_broadcast_download(download_id)

        # Send notification about scheduled download
        time_str = scheduled_time.strftime("%Y-%m-%d %H:%M:%S")
        recur_str = ""
        if schedule_settings.recurrence:
            recur_str = f" ({schedule_settings.recurrence.value})"  # Use .value to get the string representation

        await self._send_notification(
            download_id,
            "Download Scheduled",
            f"'{download.name}' scheduled for {time_str}{recur_str}",
            "info",
        )

        return download

    def _calculate_next_scheduled_time(self, schedule, current_time):
        """Calculate the next occurrence time for a recurring schedule"""
        scheduled_time = schedule.scheduled_time

        # Ensure we're working with timezone-aware datetimes
        if not hasattr(current_time, "tzinfo") or current_time.tzinfo is None:
            current_time = current_time.replace(tzinfo=UTC)

        if not hasattr(scheduled_time, "tzinfo") or scheduled_time.tzinfo is None:
            scheduled_time = scheduled_time.replace(tzinfo=UTC)

        if schedule.recurrence == RecurrenceType.DAILY:
            # Schedule for tomorrow at the same time
            return scheduled_time + timedelta(days=1)

        elif schedule.recurrence == RecurrenceType.WEEKLY and schedule.days_of_week:
            # Find the next occurrence based on days_of_week
            today_weekday = current_time.weekday()  # 0=Monday, 6=Sunday
            next_day = None

            # Sort the days to find the next upcoming day
            for day in sorted(schedule.days_of_week):
                if day > today_weekday:
                    next_day = day
                    break

            # If no day found, wrap around to the first day in the list
            if next_day is None and schedule.days_of_week:
                next_day = min(schedule.days_of_week)
                days_ahead = 7 - today_weekday + next_day
            else:
                days_ahead = next_day - today_weekday

            return scheduled_time + timedelta(days=days_ahead)

        elif schedule.recurrence == RecurrenceType.MONTHLY and schedule.day_of_month:
            # Get the target day of month
            target_day = min(schedule.day_of_month, 28)  # Use 28 as a safe max

            # Get the next month
            next_month = current_time.month + 1
            next_year = current_time.year

            if next_month > 12:
                next_month = 1
                next_year += 1

            # Create the next scheduled time
            return scheduled_time.replace(
                year=next_year,
                month=next_month,
                day=min(target_day, calendar.monthrange(next_year, next_month)[1]),
            )

        # Default fallback (shouldn't normally reach here)
        return scheduled_time + timedelta(days=1)

    def _clone_download_for_next_occurrence(self, download, next_time):
        """Create a clone of a download for the next scheduled occurrence"""
        # Generate a new ID for the cloned download
        new_id = str(uuid.uuid4())

        # Create a new download using the model_dump of the original
        download_dict = download.model_dump()

        # Update fields for the new instance
        download_dict["id"] = new_id
        download_dict["status"] = DownloadStatus.SCHEDULED
        download_dict["date_added"] = datetime.now(UTC)  # Use timezone-aware datetime
        download_dict["size_downloaded"] = 0
        download_dict["speed"] = 0
        download_dict["time_left"] = None

        # Update the schedule with the new time and reset retry count
        if download_dict.get("schedule"):
            # Ensure next_time is timezone-aware
            if not hasattr(next_time, "tzinfo") or next_time.tzinfo is None:
                next_time = next_time.replace(tzinfo=UTC)

            download_dict["schedule"]["scheduled_time"] = next_time
            download_dict["schedule"]["current_schedule_retries"] = 0

        # Create a fresh download item from the dictionary
        new_download = DownloadItem(**download_dict)

        # Log the new scheduled download
        print(f"Created recurring download {new_id} scheduled for {next_time.isoformat()}")

        return new_download


# Singleton instance
download_manager = DownloadManager()
