import sys
import time
import os
import json
import threading
from pathlib import Path
from PyQt5.QtWidgets import (QMainWindow, QApplication, QWidget, QVBoxLayout, QHBoxLayout,
                           QLineEdit, QPushButton, QProgressBar, QLabel, QTabWidget,
                           QListWidget, QListWidgetItem, QFileDialog, QMessageBox,
                           QToolButton, QAction, QRadioButton, QButtonGroup)
from PyQt5.QtCore import Qt, QThread, pyqtSignal
from PyQt5.QtGui import QIcon
from idm import HttpDownloader, logger
from pytubefix import YouTube
from pytubefix.exceptions import PytubeFixError as PytubeError
from curl_cffi import requests as curl_requests
from curl_cffi.requests.errors import RequestsError
from moviepy.video.io.VideoFileClip import VideoFileClip
from moviepy.video.VideoClip import TextClip
from moviepy.video.compositing.CompositeVideoClip import CompositeVideoClip
from moviepy import concatenate_videoclips
from moviepy.audio.io.AudioFileClip import AudioFileClip

class YoutubeDownloader:
    def __init__(self, url, output_path, num_threads=4, format_type='video', progress_callback=None):
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
        self.session = curl_requests.Session()
        self.title = ""
        self.download_url = ""
        self.progress_callback = progress_callback
        
        # For separate video and audio downloads
        self.video_file = None
        self.audio_file = None
        self.video_size = 0
        self.audio_size = 0

    def select_stream(self):
        try:
            yt = YouTube(self.url)
            self.title = yt.title
            
            if self.format_type == 'audio':
                # For audio-only, get the highest quality audio stream
                streams = yt.streams.filter(only_audio=True).order_by('abr').desc()
                if not streams:
                    raise ValueError("No audio streams available.")
                self.selected_stream = streams[0]
                self.download_url = self.selected_stream.url
            else:
                # For video, we'll download the highest quality video and audio separately
                # Get the highest quality video stream (without audio)
                video_streams = yt.streams.filter(adaptive=True, file_extension='mp4', only_video=True).order_by('resolution').desc()
                if not video_streams:
                    # Fallback to progressive stream if no adaptive streams available
                    self.selected_stream = yt.streams.get_highest_resolution()
                    if not self.selected_stream:
                        raise ValueError("No video streams available.")
                    self.download_url = self.selected_stream.url
                    return
                    
                # Get the highest quality audio stream
                audio_streams = yt.streams.filter(only_audio=True).order_by('abr').desc()
                if not audio_streams:
                    raise ValueError("No audio streams available.")
                
                # Set up video and audio streams
                self.video_stream = video_streams[0]
                self.audio_stream = audio_streams[0]
                
                # Set up temporary file paths
                base_path = os.path.splitext(self.output_path)[0]
                self.video_file = f"{base_path}_video_temp.mp4"
                self.audio_file = f"{base_path}_audio_temp.mp4"
                
                # For progress tracking, we'll use the combined size
                self.video_size = int(self.video_stream.filesize)
                self.audio_size = int(self.audio_stream.filesize)
                self.total_size = self.video_size + self.audio_size
                
                # For compatibility with existing code, set download_url to video stream
                self.download_url = self.video_stream.url
        except PytubeError as e:
            raise RuntimeError(f"Error accessing YouTube video: {e}")

    def _get_total_size(self):
        try:
            resp = self.session.head(self.download_url, allow_redirects=True, impersonate="chrome110")
            self.total_size = int(resp.headers.get('Content-Length', 0))
            if self.total_size == 0:
                raise RuntimeError("Unable to determine file size.")
            resp.close()
        except RequestsError as e:
            raise RuntimeError(f"Connection failed: {e}")

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

    def _load_progress(self):
        if os.path.exists(self.progress_file):
            with open(self.progress_file, 'r') as f:
                data = json.load(f)
                self.total_size = data['total_size']
                self.chunks = data['chunks']
                self.temp_file = data['temp_file']
                self.output_path = data['output_path']
                self.download_url = data['url']  # Load the download URL
                self.downloaded_size = sum(chunk['downloaded'] for chunk in self.chunks)
                return True
        return False

    def _save_progress(self):
        data = {
            'url': self.download_url,
            'total_size': self.total_size,
            'chunks': self.chunks,
            'output_path': self.output_path,
            'temp_file': self.temp_file
        }
        with open(self.progress_file, 'w') as f:
            json.dump(data, f)

    def _download_chunk(self, chunk_index):
        chunk = self.chunks[chunk_index]
        start = chunk['start'] + chunk['downloaded']
        end = chunk['end']
        if start > end:
            return

        headers = {'Range': f'bytes={start}-{end}'}
        try:
            resp = self.session.get(
                self.download_url, 
                headers=headers, 
                stream=True, 
                timeout=30,
                impersonate="chrome110",
                verify=False
            )
            resp.raise_for_status()

            with open(self.temp_file, 'rb+') as f:
                f.seek(start)
                for data in resp.iter_bytes(chunk_size=8192):
                    if self.stopped.is_set():
                        break
                    with self.lock:
                        f.write(data)
                        chunk['downloaded'] += len(data)
                        self.downloaded_size += len(data)
            resp.close()
        except RequestsError as e:
            print(f"Error downloading chunk {chunk_index}: {e}")
        finally:
            if 'resp' in locals():
                resp.close()  # Ensure response is closed

    def _combine_chunks(self):
        if os.path.exists(self.output_path):
            os.remove(self.output_path)
        os.rename(self.temp_file, self.output_path)
        if os.path.exists(self.progress_file):
            os.remove(self.progress_file)

    def _download_video_audio(self):
        """Download video and audio streams separately"""
        try:
            # Download video stream
            print(f"Downloading video stream for {self.title}...")
            video_start_time = time.time()
            video_downloaded = 0
             
             # Custom progress callback for video download
            def video_progress_callback(stream, chunk, bytes_remaining):
                nonlocal video_downloaded
                video_downloaded = self.video_size - bytes_remaining
                video_progress = (video_downloaded / self.video_size) * 25  # 0-25% range
                video_speed = video_downloaded / (time.time() - video_start_time + 0.1) / 1024
                 
                if self.progress_callback:
                    self.progress_callback({
                        'percent': video_progress,
                        'speed': f"{video_speed:.2f} KB/s (Video)"
                    })
             
             # Set up callback and download video
            self.video_stream.on_progress = video_progress_callback
            self.video_stream.download(output_path=os.path.dirname(self.video_file), filename=os.path.basename(self.video_file))
            
            # Download audio stream
            print(f"Downloading audio stream for {self.title}...")
            
            # Set up progress tracking for audio download (25-50%)
            audio_start_time = time.time()
            audio_downloaded = 0
            
            # Custom progress callback for audio download
            def audio_progress_callback(stream, chunk, bytes_remaining):
                nonlocal audio_downloaded
                audio_downloaded = self.audio_size - bytes_remaining
                audio_progress = 25 + (audio_downloaded / self.audio_size) * 25  # 25-50% range
                audio_speed = audio_downloaded / (time.time() - audio_start_time + 0.1) / 1024
                
                if self.progress_callback:
                    self.progress_callback({
                        'percent': audio_progress,
                        'speed': f"{audio_speed:.2f} KB/s (Audio)"
                    })
            
            # Set up callback and download audio
            self.audio_stream.on_progress = audio_progress_callback
            self.audio_stream.download(output_path=os.path.dirname(self.audio_file), filename=os.path.basename(self.audio_file))
            
            return True
        except Exception as e:
            print(f"Error downloading streams: {e}")
            return False
    
    def _monitor_merge_progress(self, start_time, duration):
        """Monitor progress of the merging process and update UI"""
        last_update_time = start_time
        
        while not self.stopped.is_set():
            current_time = time.time()
            # Update progress every 200ms
            if current_time - last_update_time >= 0.2:
                elapsed = current_time - start_time
                # Estimate progress between 50-95% (final 5% for file operations)
                estimated_progress = min(95, 50 + (elapsed / max(1, duration)) * 45)
                
                if self.progress_callback:
                    self.progress_callback({
                        'percent': estimated_progress,
                        'speed': f"Merging: {min(100, int((elapsed / max(1, duration * 1.5)) * 100))}%"
                    })
                
                last_update_time = current_time
            
            # Sleep to avoid high CPU usage
            time.sleep(0.1)
    
    def _merge_video_audio(self):
        """Merge video and audio using moviepy"""
        try:
            print(f"Merging video and audio for {self.title}...")
            video_clip = VideoFileClip(self.video_file)
            audio_clip = AudioFileClip(self.audio_file)
            
            # Create a new clip with audio using the correct API
            final_clip = video_clip.copy()
            final_clip.audio = audio_clip
            
            # Set up progress tracking for merging process (50-100%)
            merge_start_time = time.time()
            
            # Reset the stopped flag before starting the progress thread
            self.stopped.clear()
            
            # Create a separate thread to monitor progress since callback isn't supported
            progress_thread = threading.Thread(
                target=self._monitor_merge_progress,
                args=(merge_start_time, video_clip.duration)
            )
            progress_thread.daemon = True
            progress_thread.start()
            
            # Write the file without callback parameter
            final_clip.write_videofile(
                self.output_path, 
                codec='libx264', 
                audio_codec='aac',
                logger=None  # Disable default logger
            )
            
            # Stop the progress monitoring
            self.stopped.set()
            progress_thread.join(timeout=1)
            
            # Close the clips to release resources
            video_clip.close()
            audio_clip.close()
            final_clip.close()
            
            # Clean up temporary files
            if os.path.exists(self.video_file):
                os.remove(self.video_file)
            if os.path.exists(self.audio_file):
                os.remove(self.audio_file)
            
            # Final 100% progress update
            if self.progress_callback:
                self.progress_callback({
                    'percent': 100,
                    'speed': "Complete"
                })
                
            return True
        except Exception as e:
            print(f"Error merging video and audio: {e}")
            return False
    
    def start_download(self):
        try:
            resume = self._load_progress()
            if resume:
                print(f"Resuming download...")
                # If resuming, we need the download URL but don't need to select a new stream
                if not self.download_url:
                    self.select_stream()
            else:
                self.select_stream()
                
                # For audio-only or if we're using the fallback progressive stream
                if self.format_type == 'audio' or not hasattr(self, 'video_stream'):
                    self._get_total_size()
                    self._initialize_chunks()
                    with open(self.temp_file, 'wb') as f:
                        f.seek(self.total_size - 1)
                        f.write(b'\0')
                    print(f"Starting download for {self.title}...")

                    threads = []
                    for i in range(len(self.chunks)):
                        thread = threading.Thread(target=self._download_chunk, args=(i,))
                        threads.append(thread)

                    for thread in threads:
                        thread.start()

                    last_save_time = time.time()
                    while any(thread.is_alive() for thread in threads):
                        time.sleep(0.5)
                        progress = (self.downloaded_size / self.total_size) * 100
                        speed = self.downloaded_size / (time.time() - last_save_time + 0.1) / 1024
                        if self.progress_callback:
                            self.progress_callback({
                                'percent': progress,
                                'speed': f"{speed:.2f} KB/s"
                            })
                        
                        current_time = time.time()
                        if current_time - last_save_time >= 1:
                            self._save_progress()
                            last_save_time = current_time

                    for thread in threads:
                        thread.join()

                    if not self.stopped.is_set():
                        self._combine_chunks()
                        print("\nDownload completed successfully.")
                        return True
                    else:
                        print("\nDownload paused. Resume later.")
                        return False
                else:
                    # For high quality video, download video and audio separately then merge
                    print(f"Starting high quality download for {self.title}...")
                    
                    # Track progress for separate downloads
                    current_progress = 0
                    
                    # Download video and audio (50% of total progress)
                    if self._download_video_audio():
                        current_progress = 50
                        if self.progress_callback:
                            self.progress_callback({
                                'percent': current_progress,
                                'speed': "Merging..."
                            })
                        
                        # Merge video and audio (remaining 50% of progress)
                        if self._merge_video_audio():
                            if self.progress_callback:
                                self.progress_callback({
                                    'percent': 100,
                                    'speed': "Complete"
                                })
                            print("\nHigh quality download completed successfully.")
                            return True
                    
                    if self.stopped.is_set():
                        print("\nDownload paused. Resume later.")
                        return False
                    else:
                        print("\nHigh quality download failed.")
                        return False

        except Exception as e:
            self.stopped.set()
            self._save_progress()
            print(f"\nDownload failed: {e}")
            return False
            
    def pause_download(self):
        self.stopped.set()
        
    def resume_download(self):
        self.stopped.clear()
        return self.start_download()


class DownloadThread(QThread):
    progress_updated = pyqtSignal(dict)
    download_complete = pyqtSignal(bool)
    paused = pyqtSignal()
    resumed = pyqtSignal()

    def __init__(self, downloader, parent=None):
        super().__init__(parent)
        self.downloader = downloader
        self.is_paused = False
        self._main_window = parent
        
        # Set up a direct callback that emits the signal
        def emit_progress(progress_data):
            self.progress_updated.emit(progress_data)
        
        # Ensure progress callback is properly set
        self.downloader.progress_callback = emit_progress

    def run(self):
        try:
            success = self.downloader.start_download()
            self.download_complete.emit(success)
        except Exception as e:
            logger.error(f"Download error: {e}")
            self.download_complete.emit(False)
        finally:
            # Clean up callback to prevent memory leaks
            self.downloader.progress_callback = None

    def pause_download(self):
        if not self.is_paused and self.isRunning():
            self.is_paused = True
            self.downloader.pause_download()
            # Save download state immediately when paused
            if self._main_window:
                self._main_window.save_download_states()
            self.paused.emit()

    def resume_download(self):
        if self.is_paused and self.isRunning():
            self.is_paused = False
            self.downloader.resume_download()
            self.resumed.emit()


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        # Initialize default theme as the very first step
        self.current_theme = "light"
        
        self.setWindowTitle("Modern Download Manager")
        self.setMinimumSize(800, 600)
        self.setWindowIcon(QIcon("resources/icons/app_icon.svg"))
        
        # Initialize downloads history file
        self.downloads_history_file = Path("downloads/downloads_history.json")
        self.downloads_history_file.parent.mkdir(parents=True, exist_ok=True)
        if not self.downloads_history_file.exists():
            self.save_downloads_history({
                "completed": [],
                "incomplete": [],
                "settings": {
                    "theme": "light"
                }
            })
            
        # Load user settings
        self.load_user_settings()

        # Main widget and layout
        self.main_widget = QWidget()
        self.setCentralWidget(self.main_widget)
        self.main_layout = QVBoxLayout(self.main_widget)
        
        # Create header with theme toggle
        self.create_header()

        # Create tabs
        self.tabs = QTabWidget()
        self.main_layout.addWidget(self.tabs)

        # HTTP Download Tab
        self.create_http_tab()
        # YouTube Download Tab
        self.create_youtube_tab()
        # Resume Downloads Tab
        self.create_resume_tab()

        # Apply styles
        self.load_styles()
        
    def closeEvent(self, event):
        # Pause all active HTTP downloads
        if hasattr(self, 'active_http_downloads'):
            for download in self.active_http_downloads:
                if download['thread'].isRunning():
                    download['thread'].pause_download()

        # Pause all active YouTube downloads
        if hasattr(self, 'active_yt_downloads'):
            for download in self.active_yt_downloads:
                if download['thread'].isRunning():
                    download['thread'].pause_download()

        # Save download states
        self.save_download_states()
        event.accept()

    def save_downloads_history(self, data):
        with open(self.downloads_history_file, 'w') as f:
            json.dump(data, f, indent=4)

    def save_download_states(self):
        try:
            # Ensure downloads_history_file is initialized
            if not hasattr(self, 'downloads_history_file'):
                self.downloads_history_file = Path("downloads/downloads_history.json")
                self.downloads_history_file.parent.mkdir(parents=True, exist_ok=True)
                
            # Load existing history
            if self.downloads_history_file.exists():
                with open(self.downloads_history_file, 'r') as f:
                    history = json.load(f)
            else:
                history = {"completed": [], "incomplete": [], "settings": {"theme": self.current_theme}}

            # Process HTTP downloads
            if hasattr(self, 'active_http_downloads'):
                for download in self.active_http_downloads:
                    try:
                        # Calculate actual progress percentage
                        progress = 0
                        if hasattr(download['thread'].downloader, 'file_size') and download['thread'].downloader.file_size > 0:
                            progress = int((download['thread'].downloader.downloaded_bytes / download['thread'].downloader.file_size) * 100)
                        else:
                            progress = download['progress'].value()

                        download_info = {
                            "type": "http",
                            "url": download['thread'].downloader.url,
                            "progress": progress,
                            "status": download['status'].text(),
                            "timestamp": time.time(),
                            "output_path": str(download['thread'].downloader.output_path)
                        }

                        if "completed successfully" in download['status'].text():
                            history["completed"].append(download_info)
                        else:
                            # Update existing incomplete entry if it exists
                            updated = False
                            for i, inc in enumerate(history["incomplete"]):
                                if inc["url"] == download_info["url"]:
                                    history["incomplete"][i] = download_info
                                    updated = True
                                    break
                            if not updated:
                                history["incomplete"].append(download_info)
                    except Exception as e:
                        logger.error(f"Error processing download state: {e}")
                        continue

            # Process YouTube downloads
            if hasattr(self, 'active_yt_downloads'):
                for download in self.active_yt_downloads:
                    try:
                        # Calculate actual progress percentage
                        progress = download['progress'].value()
                        
                        # Get additional information from the downloader
                        downloader = download['thread'].downloader
                        format_type = getattr(downloader, 'format_type', 'video')
                        
                        download_info = {
                            "type": "youtube",
                            "url": downloader.url,
                            "progress": progress,
                            "status": download['status'].text(),
                            "timestamp": time.time(),
                            "output_path": str(downloader.output_path),
                            "format_type": format_type
                        }
                        
                        # Add title if available
                        if hasattr(downloader, 'title') and downloader.title:
                            download_info["title"] = downloader.title

                        if "completed successfully" in download['status'].text():
                            history["completed"].append(download_info)
                        else:
                            # Update existing incomplete entry if it exists
                            updated = False
                            for i, inc in enumerate(history["incomplete"]):
                                if inc.get("url") == download_info["url"] and inc.get("type") == "youtube":
                                    history["incomplete"][i] = download_info
                                    updated = True
                                    break
                            if not updated:
                                history["incomplete"].append(download_info)
                    except Exception as e:
                        logger.error(f"Error processing YouTube download state: {e}")
                        continue

            # Save updated history
            self.save_downloads_history(history)

        except Exception as e:
            logger.error(f"Error saving download states: {e}")


    def load_user_settings(self):
        try:
            if self.downloads_history_file.exists():
                with open(self.downloads_history_file, 'r') as f:
                    history = json.load(f)
                    # Get theme setting or default to light
                    self.current_theme = history.get("settings", {}).get("theme", "light")
            else:
                self.current_theme = "light"
        except Exception as e:
            logger.error(f"Error loading user settings: {e}")
            self.current_theme = "light"
    
    def save_user_settings(self):
        try:
            if self.downloads_history_file.exists():
                with open(self.downloads_history_file, 'r') as f:
                    history = json.load(f)
            else:
                history = {"completed": [], "incomplete": [], "settings": {}}
            
            # Update theme setting
            if "settings" not in history:
                history["settings"] = {}
            history["settings"]["theme"] = self.current_theme
            
            # Save updated history
            self.save_downloads_history(history)
        except Exception as e:
            logger.error(f"Error saving user settings: {e}")
    
    def create_header(self):
        header_layout = QHBoxLayout()
        
        # Add app title to the left
        app_title = QLabel("Modern Download Manager")
        app_title.setStyleSheet("font-size: 16px; font-weight: bold;")
        header_layout.addWidget(app_title)
        
        # Add spacer to push the theme toggle to the right
        header_layout.addStretch()
        
        # Create theme toggle button with improved styling
        self.theme_toggle = QPushButton()
        self.theme_toggle.setToolTip("Toggle Dark/Light Mode")
        self.theme_toggle.setFixedSize(36, 36)
        self.theme_toggle.setStyleSheet(
            "QPushButton { border-radius: 18px; padding: 5px; }"
            "QPushButton:hover { background-color: rgba(128, 128, 128, 0.2); }"
        )
        self.theme_toggle.clicked.connect(self.toggle_theme)
        self.update_theme_button_icon()
        
        header_layout.addWidget(self.theme_toggle)
        self.main_layout.addLayout(header_layout)
    
    def update_theme_button_icon(self):
        # Check if current_theme attribute exists, default to light if not
        theme = getattr(self, 'current_theme', 'light')
        
        if theme == "dark":
            self.theme_toggle.setText("☀️")
            self.theme_toggle.setToolTip("Switch to Light Mode")
        else:
            self.theme_toggle.setText("🌙")
            self.theme_toggle.setToolTip("Switch to Dark Mode")
    
    def toggle_theme(self):
        # Toggle between light and dark themes - safely get current theme
        theme = getattr(self, 'current_theme', 'light')
        self.current_theme = "light" if theme == "dark" else "dark"
        self.apply_theme()
        self.update_theme_button_icon()
        self.save_user_settings()
    
    def apply_theme(self):
        # Apply the current theme - safely get the theme value
        theme = getattr(self, 'current_theme', 'light')
        self.main_widget.setProperty("theme", theme)
        self.load_styles()
        
        # Force style refresh
        self.main_widget.style().unpolish(self.main_widget)
        self.main_widget.style().polish(self.main_widget)
        self.main_widget.update()
        
        # Update all child widgets
        for widget in self.findChildren(QWidget):
            widget.setProperty("theme", theme)
            widget.style().unpolish(widget)
            widget.style().polish(widget)
            widget.update()
    
    def load_styles(self):
        # Get the current theme value safely
        theme = getattr(self, 'current_theme', 'light')
        
        # Set the object name for the main widget to help with CSS styling
        self.main_widget.setObjectName("main_widget")
        
        # Load the stylesheet
        with open("style.qss", "r") as f:
            self.setStyleSheet(f.read())
            
        # Apply the theme property to the main widget
        self.main_widget.setProperty("theme", theme)

    def create_http_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)

        # URL List with improved header
        header_label = QLabel("URLs to download:")
        header_label.setStyleSheet("font-weight: bold; margin-top: 5px;")
        layout.addWidget(header_label)
        
        self.http_url_list = QListWidget()
        self.http_url_list.setSelectionMode(QListWidget.MultiSelection)
        self.http_url_list.setAlternatingRowColors(True)  # Improve readability
        layout.addWidget(self.http_url_list)

        # URL Input
        url_layout = QHBoxLayout()
        self.http_url = QLineEdit()
        self.http_url.setPlaceholderText("Enter HTTP URL to download")
        url_layout.addWidget(self.http_url)

        # Add URL Button
        self.http_add_btn = QPushButton("Add URL")
        self.http_add_btn.clicked.connect(self.add_http_url)
        url_layout.addWidget(self.http_add_btn)

        layout.addLayout(url_layout)

        # Download Button
        self.http_download_btn = QPushButton()
        self.http_download_btn.setIcon(QIcon("resources/icons/download.svg"))
        self.http_download_btn.setText("Download All")
        self.http_download_btn.clicked.connect(self.start_http_downloads)
        layout.addWidget(self.http_download_btn)

        # Downloads Container
        self.http_downloads_container = QWidget()
        self.http_downloads_layout = QVBoxLayout(self.http_downloads_container)
        layout.addWidget(self.http_downloads_container)

        self.tabs.addTab(tab, "HTTP Download")

    def create_youtube_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)

        # URL List with improved header
        header_label = QLabel("YouTube URLs to download:")
        header_label.setStyleSheet("font-weight: bold; margin-top: 5px;")
        layout.addWidget(header_label)
        
        self.yt_url_list = QListWidget()
        self.yt_url_list.setSelectionMode(QListWidget.MultiSelection)
        self.yt_url_list.setAlternatingRowColors(True)  # Improve readability
        layout.addWidget(self.yt_url_list)

        # URL Input
        url_layout = QHBoxLayout()
        self.yt_url = QLineEdit()
        self.yt_url.setPlaceholderText("Enter YouTube URL")
        url_layout.addWidget(self.yt_url)

        # Add URL Button
        self.yt_add_btn = QPushButton("Add URL")
        self.yt_add_btn.clicked.connect(self.add_yt_url)
        url_layout.addWidget(self.yt_add_btn)

        layout.addLayout(url_layout)
        
        # Format Selection
        format_layout = QHBoxLayout()
        format_layout.addWidget(QLabel("Format:"))
        
        # Create radio buttons for format selection
        self.format_group = QButtonGroup()
        
        self.video_radio = QRadioButton("Video")
        self.video_radio.setChecked(True)  # Default selection
        self.format_group.addButton(self.video_radio)
        format_layout.addWidget(self.video_radio)
        
        self.audio_radio = QRadioButton("Audio")
        self.format_group.addButton(self.audio_radio)
        format_layout.addWidget(self.audio_radio)
        
        format_layout.addStretch()
        layout.addLayout(format_layout)

        # Download Button
        self.yt_download_btn = QPushButton()
        self.yt_download_btn.setIcon(QIcon("resources/icons/youtube.svg"))
        self.yt_download_btn.setText("Download All")
        self.yt_download_btn.clicked.connect(self.start_yt_downloads)
        layout.addWidget(self.yt_download_btn)

        # Downloads Container
        self.yt_downloads_container = QWidget()
        self.yt_downloads_layout = QVBoxLayout(self.yt_downloads_container)
        layout.addWidget(self.yt_downloads_container)

        self.tabs.addTab(tab, "YouTube Download")

    def add_http_url(self):
        url = self.http_url.text().strip()
        if url:
            self.http_url_list.addItem(url)
            self.http_url.clear()

    def start_http_downloads(self):
        if self.http_url_list.count() == 0:
            QMessageBox.warning(self, "Error", "Please add at least one URL")
            return

        output_path = QFileDialog.getExistingDirectory(self, "Select Download Directory")
        if not output_path:
            return

        self.http_download_btn.setEnabled(False)
        
        # Clear previous downloads
        for i in reversed(range(self.http_downloads_layout.count())): 
            self.http_downloads_layout.itemAt(i).widget().setParent(None)
        
        self.active_http_downloads = []
        
        # Update tab title to show active downloads
        self.update_tab_titles()
        
        for i in range(self.http_url_list.count()):
            url = self.http_url_list.item(i).text()
            
            # Create download item UI
            download_item = QWidget()
            layout = QVBoxLayout(download_item)
            
            url_label = QLabel(f"URL: {url}")
            progress = QProgressBar()
            status = QLabel("Pending...")
            
            # Add pause button
            control_layout = QHBoxLayout()
            pause_btn = QPushButton()
            pause_btn.setIcon(QIcon("resources/icons/pause.svg"))
            pause_btn.setToolTip("Pause Download")
            
            control_layout.addWidget(pause_btn)
            control_layout.addStretch()
            
            layout.addWidget(url_label)
            layout.addWidget(progress)
            layout.addWidget(status)
            layout.addLayout(control_layout)
            
            self.http_downloads_layout.addWidget(download_item)
            
            # Start download
            downloader = HttpDownloader(url, output_path=output_path)
            thread = DownloadThread(downloader)
            thread.progress_updated.connect(lambda progress, idx=i: self.update_http_progress(progress, idx))
            thread.download_complete.connect(lambda success, idx=i: self.http_download_finished(success, idx))
            thread.paused.connect(lambda: self.on_http_download_paused(pause_btn))
            thread.start()
            
            # Connect pause button
            pause_btn.clicked.connect(thread.pause_download)
            
            self.active_http_downloads.append({
                'thread': thread,
                'progress': progress,
                'status': status,
                'pause_btn': pause_btn
            })

    def on_http_download_paused(self, pause_btn):
        pause_btn.hide()

    def update_http_progress(self, progress, idx):
        if idx < len(self.active_http_downloads):
            try:
                # Convert percent to integer before setting value
                if isinstance(progress, dict):
                    percent = float(progress.get('percent', 0))
                    self.active_http_downloads[idx]['progress'].setValue(int(percent))
                    
                    # Add remaining time estimate if download is progressing
                    if percent > 0 and percent < 100 and 'speed' in progress:
                        speed = progress['speed']
                        if isinstance(speed, str) and ' ' in speed:
                            speed_value = float(speed.split()[0])
                            if speed_value > 0:
                                self.active_http_downloads[idx]['status'].setText(f"Downloading: {speed}")
                            else:
                                self.active_http_downloads[idx]['status'].setText("Starting download...")
                        else:
                            self.active_http_downloads[idx]['status'].setText("Calculating speed...")
                    else:
                        self.active_http_downloads[idx]['status'].setText("Processing...")
            except (ValueError, TypeError, KeyError) as e:
                logger.error(f"Error updating progress: {e}")
                self.active_http_downloads[idx]['status'].setText("Error updating progress")

    def http_download_finished(self, success, idx):
        if idx < len(self.active_http_downloads):
            if success:
                self.active_http_downloads[idx]['status'].setText("Download completed successfully")
                self.active_http_downloads[idx]['progress'].setValue(100)  # Ensure progress shows 100%
                # Show a notification
                QMessageBox.information(self, "Download Complete", 
                                      f"The HTTP download has completed successfully.\n\nFile: {self.active_http_downloads[idx]['thread'].downloader.output_path}")
            else:
                self.active_http_downloads[idx]['status'].setText("Download failed")
                # Show error notification with retry option
                retry = QMessageBox.question(self, "Download Failed", 
                                           "The HTTP download failed. Would you like to retry?",
                                           QMessageBox.Yes | QMessageBox.No)
                if retry == QMessageBox.Yes:
                    # Restart the download
                    thread = self.active_http_downloads[idx]['thread']
                    url = thread.downloader.url
                    output_path = thread.downloader.output_path
                    
                    # Create new downloader and thread
                    downloader = HttpDownloader(url, output_path=output_path)
                    new_thread = DownloadThread(downloader)
                    new_thread.progress_updated.connect(lambda progress, i=idx: self.update_http_progress(progress, i))
                    new_thread.download_complete.connect(lambda success, i=idx: self.http_download_finished(success, i))
                    new_thread.start()
                    
                    # Update references
                    self.active_http_downloads[idx]['thread'] = new_thread
                    self.active_http_downloads[idx]['status'].setText("Retrying download...")
                    return
            # Hide pause button when download is complete
            self.active_http_downloads[idx]['pause_btn'].hide()
        
        # Enable button when all downloads complete
        if all(not d['thread'].isRunning() for d in self.active_http_downloads):
            self.http_download_btn.setEnabled(True)

    def add_yt_url(self):
        url = self.yt_url.text().strip()
        if url:
            self.yt_url_list.addItem(url)
            self.yt_url.clear()

    def start_yt_downloads(self):
        if self.yt_url_list.count() == 0:
            QMessageBox.warning(self, "Error", "Please add at least one YouTube URL")
            return

        output_path = QFileDialog.getExistingDirectory(self, "Select Download Directory")
        if not output_path:
            return

        self.yt_download_btn.setEnabled(False)
        
        # Clear previous downloads
        for i in reversed(range(self.yt_downloads_layout.count())): 
            self.yt_downloads_layout.itemAt(i).widget().setParent(None)
        
        self.active_yt_downloads = []
        
        # Update tab title to show active downloads
        self.update_tab_titles()
        
        for i in range(self.yt_url_list.count()):
            url = self.yt_url_list.item(i).text()
            
            # Create download item UI
            download_item = QWidget()
            layout = QVBoxLayout(download_item)
            
            url_label = QLabel(f"URL: {url}")
            progress = QProgressBar()
            status = QLabel("Pending...")
            
            # Add pause button
            control_layout = QHBoxLayout()
            pause_btn = QPushButton()
            pause_btn.setIcon(QIcon("resources/icons/pause.svg"))
            pause_btn.setToolTip("Pause Download")
            
            control_layout.addWidget(pause_btn)
            control_layout.addStretch()
            
            layout.addWidget(url_label)
            layout.addWidget(progress)
            layout.addWidget(status)
            layout.addLayout(control_layout)
            
            self.yt_downloads_layout.addWidget(download_item)
            
            # Get selected format
            format_type = 'audio' if self.audio_radio.isChecked() else 'video'
            
            # Start download
            # Use .mp3 extension for audio files, .mp4 for video files
            file_extension = '.mp3' if format_type == 'audio' else '.mp4'
            file_prefix = 'audio' if format_type == 'audio' else 'video'
            downloader = YoutubeDownloader(url, output_path=f"{output_path}/{file_prefix}_{i}{file_extension}", format_type=format_type)
            thread = DownloadThread(downloader)
            thread.progress_updated.connect(lambda progress, idx=i: self.update_yt_progress(progress, idx))
            thread.download_complete.connect(lambda success, idx=i: self.yt_download_finished(success, idx))
            thread.paused.connect(lambda: self.on_yt_download_paused(pause_btn))
            thread.start()
            
            # Connect pause button
            pause_btn.clicked.connect(thread.pause_download)
            
            self.active_yt_downloads.append({
                'thread': thread,
                'progress': progress,
                'status': status,
                'pause_btn': pause_btn
            })

    def on_yt_download_paused(self, pause_btn):
        pause_btn.hide()

    def on_yt_download_resumed(self, pause_btn, resume_btn):
        resume_btn.hide()
        pause_btn.show()

    def update_yt_progress(self, progress, idx):
        if idx < len(self.active_yt_downloads):
            # Convert percent to integer before setting value
            percent = int(progress.get('percent', 0)) if isinstance(progress, dict) else 0
            self.active_yt_downloads[idx]['progress'].setValue(percent)
            
            # Update status text with speed or phase information
            status_text = ""
            if isinstance(progress, dict) and 'speed' in progress:
                speed_info = progress['speed']
                
                # Handle special status messages like "Merging..." or "Complete"
                if isinstance(speed_info, str) and not speed_info.replace('.', '', 1).isdigit():
                    status_text = f"Status: {speed_info}"
                    
                    # For merging phase, implement incremental progress updates
                    if "Merging" in speed_info and percent == 50:
                        # Start a timer to simulate progress during merging
                        if not hasattr(self.active_yt_downloads[idx], 'merge_timer'):
                            self.active_yt_downloads[idx]['merge_start_time'] = time.time()
                            self.active_yt_downloads[idx]['merge_timer'] = True
                            
                            # Create a timer to update progress during merging
                            def update_merge_progress():
                                if idx < len(self.active_yt_downloads) and percent < 100:
                                    elapsed = time.time() - self.active_yt_downloads[idx]['merge_start_time']
                                    # Simulate progress from 50% to 95% over approximately 30 seconds
                                    simulated_progress = min(95, 50 + int(elapsed / 30 * 45))
                                    self.active_yt_downloads[idx]['progress'].setValue(simulated_progress)
                                    
                                    # Continue updating until we reach 95% or the actual process completes
                                    if simulated_progress < 95 and idx < len(self.active_yt_downloads):
                                        QApplication.processEvents()  # Keep UI responsive
                                        threading.Timer(0.5, update_merge_progress).start()
                            
                            # Start the progress update timer
                            threading.Timer(0.5, update_merge_progress).start()
                else:
                    # For normal download progress with speed information
                    try:
                        speed_text = str(speed_info)
                        if speed_text.replace('.', '', 1).isdigit():
                            speed_kbps = float(speed_text)
                            status_text = f"Speed: {speed_kbps:.2f} KB/s"
                            
                            # Calculate estimated time remaining if we have size information
                            if hasattr(self.active_yt_downloads[idx]['thread'].downloader, 'total_size'):
                                total_size = self.active_yt_downloads[idx]['thread'].downloader.total_size
                                if total_size > 0:
                                    downloaded = total_size * (percent / 100)
                                    remaining = total_size - downloaded
                                    if speed_kbps > 0:
                                        eta_seconds = remaining / (speed_kbps * 1024)
                                        if eta_seconds < 60:
                                            status_text += f" | ETA: {int(eta_seconds)} sec"
                                        elif eta_seconds < 3600:
                                            status_text += f" | ETA: {int(eta_seconds/60)} min"
                                        else:
                                            status_text += f" | ETA: {eta_seconds/3600:.1f} hr"
                        else:
                            status_text = f"Status: {speed_info}"
                    except (ValueError, TypeError):
                        status_text = f"Status: {speed_info}"
            
            # Update the status label
            if status_text:
                self.active_yt_downloads[idx]['status'].setText(status_text)
            
            # Force UI update to ensure smooth progress bar updates
            QApplication.processEvents()

    def yt_download_finished(self, success, idx):
        if idx < len(self.active_yt_downloads):
            downloader = self.active_yt_downloads[idx]['thread'].downloader
            if downloader.stopped.is_set():
                # Download was paused, don't show error dialog
                self.active_yt_downloads[idx]['status'].setText("Download paused")
                self.active_yt_downloads[idx]['pause_btn'].hide()
                return
            
            if success:
                self.active_yt_downloads[idx]['status'].setText("Download completed successfully")
                self.active_yt_downloads[idx]['progress'].setValue(100)  # Ensure progress shows 100%
                # Show a notification
                QMessageBox.information(self, "Download Complete", 
                                      f"The YouTube download has completed successfully.\n\nFile: {self.active_yt_downloads[idx]['thread'].downloader.output_path}")
            else:
                self.active_yt_downloads[idx]['status'].setText("Download failed")
                # Show error notification with retry option
                retry = QMessageBox.question(self, "Download Failed", 
                                           "The YouTube download failed. Would you like to retry?",
                                           QMessageBox.Yes | QMessageBox.No)
                if retry == QMessageBox.Yes:
                    # Restart the download
                    thread = self.active_yt_downloads[idx]['thread']
                    url = thread.downloader.url
                    output_path = thread.downloader.output_path
                    format_type = thread.downloader.format_type
                    
                    # Create new downloader and thread
                    downloader = YoutubeDownloader(url, output_path=output_path, format_type=format_type)
                    new_thread = DownloadThread(downloader)
                    new_thread.progress_updated.connect(lambda progress, i=idx: self.update_yt_progress(progress, i))
                    new_thread.download_complete.connect(lambda success, i=idx: self.yt_download_finished(success, i))
                    new_thread.start()
                    
                    # Update references
                    self.active_yt_downloads[idx]['thread'] = new_thread
                    self.active_yt_downloads[idx]['status'].setText("Retrying download...")
                    return
                        
            # Hide pause button when download is complete
            self.active_yt_downloads[idx]['pause_btn'].hide()
        
        # Enable button when all downloads complete
        if all(not d['thread'].isRunning() for d in self.active_yt_downloads):
            self.yt_download_btn.setEnabled(True)


    def create_resume_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)

        # Resume Downloads List with improved header
        header_label = QLabel("Interrupted Downloads:")
        header_label.setStyleSheet("font-weight: bold; margin-top: 5px;")
        layout.addWidget(header_label)
        
        self.resume_list = QListWidget()
        self.resume_list.setAlternatingRowColors(True)  # Improve readability
        layout.addWidget(self.resume_list)

        # Refresh and Clear Buttons
        btn_layout = QHBoxLayout()
        self.refresh_btn = QPushButton("Refresh List")
        self.refresh_btn.clicked.connect(self.refresh_resume_list)
        self.clear_btn = QPushButton("Clear Selected")
        self.clear_btn.clicked.connect(self.clear_selected_resume)
        btn_layout.addWidget(self.refresh_btn)
        btn_layout.addWidget(self.clear_btn)
        layout.addLayout(btn_layout)

        # Resume Button
        self.resume_btn = QPushButton()
        self.resume_btn.setIcon(QIcon("resources/icons/resume.svg"))
        self.resume_btn.setText("Resume Selected")
        self.resume_btn.clicked.connect(self.resume_selected_downloads)
        layout.addWidget(self.resume_btn)

        # Resume Downloads Container
        self.resume_downloads_container = QWidget()
        self.resume_downloads_layout = QVBoxLayout(self.resume_downloads_container)
        layout.addWidget(self.resume_downloads_container)

        self.tabs.addTab(tab, "Resume Downloads")
        
        # Load incomplete downloads when tab is created
        self.refresh_resume_list()

    def refresh_resume_list(self):
        self.resume_list.clear()
        # Load incomplete downloads from history file
        if not hasattr(self, 'downloads_history_file'):
            self.downloads_history_file = Path("downloads/downloads_history.json")
            
        if not self.downloads_history_file.exists():
            self.downloads_history_file.parent.mkdir(parents=True, exist_ok=True)
            self.save_downloads_history({
                "completed": [],
                "incomplete": []
            })
            return
            
        try:
            with open(self.downloads_history_file, 'r') as f:
                history = json.load(f)
            
            # Check if there are any incomplete downloads    
            if not history.get("incomplete", []):
                # Add a placeholder message if no downloads are available
                placeholder = QListWidgetItem("No interrupted downloads found")
                placeholder.setFlags(placeholder.flags() & ~Qt.ItemIsSelectable)
                self.resume_list.addItem(placeholder)
                return
                
            for download in history.get("incomplete", []):
                url = download.get('url', '')
                progress = download.get('progress', 0)
                status = download.get('status', '')
                output_path = download.get('output_path', '')
                download_type = download.get('type', 'http')
                timestamp = download.get('timestamp', 0)
                
                # Format the timestamp
                date_str = time.strftime("%Y-%m-%d %H:%M", time.localtime(timestamp)) if timestamp else "Unknown date"
                
                # Create a more informative item text
                item_text = f"{url[:50]}{'...' if len(url) > 50 else ''} ({progress}% completed)\n"
                item_text += f"Type: {download_type.upper()} | Date: {date_str} | Path: {os.path.basename(output_path)}"
                
                item = QListWidgetItem(item_text)
                item.setData(Qt.UserRole, {
                    'url': url, 
                    'output_path': output_path,
                    'type': download_type,
                    'progress': progress,
                    'timestamp': timestamp
                })
                self.resume_list.addItem(item)
        except Exception as e:
            logger.error(f"Error reading downloads history: {e}")
            QMessageBox.warning(self, "Error", f"Failed to load download history: {e}")
            
            # Add a placeholder message if there was an error
            placeholder = QListWidgetItem("Error loading downloads")
            placeholder.setFlags(placeholder.flags() & ~Qt.ItemIsSelectable)
            self.resume_list.addItem(placeholder)


    def clear_selected_resume(self):
        if not self.resume_list.selectedItems():
            return
            
        try:
            # Load current history
            with open(self.downloads_history_file, 'r') as f:
                history = json.load(f)
                
            # Remove selected items from history
            for item in self.resume_list.selectedItems():
                data = item.data(Qt.UserRole)
                if data and 'url' in data:
                    # Find and remove the download from incomplete list
                    for i, download in enumerate(history["incomplete"]):
                        if download.get('url') == data['url']:
                            history["incomplete"].pop(i)
                            break
                    # Remove from list widget
                    self.resume_list.takeItem(self.resume_list.row(item))
            
            # Save updated history
            self.save_downloads_history(history)
            
        except Exception as e:
            logger.error(f"Error clearing resume items: {e}")
            QMessageBox.warning(self, "Error", f"Failed to clear selected downloads: {e}")


    def resume_selected_downloads(self):
        if not self.resume_list.selectedItems():
            QMessageBox.warning(self, "Error", "Please select downloads to resume")
            return

        self.resume_btn.setEnabled(False)
        
        # Clear previous downloads
        for i in reversed(range(self.resume_downloads_layout.count())): 
            self.resume_downloads_layout.itemAt(i).widget().setParent(None)
        
        self.active_resume_downloads = []
        
        for item in self.resume_list.selectedItems():
            data = item.data(Qt.UserRole)
            if not data or 'url' not in data or 'output_path' not in data:
                continue
                
            url = data['url']
            output_path = data['output_path']
            download_type = data.get('type', 'http')
            initial_progress = int(float(data.get('progress', 0)))  # Convert progress to int
            
            # Create download item UI
            download_item = QWidget()
            layout = QVBoxLayout(download_item)
            
            url_label = QLabel(f"URL: {url}")
            progress = QProgressBar()
            progress.setValue(initial_progress)  # Set initial progress value
            status = QLabel(f"Resuming {download_type} download...")
            
            # Add pause/stop buttons
            control_layout = QHBoxLayout()
            pause_btn = QPushButton()
            pause_btn.setIcon(QIcon("resources/icons/pause.svg"))
            pause_btn.setToolTip("Pause Download")
            stop_btn = QPushButton()
            stop_btn.setIcon(QIcon("resources/icons/stop.svg"))
            stop_btn.setToolTip("Stop Download")
            
            control_layout.addWidget(pause_btn)
            control_layout.addWidget(stop_btn)
            control_layout.addStretch()
            
            layout.addWidget(url_label)
            layout.addWidget(progress)
            layout.addWidget(status)
            layout.addLayout(control_layout)
            
            self.resume_downloads_layout.addWidget(download_item)
            
            # Start download based on type
            if download_type == 'youtube':
                downloader = YoutubeDownloader(url, output_path=output_path)
            else:  # Default to HTTP
                downloader = HttpDownloader(url, output_path=output_path)
                
            thread = DownloadThread(downloader)
            thread.progress_updated.connect(lambda p, idx=len(self.active_resume_downloads): 
                                         self.update_resume_progress(p, idx))
            thread.download_complete.connect(lambda success, idx=len(self.active_resume_downloads): 
                                          self.resume_download_finished(success, idx))
            thread.paused.connect(lambda btn=pause_btn: self.on_resume_download_paused(btn))
            thread.resumed.connect(lambda: self.on_resume_download_resumed())
            thread.start()
            
            # Connect control buttons
            pause_btn.clicked.connect(thread.pause_download)
            stop_btn.clicked.connect(lambda: self.stop_resume_download(thread, item))
            
            self.active_resume_downloads.append({
                'thread': thread,
                'progress': progress,
                'status': status,
                'pause_btn': pause_btn,
                'stop_btn': stop_btn,
                'item': item  # Store reference to list item
            })

    def update_resume_progress(self, progress, idx):
        if idx < len(self.active_resume_downloads):
            try:
                # Convert percent to integer before setting value
                if isinstance(progress, dict):
                    percent = float(progress.get('percent', 0))
                    self.active_resume_downloads[idx]['progress'].setValue(int(percent))
                    
                    # Update progress in the downloads history
                    item = self.active_resume_downloads[idx]['item']
                    data = item.data(Qt.UserRole)
                    if data:
                        data['progress'] = int(percent)
                        item.setData(Qt.UserRole, data)
                    
                    # Add remaining time estimate if download is progressing
                    if percent > 0 and percent < 100:
                        speed = progress.get('speed', 'N/A')
                        downloaded = progress.get('downloaded', 0)
                        total = progress.get('total', 0)
                        
                        if total > 0 and speed and 'MB/s' in speed:
                            speed_value = float(speed.split()[0])  # Extract numeric value
                            remaining = total - downloaded
                            if speed_value > 0:
                                seconds_left = remaining / (speed_value * 1024 * 1024)
                                
                                # Format time remaining
                                if seconds_left < 60:
                                    time_str = f"{seconds_left:.0f} seconds"
                                elif seconds_left < 3600:
                                    time_str = f"{seconds_left/60:.1f} minutes"
                                else:
                                    time_str = f"{seconds_left/3600:.1f} hours"
                                    
                                self.active_resume_downloads[idx]['status'].setText(
                                    f"Downloading: {speed} - ETA: {time_str}")
                            else:
                                self.active_resume_downloads[idx]['status'].setText(f"Downloading: {speed}")
                        else:
                            self.active_resume_downloads[idx]['status'].setText(f"Downloading: {speed}")
                        
                        # Save progress periodically
                        self.save_download_states()
            except (ValueError, TypeError, KeyError) as e:
                logger.error(f"Error updating progress: {e}")
                self.active_resume_downloads[idx]['status'].setText("Error updating progress")

    def resume_download_finished(self, success, idx):
        if idx < len(self.active_resume_downloads):
            if success:
                self.active_resume_downloads[idx]['status'].setText("Download completed successfully")
                self.active_resume_downloads[idx]['progress'].setValue(100)  # Ensure progress shows 100%
                # Update history - move from incomplete to completed
                self.update_download_history(self.active_resume_downloads[idx]['item'], completed=True)
                
                # Show a notification
                download_type = self.active_resume_downloads[idx]['thread'].downloader.__class__.__name__
                QMessageBox.information(self, "Download Complete", 
                                      f"The resumed {download_type} download has completed successfully.\n\nFile: {self.active_resume_downloads[idx]['thread'].downloader.output_path}")
            else:
                self.active_resume_downloads[idx]['status'].setText("Download failed")
                
                # Show error notification with retry option
                retry = QMessageBox.question(self, "Download Failed", 
                                           "The resumed download failed. Would you like to retry?",
                                           QMessageBox.Yes | QMessageBox.No)
                if retry == QMessageBox.Yes:
                    # Restart the download
                    thread = self.active_resume_downloads[idx]['thread']
                    url = thread.downloader.url
                    output_path = thread.downloader.output_path
                    
                    # Determine the downloader type
                    if hasattr(thread.downloader, 'format_type'):
                        # YouTube downloader
                        format_type = thread.downloader.format_type
                        downloader = YoutubeDownloader(url, output_path=output_path, format_type=format_type)
                    else:
                        # HTTP downloader
                        downloader = HttpDownloader(url, output_path=output_path)
                    
                    # Create new thread
                    new_thread = DownloadThread(downloader)
                    new_thread.progress_updated.connect(lambda progress, i=idx: self.update_resume_progress(progress, i))
                    new_thread.download_complete.connect(lambda success, i=idx: self.resume_download_finished(success, i))
                    new_thread.start()
                    
                    # Update references
                    self.active_resume_downloads[idx]['thread'] = new_thread
                    self.active_resume_downloads[idx]['status'].setText("Retrying download...")
                    return
                    
            self.active_resume_downloads[idx]['pause_btn'].setEnabled(False)
            self.active_resume_downloads[idx]['stop_btn'].setEnabled(False)
        
        if all(not d['thread'].isRunning() for d in self.active_resume_downloads):
            self.resume_btn.setEnabled(True)
            self.refresh_resume_list()
            
    def on_resume_download_paused(self, pause_btn):
        pause_btn.hide()
        
    def on_resume_download_resumed(self):
        # This method is called when a download is resumed
        pass
        
    def update_download_history(self, item, completed=False):
        """Update the download history when a download is completed or failed"""
        try:
            # Load current history
            with open(self.downloads_history_file, 'r') as f:
                history = json.load(f)
                
            data = item.data(Qt.UserRole)
            if data and 'url' in data:
                # Find the download in incomplete list
                for i, download in enumerate(history["incomplete"]):
                    if download.get('url') == data['url']:
                        # If completed, move to completed list
                        if completed:
                            history["completed"].append(download)
                        # Remove from incomplete list
                        history["incomplete"].pop(i)
                        break
                        
            # Save updated history
            self.save_downloads_history(history)
            
        except Exception as e:
            logger.error(f"Error updating download history: {e}")

    def stop_resume_download(self, thread, list_item):
        thread.downloader.shutdown_flag.set()
        thread.wait()
        
        # Update history file to remove the stopped download
        try:
            with open(self.downloads_history_file, 'r') as f:
                history = json.load(f)
                
            data = list_item.data(Qt.UserRole)
            if data and 'url' in data:
                # Find and remove the download from incomplete list
                for i, download in enumerate(history["incomplete"]):
                    if download.get('url') == data['url']:
                        history["incomplete"].pop(i)
                        break
                        
            # Save updated history
            self.save_downloads_history(history)
            
        except Exception as e:
            logger.error(f"Error updating history after stopping download: {e}")
            
        self.resume_list.takeItem(self.resume_list.row(list_item))


    def update_tab_titles(self):
        """Update tab titles to reflect current download status"""
        http_downloads = len([d for d in getattr(self, 'active_http_downloads', []) if d['thread'].isRunning()])
        yt_downloads = len([d for d in getattr(self, 'active_yt_downloads', []) if d['thread'].isRunning()])
        
        self.tabs.setTabText(0, f"HTTP Download ({http_downloads} active)")
        self.tabs.setTabText(1, f"YouTube Download ({yt_downloads} active)")
        self.tabs.setTabText(2, "Resume Downloads")


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec_())
