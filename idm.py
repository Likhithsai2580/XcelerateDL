import os
import json
import signal
import requests
import threading
import time
import logging
import colorlog
from pathlib import Path
from urllib.parse import urlparse
import yt_dlp

# --------------------------
# Logger Configuration
# --------------------------
class LogFormatter(colorlog.ColoredFormatter):
    def __init__(self):
        super().__init__(
            "%(log_color)s%(levelname)-8s%(reset)s %(blue)s%(message)s",
            log_colors={
                'DEBUG': 'cyan',
                'INFO': 'green',
                'WARNING': 'yellow',
                'ERROR': 'red',
                'CRITICAL': 'red,bg_white',
            }
        )

logger = logging.getLogger('PyIDM')
logger.setLevel(logging.INFO)
handler = logging.StreamHandler()
handler.setFormatter(LogFormatter())
logger.addHandler(handler)

class HttpDownloader:
    def __init__(self, url, num_threads=8, output_path="downloads", dynamic_threads=False):
        self.url = url
        self.num_threads = num_threads
        self.output_path = Path(output_path).resolve()
        self.file_size = 0
        self.lock = threading.Lock()
        self.downloaded_bytes = 0
        self.start_time = None
        self.filename = self._clean_filename(url)
        self.session = requests.Session()
        self.resume_file = self.output_path / f"{self.filename}.resume"
        self.part_progress = {}
        self.shutdown_flag = threading.Event()
        self.active_threads = []
        self.last_save_time = 0
        self.error_count = 0
        self.global_retries = 3
        self.supports_partial = True
        self.unknown_size = False
        self.part_dir = self.output_path / f".{self.filename}.parts"
        self.dynamic_threads = dynamic_threads
        self.original_num_threads = num_threads  # Store original thread count

        signal.signal(signal.SIGINT, self.signal_handler)
        signal.signal(signal.SIGTERM, self.signal_handler)
        self._init_resume_data()

    def _clean_filename(self, url):
        parsed = urlparse(url)
        name = Path(parsed.path).name
        clean = "".join(c for c in name if c.isalnum() or c in ('.', '-', '_'))[:255]
        return clean or "download"

    def _init_resume_data(self):
        if self.resume_file.exists():
            try:
                with open(self.resume_file, 'r') as f:
                    data = json.load(f)
                    self._validate_resume_data(data)
                    self.part_progress = {int(k): v for k, v in data['parts'].items()}
                    self.downloaded_bytes = sum(self.part_progress.values())
                    self.original_num_threads = data.get('original_threads', self.num_threads)
                    logger.info(f"Resuming download: {self.downloaded_bytes:,} bytes already downloaded")
                    self._verify_existing_parts()
            except Exception as e:
                logger.warning(f"Invalid resume file: {str(e)}")
                self._clear_resume_data()

    def _verify_existing_parts(self):
        for part_id in list(self.part_progress.keys()):
            part_file = self.part_dir / f"part_{part_id}"
            if not part_file.exists():
                logger.warning(f"Missing part file {part_id}, resetting progress")
                self.part_progress[part_id] = 0
        self.downloaded_bytes = sum(self.part_progress.values())

    def _validate_resume_data(self, data):
        required_keys = {'url', 'filename', 'file_size', 'parts'}
        if not required_keys.issubset(data.keys()):
            raise ValueError("Missing required keys in resume file")
        if data['url'] != self.url:
            raise ValueError("URL mismatch")
        if data['filename'] != self.filename:
            raise ValueError("Filename mismatch")

    def _clear_resume_data(self):
        self.resume_file.unlink(missing_ok=True)
        self.part_progress = {}
        if self.part_dir.exists():
            for f in self.part_dir.glob('part_*'):
                f.unlink(missing_ok=True)
            try:
                self.part_dir.rmdir()
            except OSError:
                logger.warning(f"Could not remove parts directory: {self.part_dir}")

    def signal_handler(self, signum, frame):
        logger.warning("\nGraceful shutdown initiated...")
        self.shutdown_flag.set()
        for t in self.active_threads:
            t.join(timeout=1)
        self._save_resume_data()
        logger.info(f"Resume data saved to: {self.resume_file}")
        exit(0)

    def _save_resume_data(self):
        if not self.supports_partial and self.unknown_size:
            logger.info("Cannot save resume data for unknown file size without partial support")
            return
        try:
            self.output_path.mkdir(parents=True, exist_ok=True, mode=0o755)
            temp_file = self.resume_file.with_name(f"{self.resume_file.name}.tmp")
            data = {
                'url': self.url,
                'filename': self.filename,
                'file_size': self.file_size,
                'parts': self.part_progress,
                'timestamp': time.time(),
                'original_threads': self.original_num_threads
            }
            # Use file locking to prevent concurrent access
            with open(temp_file, 'w', encoding='utf-8') as f:
                json.dump(data, f)
                f.flush()
                os.fsync(f.fileno())
            
            # Retry mechanism for file replacement
            max_retries = 3
            for attempt in range(max_retries):
                try:
                    os.replace(temp_file, self.resume_file)
                    break
                except PermissionError as e:
                    if attempt == max_retries - 1:
                        logger.error(f"Failed to save resume data after {max_retries} attempts: {str(e)}")
                        raise
                    time.sleep(0.5)
        except Exception as e:
            logger.error(f"Failed to save resume data: {str(e)}")
            if isinstance(e, PermissionError):
                logger.info("This is usually caused by another process accessing the file. Try again later.")
            self._check_directory_permissions()

    def _check_directory_permissions(self):
        try:
            test_file = self.output_path / "permission_test.txt"
            with open(test_file, 'w') as f:
                f.write("test")
            test_file.unlink()
        except PermissionError:
            logger.error(f"No write permissions in {self.output_path}. Try:")
            logger.error("1. Run as Administrator")
            logger.error("2. Choose different output directory")
            logger.error("3. Check folder permissions")

    def _safe_save_resume(self):
        now = time.time()
        if now - self.last_save_time > 5:
            self._save_resume_data()
            self.last_save_time = now

    def get_file_size(self):
        try:
            with self.session.head(self.url, allow_redirects=True, timeout=10) as resp:
                if resp.status_code != 200:
                    logger.error(f"HEAD failed: HTTP {resp.status_code}")
                    return False
                
                content_length = resp.headers.get('Content-Length')
                if content_length:
                    self.file_size = int(content_length)
                    self.unknown_size = False
                else:
                    self.unknown_size = True
                    self.file_size = 0
                    logger.warning("Server did not provide Content-Length. Progress unknown.")

                if 'Accept-Ranges' in resp.headers:
                    self.supports_partial = True
                else:
                    logger.warning("Server doesn't support resumable downloads. Using single-threaded mode.")
                    self.supports_partial = False
                    self.num_threads = 1
                    if self.part_progress:
                        logger.info("Clearing incompatible resume data")
                        self._clear_resume_data()

                if not self.supports_partial and self.resume_file.exists():
                    logger.info("Server doesn't support resume, clearing resume data")
                    self._clear_resume_data()

                return True
        except Exception as e:
            logger.error(f"Connection failed: {str(e)}")
            return False

    def _calculate_expected_size(self):
        if not self.part_progress or self.unknown_size:
            return 0
        return sum(self._calculate_part_size(pid) for pid in self.part_progress.keys())

    def _calculate_part_size(self, part_id):
        if not self.supports_partial:
            return self.file_size
        # Always use original thread count for consistent part size calculation
        chunk_size = self.file_size // self.original_num_threads
        if part_id == self.original_num_threads - 1:
            return self.file_size - (chunk_size * (self.original_num_threads - 1))
        return chunk_size

    def download_segment(self, part_id):
        part_file = self.part_dir / f"part_{part_id}"
        self.part_dir.mkdir(parents=True, exist_ok=True)
        headers = {}
        start = 0
        end = None

        if self.supports_partial:
            chunk_size = self.file_size // self.original_num_threads
            start = part_id * chunk_size + self.part_progress.get(part_id, 0)
            if part_id == self.original_num_threads - 1:
                end = self.file_size - 1
            else:
                end = ((part_id + 1) * chunk_size) - 1
            headers['Range'] = f'bytes={start}-{end}'
        else:
            if part_id != 0:
                return
            start = self.part_progress.get(0, 0)
            if start > 0:
                headers['Range'] = f'bytes={start}-'

        retries = 3
        for attempt in range(retries):
            if self.shutdown_flag.is_set():
                return
            try:
                with self.session.get(self.url, headers=headers, stream=True, timeout=30) as resp:
                    if resp.status_code not in (200, 206):
                        logger.error(f"Part {part_id} failed: HTTP {resp.status_code}")
                        continue

                    if self.supports_partial and resp.status_code != 206:
                        logger.error(f"Server violated range request for part {part_id}")
                        continue

                    mode = 'ab' if part_file.exists() else 'wb'
                    with open(part_file, mode) as f:
                        current_pos = f.tell() if mode == 'ab' else 0
                        chunk_size = 8192 * 8
                        for chunk in resp.iter_content(chunk_size=chunk_size):
                            if self.shutdown_flag.is_set():
                                return
                            if chunk:
                                f.write(chunk)
                                with self.lock:
                                    self.downloaded_bytes += len(chunk)
                                    self.part_progress[part_id] = current_pos + f.tell()
                                    if time.time() - self.last_save_time > 1:
                                        self._save_resume_data()
                                        self.last_save_time = time.time()
                                    
                                    # Calculate progress percentage and speed
                                    if self.file_size > 0:
                                        percent = (self.downloaded_bytes / self.file_size) * 100
                                        elapsed = time.time() - self.start_time
                                        speed = self.downloaded_bytes / (1024 * 1024 * elapsed) if elapsed > 0 else 0
                                        
                                        # Emit progress update
                                        if hasattr(self, 'progress_callback'):
                                            self.progress_callback({
                                                'percent': int(percent),
                                                'speed': f"{speed:.2f} MB/s"
                                            })
                    logger.info(f"Part {part_id} completed")
                    return
            except Exception as e:
                logger.warning(f"Part {part_id} attempt {attempt+1} failed: {str(e)}")
                time.sleep(2 ** attempt)

        with self.lock:
            self.error_count += 1
        logger.error(f"Part {part_id} failed after {retries} attempts")

    def calculate_stats(self):
        duration = time.time() - self.start_time
        mb_downloaded = self.downloaded_bytes / (1024 ** 2)
        speed = mb_downloaded / duration if duration > 0 else 0
        logger.info(f"\nDownload Statistics:")
        logger.info(f"- Time elapsed: {duration:.2f}s")
        logger.info(f"- Downloaded: {mb_downloaded:.2f} MB")
        logger.info(f"- Avg speed: {speed:.2f} MB/s")

    def merge_files(self):
        output_file = self.output_path / self.filename
        try:
            # First verify all parts exist and have correct sizes
            for part_id in sorted(self.part_progress.keys()):
                part_file = self.part_dir / f"part_{part_id}"
                if not part_file.exists():
                    logger.error(f"Missing part file {part_id}")
                    return False
                
                expected_size = self._calculate_part_size(part_id)
                actual_size = part_file.stat().st_size
                
                # Strict size validation - no tolerance, no truncation
                if actual_size != expected_size and not self.unknown_size:
                    logger.error(f"Part {part_id} size mismatch: expected {expected_size}, got {actual_size}")
                    return False
            
            # Now merge all verified parts
            with open(output_file, 'wb') as outfile:
                for part_id in sorted(self.part_progress.keys()):
                    part_file = self.part_dir / f"part_{part_id}"
                    with open(part_file, 'rb') as infile:
                        outfile.write(infile.read())
            
            # Cleanup parts and resume file after successful merge
            self._clear_resume_data()
            logger.info(f"Successfully merged to: {output_file}")
            return True
        except Exception as e:
            logger.error(f"Merge failed: {str(e)}")
            return False

    def start_download(self):
        self.start_time = time.time()
        logger.info(f"Starting download: {self.filename}")

        if not self.get_file_size():
            return False

        if not self.part_progress:
            if self.supports_partial:
                self.part_progress = {i: 0 for i in range(self.original_num_threads)}
            else:
                self.part_progress = {0: 0}

        self.output_path.mkdir(parents=True, exist_ok=True)

        global_retry = self.global_retries
        while global_retry > 0 and not self.shutdown_flag.is_set():
            self.error_count = 0
            self.active_threads = []

            for part_id in self.part_progress:
                if self.supports_partial:
                    expected_size = self._calculate_part_size(part_id)
                else:
                    expected_size = self.file_size if not self.unknown_size else float('inf')
                
                current_progress = self.part_progress.get(part_id, 0)
                if current_progress < expected_size:
                    thread = threading.Thread(target=self.download_segment, args=(part_id,))
                    self.active_threads.append(thread)
                    thread.start()

            # Monitor download progress
            while any(t.is_alive() for t in self.active_threads):
                time.sleep(1)
                with self.lock:
                    if self.file_size > 0:
                        pct = (self.downloaded_bytes / self.file_size) * 100
                        logger.info(f"Progress: {self.downloaded_bytes:,}/{self.file_size:,} bytes ({pct:.1f}%)")
                    else:
                        logger.info(f"Progress: {self.downloaded_bytes:,} bytes")
                if self.shutdown_flag.is_set():
                    logger.info("Download paused")
                    return False

            # Check if all parts completed successfully
            if self.error_count == 0:
                # If download is complete, terminate any remaining threads
                if self.downloaded_bytes >= self.file_size and self.file_size > 0:
                    logger.info("Download complete. Terminating any remaining threads.")
                    for t in self.active_threads:
                        if t.is_alive():
                            t.join(timeout=1)
                    break

            global_retry -= 1
            if global_retry > 0:
                logger.warning(f"Temporary failure in {self.error_count} parts. Retrying in 30 seconds ({global_retry} retries left)...")
                time.sleep(30)
                self.session = requests.Session()
            else:
                logger.error("Maximum retry attempts reached. Download failed.")
                return False

        if self.merge_files():
            self.calculate_stats()
            return True
        else:
            logger.error("Download incomplete. Resume the download later or check network connection.")

    def pause_download(self):
        """Pause the download by setting the shutdown flag"""
        self.shutdown_flag.set()
        logger.info("Download paused")

    def resume_download(self):
        """Resume the download by clearing the shutdown flag and restarting"""
        self.shutdown_flag.clear()
        self.start_download()
        logger.info("Download resumed")

# Import pytubefix for YouTube downloads
from pytubefix import YouTube
from pytubefix.exceptions import PytubeFixError as PytubeError
from requests.exceptions import RequestException

class YoutubeDownloader:
    def __init__(self, url, output_path, num_threads=4, format_type='video'):
        self.url = url
        self.output_path = output_path
        self.num_threads = num_threads
        self.format_type = format_type
        self.temp_file = output_path + '.tmp'
        self.progress_file = output_path + '.progress'
        self.stopped = threading.Event()
        self.chunks = []
        self.total_size = 0
        self.downloaded_size = 0
        self.lock = threading.Lock()
        self.session = requests.Session()
        self.title = ""
        self.download_url = ""
        
        # For GUI compatibility
        self.progress = {'percent': 0.0, 'speed': 'N/A'}
        self.last_save_time = time.time()
        self.progress_updated = None  # Will be set by GUI if needed
        
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)

    def _signal_handler(self, signum, frame):
        self.stopped.set()
        logger.info("\nReceived interrupt. Saving progress and exiting.")
        self._save_progress()
        exit(0)

    def select_stream(self):
        try:
            logger.info(f"Fetching stream information for: {self.url}")
            yt = YouTube(self.url)
            if self.format_type == 'audio':
                streams = yt.streams.filter(only_audio=True).order_by('abr').desc()
                if not streams:
                    raise ValueError("No audio streams available.")
                self.selected_stream = streams[0]
                logger.info(f"Selected audio stream: {self.selected_stream.abr}kbps {self.selected_stream.mime_type}")
            else:
                self.selected_stream = yt.streams.get_highest_resolution()
                if not self.selected_stream:
                    raise ValueError("No video streams available.")
                logger.info(f"Selected video stream: {self.selected_stream.resolution} {self.selected_stream.mime_type}")
            
            self.download_url = self.selected_stream.url
            self.title = yt.title
            logger.info(f"Title: {self.title}")
        except PytubeError as e:
            logger.error(f"Error accessing YouTube video: {e}")
            raise RuntimeError(f"Error accessing YouTube video: {e}")

    def _get_total_size(self):
        response = self.session.head(self.download_url, allow_redirects=True)
        response.raise_for_status()
        self.total_size = int(response.headers.get('Content-Length', 0))
        if self.total_size == 0:
            logger.error("Unable to determine file size.")
            raise RuntimeError("Unable to determine file size.")
        logger.info(f"Total file size: {self.total_size:,} bytes")

    def _initialize_chunks(self):
        chunk_size = self.total_size // self.num_threads
        self.chunks = []
        for i in range(self.num_threads):
            start = i * chunk_size
            end = start + chunk_size - 1 if i < self.num_threads - 1 else self.total_size - 1
            self.chunks.append({
                'start': start,
                'end': end,
                'downloaded': 0
            })
        logger.debug(f"Initialized {self.num_threads} download chunks")

    def _load_progress(self):
        if os.path.exists(self.progress_file):
            try:
                with open(self.progress_file, 'r') as f:
                    data = json.load(f)
                    if data.get('url') != self.download_url:
                        logger.warning("URL mismatch. Cannot resume download.")
                        return False
                    self.total_size = data['total_size']
                    self.chunks = data['chunks']
                    self.temp_file = data['temp_file']
                    self.output_path = data['output_path']
                    self.downloaded_size = sum(chunk['downloaded'] for chunk in self.chunks)
                    logger.info(f"Resuming download: {self.downloaded_size:,}/{self.total_size:,} bytes ({(self.downloaded_size/self.total_size)*100:.1f}%)")
                    return True
            except Exception as e:
                logger.warning(f"Invalid progress file: {str(e)}")
                self._clear_resume_data()
        return False

    def _save_progress(self):
        try:
            # Ensure parent directory exists
            os.makedirs(os.path.dirname(self.progress_file), exist_ok=True)
            
            data = {
                'url': self.download_url,
                'total_size': self.total_size,
                'chunks': self.chunks,
                'output_path': self.output_path,
                'temp_file': self.temp_file
            }
            
            # Write to temp file first
            temp_progress = f"{self.progress_file}.tmp"
            with open(temp_progress, 'w') as f:
                json.dump(data, f)
            
            # Then rename to final file (atomic operation)
            os.replace(temp_progress, self.progress_file)
            self.last_save_time = time.time()
            
            logger.debug("Progress saved successfully")
        except Exception as e:
            logger.error(f"Failed to save progress: {str(e)}")

    def _clear_resume_data(self):
        try:
            if os.path.exists(self.progress_file):
                os.remove(self.progress_file)
            if os.path.exists(f"{self.progress_file}.tmp"):
                os.remove(f"{self.progress_file}.tmp")
        except Exception as e:
            logger.warning(f"Error clearing resume data: {str(e)}")

    def _download_chunk(self, chunk_index):
        chunk = self.chunks[chunk_index]
        start = chunk['start'] + chunk['downloaded']
        end = chunk['end']
        if start > end:
            return

        headers = {'Range': f'bytes={start}-{end}'}
        try:
            response = self.session.get(self.download_url, headers=headers, stream=True, timeout=10)
            response.raise_for_status()
        except RequestException as e:
            logger.error(f"Error downloading chunk {chunk_index}: {e}")
            return

        with open(self.temp_file, 'rb+') as f:
            f.seek(start)
            for data in response.iter_content(chunk_size=8192):
                if self.stopped.is_set():
                    break
                with self.lock:
                    f.write(data)
                    chunk['downloaded'] += len(data)
                    self.downloaded_size += len(data)
                    
                    # Update progress for GUI
                    if self.total_size > 0:
                        percent = (self.downloaded_size / self.total_size) * 100
                        elapsed = time.time() - self.last_save_time
                        if elapsed > 0:
                            bytes_since_last = chunk['downloaded']
                            speed_mbps = bytes_since_last / (1024 * 1024 * elapsed)
                            speed = f"{speed_mbps:.2f} MB/s"
                        else:
                            speed = "Calculating..."
                        
                        self.progress = {'percent': percent, 'speed': speed}
                        
                        # Emit progress update to GUI if available
                        if hasattr(self, 'progress_updated') and self.progress_updated is not None:
                            try:
                                self.progress_updated.emit(self.progress)
                            except AttributeError:
                                # Handle case where progress_updated exists but isn't a signal
                                if callable(self.progress_updated):
                                    self.progress_updated(self.progress)

    def _combine_chunks(self):
        try:
            # Ensure output directory exists
            os.makedirs(os.path.dirname(os.path.abspath(self.output_path)), exist_ok=True)
            
            # Check if output file exists and is writable
            if os.path.exists(self.output_path):
                try:
                    # Test if we can write to the file
                    with open(self.output_path, 'a') as f:
                        pass
                    os.remove(self.output_path)
                except (PermissionError, OSError) as e:
                    logger.error(f"Cannot write to output file: {e}")
                    # Try with a different filename
                    base, ext = os.path.splitext(self.output_path)
                    self.output_path = f"{base}_new{ext}"
                    logger.info(f"Using alternative filename: {self.output_path}")
            
            # Copy the temp file to output path instead of renaming
            with open(self.temp_file, 'rb') as src:
                with open(self.output_path, 'wb') as dst:
                    dst.write(src.read())
            
            # Remove temp files after successful copy
            try:
                os.remove(self.temp_file)
                if os.path.exists(self.progress_file):
                    os.remove(self.progress_file)
            except Exception as e:
                logger.warning(f"Could not remove temporary files: {e}")
                
            logger.info(f"Successfully saved to: {self.output_path}")
            return True
        except Exception as e:
            logger.error(f"Error combining chunks: {e}")
            return False

    def download(self):
        try:
            # Create output directory if it doesn't exist
            os.makedirs(os.path.dirname(self.output_path), exist_ok=True)
            
            # Try to resume download if possible
            resume = self._load_progress()
            if resume:
                logger.info(f"Resuming download for {self.title}...")
            else:
                # Get stream information and initialize download
                self.select_stream()
                self._get_total_size()
                self._initialize_chunks()
                
                # Create empty file of required size
                with open(self.temp_file, 'wb') as f:
                    f.seek(self.total_size - 1)
                    f.write(b'\0')
                    
                logger.info(f"Starting download for {self.title}...")

            # Start download threads
            threads = []
            for i in range(len(self.chunks)):
                thread = threading.Thread(target=self._download_chunk, args=(i,))
                threads.append(thread)
                thread.start()

            # Monitor download progress
            last_save_time = time.time()
            while any(thread.is_alive() for thread in threads):
                time.sleep(0.5)
                
                # Calculate and display progress
                if self.total_size > 0:
                    progress = (self.downloaded_size / self.total_size) * 100
                    logger.info(f"Progress: {progress:.2f}%")
                
                # Save progress periodically
                current_time = time.time()
                if current_time - last_save_time >= 1:
                    self._save_progress()
                    last_save_time = current_time
                    
                # Check if download was stopped
                if self.stopped.is_set():
                    logger.info("Download paused")
                    return False

            # Wait for all threads to complete
            for thread in threads:
                thread.join()

            # Combine chunks if download completed successfully
            if not self.stopped.is_set():
                if self._combine_chunks():
                    logger.info("Download completed successfully.")
                    self._clear_resume_data()
                    return True
                else:
                    logger.error("Failed to combine downloaded chunks.")
                    return False
            else:
                logger.info("Download paused. Resume later.")
                return False

        except Exception as e:
            self.stopped.set()
            self._save_progress()
            logger.error(f"Download failed: {e}")
            return False

    # Compatibility methods for the existing codebase
    def start_download(self):
        return self.download()
            
    def stop_download(self):
        logger.info("Stopping download...")
        self.stopped.set()

# Wrapper class for backward compatibility
class YTDownloader:
    def __init__(self, url, output_path, format='best', convert_to=None):
        format_type = 'audio' if convert_to == 'mp3' else 'video'
        self.downloader = YoutubeDownloader(url, output_path, format_type=format_type)
        self.progress = self.downloader.progress
        self.shutdown_flag = self.downloader.stopped
        self.stopped = self.downloader.stopped  # For compatibility
        self.url = url
        self.output_path = output_path
        self.format = format
        self.convert_to = convert_to
        self.progress_callback = None  # Initialize progress_callback
        
    def start_download(self):
        # Set progress_callback on the downloader before starting
        if hasattr(self, 'progress_callback') and self.progress_callback is not None:
            self.downloader.progress_updated = self.progress_callback
        return self.downloader.download()
        
    def stop_download(self):
        self.downloader.stop_download()
        
    def pause_download(self):
        """Pause the download by calling stop_download on the underlying downloader"""
        self.downloader.stop_download()
        
    def resume_download(self):
        """Resume the download by calling start_download on the underlying downloader"""
        # Reset the stopped flag before resuming
        self.downloader.stopped.clear()
        # Preserve the progress callback during resume
        if hasattr(self, 'progress_callback') and self.progress_callback is not None:
            self.downloader.progress_updated = self.progress_callback
        return self.downloader.download()
    
    # Forward attribute access to the downloader when appropriate
    def __getattr__(self, name):
        if hasattr(self.downloader, name):
            return getattr(self.downloader, name)
        raise AttributeError(f"'{self.__class__.__name__}' object has no attribute '{name}'")
        
    # Forward progress_callback if set by GUI
    def __setattr__(self, name, value):
        if name == 'progress_callback':
            # Use object.__setattr__ to avoid recursion
            object.__setattr__(self, 'progress_callback', value)
            if hasattr(self, 'downloader'):
                self.downloader.progress_updated = value
        elif name == 'progress_updated':  # For backward compatibility
            # Use object.__setattr__ to avoid recursion
            object.__setattr__(self, 'progress_callback', value)
            if hasattr(self, 'downloader'):
                self.downloader.progress_updated = value
        else:
            super().__setattr__(name, value)

def get_downloader(url, output_path, **kwargs):
    if any(domain in url for domain in ['youtube.com', 'youtu.be', 'instagram.com', 'tiktok.com']):
        # Filter kwargs for YTDownloader
        yt_kwargs = {
            k: v for k, v in kwargs.items() 
            if k in ['format', 'convert_to']
        }
        return YTDownloader(url, output_path, **yt_kwargs)
    else:
        # Filter kwargs for HttpDownloader
        http_kwargs = {
            k: v for k, v in kwargs.items()
            if k in ['num_threads', 'dynamic_threads']
        }
        return HttpDownloader(url, output_path=output_path, **http_kwargs)

if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description='PyIDM - Intelligent Download Manager')
    parser.add_argument('url', help='Download URL')
    parser.add_argument('-o', '--output', default='downloads', help='Output directory')
    parser.add_argument('-t', '--threads', type=int, default=8, help='Number of threads (HTTP only)')
    parser.add_argument('-f', '--format', help='YT format code/quality')
    parser.add_argument('--convert', choices=['mp3', 'mp4'], help='Convert to audio/video format')
    args = parser.parse_args()

    downloader = get_downloader(
        args.url,
        args.output,
        num_threads=args.threads,
        format=args.format,
        convert_to=args.convert
    )
    
    try:
        downloader.start_download()
    except KeyboardInterrupt:
        downloader.stop_download()
        logger.info("Download stopped by user.")