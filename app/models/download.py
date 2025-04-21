from datetime import datetime
from enum import Enum, IntEnum
from typing import Optional
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
    YOUTUBE = "youtube"  # New category for YouTube downloads
    OTHER = "other"


class YoutubeDownloadType(str, Enum):
    AUDIO = "audio"
    VIDEO = "video"


class DownloadPriority(IntEnum):
    LOW = 1
    NORMAL = 2
    HIGH = 3


class CreateDownloadRequest(BaseModel):
    url: HttpUrl
    filename: str | None = None
    save_path: str | None = None
    category: FileCategory | None = None
    is_youtube: bool = False
    youtube_type: YoutubeDownloadType | None = None
    priority: DownloadPriority = DownloadPriority.NORMAL
    max_speed: int | None = None  # Speed limit in bytes per second (None = unlimited)
    max_retries: int = 3  # Maximum number of retry attempts


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
    is_youtube: bool = False
    youtube_type: YoutubeDownloadType | None = None
    cancel_callback: object = None
    pause_resume_callback: object = None
    priority: DownloadPriority = DownloadPriority.NORMAL
    max_speed: int | None = None  # Speed limit in bytes per second (None = unlimited)
    max_retries: int = 3  # Maximum number of retry attempts
    retry_count: int = 0  # Current retry count

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


class Download(BaseModel):
    """Download model representing a file download."""
    id: str
    url: str
    filename: str
    save_path: Optional[str] = None
    category: str = "other"
    status: str = "queued"
    size: int = 0
    downloaded: int = 0
    speed: float = 0
    time_left: float = 0
    date_added: float = datetime.now().timestamp()
    is_youtube: bool = False
    youtube_type: str | None = None
    priority: int = 2  # Default to normal priority
    max_speed: int | None = None  # Speed limit in bytes per second
    max_retries: int = 3  # Maximum retry attempts
    retry_count: int = 0  # Current retry count

    def to_dict(self) -> dict:
        """Convert the download to a dictionary."""
        return {
            "id": self.id,
            "url": self.url,
            "filename": self.filename,
            "save_path": self.save_path,
            "category": self.category,
            "status": self.status,
            "size": self.size,
            "downloaded": self.downloaded,
            "speed": self.speed,
            "time_left": self.time_left,
            "date_added": self.date_added,
            "is_youtube": self.is_youtube,
            "youtube_type": self.youtube_type,
            "priority": self.priority,
            "max_speed": self.max_speed,
            "max_retries": self.max_retries,
            "retry_count": self.retry_count
        }
