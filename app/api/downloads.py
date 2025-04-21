from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query

from app.models.download import (
    CreateDownloadRequest,
    DownloadItemResponse,
    DownloadPriority,
    DownloadResponse,
    DownloadsListResponse,
    DownloadStatus,
    FileCategory,
)
from app.services.downloader import download_manager

router = APIRouter(prefix="/api/downloads", tags=["downloads"])


@router.post("", response_model=DownloadResponse)
async def create_download(request: CreateDownloadRequest):
    """Add a new download"""
    download = await download_manager.add_download(request)
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
        unique_downloads = {download['id']: download for download in all_downloads}
        downloads = list(unique_downloads.values())
    else:
        # Just filter by category
        downloads = download_manager.get_downloads(category)

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
            raise HTTPException(status_code=404, detail="Download not found")
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
    priority: Optional[DownloadPriority] = None,
    max_speed: Optional[int] = None,
    max_retries: Optional[int] = None,
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
    
    # Save changes
    await download_manager.save_downloads()
    
    # Broadcast update to clients
    await download_manager._broadcast_download_update(download_id)
    
    # Return updated download
    download_dict = download_manager._prepare_download_for_api(download)
    return {"download": download_dict}
