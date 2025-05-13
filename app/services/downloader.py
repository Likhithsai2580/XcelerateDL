import asyncio
import calendar
import contextlib
import json
import mimetypes
import os
import re
import subprocess
import sys
import time
import uuid
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, unquote, urlencode, urlparse, urlunparse

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


# Simple rate limiter for download speed control
class RateLimiter:
    def __init__(self, max_bytes_per_second=None):
        if max_bytes_per_second is not None and max_bytes_per_second <= 0:
            self.max_bytes_per_second = None  # Effectively disable if limit is 0 or negative
        else:
            self.max_bytes_per_second = max_bytes_per_second
        self.last_check_time = time.time()
        self.bytes_read_since_check = 0

    def limit(self, chunk_size):
        if not self.max_bytes_per_second:
            return

        self.bytes_read_since_check += chunk_size
        current_time = time.time()
        time_passed = current_time - self.last_check_time

        if time_passed > 0:
            rate = self.bytes_read_since_check / time_passed
            if rate > self.max_bytes_per_second:
                sleep_time = self.bytes_read_since_check / self.max_bytes_per_second - time_passed
                if sleep_time > 0:
                    time.sleep(sleep_time)

            # Reset counters
            self.last_check_time = time.time()
            self.bytes_read_since_check = 0


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
        self._scheduler_shutdown = False  # Flag to signal scheduler shutdown

        # Create the download directory if it doesn't exist
        os.makedirs(self.download_dir, exist_ok=True)

        # Create category directories
        for category in FileCategory:
            if category != FileCategory.ALL:
                os.makedirs(os.path.join(self.download_dir, category.value), exist_ok=True)

    async def initialize(self):
        """Load saved downloads and resume interrupted ones"""
        # Set up backup recovery in case of data corruption
        try:
            await self.load_downloads()
            await self.load_bandwidth_settings()
        except Exception as e:
            print(f"Error loading saved downloads: {e}")
            # Try to recover from backup files
            try:
                print("Attempting to recover from backup files...")
                # Look for backup files
                storage_dir = os.path.dirname(self.storage_file)
                backup_files = [f for f in os.listdir(storage_dir) if f.endswith('.bak') and f.startswith(os.path.basename(self.storage_file))]
                
                if backup_files:
                    # Sort by modification time (newest first)
                    backup_files.sort(key=lambda f: os.path.getmtime(os.path.join(storage_dir, f)), reverse=True)
                    latest_backup = os.path.join(storage_dir, backup_files[0])
                    print(f"Found backup file: {latest_backup}")
                    
                    # Replace the corrupted file with the backup
                    import shutil
                    shutil.copy2(latest_backup, self.storage_file)
                    print(f"Restored from backup: {latest_backup}")
                    
                    # Try loading again
                    await self.load_downloads()
                else:
                    print("No backup files found. Starting with empty downloads list.")
                    self.downloads = {}
            except Exception as recovery_error:
                print(f"Error recovering from backup: {recovery_error}")
                print("Starting with empty downloads list.")
                self.downloads = {}
            
            # Always try to load bandwidth settings, even if downloads failed
            try:
                await self.load_bandwidth_settings()
            except:
                print("Error loading bandwidth settings. Using defaults.")

        # Create a backup of the current state after successful load
        try:
            if os.path.exists(self.storage_file):
                backup_path = f"{self.storage_file}.{int(time.time())}.bak"
                import shutil
                shutil.copy2(self.storage_file, backup_path)
                
                # Clean up old backups (keep only the 5 most recent)
                storage_dir = os.path.dirname(self.storage_file)
                backup_files = [f for f in os.listdir(storage_dir) if f.endswith('.bak') and f.startswith(os.path.basename(self.storage_file))]
                if len(backup_files) > 5:
                    backup_files.sort(key=lambda f: os.path.getmtime(os.path.join(storage_dir, f)))
                    for old_backup in backup_files[:-5]:
                        try:
                            os.remove(os.path.join(storage_dir, old_backup))
                        except:
                            pass
        except Exception as backup_error:
            print(f"Error creating backup: {backup_error}")

        # Check for partially downloaded files and resume them
        downloads_to_resume = []
        downloads_to_check = list(self.downloads.items())
        
        for download_id, download in downloads_to_check:
            # Only resume downloads that were in progress
            if download.status in [
                DownloadStatus.DOWNLOADING,
                DownloadStatus.QUEUED,
                DownloadStatus.PAUSED,
            ]:
                try:
                    # Verify save_path is valid before attempting to access
                    if not download.save_path or not isinstance(download.save_path, str):
                        print(f"Warning: Invalid save path for download {download_id}. Setting to default location.")
                        # Set a default location based on the download name or ID
                        filename = download.name if download.name else f"download_{download_id}"
                        download.save_path = os.path.join(self.download_dir, filename)
                    
                    # Make sure the parent directory exists
                    os.makedirs(os.path.dirname(download.save_path), exist_ok=True)
                    
                    # Check if the file exists but is incomplete
                    if os.path.exists(download.save_path):
                        try:
                            current_size = os.path.getsize(download.save_path)
                            
                            # If file is accessible, check its integrity
                            try:
                                # Try to open the file to verify it's not locked or corrupted
                                with open(download.save_path, "rb") as f:
                                    # Just read a small chunk to check access
                                    f.seek(0)
                                    f.read(1)
                                    
                                    # If size is known, try to read from the end to ensure integrity
                                    if current_size > 1024:  # Only for files > 1KB
                                        f.seek(max(0, current_size - 1024))
                                        f.read(1024)
                                
                                # File is accessible, proceed with normal checks
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
                                elif download.size and current_size > download.size:
                                    # File on disk is larger than expected, might be corrupted or a different file
                                    print(f"Warning: File {download.save_path} for {download_id} is larger ({current_size}) than expected ({download.size}). Marking as failed.")
                                    download.status = DownloadStatus.FAILED
                                    download.notes = f"File on disk ({current_size} bytes) is larger than metadata size ({download.size} bytes). Download marked as failed."
                                elif current_size == 0 and download.size_downloaded > 0:
                                    # If metadata says downloaded but file is 0 bytes
                                    print(f"Warning: File {download.save_path} for {download_id} is 0 bytes but metadata shows {download.size_downloaded} downloaded. Resetting and queuing.")
                                    download.size_downloaded = 0
                                    download.status = DownloadStatus.QUEUED
                                    downloads_to_resume.append(download_id)
                                else:
                                    # Covers current_size == 0 and current_size == download.size (if not completed)
                                    # File exists but is empty or complete, determine status
                                    if download.size and current_size >= download.size:
                                        # File is complete
                                        download.status = DownloadStatus.COMPLETED
                                        download.size_downloaded = download.size
                                    else:
                                        # File is empty or we don't know the size, queue it
                                        download.size_downloaded = current_size
                                        download.status = DownloadStatus.QUEUED
                                        downloads_to_resume.append(download_id)
                            
                            except (IOError, PermissionError) as file_access_error:
                                print(f"Warning: File {download.save_path} for {download_id} exists but may be locked or corrupted: {file_access_error}. Requeuing.")
                                # File exists but couldn't be accessed properly
                                download.status = DownloadStatus.QUEUED
                                # Keep current size_downloaded but flag for restart
                                downloads_to_resume.append(download_id)
                        
                        except OSError as e:
                            print(f"Error getting file size for {download.save_path}: {e}. Requeuing download.")
                            download.status = DownloadStatus.QUEUED
                            downloads_to_resume.append(download_id)
                    else:
                        # File doesn't exist, mark as queued
                        download.size_downloaded = 0
                        download.status = DownloadStatus.QUEUED
                        downloads_to_resume.append(download_id)
                
                except OSError as e:
                    print(f"Error accessing file {download.save_path} for download {download_id} during resume: {e}. Marking as failed.")
                    download.status = DownloadStatus.FAILED
                    download.notes = f"Error during file check on resume: {e}"
                
                except Exception as e:
                    print(f"Unexpected error processing download {download_id} for resume: {e}. Marking as failed.")
                    download.status = DownloadStatus.FAILED
                    download.notes = f"Unexpected error on resume: {e}"
                    import traceback
                    traceback.print_exc()

        # Update download state before resuming
        await self.save_downloads()
        
        # Resume downloads that were interrupted
        for download_id in downloads_to_resume:
            if (
                download_id in self.downloads
                and self.downloads[download_id].status != DownloadStatus.SCHEDULED
            ):
                try:
                    if self.downloads[download_id].is_youtube:
                        self.tasks[download_id] = asyncio.create_task(
                            self._download_youtube(download_id)
                        )
                    else:
                        self.tasks[download_id] = asyncio.create_task(self._download_file(download_id))
                except Exception as e:
                    print(f"Error resuming download {download_id}: {e}")
                    self.downloads[download_id].status = DownloadStatus.FAILED
                    self.downloads[download_id].notes = f"Error starting download task: {e}"

        print(
            f"Restored {len(self.downloads)} downloads, resumed {len(downloads_to_resume)} downloads"
        )

        # Start periodic saving
        self._start_autosave()

        # Start scheduler for scheduled downloads
        self._start_scheduler()

        return len(downloads_to_resume)

    def _start_autosave(self, interval_seconds: int = 5):
        """Start an automatic save task to run periodically"""

        async def autosave_task():
            while True:
                try:
                    await asyncio.sleep(interval_seconds)
                    
                    # Save downloads with active ones first to prioritize their state
                    active_downloads_exist = any(
                        download.status == DownloadStatus.DOWNLOADING
                        for download in self.downloads.values()
                    )
                    
                    # More frequent saves if there are active downloads
                    if active_downloads_exist:
                        # Save immediately and then set a shorter interval for next save
                        await self.save_downloads()
                        await self.save_bandwidth_settings()
                        await asyncio.sleep(interval_seconds // 2)  # Half interval for active downloads
                    else:
                        # Normal save for inactive state
                        await self.save_downloads()
                        await self.save_bandwidth_settings()
                except Exception as e:
                    print(f"Error in autosave task: {e}")
                    # Don't let exceptions stop the autosave - log and continue
                    import traceback
                    traceback.print_exc()
                    # Shorter sleep after error to try again quickly
                    await asyncio.sleep(interval_seconds // 2)

        # Create the autosave task with a name for better debugging
        autosave_task_obj = asyncio.create_task(autosave_task(), name="download_autosave_task")
        
        # Store reference to the task so it doesn't get garbage collected
        self._autosave_task = autosave_task_obj

    def _start_scheduler(self, check_interval_seconds: int = 60):
        """Start the scheduler for scheduled downloads"""

        async def scheduler_task():
            while not self._scheduler_shutdown:
                try:
                    await asyncio.sleep(check_interval_seconds)

                    # Check if shutdown flag is set before proceeding
                    if self._scheduler_shutdown:
                        print("Scheduler shutdown flag detected, stopping scheduler task")
                        break

                    print(
                        f"Running scheduler check at {datetime.now().isoformat()} with interval {check_interval_seconds}s"
                    )
                    await self._process_scheduled_downloads()
                    await self._process_failed_scheduled_downloads()
                except asyncio.CancelledError:
                    print("Scheduler task cancelled")
                    break
                except Exception as e:
                    print(f"ERROR in scheduler task: {str(e)}")
                    # Don't let exceptions stop the scheduler - log and continue
                    import traceback

                    traceback.print_exc()
                    await asyncio.sleep(5)  # Wait a bit before trying again after error

            print("Scheduler task exited cleanly")

        if self.scheduler_task is None or self.scheduler_task.done():
            # Reset shutdown flag when starting a new scheduler
            self._scheduler_shutdown = False
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

                        print(
                            f"UTC comparison - Current: {current_time.isoformat()}, Scheduled: {utc_scheduled_time.isoformat()}, Should start: {should_start_now}"
                        )
                        # Only start if current time is AFTER or EQUAL TO scheduled time
                        should_start_now = current_time >= utc_scheduled_time

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

        # Check if we already have a save in progress
        if hasattr(self, "_save_in_progress") and self._save_in_progress:
            # Skip this save operation as another one is already happening
            return False

        # Set flag to indicate a save is in progress
        self._save_in_progress = True

        try:
            # Convert downloads to serializable format
            downloads_data = {}
            for download_id, download in self.downloads.items():
                try:
                    # Skip downloads with invalid statuses to prevent corrupt data
                    if not download.status or not isinstance(download.status, DownloadStatus):
                        print(f"Skipping download with invalid status: {download_id}")
                        continue

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

                    # Remove any temporary attributes we added
                    if "_last_broadcast_time" in download_dict:
                        download_dict.pop("_last_broadcast_time", None)

                    # Remove custom tracking attributes we've added
                    for attr in ["_last_saved_size", "_last_time", "_last_size"]:
                        if attr in download_dict:
                            download_dict.pop(attr, None)

                    downloads_data[download_id] = download_dict
                except Exception as e:
                    print(f"Error serializing download {download_id}: {e}")
                    continue

            try:
                # Use a temporary file for writing to avoid data corruption
                import random
                import string

                # Generate a unique temp file name to avoid conflicts
                random_suffix = "".join(random.choices(string.ascii_letters + string.digits, k=8))
                temp_file = f"{self.storage_file}.{random_suffix}.tmp"

                # Write to the temp file
                async with aiofiles.open(temp_file, "w") as f:
                    await f.write(json.dumps(downloads_data, indent=2))

                # Validate the JSON was written correctly
                async with aiofiles.open(temp_file) as f:
                    content = await f.read()
                    # Try to parse the JSON to ensure it's valid
                    json.loads(content)

                # Only replace the original file if temp file is valid
                import shutil

                # Use proper error handling for the file move
                max_retries = 3
                retry_delay = 0.5  # seconds

                for attempt in range(max_retries):
                    try:
                        # Use atomic replacement where possible
                        if hasattr(shutil, "move"):
                            # On Windows, close any open handles to the file before replacing it
                            if os.path.exists(self.storage_file) and sys.platform == "win32":
                                try:
                                    # Force Python's garbage collection to release file handles
                                    import gc

                                    gc.collect()
                                except Exception:
                                    pass

                            # Move the file (replace existing)
                            shutil.move(temp_file, self.storage_file)
                            break  # Success, exit the retry loop
                        else:
                            # Fallback for older Python versions
                            if os.path.exists(self.storage_file):
                                os.remove(self.storage_file)
                            os.rename(temp_file, self.storage_file)
                            break  # Success, exit the retry loop
                    except PermissionError as e:
                        if attempt < max_retries - 1:
                            # Wait and retry
                            print(
                                f"File access conflict during save, retrying in {retry_delay}s..."
                            )
                            await asyncio.sleep(retry_delay)
                            retry_delay *= 2  # Exponential backoff
                        else:
                            # Last attempt failed
                            print(f"Failed to save downloads after {max_retries} attempts: {e}")
                            if os.path.exists(temp_file):
                                try:
                                    os.remove(temp_file)
                                except:
                                    pass
                            return False

                return True
            except Exception as e:
                print(f"Error saving downloads: {e}")
                # Try to clean up temp file if it exists
                if os.path.exists(temp_file):
                    try:
                        os.remove(temp_file)
                    except:
                        pass
                return False
        finally:
            # Clear the flag when done
            self._save_in_progress = False

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
            # download.max_speed has already been set by the logic above
            # Instead of calling the callback, set a flag to signal the download task
            download.needs_rate_limit_update = True
            # The download task itself will pick this up and re-initialize its rate limiter.

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
            title = re.sub(r'[\\/*?:"<>|]', "_", title)  # Remove illegal characters
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
        should_start_now = True  # Default is to start now

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
                    f"UTC comparison - Now: {now.isoformat()}, Scheduled: {utc_scheduled_time.isoformat()}, Should start: {should_start_now}"
                )
                # Only queue immediately if current time is AFTER or EQUAL TO scheduled time
                should_start_now = now >= utc_scheduled_time

                print(f"Should start now: {should_start_now}")
            else:
                # Make naive time timezone-aware by assuming it's in UTC
                utc_scheduled_time = scheduled_time.replace(tzinfo=UTC)

                # Only queue immediately if current time is AFTER or EQUAL TO scheduled time
                should_start_now = now >= utc_scheduled_time

                print(
                    f"Converted naive time to UTC: {utc_scheduled_time.isoformat()}, Should start: {should_start_now}"
                )

            # Check if it's time to start this download
            if should_start_now:
                # Immediate scheduling - start the download right away
                initial_status = DownloadStatus.QUEUED
            else:
                # Future scheduling - mark as scheduled
                initial_status = DownloadStatus.SCHEDULED

        print(f"Initial status for download: {initial_status.value}")

        # Check for existing downloads with the same URL to avoid duplicates
        original_url = str(request.url).strip()
        normalized_url = normalize_url(original_url)
        existing_download = None

        for existing_id, download in self.downloads.items():
            download_normalized_url = normalize_url(str(download.url))
            if download_normalized_url == normalized_url:
                # Found a matching URL - potential duplicate
                existing_download = download

                # If there's an active or completed download with this URL
                if download.status in [
                    DownloadStatus.DOWNLOADING,
                    DownloadStatus.QUEUED,
                    DownloadStatus.COMPLETED,
                    DownloadStatus.PAUSED,
                ]:
                    print(f"Download already exists for URL: {normalized_url}, ID: {existing_id}")
                    # Return the existing download instead of creating a new one
                    return download

                # If download is failed, we'll replace it with a new one
                elif download.status == DownloadStatus.FAILED:
                    print(f"Replacing failed download for URL: {normalized_url}, ID: {existing_id}")
                    # Cancel any existing task if it's still active but failed
                    if existing_id in self.tasks and not self.tasks[existing_id].done():
                        try:
                            self.tasks[existing_id].cancel()
                            # Give it a moment to clean up
                            await asyncio.sleep(0.1)
                        except Exception as e:
                            print(f"Error cancelling existing task: {e}")

                    # We'll continue with the add process but will replace this download
                    break

        # Generate a unique ID for the download (or reuse existing ID if replacing a failed download)
        if existing_download and existing_download.status == DownloadStatus.FAILED:
            download_id = existing_download.id
        else:
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
        """
        Download a file with automatic selection between chunked and regular methods.
        This method decides whether to use parallel chunking or regular single connection.
        """
        download = self.downloads.get(download_id)
        if not download:
            print(f"Download {download_id} not found")
            return
        
        # Try to get file size information first if not already known
        if not download.size:
            try:
                name, size = await self.get_file_info(str(download.url))
                if name and not download.name.startswith("download_"):
                    download.name = name
                    download.save_path = os.path.join(os.path.dirname(download.save_path), name)
                if size:
                    download.size = size
            except Exception as e:
                print(f"Error getting file info: {e}")
        
        # Determine whether to use chunked download
        # Files over 10MB will use chunked download
        min_size_for_chunking = 10 * 1024 * 1024  # 10MB
        
        if download.size and download.size >= min_size_for_chunking:
            print(f"Using parallel chunked download for {download_id} ({download.size} bytes)")
            await self._download_file_chunked(download_id)
        else:
            print(f"Using regular download for {download_id}")
            await self._download_file_regular(download_id)
        
    async def _download_file_regular(self, download_id: str) -> None:
        """Download a file using the original single-connection method."""
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
        current_url_to_try = str(download.url) # Initialize with the original URL
        attempted_protocol_switch = False # Flag to ensure we only switch protocol once

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
                        headers["Range"] = f"bytes={initial_size}-"

                    # Use current_url_to_try for the GET request
                    async with session.get(current_url_to_try, headers=headers) as response:
                        # Raise an HTTPError for bad responses (4xx or 5xx)
                        response.raise_for_status()

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

                            # New variables for improved speed and progress tracking
                            last_time = time.time()
                            last_size = download.size_downloaded

                            # Rate limiting setup
                            rate_limit = download.max_speed if download.max_speed else None
                            rate_limiter = RateLimiter(rate_limit) if rate_limit else None

                            # Create a pause event to handle pausing
                            pause_event = asyncio.Event()
                            pause_event.set()  # Not paused initially

                            # Variable for rate limiting
                            # Initialize rate_limit based on current download.max_speed
                            rate_limit = download.max_speed if download.max_speed else None
                            last_chunk_time = datetime.now()
                            download.needs_rate_limit_update = False # Clear flag as we're setting rate_limit now

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

                                    # Update rate_limit if signaled by an external change (e.g., recalculate_bandwidth)
                                    if download.needs_rate_limit_update:
                                        new_max_speed = download.max_speed if download.max_speed else None
                                        if rate_limit != new_max_speed:
                                            rate_limit = new_max_speed
                                            print(f"Download {download_id}: Internal rate_limit updated to {rate_limit} due to flag.")
                                        download.needs_rate_limit_update = False # Reset the flag

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

                                    # Calculate speed and ETA
                                    current_time = time.time()
                                    time_diff = current_time - last_time
                                    size_diff = download.size_downloaded - last_size

                                    if time_diff >= 1.0:  # Update at most once per second
                                        # Calculate download speed in bytes/s
                                        download.speed = int(size_diff / time_diff)

                                        # Calculate ETA
                                        if download.size and download.speed > 0:
                                            remaining_size = (
                                                download.size - download.size_downloaded
                                            )
                                            download.time_left = int(
                                                remaining_size / download.speed
                                            )

                                        # Update progress percentage if size is known
                                        if download.size:
                                            # Calculate the progress value but use it to update size_downloaded
                                            progress_percentage = min(
                                                100,
                                                (download.size_downloaded / download.size) * 100,
                                            )
                                            # No need to set progress directly as it's a read-only property

                                        # Broadcast the update
                                        self._broadcast_download_update_sync(download_id)

                                        # Save progress more frequently for large files
                                        if (
                                            download.size and download.size > 10 * 1024 * 1024
                                        ):  # > 10MB
                                            # Save every 5% for large files
                                            current_progress = download.progress  # Use the property
                                            if current_progress % 5 < (
                                                100 * size_diff / download.size
                                            ):
                                                asyncio.create_task(self.save_downloads())
                                        else:
                                            # For smaller files, save less frequently
                                            current_progress = download.progress  # Use the property
                                            if current_progress % 10 < (
                                                100 * size_diff / download.size
                                            ):
                                                asyncio.create_task(self.save_downloads())

                                        # Update reference values for next iteration
                                        last_time = current_time
                                        last_size = download.size_downloaded

                            except asyncio.CancelledError:
                                print(f"Download {download_id} cancelled.")
                                download.status = DownloadStatus.FAILED # Or some other appropriate status
                                download.notes = "Download was cancelled."
                                await self.save_and_broadcast_download(download_id, "cancel")
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

                            # Make sure size and progress are updated correctly
                            if download.size:
                                download.size_downloaded = download.size
                                # Progress will be calculated automatically from size_downloaded / size

                            # Explicitly save to file immediately when completed
                            await self.save_downloads()

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

            except asyncio.CancelledError:
                print(f"Download {download_id} cancelled.")
                download.status = DownloadStatus.FAILED # Or some other appropriate status
                download.notes = "Download was cancelled."
                await self.save_and_broadcast_download(download_id, "cancel")
                await self._recalculate_bandwidth_allocation()
                return

            except aiohttp.ClientError as e: # Covers ClientResponseError, ClientConnectionError, etc.
                error_message = f"Download error for {download_id} on URL {current_url_to_try}: {e}"
                if isinstance(e, aiohttp.ClientResponseError):
                    error_message = f"HTTP error for {download_id} on URL {current_url_to_try}: {e.status} {e.message}"
                    # Specific handling for 416 Range Not Satisfiable
                    if e.status == 416 and initial_size > 0:
                        print(f"Got 416 Range Not Satisfiable for {download_id}. Resetting download from beginning.")
                        initial_size = 0
                        download.size_downloaded = 0
                        download.notes = f"Reset due to 416 error on {current_url_to_try}."
                        if os.path.exists(download.save_path):
                            try:
                                async with aiofiles.open(download.save_path, "wb") as f_truncate:
                                    await f_truncate.truncate(0)
                            except Exception as fe:
                                print(f"Error truncating file {download.save_path}: {fe}")
                        await self.save_and_broadcast_download(download_id)
                        continue # Retry immediately with initial_size = 0 for the same URL

                print(error_message)
                download.notes = error_message

                # Attempt HTTP to HTTPS fallback
                parsed_url = urlparse(current_url_to_try)
                if parsed_url.scheme == "http" and not attempted_protocol_switch:
                    print(f"HTTP download for {download_id} failed ({type(e).__name__}). Attempting HTTPS.")
                    current_url_to_try = urlunparse(parsed_url._replace(scheme="https"))
                    attempted_protocol_switch = True
                    initial_size = 0 # Reset progress for new protocol
                    download.size_downloaded = 0
                    download.notes = f"Switched to HTTPS after {type(e).__name__}. Previous error: {e}"
                    await self.save_and_broadcast_download(download_id)
                    # Don't increment retry_count for this specific failure, continue to try new URL
                    continue
                # HTTPS to HTTP fallback
                elif parsed_url.scheme == "https" and not attempted_protocol_switch:
                    print(f"HTTPS download for {download_id} failed ({type(e).__name__}). Attempting HTTP.")
                    current_url_to_try = urlunparse(parsed_url._replace(scheme="http"))
                    attempted_protocol_switch = True
                    initial_size = 0 # Reset progress for new protocol
                    download.size_downloaded = 0
                    download.notes = f"Switched to HTTP after {type(e).__name__}. Previous error: {e}"
                    await self.save_and_broadcast_download(download_id)
                    # Don't increment retry_count for this specific failure, continue to try new URL
                    continue

            except Exception as e:
                print(f"Unexpected download error for {download_id} on {current_url_to_try}: {e}")
                import traceback
                traceback.print_exc()
                download.notes = f"Unexpected error on {current_url_to_try}: {e}"

            # If we are here, an error occurred that wasn't handled by a 'continue' (like protocol switch or 416)
            retry_count += 1
            download.retry_count = retry_count
            if retry_count <= max_retries:
                await self.save_and_broadcast_download(download_id)
                retry_delay = min(2**retry_count, 60)  # Exponential backoff with a cap, e.g., 60s
                print(f"Retrying download {download_id} ({retry_count}/{max_retries}) for {current_url_to_try} in {retry_delay}s...")
                await asyncio.sleep(retry_delay)
            else:
                print(f"Download {download_id} failed after {max_retries} retries on {current_url_to_try}.")
                download.status = DownloadStatus.FAILED
                # Ensure notes reflect the final URL tried and the number of retries
                final_note = f"Failed after {max_retries} retries. Last URL tried: {current_url_to_try}. Last error: {download.notes}"
                if download.notes and str(e) not in download.notes: # Append if distinct
                     final_note = f"Failed after {max_retries} retries on {current_url_to_try}. Last error before final retry: {download.notes}. Final error: {e}"
                elif not download.notes:
                    final_note = f"Failed after {max_retries} retries on {current_url_to_try}. Error: {e}"
                download.notes = final_note[:500] # Truncate notes if too long

                await self.save_and_broadcast_download(download_id)

    async def _download_file_chunked(self, download_id: str, chunks: int = 4, min_chunk_size: int = 5242880) -> None:
        """
        Download a file in parallel chunks for improved speed
        
        Args:
            download_id: The ID of the download
            chunks: Number of chunks to split the file into (default: 4)
            min_chunk_size: Minimum size per chunk in bytes (default: 5MB)
        """
        download = self.downloads.get(download_id)
        if not download:
            print(f"Download {download_id} not found")
            return

        # Ensure we have a file size for chunking
        if not download.size:
            print(f"Cannot use chunked download for {download_id} - file size unknown")
            # Fall back to regular download
            await self._download_file_regular(download_id)
            return

        # Verify the file size is large enough to justify chunking
        if download.size < min_chunk_size * 2:
            print(f"File too small for chunked download ({download.size} bytes), using regular download")
            await self._download_file_regular(download_id)
            return

        # Create parent directory if it doesn't exist
        os.makedirs(os.path.dirname(download.save_path), exist_ok=True)

        # Check if this is resuming a previously paused chunked download
        resuming_chunked = False
        temp_dir = None
        
        if download.status == DownloadStatus.PAUSED and hasattr(download, '_chunked_download_state'):
            try:
                # Get saved state data
                saved_state = download._chunked_download_state
                if saved_state and 'temp_dir' in saved_state and 'chunk_boundaries' in saved_state:
                    temp_dir = saved_state['temp_dir']
                    chunk_boundaries = saved_state['chunk_boundaries']
                    saved_progress = saved_state.get('chunk_progress', [])
                    
                    # Verify temp dir still exists
                    if os.path.isdir(temp_dir):
                        print(f"Resuming chunked download with {len(chunk_boundaries)} chunks from temp dir: {temp_dir}")
                        resuming_chunked = True
                    else:
                        print(f"Temp directory {temp_dir} no longer exists, will start new chunked download")
                        temp_dir = None
            except Exception as e:
                print(f"Error checking saved chunked state: {e}")
                # Will fall back to starting a new chunked download
                
        # Check if we can resume from existing file
        if not resuming_chunked:
            if os.path.exists(download.save_path):
                try:
                    current_size = os.path.getsize(download.save_path)
                    if current_size >= download.size:
                        # File is already complete
                        download.size_downloaded = download.size
                        download.status = DownloadStatus.COMPLETED
                        await self.save_and_broadcast_download(download_id)
                        return
                    elif current_size > 0:
                        # File exists but is incomplete - we'll restart the chunked download
                        print(f"Found partial download of {current_size} bytes, but restarting for chunked method")
                        # We won't set download.size_downloaded here because we'll be using a different tracking method
                except (OSError, FileNotFoundError) as e:
                    print(f"Error checking existing file: {e}")

        # Test if server supports byte range requests (only if not resuming with existing chunks)
        if not resuming_chunked:
            supports_range = False
            current_url_to_try = str(download.url)
            attempted_protocol_switch = False
            
            async with aiohttp.ClientSession() as session:
                try:
                    # Try a small range request to test server support
                    headers = {"Range": "bytes=0-1"}
                    async with session.get(current_url_to_try, headers=headers) as response:
                        supports_range = response.status == 206  # Partial Content response
                except Exception as e:
                    print(f"Error testing range support: {e}")
            
            if not supports_range:
                print(f"Server does not support byte range requests for {download_id}, using regular download")
                await self._download_file_regular(download_id)
                return

            # Determine chunk size and count
            file_size = download.size
            optimal_chunk_size = max(min_chunk_size, file_size // chunks)
            
            # Calculate chunk boundaries
            chunk_boundaries = []
            for i in range(chunks):
                start = i * optimal_chunk_size
                end = min(start + optimal_chunk_size - 1, file_size - 1)
                if start > end:
                    break  # Skip empty chunks
                chunk_boundaries.append((start, end))
        
        # Set up tracking variables
        download.status = DownloadStatus.DOWNLOADING
        if not resuming_chunked:
            download.size_downloaded = 0
        
        # Set up pause/resume handling
        pause_event = asyncio.Event()
        pause_event.set()  # Start in unpaused state
        
        # Create a temporary directory for chunks if not resuming
        if not temp_dir:
            import tempfile
            temp_dir = tempfile.mkdtemp(prefix=f"download_{download_id}_")
        
        # Set up pause/resume callback function
        async def pause_resume_callback(paused: bool = None):
            nonlocal chunk_progress, temp_dir
            
            if paused is None:
                # Toggle pause state
                if pause_event.is_set():
                    pause_event.clear()
                    download.status = DownloadStatus.PAUSED
                    
                    # Save current download state and chunk progress
                    download._chunked_download_state = {
                        "chunk_boundaries": chunk_boundaries,
                        "chunk_progress": chunk_progress.copy(),
                        "temp_dir": temp_dir
                    }
                    
                    await self.save_and_broadcast_download(download_id, "pause")
                    await self._recalculate_bandwidth_allocation()
                    return True
                else:
                    pause_event.set()
                    download.status = DownloadStatus.DOWNLOADING
                    await self.save_and_broadcast_download(download_id, "resume")
                    await self._recalculate_bandwidth_allocation()
                    return True
            else:
                # Explicit pause/unpause
                if paused:
                    pause_event.clear()
                    download.status = DownloadStatus.PAUSED
                    
                    # Save current download state and chunk progress
                    download._chunked_download_state = {
                        "chunk_boundaries": chunk_boundaries,
                        "chunk_progress": chunk_progress.copy(),
                        "temp_dir": temp_dir
                    }
                    
                    await self.save_and_broadcast_download(download_id, "pause")
                    await self._recalculate_bandwidth_allocation()
                    return True
                else:
                    pause_event.set()
                    download.status = DownloadStatus.DOWNLOADING
                    await self.save_and_broadcast_download(download_id, "resume")
                    await self._recalculate_bandwidth_allocation()
                    return True
        
        # Set up cancel callback function
        async def cancel_callback():
            download.status = DownloadStatus.FAILED
            await self.save_and_broadcast_download(download_id, "cancel")
            await self._recalculate_bandwidth_allocation()
            # Clean up temp files on cancellation
            try:
                import shutil
                shutil.rmtree(temp_dir, ignore_errors=True)
            except Exception as e:
                print(f"Error cleaning up temp directory: {e}")
            return True
        
        # Set callbacks
        download.pause_resume_callback = pause_resume_callback
        download.cancel_callback = cancel_callback
        
        # Update download status
        await self.save_and_broadcast_download(download_id)
        await self._recalculate_bandwidth_allocation()
        
        try:
            # Create tasks for downloading each chunk
            chunk_tasks = []
            
            # Initialize chunk progress - either from saved state or fresh
            if resuming_chunked and 'chunk_progress' in download._chunked_download_state:
                # Use the saved progress
                chunk_progress = list(download._chunked_download_state['chunk_progress'])
                # Ensure list is right size
                if len(chunk_progress) != len(chunk_boundaries):
                    # Pad with zeros if necessary
                    chunk_progress.extend([0] * (len(chunk_boundaries) - len(chunk_progress)))
                print(f"Restored chunk progress: {sum(chunk_progress)}/{download.size} bytes")
            else:
                # Start fresh progress tracking
                chunk_progress = [0] * len(chunk_boundaries)
            
            async def download_chunk(chunk_index, start_byte, end_byte):
                chunk_file = os.path.join(temp_dir, f"chunk_{chunk_index}")
                existing_progress = chunk_progress[chunk_index]
                
                # If this chunk is already complete, skip downloading
                chunk_size = end_byte - start_byte + 1
                if existing_progress == chunk_size and os.path.exists(chunk_file):
                    chunk_file_size = os.path.getsize(chunk_file)
                    if chunk_file_size == chunk_size:
                        print(f"Chunk {chunk_index} already complete ({chunk_size} bytes), skipping")
                        return True
                
                # If we have partial progress, adjust the range
                if existing_progress > 0 and os.path.exists(chunk_file):
                    adjusted_start = start_byte + existing_progress
                    print(f"Resuming chunk {chunk_index} from position {adjusted_start} (skipping {existing_progress} bytes)")
                    headers = {"Range": f"bytes={adjusted_start}-{end_byte}"}
                    # Open file in append mode
                    file_mode = 'ab'
                else:
                    # Start from beginning of chunk
                    headers = {"Range": f"bytes={start_byte}-{end_byte}"}
                    # Open file in write mode
                    file_mode = 'wb'
                    # Reset progress for this chunk
                    chunk_progress[chunk_index] = 0
                
                timeout = aiohttp.ClientTimeout(total=None, sock_connect=60, sock_read=60)
                
                # Handle retries for this chunk
                retry_count = 0
                max_retries = download.max_retries
                
                while retry_count <= max_retries:
                    try:
                        async with aiohttp.ClientSession(timeout=timeout) as session:
                            async with session.get(current_url_to_try, headers=headers) as response:
                                if response.status != 206:
                                    print(f"Warning: Chunk {chunk_index} got status {response.status} instead of 206")
                                    if response.status == 416:  # Range Not Satisfiable
                                        # Try requesting the whole chunk again
                                        headers = {"Range": f"bytes={start_byte}-{end_byte}"}
                                        continue
                                        
                                # Open file using correct mode
                                async with aiofiles.open(chunk_file, file_mode) as f:
                                    chunk_size = 1048576  # 1MB read buffer
                                    bytes_downloaded = 0
                                    expected_size = end_byte - start_byte + 1
                                    
                                    # Read and write the chunk
                                    async for data in response.content.iter_chunked(chunk_size):
                                        # Check for pause
                                        await pause_event.wait()
                                        
                                        # Check for cancellation
                                        if download.status == DownloadStatus.FAILED:
                                            return False
                                        
                                        # Write data
                                        await f.write(data)
                                        bytes_downloaded += len(data)
                                        
                                        # Update progress for this chunk
                                        chunk_progress[chunk_index] = bytes_downloaded
                                
                                # Verify chunk size
                                if bytes_downloaded != expected_size:
                                    print(f"Warning: Chunk {chunk_index} size mismatch. Got {bytes_downloaded}, expected {expected_size}")
                                    if bytes_downloaded < expected_size and retry_count < max_retries:
                                        # Retry this chunk
                                        retry_count += 1
                                        continue
                                
                                return True  # Success
                                
                    except asyncio.CancelledError:
                        print(f"Chunk {chunk_index} cancelled")
                        return False
                    except aiohttp.ClientError as e:
                        print(f"Error downloading chunk {chunk_index}: {e}")
                        retry_count += 1
                        if retry_count <= max_retries:
                            await asyncio.sleep(2**retry_count)  # Exponential backoff
                        else:
                            return False
                    except Exception as e:
                        print(f"Unexpected error downloading chunk {chunk_index}: {e}")
                        retry_count += 1
                        if retry_count <= max_retries:
                            await asyncio.sleep(2**retry_count)
                        else:
                            return False
            
            # Initialize total progress tracking
            total_progress = sum(chunk_progress)
            download.size_downloaded = total_progress
            download._last_total_progress = total_progress
            
            # Start downloading chunks
            for i, (start, end) in enumerate(chunk_boundaries):
                task = asyncio.create_task(download_chunk(i, start, end))
                chunk_tasks.append(task)
            
            # Monitor progress while chunks are downloading
            last_update_time = time.time()
            
            while chunk_tasks:
                # Check for pause or cancel
                if download.status == DownloadStatus.FAILED:
                    for task in chunk_tasks:
                        task.cancel()
                    break
                
                # Check progress
                current_time = time.time()
                if current_time - last_update_time >= 1.0:  # Update once per second
                    # Calculate total progress
                    total_downloaded = sum(chunk_progress)
                    download.size_downloaded = total_downloaded
                    
                    # Calculate speed and ETA
                    time_diff = current_time - last_update_time
                    if time_diff > 0:
                        progress_diff = total_downloaded - download._last_total_progress
                        download.speed = int(progress_diff / time_diff)
                        
                        # Calculate ETA
                        if download.speed > 0:
                            remaining_size = download.size - total_downloaded
                            download.time_left = int(remaining_size / download.speed)
                    
                    # Save the progress for next calculation
                    download._last_total_progress = total_downloaded
                    last_update_time = current_time
                    
                    # Broadcast update
                    await self._broadcast_download_update(download_id)
                
                # Check if any tasks have completed
                done, pending = await asyncio.wait(chunk_tasks, timeout=0.5)
                
                # Process completed tasks
                for task in done:
                    try:
                        success = await task
                        if not success:
                            # Cancel all remaining tasks if one fails
                            print("Chunk download failed, cancelling remaining chunks")
                            for remaining_task in pending:
                                remaining_task.cancel()
                            download.status = DownloadStatus.FAILED
                            await self.save_and_broadcast_download(download_id, "error")
                            return
                    except Exception as e:
                        print(f"Error in chunk task: {e}")
                        download.status = DownloadStatus.FAILED
                        await self.save_and_broadcast_download(download_id, "error")
                        return
                
                # Update remaining tasks
                chunk_tasks = list(pending)
            
            # If we reach here, all chunks completed or were cancelled
            if download.status == DownloadStatus.FAILED:
                print(f"Download {download_id} was cancelled during chunk download")
                return
            
            # Combine chunks into the final file
            print(f"All chunks downloaded, combining into final file: {download.save_path}")
            try:
                async with aiofiles.open(download.save_path, 'wb') as outfile:
                    for i in range(len(chunk_boundaries)):
                        chunk_file = os.path.join(temp_dir, f"chunk_{i}")
                        if os.path.exists(chunk_file):
                            async with aiofiles.open(chunk_file, 'rb') as infile:
                                while True:
                                    data = await infile.read(10485760)  # Read 10MB at a time
                                    if not data:
                                        break
                                    await outfile.write(data)
                
                # Update download status
                download.status = DownloadStatus.COMPLETED
                download.size_downloaded = download.size
                download.speed = 0
                download.time_left = None
                
                # Clear callbacks
                download.pause_resume_callback = None
                download.cancel_callback = None
                
                # Final update
                await self.save_and_broadcast_download(download_id, "complete")
                await self._recalculate_bandwidth_allocation()
                
                # Send notification
                await self._send_notification(
                    download_id,
                    "Download Complete",
                    f"The download '{download.name}' has completed successfully.",
                    "success"
                )
                
            except Exception as e:
                print(f"Error combining chunks: {e}")
                download.status = DownloadStatus.FAILED
                download.notes = f"Error combining chunks: {e}"
                await self.save_and_broadcast_download(download_id, "error")
        
        finally:
            # Clean up temp directory
            try:
                import shutil
                shutil.rmtree(temp_dir, ignore_errors=True)
            except Exception as e:
                print(f"Error cleaning up temp directory: {e}")

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

        print(f"Starting YouTube download for {download_id} - URL: {download.url}")

        # Set the download status to downloading
        download.status = DownloadStatus.DOWNLOADING
        await self._broadcast_download_update(download_id)

        # Recalculate bandwidth allocation now that this download is active
        await self._recalculate_bandwidth_allocation()

        # Create output directory if it doesn't exist
        os.makedirs(os.path.dirname(download.save_path), exist_ok=True)

        # Determine the output path
        output_path = download.save_path
        print(f"Initial output path: {output_path}")

        # Get basic info about the video to display
        try:
            print(f"Getting YouTube info for: {download.url}")
            title, size, description = await self.get_youtube_info(str(download.url))
            print(f"YouTube info: title={title}, size={size}")

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
                    print(f"Updated output path: {output_path}")
            if size:
                download.size = size
        except Exception as e:
            print(f"Error getting YouTube info: {e}")
            import traceback

            traceback.print_exc()
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

                await self._broadcast_download_update(download_id)
                await self._recalculate_bandwidth_allocation()
                return True

        # Set callback functions in download object
        download.cancel_callback = cancel_callback
        download.pause_resume_callback = pause_callback

        # Find yt-dlp
        print("Finding yt-dlp command...")
        yt_dlp_cmd = self._find_yt_dlp_command()
        if not yt_dlp_cmd:
            print("ERROR: yt-dlp command not found")
            download.status = DownloadStatus.FAILED
            await self._send_notification(
                download_id,
                "Download Failed",
                "yt-dlp command not found. Please install it using: pip install yt-dlp",
                "error",
            )
            await self._broadcast_download_update(download_id)
            return

        print(f"Using yt-dlp command: {yt_dlp_cmd}")

        # Prepare the yt-dlp command
        cmd = yt_dlp_cmd.copy()

        # Define the progress template
        # Fields: status|total_bytes|downloaded_bytes|speed|eta|filename
        YOUTUBE_PROGRESS_TEMPLATE = "PROGRESS:%(progress.status)s|%(progress.total_bytes)s|%(progress.downloaded_bytes)s|%(progress.speed)s|%(progress.eta)s|%(progress.filename)s"

        # Add rate limit if specified
        if download.max_speed:
            cmd.extend(["--limit-rate", f"{download.max_speed}"])

        # Add options for better progress reporting
        cmd.extend(["--newline"])  # Ensure each message is on a new line
        cmd.extend(["--progress-template", YOUTUBE_PROGRESS_TEMPLATE])

        # Verbose mode for more detailed output
        cmd.append("-v")

        # Add format options based on YouTube type
        if download.youtube_type == YoutubeDownloadType.VIDEO:
            # Download video with highest quality
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
            print(f"YouTube VIDEO download command: {' '.join(cmd)}")
        elif download.youtube_type == YoutubeDownloadType.AUDIO:
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
            print(f"YouTube AUDIO download command: {' '.join(cmd)}")

        # Test yt-dlp command directly
        print("Testing yt-dlp directly...")
        test_cmd = yt_dlp_cmd.copy() + ["--version"]
        try:
            result = subprocess.run(
                test_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False
            )
            print(f"yt-dlp test result: code={result.returncode}, output={result.stdout.strip()}")
        except Exception as e:
            print(f"Error testing yt-dlp: {e}")

        # Start the download process
        if sys.platform == "win32":
            # Windows implementation (unchanged)
            # Create a queue for reading output from the process
            import threading
            from queue import Queue

            output_queue = Queue()

            # Update info about the download
            def run_process():
                # Create a subprocess and capture output
                try:
                    print(f"Starting YouTube download subprocess with command: {' '.join(cmd)}")
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
                    print(f"YouTube download process completed with return code: {returncode}")
                    output_queue.put(f"YTDL_RC:{returncode}")
                except Exception as e:
                    print(f"Error in YouTube download process: {e}")
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
            success_message_found = False  # Track if we've seen a success message in the output

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
                    print(f"yt-dlp output: {line}")

                    # Check for process completion
                    if line.startswith("YTDL_RC:"):
                        return_code = int(line.split(":", 1)[1])
                        print(f"Received return code from yt-dlp: {return_code}")
                        if return_code == 0:
                            download_complete = True
                        break
                    elif line.startswith("YTDL_ERROR:"):
                        error_message = line.split(":", 1)[1]
                        print(f"Received error from yt-dlp: {error_message}")
                        break

                    # Check for success message in the output
                    if (
                        "has already been downloaded" in line
                        or "Merging formats into" in line
                        or "100%" in line
                        or "Deleting original file" in line
                        or "has already been downloaded and merged" in line
                        or "[ExtractAudio] Destination:" in line
                        or "[download] Download completed" in line
                        or "[info] Downloaded" in line
                        or ("ffmpeg" in line and "Merging" in line)
                    ):
                        success_message_found = True
                        print(f"Success indicator found in output: {line}")

                    # Parse progress information
                    self._parse_youtube_progress_line(download, line)

                    # Log progress occasionally for debugging
                    if "[download]" in line and "%" in line:
                        print(f"YouTube download progress: {line}")
                        if download.speed:
                            print(f"  Speed detected: {download.speed} bytes/s")

                    # Update download state periodically - for YouTube downloads we update more frequently
                    # to show real-time speed information
                    current_time = time.time()
                    if (
                        not hasattr(download, "_last_broadcast_time")
                        or current_time - getattr(download, "_last_broadcast_time", 0)
                        >= 0.25  # Changed from 0.5
                    ):
                        # Update at most four times per second
                        await self._broadcast_download_update(download_id)
                        download._last_broadcast_time = current_time

                except Exception as e:
                    # Timeout or other errors, continue
                    print(f"Error processing YouTube output: {e}")
                    await asyncio.sleep(0.1)

            # Handle results
            print(
                f"Download loop completed. complete={download_complete}, success_message={success_message_found}, return_code={return_code}"
            )
            if (
                download_complete
                or success_message_found
                or (return_code is not None and return_code == 0)
                or (
                    os.path.exists(download.save_path)
                    and os.path.getsize(download.save_path) > 0
                    and (
                        download.size is None
                        or os.path.getsize(download.save_path) >= download.size * 0.98
                    )
                )
            ):
                # If we found success indicators, consider the download successful even if return code isn't 0
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
                    if os.path.exists(download.save_path):
                        download.size = os.path.getsize(download.save_path)
                    else:
                        # Try to find the file if name has changed during download
                        dir_path = os.path.dirname(download.save_path)
                        possible_files = os.listdir(dir_path)
                        if possible_files:
                            # Get most recently created file in the directory
                            last_file = max(
                                [os.path.join(dir_path, f) for f in possible_files],
                                key=os.path.getctime,
                            )
                            if os.path.isfile(last_file) and last_file.endswith((".mp4", ".mp3")):
                                download.save_path = last_file
                                download.size = os.path.getsize(last_file)

                download.size_downloaded = download.size

                # File is ready
                await self._broadcast_download_update(download_id)
                await self._send_notification(
                    download_id,
                    "Download Complete",
                    f"'{download.name}' has been downloaded successfully.",
                    "success",
                )

                # Save immediately to ensure completed state is recorded
                await self.save_downloads()

                # Ensure the process has shutdown properly
                try:
                    if "process_thread" in locals() and process_thread.is_alive():
                        print(
                            f"Waiting for YouTube download process to terminate for {download.name}"
                        )
                        process_thread.join(
                            timeout=5.0
                        )  # Wait up to 5 seconds for thread to finish
                        if process_thread.is_alive():
                            print(f"Process thread still alive after timeout for {download.name}")
                except Exception as e:
                    print(f"Error during thread cleanup: {e}")

                # Recalculate bandwidth allocation now that this download is complete
                await self._recalculate_bandwidth_allocation()
            elif is_cancelled:
                print(f"Download cancelled: {download.name}")
                download.status = DownloadStatus.FAILED
                await self._broadcast_download_update(download_id)

                # Save immediately to ensure failed state is recorded
                await self.save_downloads()

                # Recalculate bandwidth allocation now that this download is cancelled
                await self._recalculate_bandwidth_allocation()
            else:
                print(
                    f"Download failed: {download.name} - Return code: {return_code}, Error: {error_message}"
                )

                # Clear any ongoing tasks for this download
                if download_id in self.tasks:
                    try:
                        if not self.tasks[download_id].done():
                            self.tasks[download_id].cancel()
                    except Exception as e:
                        print(f"Error canceling task for failed download: {e}")

                download.status = DownloadStatus.FAILED
                await self._broadcast_download_update(download_id)

                # Save immediately to ensure failed state is recorded
                await self.save_downloads()

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
        else:
            # Linux implementation using direct subprocess call
            print(f"Starting Linux subprocess for YouTube download: {' '.join(cmd)}")
            try:
                process = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    bufsize=1,
                    universal_newlines=True,
                    text=True,
                )

                # Process output and update progress
                line_buffer = ""
                download_complete = False
                success_message_found = False

                for line in iter(process.stdout.readline, ""):
                    # Check for cancellation
                    if is_cancelled:
                        process.kill()
                        break

                    # Check for pause state
                    if is_paused:
                        await asyncio.sleep(0.5)
                        continue

                    line = line.strip()
                    print(f"yt-dlp output: {line}")

                    # Check for success indicators
                    if (
                        "has already been downloaded" in line
                        or "Merging formats into" in line
                        or "100%" in line
                        or "Deleting original file" in line
                        or "has already been downloaded and merged" in line
                        or "[ExtractAudio] Destination:" in line
                        or "[download] Download completed" in line
                        or "[info] Downloaded" in line
                        or ("ffmpeg" in line and "Merging" in line)
                    ):
                        success_message_found = True
                        print(f"Success indicator found in output: {line}")

                    # Parse progress information
                    self._parse_youtube_progress_line(download, line)

                    # Update UI frequently
                    current_time = time.time()
                    if (
                        not hasattr(download, "_last_broadcast_time")
                        or current_time - getattr(download, "_last_broadcast_time", 0) >= 0.25
                    ):
                        await self._broadcast_download_update(download_id)
                        download._last_broadcast_time = current_time

                # Wait for process to finish
                return_code = process.wait()
                print(f"Linux YouTube download process completed with return code: {return_code}")

                # Handle the result
                if return_code == 0 or success_message_found:
                    # Success!
                    if download.youtube_type == YoutubeDownloadType.AUDIO:
                        print(f"Audio extracted successfully: {download.name}")
                    else:
                        print(f"Video downloaded successfully: {download.name}")

                    download.status = DownloadStatus.COMPLETED
                    download.speed = 0
                    download.time_left = None

                    # Update file information
                    if os.path.exists(download.save_path):
                        download.size = os.path.getsize(download.save_path)
                        download.size_downloaded = download.size
                    else:
                        # Look for the file with a different name
                        dir_path = os.path.dirname(download.save_path)
                        if os.path.exists(dir_path):
                            files = os.listdir(dir_path)
                            if files:
                                last_file = max(
                                    [os.path.join(dir_path, f) for f in files], key=os.path.getctime
                                )
                                if os.path.isfile(last_file) and last_file.endswith(
                                    (".mp4", ".mp3")
                                ):
                                    download.save_path = last_file
                                    download.size = os.path.getsize(last_file)
                                    download.size_downloaded = download.size

                    # Send notifications
                    await self._broadcast_download_update(download_id)
                    await self._send_notification(
                        download_id,
                        "Download Complete",
                        f"'{download.name}' has been downloaded successfully.",
                        "success",
                    )

                    # Save status
                    await self.save_downloads()
                    await self._recalculate_bandwidth_allocation()
                else:
                    # Download failed
                    error_message = f"Process failed with return code: {return_code}"
                    print(f"Download failed: {download.name} - {error_message}")

                    download.status = DownloadStatus.FAILED
                    await self._broadcast_download_update(download_id)
                    await self.save_downloads()

                    # Send error notification
                    await self._send_notification(
                        download_id,
                        "Download Failed",
                        f"Failed to download '{download.name}': {error_message}",
                        "error",
                    )

                    await self._recalculate_bandwidth_allocation()

            except Exception as e:
                print(f"Error in Linux YouTube download process: {e}")
                import traceback

                traceback.print_exc()

                download.status = DownloadStatus.FAILED
                await self._broadcast_download_update(download_id)
                await self.save_downloads()
                await self._send_notification(
                    download_id,
                    "Download Failed",
                    f"Error downloading '{download.name}': {str(e)}",
                    "error",
                )

    def _broadcast_download_update_sync(self, download_id: str):
        """Synchronous version of _broadcast_download_update for use in callbacks"""
        asyncio.create_task(self._broadcast_download_update(download_id))

    def _find_yt_dlp_command(self) -> list[str]:
        """Find the yt-dlp command on the system, returns a list of command parts ready for subprocess"""
        possible_commands = [["yt-dlp"], ["yt-dlp.exe"], ["python", "-m", "yt_dlp"]]

        # Add direct path to virtual environment if detected
        venv_path = os.environ.get("VIRTUAL_ENV")
        if venv_path:
            # Check for yt-dlp in virtual environment bin directory
            venv_bin = os.path.join(venv_path, "bin", "yt-dlp")
            if os.path.exists(venv_bin):
                possible_commands.insert(0, [venv_bin])  # Highest priority

        # Check for .venv directory in project root
        current_dir = os.getcwd()
        venv_dir = os.path.join(current_dir, ".venv")
        if os.path.exists(venv_dir):
            venv_bin = os.path.join(venv_dir, "bin", "yt-dlp")
            if os.path.exists(venv_bin):
                possible_commands.insert(0, [venv_bin])  # Highest priority

        # Check if yt-dlp is in the Python scripts directory
        if sys.platform == "win32":
            # Windows-specific paths (unchanged)
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
        else:
            # Linux/Mac paths
            python_dir = os.path.dirname(sys.executable)
            linux_paths = [
                "/usr/bin/yt-dlp",
                "/usr/local/bin/yt-dlp",
                os.path.expanduser("~/.local/bin/yt-dlp"),
                os.path.join(python_dir, "yt-dlp"),
            ]

            # Add system Python executable with -m yt_dlp as high priority option
            possible_commands.insert(0, [sys.executable, "-m", "yt_dlp"])

            # Add Linux paths
            for path in linux_paths:
                if os.path.exists(path):
                    possible_commands.insert(0, [path])  # Give Linux paths higher priority

        # Try each command and print detailed debug info
        print(f"Trying these possible yt-dlp commands: {possible_commands}")
        for cmd in possible_commands:
            try:
                # Use subprocess to check if command exists
                if len(cmd) > 1:  # For commands like ["python", "-m", "yt_dlp"]
                    test_cmd = cmd.copy() + ["--version"]
                else:
                    test_cmd = cmd.copy() + ["--version"]

                print(f"Testing yt-dlp command: {' '.join(test_cmd)}")
                result = subprocess.run(
                    test_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False
                )

                print(
                    f"Result: code={result.returncode}, stdout={result.stdout.strip()}, stderr={result.stderr.strip()}"
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
        """Broadcast a download update to WebSocket clients and save downloads data"""
        # Get the download
        download = self.downloads.get(download_id)
        if not download:
            return

        # Convert to a serializable format
        download_dict = self._prepare_download_for_api(download)

        # Broadcast the update
        await ws_manager.broadcast(
            {"type": "download_update", "download": download_dict, "update_type": "update"}
        )

        # Save downloads data to file more frequently during active downloads
        if download.status in [DownloadStatus.DOWNLOADING, DownloadStatus.PAUSED]:
            await self.save_downloads()

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

        # First try to use the pause_resume_callback if available
        # This supports both regular and chunked downloads that implement their own pause logic
        if download.pause_resume_callback:
            try:
                print(f"Using pause callback for download {download_id}")
                if asyncio.iscoroutinefunction(download.pause_resume_callback):
                    result = await download.pause_resume_callback(paused=True)
                else:
                    result = download.pause_resume_callback(paused=True)
                    
                # If callback succeeded, we're done
                if result:
                    # Even though the callback should have updated the status,
                    # let's make sure it's set correctly
                    if download.status != DownloadStatus.PAUSED:
                        download.status = DownloadStatus.PAUSED
                        await self._broadcast_download_update(download_id)
                    return download
            except Exception as e:
                print(f"Error using pause callback: {e}")
                # Continue with legacy pause method if callback fails

        # Legacy method - cancel the task
        task = self.tasks.get(download_id)
        if task:
            task.cancel()
            await asyncio.sleep(0.1)  # Give the task time to handle cancellation

        download.status = DownloadStatus.PAUSED
        await self._broadcast_download_update(download_id)
        return download

    async def resume_download(self, download_id: str) -> DownloadItem | None:
        """Resume a paused download"""
        download = self.downloads.get(download_id)
        if not download:
            print(f"Download {download_id} not found, attempting recovery")
            # Try to find a download with same URL in failed state (recovery attempt)
            normalized_url = ""
            for potential_id, potential_download in self.downloads.items():
                if potential_download.status == DownloadStatus.FAILED and potential_id.startswith(
                    download_id[:8]
                ):
                    # Found a potential match by ID prefix
                    download = potential_download
                    download_id = potential_id
                    print(f"Found potential download by ID prefix: {potential_id}")
                    break

            if not download and normalized_url:
                # Last resort: try to find by URL if we somehow have a URL but no download
                for potential_id, potential_download in self.downloads.items():
                    potential_url = normalize_url(str(potential_download.url))
                    if potential_url == normalized_url:
                        if potential_download.status == DownloadStatus.FAILED:
                            # Found a failed download with same URL
                            download = potential_download
                            download_id = potential_id
                            print(f"Found potential download by URL: {potential_id}")
                            break
                        elif potential_download.status != DownloadStatus.COMPLETED:
                            # Found a non-completed download with same URL
                            print(f"Cannot resume: URL is already being downloaded: {potential_id}")
                            return potential_download  # Return existing download

            if not download:
                print(f"Download {download_id} recovery failed, not found")
                return None

        # Check if already downloading or completed
        if download.status == DownloadStatus.DOWNLOADING:
            print(f"Download {download_id} is already downloading")
            return download
        elif download.status == DownloadStatus.COMPLETED:
            print(f"Download {download_id} is already completed")
            return download
        elif download.status == DownloadStatus.SCHEDULED:
            print("Can't resume scheduled download, will start at its scheduled time")
            return download

        # If the download has a pause_resume_callback, use it directly
        # This handles in-memory paused downloads without restarting them
        if download.status == DownloadStatus.PAUSED and download.pause_resume_callback:
            try:
                print(f"Using existing pause/resume callback for download {download_id}")
                if asyncio.iscoroutinefunction(download.pause_resume_callback):
                    result = await download.pause_resume_callback(paused=False)
                else:
                    result = download.pause_resume_callback(paused=False)
                    
                # If the callback was successful, we're done
                if result:
                    return download
            except Exception as e:
                print(f"Error using existing pause/resume callback: {e}")
                # Continue with standard resume process below if callback fails
        
        # Set status to QUEUED for restarting the download
        download.status = DownloadStatus.QUEUED

        # If there's an existing task, cancel it before starting a new one
        if download_id in self.tasks and not self.tasks[download_id].done():
            print(f"Cancelling existing task for download {download_id}")
            try:
                self.tasks[download_id].cancel()
                # Give it a moment to clean up
                await asyncio.sleep(0.1)
            except Exception as e:
                print(f"Error cancelling existing task: {e}")

        # Start a new download task
        if download.is_youtube:
            self.tasks[download_id] = asyncio.create_task(self._download_youtube(download_id))
        else:
            self.tasks[download_id] = asyncio.create_task(self._download_file(download_id))

        # Broadcast the update
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
        
    async def cancel_all_active_downloads(self) -> int:
        """Cancel all active downloads during shutdown"""
        count = 0
        for download_id, download in self.downloads.items():
            if download.status in [DownloadStatus.DOWNLOADING, DownloadStatus.QUEUED, DownloadStatus.PAUSED]:
                # Use the cancel callback if available
                if download.cancel_callback:
                    if asyncio.iscoroutinefunction(download.cancel_callback):
                        await download.cancel_callback()
                    else:
                        download.cancel_callback()
                
                # Cancel the task if it exists
                if download_id in self.tasks and not self.tasks[download_id].done():
                    try:
                        self.tasks[download_id].cancel()
                        # Give it a moment to cancel
                        with contextlib.suppress(asyncio.CancelledError):
                            await asyncio.wait_for(self.tasks[download_id], timeout=1.0)
                    except Exception as e:
                        print(f"Error canceling task for {download_id}: {e}")
                
                # Update status
                download.status = DownloadStatus.FAILED
                count += 1
                
                # Don't broadcast during shutdown as WebSockets may be closed
                # Just ensure the state is saved
                
        # Save the state after cancelling all downloads        
        if count > 0:
            await self.save_downloads()
            print(f"Cancelled {count} active downloads during shutdown")
        
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
        """Parse a progress line from yt-dlp"""
        try:
            # 1. Check for new PROGRESS: template
            if line.startswith("PROGRESS:"):
                parts = line[len("PROGRESS:"):].split("|")
                if len(parts) == 6:
                    status, total_bytes_str, downloaded_bytes_str, speed_str, eta_str, filename_str = parts
                    
                    # Update status (for logging or future use)
                    # print(f"YT Progress Status: {status}, Filename: {filename_str}")

                    if total_bytes_str and total_bytes_str.lower() != 'na' and total_bytes_str.strip():
                        try:
                            total_bytes = int(float(total_bytes_str))
                            if total_bytes > 0:
                                download.size = total_bytes
                        except ValueError:
                            pass # Could not parse total_bytes

                    if downloaded_bytes_str and downloaded_bytes_str.lower() != 'na' and downloaded_bytes_str.strip():
                        try:
                            download.size_downloaded = int(float(downloaded_bytes_str))
                        except ValueError:
                            pass # Could not parse downloaded_bytes
                    
                    if speed_str and speed_str.lower() != 'na' and speed_str.strip():
                        try:
                            speed = int(float(speed_str))
                            if speed >= 0: # Speed can be 0
                                download.speed = speed
                        except ValueError:
                            download.speed = 0 # Default if parsing numeric fails
                    elif speed_str and speed_str.strip().lower() == 'na': # Explicitly handle 'na'
                        download.speed = 0

                    if eta_str and eta_str.lower() != 'na' and eta_str.strip():
                        try:
                            eta = int(float(eta_str))
                            if eta >= 0: # ETA can be 0
                                download.time_left = eta
                        except ValueError:
                            download.time_left = None # Default if parsing numeric fails
                    elif eta_str and eta_str.strip().lower() == 'na': # Explicitly handle 'na'
                        download.time_left = None
                    
                    # If total size is known, ensure downloaded_bytes does not exceed total_bytes
                    if download.size and download.size > 0:
                        download.size_downloaded = min(download.size_downloaded, download.size)

                    return download # Processed with new template

            # Check for our custom template format (bytes|speed|eta) - This was a prior attempt, new template is better
            # if "|" in line and line.count("|") == 2 and "/" in line and line.count("/") == 1:
            # try:
            # Format should be: downloaded_bytes/total_bytes|speed|eta
            # size_part, speed_part, eta_part = line.split("|")
            # downloaded, total = size_part.split("/")

            # # Parse size values, handling 'NA' values
            # if downloaded.strip() and downloaded.strip().upper() != "NA":
            # download.size_downloaded = int(downloaded)

            # if total.strip() and total.strip().upper() != "NA":
            # download.size = int(total)

            # # Parse speed (should be in bytes/s), handling 'NA' values
            # if speed_part.strip() and speed_part.strip().upper() != "NA":
            # download.speed = int(float(speed_part))

            # # Parse ETA (should be in seconds), handling 'NA' values
            # if eta_part.strip() and eta_part.strip().upper() != "NA":
            # download.time_left = int(float(eta_part))

            # return download
            # except (ValueError, IndexError) as e:
            # print(f"Error parsing custom progress template: {e}")


            if "[download]" in line:
                # Handle regular progress lines like:
                # [download]  17.9% of 48.59MiB at 957.45KiB/s ETA 00:43
                if "%" in line:
                    parts = line.split()
                    try:
                        # Extract progress percentage
                        percent_str = next((p for p in parts if "%" in p), None)
                        if percent_str:
                            progress_percent = float(percent_str.replace("%", ""))
                            # Update size_downloaded based on percentage instead of setting progress directly
                            if download.size and download.size > 0:
                                download.size_downloaded = int(
                                    download.size * (progress_percent / 100)
                                )

                        # Extract download speed using the keyword "at" which precedes speed in yt-dlp output
                        at_index = -1
                        if "at" in parts:
                            try:
                                at_index = parts.index("at")
                                if at_index + 1 < len(parts):
                                    speed_str = parts[at_index + 1]
                                    if any(
                                        unit in speed_str.upper()
                                        for unit in ["KIB/S", "MIB/S", "B/S", "GIB/S"]
                                    ):
                                        speed = self._parse_speed(speed_str)
                                        if speed > 0:
                                            download.speed = speed
                            except ValueError:
                                pass

                        # Fallback to the old method if "at" pattern not found
                        if at_index == -1 and len(parts) > 3:
                            # Look for speed near the end of the line
                            for i in range(len(parts) - 1, max(0, len(parts) - 5), -1):
                                if i >= 0 and any(
                                    unit in parts[i].upper()
                                    for unit in ["KIB/S", "MIB/S", "B/S", "GIB/S"]
                                ):
                                    speed = self._parse_speed(parts[i])
                                    if speed > 0:
                                        download.speed = speed
                                    break

                        # Extract ETA
                        if "ETA" in line:
                            eta_index = parts.index("ETA") + 1
                            if eta_index < len(parts):
                                eta = parts[eta_index]
                                seconds = self._parse_eta(eta)
                                download.time_left = seconds

                        # If we got speed data but not size, try to estimate size based on progress
                        if download.speed > 0 and download.time_left and download.size is None:
                            # Estimate total size = current downloaded + (speed * time left)
                            estimated_remaining = download.speed * download.time_left
                            if progress_percent > 0:
                                estimated_total = download.size_downloaded + estimated_remaining
                                download.size = estimated_total

                        # Save progress more frequently for large files or when progress changes significantly
                        if download.size and download.size > 0:
                            progress = download.progress  # Use the property
                            if download.size > 10 * 1024 * 1024:  # 10MB threshold for "large" files
                                # Save every 5% progress for large files
                                if not hasattr(download, "_last_saved_size"):
                                    download._last_saved_size = 0

                                size_diff = download.size_downloaded - getattr(
                                    download, "_last_saved_size", 0
                                )
                                if progress % 5 < (100 * size_diff / download.size):
                                    download._last_saved_size = download.size_downloaded
                                    asyncio.create_task(self.save_downloads())
                            else:
                                # Save every 10% progress for smaller files
                                if not hasattr(download, "_last_saved_size"):
                                    download._last_saved_size = 0

                                size_diff = download.size_downloaded - getattr(
                                    download, "_last_saved_size", 0
                                )
                                if progress % 10 < (100 * size_diff / download.size):
                                    download._last_saved_size = download.size_downloaded
                                    asyncio.create_task(self.save_downloads())

                        # Update reference values for next iteration
                        if not hasattr(download, "_last_time"):
                            download._last_time = time.time()
                        if not hasattr(download, "_last_size"):
                            download._last_size = download.size_downloaded

                        download._last_time = time.time()
                        download._last_size = download.size_downloaded

                    except (ValueError, IndexError) as e:
                        # Skip lines that don't match expected format
                        print(f"Error parsing progress: {e}")
                        pass

                # Handle specific size info lines like:
                # [download] 100% of 48.59MiB in 00:13
                elif "100%" in line and "of" in line and "in" in line:
                    try:
                        # Extract the file size part
                        size_part = line.split("of")[1].split("in")[0].strip()
                        size_bytes = self._parse_size(size_part)
                        if size_bytes > 0:
                            download.size = size_bytes
                            download.size_downloaded = size_bytes
                            # No need to set progress directly as it will be calculated from size_downloaded

                            # Initialize size_diff for this final state
                            if not hasattr(download, "_last_saved_size"):
                                download._last_saved_size = 0
                            size_diff = size_bytes - getattr(download, "_last_saved_size", 0)
                            download._last_saved_size = size_bytes

                        # Save the final state immediately
                        asyncio.create_task(self.save_downloads())
                    except (ValueError, IndexError):
                        pass

            # Handle destination lines to get the final filename
            elif "[Merger] Merging" in line and "into" in line:
                try:
                    # Extract the final filename
                    final_file = line.split("into")[1].strip().strip("\"'")
                    if final_file:
                        # Update the save path with the final filename
                        save_dir = os.path.dirname(download.save_path)
                        download.save_path = os.path.join(save_dir, os.path.basename(final_file))
                except IndexError:
                    pass

            # Parse detected output filename lines
            elif "Detected output filename:" in line:
                try:
                    filename = line.split(":", 1)[1].strip()
                    if filename:
                        print(f"Detected output filename: {filename}")
                        # Update only if this is the final file (not a temporary audio/video component)
                        filename_lower = filename.lower()
                        if (
                            download.youtube_type == YoutubeDownloadType.VIDEO
                            and ".mp4" in filename_lower
                        ) or (
                            download.youtube_type == YoutubeDownloadType.AUDIO
                            and ".mp3" in filename_lower
                        ):
                            download.save_path = filename
                            download.name = os.path.basename(filename)
                            # Save this info immediately
                            asyncio.create_task(self.save_downloads())
                except Exception as e:
                    print(f"Error parsing output filename: {e}")

        except Exception as e:
            # Log any other parsing errors
            print(f"Error parsing yt-dlp output: {e}")

        return download

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

    async def shutdown_scheduler(self):
        """Gracefully shutdown the scheduler and save any pending tasks"""
        print("Shutting down scheduler...")

        # Cancel the scheduler task if it exists
        if self.scheduler_task:
            try:
                # Set a flag to indicate scheduler is shutting down
                self._scheduler_shutdown = True

                # Cancel the task
                self.scheduler_task.cancel()

                # Wait for the task to be cancelled
                try:
                    await asyncio.wait_for(self.scheduler_task, timeout=2.0)
                except TimeoutError:
                    print("Scheduler task cancellation timed out")
                except asyncio.CancelledError:
                    print("Scheduler task cancelled successfully")

                # Clear the scheduler task
                self.scheduler_task = None

                # Save any scheduled downloads state
                await self.save_downloads()

                print("Scheduler shutdown complete")
                return True
            except Exception as e:
                print(f"Error shutting down scheduler: {e}")
                return False
        else:
            print("No active scheduler task to shutdown")
            return True

    def _parse_speed(self, speed_str: str) -> int:
        """Parse speed string (like '1.2MiB/s') and convert to bytes per second"""
        try:
            # Handle NA or empty values
            if not speed_str or speed_str.strip().upper() == "NA":
                return 0

            # Remove the '/s' part
            if "/s" in speed_str:
                speed_str = speed_str.split("/s")[0]

            # Extract the numeric part and unit
            if speed_str.upper().endswith("KIB"):
                value = float(speed_str[:-3])
                return int(value * 1024)
            elif speed_str.upper().endswith("MIB"):
                value = float(speed_str[:-3])
                return int(value * 1024 * 1024)
            elif speed_str.upper().endswith("GIB"):
                value = float(speed_str[:-3])
                return int(value * 1024 * 1024 * 1024)
            elif speed_str.upper().endswith("B"):
                value = float(speed_str[:-1])
                return int(value)
            else:
                # Try to parse as a plain number
                return int(float(speed_str))
        except (ValueError, IndexError):
            return 0

    def _parse_size(self, size_str: str) -> int:
        """Parse size string (like '48.59MiB') and convert to bytes"""
        try:
            if size_str.upper().endswith("KIB"):
                value = float(size_str[:-3])
                return int(value * 1024)
            elif size_str.upper().endswith("MIB"):
                value = float(size_str[:-3])
                return int(value * 1024 * 1024)
            elif size_str.upper().endswith("GIB"):
                value = float(size_str[:-3])
                return int(value * 1024 * 1024 * 1024)
            elif size_str.upper().endswith("B"):
                value = float(size_str[:-1])
                return int(value)
            else:
                # Try to parse as a plain number
                return int(float(size_str))
        except (ValueError, IndexError):
            return 0

    def _parse_eta(self, eta_str: str) -> int:
        """Parse ETA string (like '01:30') and convert to seconds"""
        try:
            parts = eta_str.split(":")
            if len(parts) == 2:
                # MM:SS format
                minutes, seconds = map(int, parts)
                return minutes * 60 + seconds
            elif len(parts) == 3:
                # HH:MM:SS format
                hours, minutes, seconds = map(int, parts)
                return hours * 3600 + minutes * 60 + seconds
            else:
                return 0
        except (ValueError, IndexError):
            return 0

    async def load_downloads(self):
        """Load downloads from a JSON file"""
        if not os.path.exists(self.storage_file):
            return False

        try:
            async with aiofiles.open(self.storage_file) as f:
                content = await f.read()
                downloads_data = json.loads(content)

            # First pass to validate the data
            valid_downloads = {}
            duplicate_urls = {}

            # Identify duplicates and track them for removal
            for download_id, download_dict in downloads_data.items():
                try:
                    # Convert ISO datetime string back to datetime
                    if isinstance(download_dict.get("date_added"), str):
                        download_dict["date_added"] = datetime.fromisoformat(
                            download_dict["date_added"]
                        )
                    else:
                        # Skip entries with invalid date format
                        print(f"Skipping download with invalid date: {download_id}")
                        continue

                    # Convert scheduled_time to ISO format if it exists
                    if download_dict.get("schedule") and download_dict["schedule"].get(
                        "scheduled_time"
                    ):
                        if isinstance(download_dict["schedule"]["scheduled_time"], str):
                            download_dict["schedule"]["scheduled_time"] = datetime.fromisoformat(
                                download_dict["schedule"]["scheduled_time"]
                            )
                        else:
                            # Invalid schedule time format
                            download_dict["schedule"]["scheduled_time"] = None

                    # Check for duplicate URLs - using improved normalization
                    original_url = str(download_dict.get("url", "")).strip()
                    url = normalize_url(original_url)

                    if url:
                        if url in duplicate_urls:
                            # Found a duplicate URL, keep the newest one or completed one
                            existing_id = duplicate_urls[url]
                            existing_dict = valid_downloads.get(existing_id)

                            # Keep the completed one if any, otherwise the newer one
                            if existing_dict.get("status") == "completed":
                                # Keep existing, ignore this one
                                print(
                                    f"Skipping duplicate download for URL: {url}, keeping completed one"
                                )
                                continue
                            elif download_dict.get("status") == "completed":
                                # Remove existing, keep this one
                                print(
                                    f"Replacing duplicate download for URL: {url} with completed one"
                                )
                                valid_downloads.pop(existing_id, None)
                                valid_downloads[download_id] = download_dict
                                duplicate_urls[url] = download_id
                            else:
                                # If any of the downloads is actively downloading or queued, prefer that one
                                active_statuses = ["downloading", "queued", "paused"]
                                if (
                                    existing_dict.get("status") in active_statuses
                                    and download_dict.get("status") not in active_statuses
                                ):
                                    # Keep the active one
                                    print(
                                        f"Skipping duplicate download for URL: {url}, keeping active one"
                                    )
                                    continue
                                elif (
                                    download_dict.get("status") in active_statuses
                                    and existing_dict.get("status") not in active_statuses
                                ):
                                    # Keep this active one, remove the inactive one
                                    print(
                                        f"Replacing inactive duplicate download for URL: {url} with active one"
                                    )
                                    valid_downloads.pop(existing_id, None)
                                    valid_downloads[download_id] = download_dict
                                    duplicate_urls[url] = download_id
                                else:
                                    # Compare dates and keep newer one if both are in similar states
                                    if download_dict["date_added"] > existing_dict["date_added"]:
                                        print(
                                            f"Replacing duplicate download for URL: {url} with newer one"
                                        )
                                        valid_downloads.pop(existing_id, None)
                                        valid_downloads[download_id] = download_dict
                                        duplicate_urls[url] = download_id
                                    else:
                                        # Keep existing, ignore this one
                                        print(
                                            f"Skipping duplicate download for URL: {url}, keeping newer one"
                                        )
                                        continue
                        else:
                            # First time seeing this URL
                            valid_downloads[download_id] = download_dict
                            duplicate_urls[url] = download_id
                    else:
                        # No URL, but still a valid entry
                        valid_downloads[download_id] = download_dict

                except Exception as e:
                    print(f"Error validating download entry {download_id}: {e}")
                    continue

            # Now create objects only from valid entries
            self.downloads = {}
            for download_id, download_dict in valid_downloads.items():
                try:
                    # Create DownloadItem from dict
                    download = DownloadItem(**download_dict)
                    self.downloads[download_id] = download
                except Exception as e:
                    print(f"Error creating download object {download_id}: {e}")

            # If we filtered out any downloads, save the cleaned up version
            if len(downloads_data) != len(self.downloads):
                print(
                    f"Cleaned up downloads: removed {len(downloads_data) - len(self.downloads)} corrupt/duplicate entries"
                )
                await self.save_downloads()

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


def normalize_url(url: str) -> str:
    """
    Normalize a URL to allow for better duplicate detection.
    Handles various edge cases like:
    - http vs https
    - www vs non-www
    - Trailing slashes
    - URL encoding differences
    - Common query parameter ordering
    """
    if not url:
        return ""

    try:
        # Parse the URL
        parsed = urlparse(url)

        # Normalize the netloc (domain) part - remove www if present
        netloc = parsed.netloc
        if netloc.startswith("www."):
            netloc = netloc[4:]

        # Normalize the path - ensure trailing slash consistency and decode URL encoding
        path = unquote(parsed.path)
        if path == "":
            path = "/"

        # Sort query parameters for consistent ordering
        if parsed.query:
            query_params = parse_qs(parsed.query)
            # Sort the query parameters by key
            sorted_query = urlencode(sorted(query_params.items()), doseq=True)
        else:
            sorted_query = ""

        # Rebuild the URL with normalized components (using https)
        # We intentionally ignore the scheme (http/https) for duplicate detection
        normalized = urlunparse(("", netloc, path, parsed.params, sorted_query, ""))

        # Remove trailing slash from normalized URL if it's just a slash
        if normalized.endswith("/") and normalized != "/":
            normalized = normalized[:-1]

        return normalized
    except Exception as e:
        print(f"Error normalizing URL {url}: {e}")
        # If normalization fails, return the original stripped URL
        return url.strip()


# Singleton instance
download_manager = DownloadManager()
