import os
import json
import signal
import threading
import time
import logging
import colorlog
from pathlib import Path
from urllib.parse import urlparse
from curl_cffi import requests as curl_requests

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
        self.session = curl_requests.Session()
        
        # Create base directories
        self.output_path.mkdir(parents=True, exist_ok=True)
        self.downloads_dir = self.output_path / "downloads"
        self.downloads_dir.mkdir(parents=True, exist_ok=True)
        self.temp_dir = self.output_path / ".temp"
        self.temp_dir.mkdir(parents=True, exist_ok=True)
        
        # Set up paths for resume and parts
        self.resume_file = self.temp_dir / f"{self.filename}.resume"
        self.part_dir = self.temp_dir / f"{self.filename}.parts"
        self.part_progress = {}
        self.shutdown_flag = threading.Event()
        self.active_threads = []
        self.last_save_time = 0
        self.error_count = 0
        self.global_retries = 3
        self.supports_partial = True
        self.unknown_size = False
        self.original_num_threads = num_threads
        self.dynamic_threads = dynamic_threads

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
        """Clean up temporary files and directories"""
        try:
            if self.resume_file.exists():
                self.resume_file.unlink()
            
            if self.part_dir.exists():
                for f in self.part_dir.glob('part_*'):
                    try:
                        f.unlink()
                    except Exception as e:
                        logger.warning(f"Could not remove part file {f}: {e}")
                try:
                    self.part_dir.rmdir()
                except Exception as e:
                    logger.warning(f"Could not remove parts directory: {e}")
            
            self.part_progress = {}
        except Exception as e:
            logger.error(f"Error during cleanup: {e}")

    def signal_handler(self, signum, frame):
        logger.warning("\nGraceful shutdown initiated...")
        self.shutdown_flag.set()
        for t in self.active_threads:
            t.join(timeout=1)
        self._save_resume_data()
        logger.info(f"Resume data saved to: {self.resume_file}")
        exit(0)

    def _check_directory_permissions(self):
        """Check write permissions for all required directories"""
        try:
            for directory in [self.output_path, self.downloads_dir, self.temp_dir]:
                test_file = directory / "permission_test.txt"
                with open(test_file, 'w') as f:
                    f.write("test")
                test_file.unlink()
            return True
        except PermissionError:
            logger.error(f"No write permissions in one of these directories:")
            logger.error(f"Output: {self.output_path}")
            logger.error(f"Downloads: {self.downloads_dir}")
            logger.error(f"Temp: {self.temp_dir}")
            logger.error("Try:")
            logger.error("1. Run as Administrator")
            logger.error("2. Choose different output directory")
            logger.error("3. Check folder permissions")
            return False

    def _save_resume_data(self):
        """Save download progress data with proper directory handling"""
        if not self.supports_partial and self.unknown_size:
            logger.info("Cannot save resume data for unknown file size without partial support")
            return

        # Ensure directories exist
        self.temp_dir.mkdir(parents=True, exist_ok=True)
        if not self._check_directory_permissions():
            return

        try:
            temp_file = self.resume_file.with_suffix('.tmp')
            data = {
                'url': self.url,
                'filename': self.filename,
                'file_size': self.file_size,
                'parts': self.part_progress,
                'timestamp': time.time(),
                'original_threads': self.original_num_threads
            }

            # Use atomic write pattern
            with open(temp_file, 'w', encoding='utf-8') as f:
                json.dump(data, f)
                f.flush()
                os.fsync(f.fileno())

            # Atomic rename
            try:
                os.replace(temp_file, self.resume_file)
            except PermissionError as e:
                logger.error(f"Failed to save resume data: {str(e)}")
                logger.info("This may be caused by another process accessing the file")
                # Try to copy the content instead
                if temp_file.exists():
                    with open(temp_file, 'r') as src, open(self.resume_file, 'w') as dst:
                        dst.write(src.read())
                    temp_file.unlink()

        except Exception as e:
            logger.error(f"Failed to save resume data: {str(e)}")
            if isinstance(e, PermissionError):
                self._check_directory_permissions()

    def _safe_save_resume(self):
        now = time.time()
        if now - self.last_save_time > 5:
            self._save_resume_data()
            self.last_save_time = now

    def get_file_size(self):
        try:
            resp = self.session.head(self.url, allow_redirects=True, timeout=10, impersonate="chrome110")
            if (content_length := resp.headers.get('Content-Length')):
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
        """Download a segment with proper directory handling"""
        # Ensure part directory exists
        self.part_dir.mkdir(parents=True, exist_ok=True)
        part_file = self.part_dir / f"part_{part_id}"

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
        last_update_time = time.time()
        bytes_since_last_update = 0
        current_speed = 0
        
        for attempt in range(retries):
            if self.shutdown_flag.is_set():
                return
            try:
                resp = self.session.get(
                    self.url, 
                    headers=headers, 
                    stream=True, 
                    timeout=30,
                    impersonate="chrome110",
                    verify=False
                )
                
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
                            self._save_resume_data()
                            return
                            
                        if chunk:
                            f.write(chunk)
                            chunk_len = len(chunk)
                            
                            with self.lock:
                                self.downloaded_bytes += chunk_len
                                self.part_progress[part_id] = current_pos + f.tell()
                                bytes_since_last_update += chunk_len
                                
                                current_time = time.time()
                                update_interval = current_time - last_update_time
                                
                                if update_interval >= 0.1:  # Update every 100ms
                                    instant_speed = bytes_since_last_update / (1024 * 1024 * update_interval)  # MB/s
                                    current_speed = (current_speed * 0.7 + instant_speed * 0.3)  # Smoothing
                                    
                                    if self.file_size > 0:
                                        percent = (self.downloaded_bytes / self.file_size) * 100
                                        if hasattr(self, 'progress_callback'):
                                            self.progress_callback({
                                                'percent': percent,
                                                'speed': f"{current_speed:.2f} MB/s",
                                                'downloaded': self.downloaded_bytes,
                                                'total': self.file_size,
                                                'eta': (self.file_size - self.downloaded_bytes) / (current_speed * 1024 * 1024) if current_speed > 0 else 0
                                            })
                                    
                                    bytes_since_last_update = 0
                                    last_update_time = current_time
                                    
                                    if time.time() - self.last_save_time > 1:
                                        self._save_resume_data()
                                        self.last_save_time = time.time()
                
                logger.info(f"Part {part_id} completed")
                resp.close()
                return
                    
            except Exception as e:
                logger.warning(f"Part {part_id} attempt {attempt+1} failed: {str(e)}")
                time.sleep(2 ** attempt)
            finally:
                if 'resp' in locals():
                    resp.close()

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
        """Merge downloaded parts with proper directory handling"""
        final_output = self.downloads_dir / self.filename
        try:
            # First verify all parts exist and have correct sizes
            for part_id in sorted(self.part_progress.keys()):
                part_file = self.part_dir / f"part_{part_id}"
                if not part_file.exists():
                    logger.error(f"Missing part file {part_id}")
                    return False
                
                expected_size = self._calculate_part_size(part_id)
                actual_size = part_file.stat().st_size
                
                if actual_size != expected_size and not self.unknown_size:
                    logger.error(f"Part {part_id} size mismatch: expected {expected_size}, got {actual_size}")
                    return False
            
            # Now merge all verified parts
            with open(final_output, 'wb') as outfile:
                for part_id in sorted(self.part_progress.keys()):
                    part_file = self.part_dir / f"part_{part_id}"
                    with open(part_file, 'rb') as infile:
                        outfile.write(infile.read())
            
            # Cleanup parts and resume file after successful merge
            self._clear_resume_data()
            logger.info(f"Successfully merged to: {final_output}")
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
                self.session = curl_requests.Session()
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
        self._save_resume_data()  # Save current progress
        logger.info("Download paused")

    def resume_download(self):
        """Resume the download by clearing the shutdown flag and restarting"""
        self.shutdown_flag.clear()
        self.start_download()
        logger.info("Download resumed")
