from datetime import datetime, timedelta

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query

from app.models.download import (
    BandwidthSettings,
    CreateDownloadRequest,
    DownloadItemResponse,
    DownloadPriority,
    DownloadResponse,
    DownloadsListResponse,
    DownloadStatus,
    FileCategory,
    ScheduleSettings,
    SearchQuery,
)
from app.services.downloader import download_manager

router = APIRouter(prefix="/api/downloads", tags=["downloads"])


@router.post("", response_model=DownloadResponse)
async def create_download(request: CreateDownloadRequest, background_tasks: BackgroundTasks):
    """Add a new download"""
    download = await download_manager.add_download(request)

    # For YouTube downloads, use a background task to extract video info
    if download.is_youtube:
        # Always extract YouTube info immediately, even for scheduled downloads
        # This ensures we have accurate metadata before the download starts
        async def update_youtube_info(download_id: str, url: str):
            try:
                # Get video info in the background
                title, size, description = await download_manager.get_youtube_info(str(url))

                # Update download info if we got a title
                if title:
                    # Get the download again - it might have changed
                    download = download_manager.get_download(download_id)
                    if download:
                        download.name = title

                        # Update save path with proper name
                        if (
                            download.save_path
                            and download.save_path.endswith(".mp4")
                            or download.save_path.endswith(".mp3")
                        ):
                            import os
                            import re

                            # Sanitize filename for filesystem
                            safe_title = re.sub(
                                r'[\\/*?:"<>|]', "", title
                            )  # Remove illegal characters
                            safe_title = re.sub(
                                r"\s+", " ", safe_title
                            ).strip()  # Normalize whitespace
                            safe_title = safe_title[:100]  # Truncate if too long

                            extension = ".mp4"
                            if download.youtube_type and download.youtube_type.value == "audio":
                                extension = ".mp3"

                            new_path = os.path.join(
                                os.path.dirname(download.save_path), f"{safe_title}{extension}"
                            )
                            download.save_path = new_path

                        # Update size if we have it
                        if size:
                            download.size = size

                        # For scheduled downloads, add a description note with extracted info
                        if download.status == DownloadStatus.SCHEDULED:
                            # Store the description and any other metadata we want to preserve
                            download.tags = list(set(download.tags + ["youtube", "scheduled"]))

                            # Add metadata about when the download will start
                            if download.schedule and download.schedule.scheduled_time:
                                time_str = download.schedule.scheduled_time.strftime(
                                    "%Y-%m-%d %H:%M:%S"
                                )
                                download.notes = f"YouTube video info extracted. Scheduled to start at {time_str}.\n\n{description or ''}"
                            else:
                                download.notes = (
                                    f"YouTube video info extracted.\n\n{description or ''}"
                                )

                        # Save the updated info
                        await download_manager.save_downloads()

                        # Broadcast the update to clients
                        await download_manager._broadcast_download_update(download_id)
            except Exception as e:
                print(f"Error updating YouTube info in background: {e}")

        # Add the background task
        background_tasks.add_task(update_youtube_info, download.id, str(download.url))

    # Get a serializable version
    download_dict = download_manager._prepare_download_for_api(download)
    return {"download": download_dict}


@router.get("", response_model=DownloadsListResponse)
async def list_downloads(category: FileCategory | None = None, status: str | None = None):
    """
    List all downloads with optional filtering

    The status parameter can be a single status or a comma-separated list of statuses
    Example: status=downloading or status=queued,downloading,paused
    """
    # Handle multiple statuses if provided as comma-separated string
    statuses = None
    if status:
        if "," in status:
            # Split the comma-separated string into a list
            status_values = status.split(",")
            # Convert each string to DownloadStatus enum values
            try:
                statuses = [DownloadStatus(s.strip()) for s in status_values]
            except ValueError as e:
                raise HTTPException(
                    status_code=422,
                    detail=(
                        "Invalid status values. Allowed values:"
                        f" {[s.value for s in DownloadStatus]}"
                    ),
                ) from e
        else:
            # Single status value
            try:
                statuses = [DownloadStatus(status)]
            except ValueError as e:
                raise HTTPException(
                    status_code=422,
                    detail=(
                        f"Invalid status value. Allowed values: {[s.value for s in DownloadStatus]}"
                    ),
                ) from e

    # Get downloads with the specified filters
    if statuses:
        # Handle multiple statuses
        all_downloads = []
        for status_value in statuses:
            # Get serializable downloads already prepared for API
            downloads_for_status = download_manager.get_downloads(category, status_value)
            all_downloads.extend(downloads_for_status)

        # Remove duplicates (in case a download matches multiple statuses)
        unique_downloads = {download["id"]: download for download in all_downloads}
        downloads = list(unique_downloads.values())
    else:
        # Just filter by category
        downloads = download_manager.get_downloads(category)

    return {"downloads": downloads, "total": len(downloads)}


@router.post("/search", response_model=DownloadsListResponse)
async def search_downloads(search_query: SearchQuery):
    """
    Search for downloads using advanced criteria

    Examples:
        - Search by text: {"query": "ubuntu"}
        - Search by category: {"category": "compressed"}
        - Search by status: {"status": ["completed", "failed"]}
        - Search by date range: {"date_from": "2023-01-01T00:00:00", "date_to": "2023-12-31T23:59:59"}
        - Search by size range: {"min_size": 1048576, "max_size": 10485760}
        - Search by tags: {"tags": ["linux", "iso"]}
        - Combined search: {"query": "ubuntu", "category": "compressed", "tags": ["linux"]}
    """
    downloads = await download_manager.search_downloads(search_query)
    return {"downloads": downloads, "total": len(downloads)}


@router.get("/{download_id}", response_model=DownloadResponse)
async def get_download(download_id: str):
    """Get a specific download by ID"""
    download = download_manager.get_download(download_id)
    if not download:
        raise HTTPException(status_code=404, detail="Download not found")
    # Get a serializable version
    download_dict = download_manager._prepare_download_for_api(download)
    return {"download": download_dict}


@router.post("/{download_id}/pause", response_model=DownloadItemResponse)
async def pause_download(download_id: str) -> dict:
    """Pause a download"""
    download = await download_manager.pause_download(download_id)

    if not download:
        raise HTTPException(
            status_code=404, detail=f"Download {download_id} not found or not in downloading state"
        )

    # Get a serializable version
    download_dict = download_manager._prepare_download_for_api(download)
    return {"download": download_dict}


@router.post("/{download_id}/resume", response_model=DownloadItemResponse)
async def resume_download(download_id: str) -> dict:
    """Resume a paused download"""
    download = await download_manager.resume_download(download_id)
    if not download:
        raise HTTPException(status_code=404, detail="Download not found or not in paused state")

    # Get a serializable version
    download_dict = download_manager._prepare_download_for_api(download)
    return {"download": download_dict}


@router.delete("/{download_id}")
async def delete_download(
    download_id: str,
    delete_file: bool = Query(False, description="Whether to delete the downloaded file as well"),
):
    """Delete a download"""
    try:
        success = await download_manager.delete_download(download_id, delete_file)
        if not success:
            # If the download is not found in the manager, still return success
            # to remove it from the client-side GUI
            return {"success": True, "message": "Download not found but removed from GUI"}
        return {"success": True}
    except Exception as e:
        # Catch any exceptions from delete_download and return a 500 error
        # This won't normally happen as we handle exceptions in the delete_download method
        raise HTTPException(
            status_code=500, detail=f"An error occurred while deleting the download: {str(e)}"
        ) from e


@router.post("/pause-all")
async def pause_all_downloads():
    """Pause all active downloads"""
    count = await download_manager.pause_all()
    return {"paused_count": count}


@router.post("/resume-all")
async def resume_all_downloads():
    """Resume all paused downloads"""
    count = await download_manager.resume_all()
    return {"resumed_count": count}


@router.post("/{download_id}/open")
async def open_download(download_id: str) -> dict:
    """Open a downloaded file with the default system application"""
    download = download_manager.get_download(download_id)

    if not download:
        raise HTTPException(status_code=404, detail=f"Download {download_id} not found")

    if download.status != DownloadStatus.COMPLETED:
        raise HTTPException(status_code=400, detail="Cannot open file: download is not completed")

    success = await download_manager.open_file(download_id)

    if not success:
        raise HTTPException(status_code=500, detail="Failed to open file")

    return {"success": True, "message": f"Opening file: {download.name}"}


@router.post("/{download_id}/settings", response_model=DownloadItemResponse)
async def update_download_settings(
    download_id: str,
    priority: DownloadPriority | None = None,
    max_speed: int | None = None,
    max_retries: int | None = None,
    bandwidth_allocation: float | None = None,
    tags: list[str] | None = None,
):
    """Update the settings for a specific download"""
    download = download_manager.get_download(download_id)

    if not download:
        raise HTTPException(status_code=404, detail="Download not found")

    # Update settings if provided
    if priority is not None:
        download.priority = priority

    if max_speed is not None:
        download.max_speed = max_speed

    if max_retries is not None:
        download.max_retries = max_retries

    if bandwidth_allocation is not None:
        download.bandwidth_allocation = bandwidth_allocation

    if tags is not None:
        download.tags = tags

    # Save changes
    await download_manager.save_downloads()

    # Broadcast update to clients
    await download_manager._broadcast_download_update(download_id)

    # Get updated download data
    download_dict = download_manager._prepare_download_for_api(download)
    return {"download": download_dict}


@router.post("/{download_id}/schedule", response_model=DownloadItemResponse)
async def schedule_download(download_id: str, schedule: ScheduleSettings):
    """
    Schedule a download to start at a specific time

    Features:
    - One-time or recurring schedules (daily, weekly, monthly)
    - Day-of-week selection for weekly recurrence
    - Day-of-month selection for monthly recurrence
    - Automatic retry for failed scheduled downloads
    - Priority boost option for scheduled downloads
    - Bandwidth allocation control
    """
    print(f"Received schedule request for download {download_id}: {schedule}")

    download = download_manager.get_download(download_id)

    if not download:
        raise HTTPException(status_code=404, detail="Download not found")

    # Only allowed for downloads that aren't currently running
    if download.status == DownloadStatus.DOWNLOADING:
        raise HTTPException(status_code=400, detail="Cannot schedule an active download")

    # Validate scheduled time
    if not schedule.scheduled_time:
        raise HTTPException(status_code=400, detail="Scheduled time must be provided")

    # Log the scheduled time for debugging
    print(f"Scheduling download for time: {schedule.scheduled_time.isoformat()}")

    # For weekly recurrence, validate days_of_week
    if schedule.recurrence == "weekly" and (
        not schedule.days_of_week or len(schedule.days_of_week) == 0
    ):
        raise HTTPException(
            status_code=400, detail="Days of week must be provided for weekly recurrence"
        )

    # For monthly recurrence, validate day_of_month
    if schedule.recurrence == "monthly" and not schedule.day_of_month:
        # If not specified, use the day from the scheduled_time
        schedule.day_of_month = schedule.scheduled_time.day
        print(f"Using day of month from scheduled time: {schedule.day_of_month}")

    # Convert to dict and preserve datetime format
    schedule_dict = schedule.model_dump()

    # Use the new dedicated method to schedule the download
    try:
        result = await download_manager.schedule_download(download_id, schedule_dict)

        if not result:
            raise HTTPException(status_code=500, detail="Failed to schedule download")

        # Get updated download data
        download_dict = download_manager._prepare_download_for_api(result)
        return {"download": download_dict}
    except Exception as e:
        print(f"ERROR scheduling download {download_id}: {str(e)}")
        import traceback

        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Error scheduling download: {str(e)}")


@router.get("/bandwidth/settings")
async def get_bandwidth_settings():
    """Get the current bandwidth settings"""
    return download_manager.bandwidth_settings


@router.post("/bandwidth/settings")
async def update_bandwidth_settings(settings: BandwidthSettings):
    """Update global bandwidth settings"""
    success = await download_manager.update_bandwidth_settings(settings)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to update bandwidth settings")
    return {"success": True}


@router.post("/scheduler/settings")
async def update_scheduler_settings(check_interval_seconds: int):
    """
    Update the scheduler check interval

    Parameters:
    - check_interval_seconds: How often to check for scheduled downloads (minimum 5 seconds)
    """
    success = await download_manager.update_scheduler_settings(check_interval_seconds)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to update scheduler settings")
    return {"success": True, "check_interval_seconds": check_interval_seconds}


@router.post("/scheduler/shutdown")
async def shutdown_scheduler():
    """
    Gracefully shutdown the scheduler for scheduled downloads

    Used during application shutdown to ensure scheduled downloads state is saved properly.
    """
    success = await download_manager.shutdown_scheduler()
    if not success:
        raise HTTPException(status_code=500, detail="Failed to shutdown scheduler")
    return {"success": True, "message": "Scheduler shutdown successfully"}


@router.post("/{download_id}/tags")
async def update_download_tags(download_id: str, tags: list[str]):
    """Update the tags for a download"""
    download = download_manager.get_download(download_id)

    if not download:
        raise HTTPException(status_code=404, detail="Download not found")

    # Update tags
    download.tags = tags

    # Save changes
    await download_manager.save_downloads()

    # Broadcast update
    await download_manager._broadcast_download_update(download_id)

    return {"success": True, "tags": tags}


@router.post("/batch-schedule")
async def batch_schedule_downloads(
    download_ids: list[str],
    schedule: ScheduleSettings,
    priority_boost: bool = False,
):
    """
    Schedule multiple downloads at once with the same schedule settings

    This is useful for scheduling batches of downloads to start at the same time
    """
    results = {"successful": [], "failed": []}

    for download_id in download_ids:
        try:
            result = await download_manager.schedule_download(
                download_id, {**schedule.model_dump(), "priority_boost": priority_boost}
            )

            if result:
                download_dict = download_manager._prepare_download_for_api(result)
                results["successful"].append(download_dict)
            else:
                results["failed"].append(
                    {"id": download_id, "error": "Failed to schedule download"}
                )
        except Exception as e:
            results["failed"].append({"id": download_id, "error": str(e)})

    return results


@router.post("/smart-schedule")
async def smart_schedule_downloads(
    schedule: ScheduleSettings,
    max_concurrent: int = Query(3, description="Maximum number of concurrent downloads to allow"),
    spacing_minutes: int = Query(5, description="Minutes between each download start"),
    download_ids: list[str] | None = None,
    status_filter: list[DownloadStatus] | None = None,
):
    """
    Create a smart schedule for downloads that spaces them out over time

    This prevents all downloads from starting at the same time, avoiding bandwidth bottlenecks.
    You can pass specific download IDs or filter by status.
    The function will create a progressive schedule with `spacing_minutes` between each download.
    """
    # Get the downloads to schedule based on filter or IDs
    downloads_to_schedule = []

    if download_ids:
        # Use the specified download IDs
        for download_id in download_ids:
            download = download_manager.get_download(download_id)
            if download:
                downloads_to_schedule.append(download)
    elif status_filter:
        # Get all downloads matching the status filter
        for status in status_filter:
            for download_dict in download_manager.get_downloads(status=status):
                download = download_manager.get_download(download_dict["id"])
                if download:
                    downloads_to_schedule.append(download)
    else:
        # Default to queued downloads if no filter specified
        for download_dict in download_manager.get_downloads(status=DownloadStatus.QUEUED):
            download = download_manager.get_download(download_dict["id"])
            if download:
                downloads_to_schedule.append(download)

    if not downloads_to_schedule:
        return {"success": False, "message": "No downloads found to schedule", "scheduled": []}

    # Sort downloads by priority and size
    downloads_to_schedule.sort(
        key=lambda d: (
            # Higher priority first
            -1
            if d.priority == DownloadPriority.HIGH
            else (0 if d.priority == DownloadPriority.NORMAL else 1),
            # YouTube downloads second (they often need more processing)
            0 if d.is_youtube else 1,
            # Smaller downloads first (they complete quicker)
            d.size if d.size else float("inf"),
        )
    )

    # Create schedules with progressive start times
    scheduled_downloads = []
    base_time = schedule.scheduled_time
    spacing_delta = timedelta(minutes=spacing_minutes)

    for i, download in enumerate(downloads_to_schedule):
        # Calculate the scheduled time for this download
        # Create batches with max_concurrent downloads starting at the same time
        batch_index = i // max_concurrent
        current_schedule_time = base_time + (batch_index * spacing_delta)

        # Create a copy of the schedule with the adjusted time
        current_schedule = ScheduleSettings(
            **{**schedule.model_dump(), "scheduled_time": current_schedule_time}
        )

        # Schedule the download
        try:
            result = await download_manager.schedule_download(
                download.id, current_schedule.model_dump()
            )

            if result:
                download_dict = download_manager._prepare_download_for_api(result)
                scheduled_downloads.append(download_dict)
        except Exception as e:
            print(f"Error scheduling download {download.id}: {e}")
            # Continue with other downloads

    return {
        "success": True,
        "message": f"Scheduled {len(scheduled_downloads)} downloads",
        "scheduled": scheduled_downloads,
    }


@router.post("/{download_id}/enhanced-youtube-schedule", response_model=DownloadItemResponse)
async def enhanced_youtube_schedule(
    download_id: str,
    schedule: ScheduleSettings,
    extract_info_now: bool = Query(
        True, description="Extract YouTube info immediately instead of waiting for scheduled time"
    ),
    backup_bandwidth: int | None = Query(
        None, description="Backup bandwidth limit if video info cannot be retrieved (KB/s)"
    ),
    auto_adjust_quality: bool = Query(
        False, description="Automatically adjust quality based on file size and available bandwidth"
    ),
):
    """
    Enhanced scheduling specifically for YouTube downloads with extra options

    Features:
    - Extract video info immediately to get accurate file size and name
    - Pre-check video availability before scheduling
    - Set fallback bandwidth limits
    - Option to automatically adjust quality based on size and bandwidth
    """
    download = download_manager.get_download(download_id)

    if not download:
        raise HTTPException(status_code=404, detail="Download not found")

    if not download.is_youtube:
        raise HTTPException(status_code=400, detail="This endpoint is only for YouTube downloads")

    # If extract_info_now is True, get YouTube info right now
    updated_download = None

    if extract_info_now:
        try:
            # Get video info now
            title, size, description = await download_manager.get_youtube_info(str(download.url))

            if title:
                download.name = title

                # Update save path with proper name
                if (
                    download.save_path
                    and download.save_path.endswith(".mp4")
                    or download.save_path.endswith(".mp3")
                ):
                    import os
                    import re

                    # Sanitize filename for filesystem
                    safe_title = re.sub(r'[\\/*?:"<>|]', "", title)  # Remove illegal characters
                    safe_title = re.sub(r"\s+", " ", safe_title).strip()  # Normalize whitespace
                    safe_title = safe_title[:100]  # Truncate if too long

                    extension = ".mp4"
                    if download.youtube_type and download.youtube_type.value == "audio":
                        extension = ".mp3"

                    new_path = os.path.join(
                        os.path.dirname(download.save_path), f"{safe_title}{extension}"
                    )
                    download.save_path = new_path

            # Update size if we have it
            if size:
                download.size = size

                # Auto-adjust quality based on size if requested
                if auto_adjust_quality and size > 0:
                    # Example logic for auto-adjusting bandwidth based on size
                    # For larger files (> 1GB), limit bandwidth more aggressively
                    if size > 1_000_000_000:  # 1 GB
                        download.max_speed = min(download.max_speed or 5000, 2000)  # 2000 KB/s max
                    # For medium files (100MB - 1GB), use moderate limits
                    elif size > 100_000_000:  # 100 MB
                        download.max_speed = min(download.max_speed or 5000, 3500)  # 3500 KB/s max

            # Set backup bandwidth if provided
            if backup_bandwidth is not None:
                # Only use backup if we couldn't get size info
                if not size:
                    download.max_speed = backup_bandwidth

            # Save the updated info
            await download_manager.save_downloads()

        except Exception as e:
            # If extraction fails, log it but continue with scheduling
            print(f"Error extracting YouTube info: {e}")
            if backup_bandwidth is not None:
                download.max_speed = backup_bandwidth
                await download_manager.save_downloads()

    # Schedule the download
    result = await download_manager.schedule_download(download_id, schedule.model_dump())

    if not result:
        raise HTTPException(status_code=500, detail="Failed to schedule download")

    # Get updated download data
    download_dict = download_manager._prepare_download_for_api(result)
    return {"download": download_dict}


@router.post("/schedule-based-on-bandwidth")
async def schedule_based_on_bandwidth(
    download_ids: list[str],
    target_completion_time: datetime | None = None,
    respect_peak_hours: bool = True,
    spacing_minutes: int = Query(5, description="Minutes between concurrent download starts"),
    adjust_quality: bool = Query(
        False, description="Adjust quality for YouTube downloads based on scheduling"
    ),
):
    """
    Schedule downloads based on bandwidth settings and target completion time

    This endpoint analyzes bandwidth settings, current schedule, and download sizes to
    create an optimized schedule that completes all downloads by the target time while
    respecting bandwidth limitations.

    If target_completion_time is not provided, it will schedule for optimal bandwidth use
    based on the current settings.
    """
    # Collect all downloads to be scheduled
    downloads_to_schedule = []
    failed_downloads = []

    # Validate that all downloads exist
    for download_id in download_ids:
        download = download_manager.get_download(download_id)
        if download:
            downloads_to_schedule.append(download)
        else:
            failed_downloads.append({"id": download_id, "error": "Download not found"})

    if not downloads_to_schedule:
        return {
            "success": False,
            "message": "No valid downloads found to schedule",
            "failed": failed_downloads,
        }

    # Get bandwidth settings
    bandwidth_settings = download_manager.bandwidth_settings

    # Calculate total bandwidth available
    total_bandwidth = bandwidth_settings.total_bandwidth

    # Determine peak hours if needed
    peak_hours_active = False
    if respect_peak_hours and bandwidth_settings.peak_hours_throttling:
        current_hour = datetime.now().hour
        if (
            bandwidth_settings.peak_hours_start <= bandwidth_settings.peak_hours_end
            and bandwidth_settings.peak_hours_start
            <= current_hour
            <= bandwidth_settings.peak_hours_end
        ) or (
            bandwidth_settings.peak_hours_start > bandwidth_settings.peak_hours_end
            and (
                current_hour >= bandwidth_settings.peak_hours_start
                or current_hour <= bandwidth_settings.peak_hours_end
            )
        ):
            peak_hours_active = True
            # Apply peak hours bandwidth limit if configured
            if bandwidth_settings.peak_hours_limit:
                total_bandwidth = bandwidth_settings.peak_hours_limit

    # Apply network buffer
    effective_bandwidth = total_bandwidth * (1 - bandwidth_settings.network_buffer / 100)

    # Count active downloads
    active_downloads_count = sum(
        1 for d in download_manager.downloads.values() if d.status == DownloadStatus.DOWNLOADING
    )

    # Calculate how many new downloads we can start based on max concurrent settings
    available_slots = max(0, bandwidth_settings.max_concurrent_downloads - active_downloads_count)

    # If no slots available, schedule for later
    if available_slots == 0:
        base_time = datetime.now() + timedelta(minutes=30)  # Default to 30 minutes from now
    else:
        base_time = datetime.now()

    # If target completion time provided, calculate optimal schedule
    if target_completion_time:
        # Calculate total size of all downloads
        total_size = sum(d.size or 0 for d in downloads_to_schedule)

        # Calculate time available until target completion
        time_available = (target_completion_time - datetime.now()).total_seconds()

        if time_available <= 0:
            return {
                "success": False,
                "message": "Target completion time must be in the future",
                "failed": download_ids,
            }

        # Calculate required bandwidth to meet deadline
        required_bandwidth = total_size / time_available if time_available > 0 else float("inf")

        if required_bandwidth > effective_bandwidth:
            # Adjust start time for each download based on its size and priority
            # Prioritize HIGH priority downloads to start first
            downloads_to_schedule.sort(
                key=lambda d: (
                    -1
                    if d.priority == DownloadPriority.HIGH
                    else (0 if d.priority == DownloadPriority.NORMAL else 1),
                    d.size or 0,  # Smaller files first if same priority
                )
            )
    else:
        # Sort by priority and size if no target completion time
        downloads_to_schedule.sort(
            key=lambda d: (
                -1
                if d.priority == DownloadPriority.HIGH
                else (0 if d.priority == DownloadPriority.NORMAL else 1),
                not d.is_youtube,  # YouTube downloads after regular downloads
                d.size or float("inf"),  # Smaller files first within same priority
            )
        )

    # Schedule the downloads
    scheduled_downloads = []
    current_offset = 0

    for i, download in enumerate(downloads_to_schedule):
        # Calculate batch index and time
        batch_index = i // available_slots if available_slots > 0 else i
        scheduled_time = base_time + timedelta(minutes=batch_index * spacing_minutes)

        # Create schedule settings
        schedule = ScheduleSettings(
            scheduled_time=scheduled_time,
            bandwidth_allocation=None,  # Will be calculated based on other settings
            notify_on_start=True,
            priority_boost=download.priority == DownloadPriority.HIGH,
            pre_download_check=True,
        )

        # For YouTube downloads with adjust_quality option
        if download.is_youtube and adjust_quality:
            try:
                # Get YouTube info first to make better scheduling decisions
                title, size, description = await download_manager.get_youtube_info(
                    str(download.url)
                )

                if size:
                    download.size = size

                    # Set bandwidth based on size for YouTube
                    if size > 1_000_000_000:  # 1 GB
                        schedule.bandwidth_allocation = 40.0  # 40% of available bandwidth
                    elif size > 500_000_000:  # 500 MB
                        schedule.bandwidth_allocation = 30.0
                    elif size > 100_000_000:  # 100 MB
                        schedule.bandwidth_allocation = 20.0
                    else:
                        schedule.bandwidth_allocation = 15.0
            except Exception as e:
                print(f"Error getting YouTube info for scheduling: {e}")
                # Default allocation if we can't get info
                schedule.bandwidth_allocation = 25.0

        # Schedule the download
        try:
            # Apply peak hours settings if active
            if peak_hours_active and bandwidth_settings.peak_hours_throttling:
                schedule.pause_on_peak_hours = True
                schedule.peak_hours_start = bandwidth_settings.peak_hours_start
                schedule.peak_hours_end = bandwidth_settings.peak_hours_end

            result = await download_manager.schedule_download(download.id, schedule.model_dump())

            if result:
                download_dict = download_manager._prepare_download_for_api(result)
                scheduled_downloads.append(download_dict)
            else:
                failed_downloads.append({"id": download.id, "error": "Failed to schedule"})
        except Exception as e:
            failed_downloads.append({"id": download.id, "error": str(e)})

    return {
        "success": True,
        "message": f"Scheduled {len(scheduled_downloads)} downloads based on bandwidth settings",
        "scheduled": scheduled_downloads,
        "failed": failed_downloads,
        "bandwidth_used": effective_bandwidth,
        "peak_hours_active": peak_hours_active,
    }
