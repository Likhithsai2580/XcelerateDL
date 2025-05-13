from datetime import datetime
from enum import Enum, IntEnum

from pydantic import BaseModel, Field, HttpUrl


class DownloadStatus(str, Enum):
    QUEUED = "queued"
    DOWNLOADING = "downloading"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    SCHEDULED = "scheduled"  # New status for scheduled downloads


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


class BandwidthAllocationMode(str, Enum):
    """Defines how bandwidth is allocated among downloads"""

    EQUAL = "equal"  # Equal share to all active downloads
    PRIORITY = "priority"  # Based on priority levels
    CUSTOM = "custom"  # Custom allocation percentages


class RecurrenceType(str, Enum):
    """Defines the recurrence types for scheduled downloads"""

    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"


class ScheduleSettings(BaseModel):
    """Settings for scheduled downloads"""

    scheduled_time: datetime | None = None  # When to start the download
    recurrence: RecurrenceType | None = (
        None  # None, RecurrenceType.DAILY, RecurrenceType.WEEKLY, RecurrenceType.MONTHLY
    )
    days_of_week: list[int] | None = None  # 0=Monday, 6=Sunday
    day_of_month: int | None = None  # 1-31 for monthly recurrence
    bandwidth_allocation: float | None = None  # Percentage of bandwidth to use (0-100)
    retry_on_failure: bool = True  # Whether to retry the scheduled download if it fails
    max_schedule_retries: int = 3  # Maximum number of retry attempts for scheduled downloads
    current_schedule_retries: int = 0  # Current retry count for scheduled downloads
    retry_delay_minutes: int = 10  # Delay between retry attempts in minutes
    notify_on_start: bool = True  # Send notification when scheduled download starts
    priority_boost: bool = False  # Boost priority when scheduled download starts
    max_duration_minutes: int | None = None  # Maximum duration after which download is cancelled
    start_window_minutes: int | None = (
        None  # Window during which the download can start (for flexible scheduling)
    )
    pre_download_check: bool = True  # Whether to check resource availability before starting
    pause_on_peak_hours: bool = False  # Whether to pause download during peak hours
    peak_hours_start: int | None = None  # Start hour for peak time (0-23)
    peak_hours_end: int | None = None  # End hour for peak time (0-23)
    description: str | None = None  # User-provided description of this schedule
    auto_retry_count: int = 0  # Number of auto-retries performed
    last_failure_reason: str | None = None  # Reason for the last failure
    alternate_url: str | None = None  # Alternative URL to try if primary fails


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
    schedule: ScheduleSettings | None = None  # Optional scheduling settings
    bandwidth_allocation: float | None = None  # Percentage of bandwidth to use (0-100)
    tags: list[str] | None = Field(default_factory=list)  # Tags for improved searching


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
    schedule: ScheduleSettings | None = None  # Schedule settings
    bandwidth_allocation: float | None = None  # Percentage of bandwidth (0-100)
    tags: list[str] = Field(default_factory=list)  # Tags for improved searching
    notes: str | None = None  # Notes about the download (description, errors, etc.)
    needs_rate_limit_update: bool = Field(default=False, exclude=True) # Flag to signal rate limit update needed

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


class BandwidthSettings(BaseModel):
    """Global bandwidth settings for the download manager"""

    total_bandwidth: int  # Total available bandwidth in bytes/second
    allocation_mode: BandwidthAllocationMode = BandwidthAllocationMode.EQUAL
    custom_allocations: dict[str, float] = {}  # download_id -> percentage (0-100)
    max_concurrent_downloads: int = 5  # Maximum number of concurrent downloads
    enable_scheduling: bool = True  # Enable smart scheduling of downloads
    peak_hours_throttling: bool = False  # Throttle downloads during peak hours
    peak_hours_start: int = 18  # Default peak hours start (6 PM)
    peak_hours_end: int = 23  # Default peak hours end (11 PM)
    peak_hours_limit: int | None = None  # Bandwidth limit during peak hours
    scheduler_check_interval: int = 60  # Seconds between scheduler checks
    auto_adjust_bandwidth: bool = True  # Automatically adjust bandwidth based on network conditions
    youtube_dedicated_bandwidth: int | None = (
        None  # Dedicated bandwidth for YouTube downloads (bytes/second)
    )
    prioritize_smaller_files: bool = True  # Prioritize smaller files for quicker completion
    network_buffer: int = 10  # Percentage of bandwidth to keep free (0-100)


class Download(BaseModel):
    """Download model representing a file download."""

    id: str
    url: str
    filename: str
    save_path: str | None = None
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
    schedule: dict | None = None  # Schedule settings
    bandwidth_allocation: float | None = None  # Percentage of bandwidth (0-100)
    tags: list[str] = Field(default_factory=list)  # Tags for search

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
            "retry_count": self.retry_count,
            "schedule": self.schedule,
            "bandwidth_allocation": self.bandwidth_allocation,
            "tags": self.tags,
        }


class SearchQuery(BaseModel):
    """Model for advanced search queries"""

    query: str = ""  # Text to search in name/url
    category: FileCategory | None = None
    status: list[DownloadStatus] | None = None
    date_from: datetime | None = None
    date_to: datetime | None = None
    tags: list[str] | None = None
    min_size: int | None = None  # Minimum file size in bytes
    max_size: int | None = None  # Maximum file size in bytes
