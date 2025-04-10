import sys
import time
from pathlib import Path
import json
from PyQt5.QtWidgets import (QMainWindow, QApplication, QWidget, QVBoxLayout, QHBoxLayout,
                           QLineEdit, QPushButton, QProgressBar, QLabel, QTabWidget,
                           QListWidget, QListWidgetItem, QFileDialog, QMessageBox,
                           QToolButton, QAction)
from PyQt5.QtCore import Qt, QThread, pyqtSignal
from PyQt5.QtGui import QIcon
from idm import HttpDownloader, YTDownloader, logger


class DownloadThread(QThread):
    progress_updated = pyqtSignal(dict)
    download_complete = pyqtSignal(bool)
    paused = pyqtSignal()
    resumed = pyqtSignal()

    def __init__(self, downloader):
        super().__init__()
        self.downloader = downloader
        self.is_paused = False
        # Set progress callback on the downloader
        # Handle both progress_callback and progress_updated attributes
        if hasattr(self.downloader, 'progress_callback'):
            self.downloader.progress_callback = self.progress_updated.emit
        if hasattr(self.downloader, 'progress_updated'):
            self.downloader.progress_updated = self.progress_updated.emit

    def run(self):
        try:
            success = self.downloader.start_download()
            self.download_complete.emit(success)
        except Exception as e:
            print(f"Download error: {e}")
            self.download_complete.emit(False)
        finally:
            # Clean up callback to prevent memory leaks
            self.downloader.progress_callback = None

    def pause_download(self):
        if not self.is_paused and self.isRunning():
            self.is_paused = True
            self.downloader.pause_download()
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
                    download_info = {
                        "type": "http",
                        "url": download['thread'].downloader.url,
                        "progress": download['progress'].value(),
                        "status": download['status'].text(),
                        "timestamp": time.time(),
                        "output_path": str(download['thread'].downloader.output_path)
                    }
                    if "completed successfully" in download['status'].text():
                        history["completed"].append(download_info)
                    else:
                        history["incomplete"].append(download_info)

            # Process YouTube downloads
            if hasattr(self, 'active_yt_downloads'):
                for download in self.active_yt_downloads:
                    download_info = {
                        "type": "youtube",
                        "url": download['thread'].downloader.url,
                        "progress": download['progress'].value(),
                        "status": download['status'].text(),
                        "timestamp": time.time(),
                        "output_path": str(download['thread'].downloader.output_path)
                    }
                    if "completed successfully" in download['status'].text():
                        history["completed"].append(download_info)
                    else:
                        history["incomplete"].append(download_info)

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
        
        # Add spacer to push the theme toggle to the right
        header_layout.addStretch()
        
        # Create theme toggle button
        self.theme_toggle = QPushButton()
        self.theme_toggle.setToolTip("Toggle Dark/Light Mode")
        self.theme_toggle.setFixedSize(30, 30)
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

        # URL List
        self.http_url_list = QListWidget()
        self.http_url_list.setSelectionMode(QListWidget.MultiSelection)
        layout.addWidget(QLabel("URLs to download:"))
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

        # URL List
        self.yt_url_list = QListWidget()
        self.yt_url_list.setSelectionMode(QListWidget.MultiSelection)
        layout.addWidget(QLabel("YouTube URLs to download:"))
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
            # Convert percent to integer before setting value
            percent = int(progress['percent']) if isinstance(progress['percent'], (int, float)) else 0
            self.active_http_downloads[idx]['progress'].setValue(percent)
            self.active_http_downloads[idx]['status'].setText(f"Downloading: {progress['speed']}")

    def http_download_finished(self, success, idx):
        if idx < len(self.active_http_downloads):
            if success:
                self.active_http_downloads[idx]['status'].setText("Download completed successfully")
            else:
                self.active_http_downloads[idx]['status'].setText("Download failed")
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
            
            # Start download
            downloader = YTDownloader(url, output_path=output_path)
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
            percent = int(progress['percent']) if isinstance(progress['percent'], (int, float)) else 0
            self.active_yt_downloads[idx]['progress'].setValue(percent)
            self.active_yt_downloads[idx]['status'].setText(f"Downloading: {progress['speed']}")

    def yt_download_finished(self, success, idx):
        if idx < len(self.active_yt_downloads):
            if success:
                self.active_yt_downloads[idx]['status'].setText("YouTube download completed successfully")
            else:
                self.active_yt_downloads[idx]['status'].setText("YouTube download failed")
            # Hide pause button when download is complete
            self.active_yt_downloads[idx]['pause_btn'].hide()
        
        # Enable button when all downloads complete
        if all(not d['thread'].isRunning() for d in self.active_yt_downloads):
            self.yt_download_btn.setEnabled(True)


    def create_resume_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)

        # Resume Downloads List
        self.resume_list = QListWidget()
        layout.addWidget(QLabel("Interrupted Downloads:"))
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
                
            for download in history.get("incomplete", []):
                url = download.get('url', '')
                progress = download.get('progress', 0)
                status = download.get('status', '')
                output_path = download.get('output_path', '')
                download_type = download.get('type', 'http')
                
                item_text = f"{url} ({progress}% completed) - {download_type}"
                item = QListWidgetItem(item_text)
                item.setData(Qt.UserRole, {
                    'url': url, 
                    'output_path': output_path,
                    'type': download_type,
                    'progress': progress
                })
                self.resume_list.addItem(item)
        except Exception as e:
            logger.error(f"Error reading downloads history: {e}")
            QMessageBox.warning(self, "Error", f"Failed to load download history: {e}")


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
            initial_progress = data.get('progress', 0)
            
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
                downloader = YTDownloader(url, output_path=output_path)
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
            # Convert percent to integer before setting value
            percent = int(progress['percent']) if isinstance(progress['percent'], (int, float)) else 0
            self.active_resume_downloads[idx]['progress'].setValue(percent)
            self.active_resume_downloads[idx]['status'].setText(f"Downloading: {progress['speed']}")

    def resume_download_finished(self, success, idx):
        if idx < len(self.active_resume_downloads):
            if success:
                self.active_resume_downloads[idx]['status'].setText("Download completed successfully")
                # Update history - move from incomplete to completed
                self.update_download_history(self.active_resume_downloads[idx]['item'], completed=True)
            else:
                self.active_resume_downloads[idx]['status'].setText("Download failed")
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

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Modern Download Manager")
        self.setMinimumSize(800, 600)
        self.setWindowIcon(QIcon("resources/icons/app_icon.svg"))

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


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec_())