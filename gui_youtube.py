import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import threading
import os
import json
from test_yt import YoutubeDownloader

class YoutubeDownloaderGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("YouTube Downloader")
        self.root.geometry("600x400")
        self.root.resizable(False, False)
        
        # Create main frame
        self.main_frame = ttk.Frame(root, padding="10")
        self.main_frame.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
        
        # URL Input
        ttk.Label(self.main_frame, text="YouTube URL:").grid(row=0, column=0, sticky=tk.W, pady=5)
        self.url_var = tk.StringVar()
        self.url_entry = ttk.Entry(self.main_frame, textvariable=self.url_var, width=50)
        self.url_entry.grid(row=0, column=1, columnspan=2, sticky=(tk.W, tk.E), pady=5)
        
        # Output Path
        ttk.Label(self.main_frame, text="Save to:").grid(row=1, column=0, sticky=tk.W, pady=5)
        self.output_var = tk.StringVar()
        self.output_entry = ttk.Entry(self.main_frame, textvariable=self.output_var, width=40)
        self.output_entry.grid(row=1, column=1, sticky=(tk.W, tk.E), pady=5)
        ttk.Button(self.main_frame, text="Browse", command=self.browse_output).grid(row=1, column=2, sticky=tk.W, pady=5, padx=5)
        
        # Format Selection
        ttk.Label(self.main_frame, text="Format:").grid(row=2, column=0, sticky=tk.W, pady=5)
        self.format_var = tk.StringVar(value="video")
        ttk.Radiobutton(self.main_frame, text="Video", variable=self.format_var, value="video").grid(row=2, column=1, sticky=tk.W, pady=5)
        ttk.Radiobutton(self.main_frame, text="Audio", variable=self.format_var, value="audio").grid(row=2, column=2, sticky=tk.W, pady=5)
        
        # Progress Bar
        self.progress_var = tk.DoubleVar()
        self.progress_bar = ttk.Progressbar(self.main_frame, variable=self.progress_var, maximum=100)
        self.progress_bar.grid(row=3, column=0, columnspan=3, sticky=(tk.W, tk.E), pady=10)
        
        # Status Label
        self.status_var = tk.StringVar(value="Ready")
        self.status_label = ttk.Label(self.main_frame, textvariable=self.status_var)
        self.status_label.grid(row=4, column=0, columnspan=3, pady=5)
        
        # Create a frame for buttons
        self.button_frame = ttk.Frame(self.main_frame)
        self.button_frame.grid(row=5, column=0, columnspan=3, pady=10)
        
        # Download Button
        self.download_button = ttk.Button(self.button_frame, text="Download", command=self.start_download)
        self.download_button.pack(side=tk.LEFT, padx=5)
        
        # Pause/Resume Button
        self.pause_button = ttk.Button(self.button_frame, text="Pause", command=self.toggle_pause, state='disabled')
        self.pause_button.pack(side=tk.LEFT, padx=5)
        
        self.downloader = None
        self.download_thread = None
        self.is_paused = False
        self.update_queued = False
        
        # Check for existing downloads
        self.check_existing_downloads()
    
    def check_existing_downloads(self):
        """Check for any existing download progress files and set up UI accordingly"""
        for file in os.listdir():
            if file.endswith('.progress'):
                try:
                    output_path = file[:-9]  # Remove .progress extension
                    with open(file, 'r') as f:
                        data = json.load(f)
                    
                    # Set the UI elements
                    self.url_var.set(data['url'])
                    self.output_var.set(data['output_path'])
                    self.progress_var.set((sum(chunk['downloaded'] for chunk in data['chunks']) / data['total_size']) * 100)
                    self.status_var.set("Previous download found - Ready to resume")
                    self.pause_button.state(['!disabled'])
                    self.pause_button.configure(text="Resume")
                    self.is_paused = True
                    break
                except (json.JSONDecodeError, KeyError, FileNotFoundError):
                    continue
    
    def browse_output(self):
        file_path = filedialog.asksaveasfilename(
            defaultextension=".mp4",
            filetypes=[("MP4 files", "*.mp4"), ("MP3 files", "*.mp3"), ("All files", "*.*")]
        )
        if file_path:
            self.output_var.set(file_path)
            # Check if there's a progress file for this output
            progress_file = file_path + '.progress'
            if os.path.exists(progress_file):
                try:
                    with open(progress_file, 'r') as f:
                        data = json.load(f)
                    self.url_var.set(data['url'])
                    # Calculate progress only if total_size is valid
                    if data['total_size'] > 0:
                        progress = (sum(chunk['downloaded'] for chunk in data['chunks']) / data['total_size']) * 100
                        self.progress_var.set(progress)
                        self.status_var.set("Previous download found - Ready to resume")
                        self.pause_button.state(['!disabled'])
                        self.pause_button.configure(text="Resume")
                        self.is_paused = True
                    else:
                        self.progress_var.set(0)
                        self.status_var.set("Invalid progress file - Starting new download")
                except (json.JSONDecodeError, KeyError):
                    self.progress_var.set(0)
                    self.status_var.set("Ready to download")
    
    def toggle_pause(self):
        if not self.is_paused:
            # Pause download
            if self.downloader:
                self.downloader.stopped.set()
                self.pause_button.configure(text="Resume")
                self.status_var.set("Download paused")
                self.is_paused = True
        else:
            # Resume download
            url = self.url_var.get().strip()
            output_path = self.output_var.get().strip()
            format_type = self.format_var.get()
            
            self.downloader = YoutubeDownloader(
                url, 
                output_path, 
                format_type=format_type,
                progress_callback=self.update_progress_callback
            )
            self.downloader.stopped.clear()
            self.pause_button.configure(text="Pause")
            self.status_var.set("Resuming download...")
            self.is_paused = False
            self.start_download(resume=True)

    def update_progress_callback(self, progress):
        if not self.update_queued:
            self.update_queued = True
            self.root.after(100, self.safe_update_progress, progress)

    def safe_update_progress(self, progress):
        self.update_queued = False
        self.progress_var.set(progress)
        self.status_var.set(f"Downloading... {progress:.1f}%")
        if progress < 100 and not self.is_paused:
            self.root.after(100, self.check_download_status)

    def check_download_status(self):
        if self.downloader and not self.is_paused:
            progress = (self.downloader.downloaded_size / self.downloader.total_size) * 100
            self.safe_update_progress(progress)

    def start_download(self, resume=False):
        if resume and not self.downloader:
            return

        if not resume:
            url = self.url_var.get().strip()
            output_path = self.output_var.get().strip()
            format_type = self.format_var.get()
            
            if not url or not output_path:
                messagebox.showerror("Error", "Please fill in all fields")
                return
            
            self.download_button.state(['disabled'])
            self.pause_button.state(['!disabled'])
            self.status_var.set("Initializing download...")
            self.progress_var.set(0)
        
        def download_thread():
            try:
                if not resume:
                    self.downloader = YoutubeDownloader(
                        url, 
                        output_path, 
                        format_type=format_type,
                        progress_callback=self.update_progress_callback
                    )
                self.downloader.download()
                
                if not self.is_paused:
                    self.root.after(0, self.download_complete)
            except Exception as error:
                error_msg = str(error)
                self.root.after(0, lambda: self.handle_error(error_msg))

        self.download_thread = threading.Thread(target=download_thread)
        self.download_thread.start()

    def download_complete(self):
        self.status_var.set("Download completed!")
        self.pause_button.state(['disabled'])
        self.download_button.state(['!disabled'])

    def handle_error(self, error_msg):
        messagebox.showerror("Error", error_msg)
        self.download_button.state(['!disabled'])
        self.pause_button.state(['disabled'])

def main():
    root = tk.Tk()
    app = YoutubeDownloaderGUI(root)
    root.mainloop()

if __name__ == "__main__":
    main()
