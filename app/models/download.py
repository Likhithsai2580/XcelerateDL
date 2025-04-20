from datetime import datetime
from enum import Enum

from pydantic import BaseModel, HttpUrl


class DownloadStatus(str, Enum):
    QUEUED = "queued"
    DOWNLOADING = "downloading"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"


class FileCategory(str, Enum):
    ALL = "all"
    COMPRESSED = "compressed"
    PROGRAMS = "programs"
    VIDEOS = "videos"
    MUSIC = "music"
    PICTURES = "pictures"
    DOCUMENTS = "documents"
    OTHER = "other"


class CreateDownloadRequest(BaseModel):
    url: HttpUrl
    filename: str | None = None
    save_path: str | None = None
    category: FileCategory | None = None


class DownloadItem(BaseModel):
    id: str
    name: str
    url: HttpUrl
    size: int | None = None
    size_downloaded: int = 0
    status: DownloadStatus
    speed: int = 0  # bytes per second
    time_left: int | None = None  # seconds
    date_added: datetime
    save_path: str
    category: FileCategory

    @property
    def progress(self) -> float:
        """Return download progress as percentage (0-100)"""
        if not self.size or self.size == 0:
            return 0.0
        return min(100.0, (self.size_downloaded / self.size) * 100)


class DownloadResponse(BaseModel):
    download: DownloadItem


class DownloadItemResponse(BaseModel):
    download: DownloadItem


class DownloadsListResponse(BaseModel):
    downloads: list[DownloadItem]
    total: int
