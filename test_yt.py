import os
import json
import threading
import requests
from pytubefix import YouTube
from pytubefix.exceptions import PytubeFixError as PytubeError
from requests.exceptions import RequestException
import time
import sys

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
        self.session = requests.Session()
        self.title = ""
        self.download_url = ""
        self.progress_callback = progress_callback

    def select_stream(self):
        try:
            yt = YouTube(self.url)
            if self.format_type == 'audio':
                streams = yt.streams.filter(only_audio=True).order_by('abr').desc()
                if not streams:
                    raise ValueError("No audio streams available.")
                self.selected_stream = streams[0]
            else:
                self.selected_stream = yt.streams.get_highest_resolution()
                if not self.selected_stream:
                    raise ValueError("No video streams available.")
            
            self.download_url = self.selected_stream.url
            self.title = yt.title
        except PytubeError as e:
            raise RuntimeError(f"Error accessing YouTube video: {e}")

    def _get_total_size(self):
        response = self.session.head(self.download_url, allow_redirects=True)
        response.raise_for_status()
        self.total_size = int(response.headers.get('Content-Length', 0))
        if self.total_size == 0:
            raise RuntimeError("Unable to determine file size.")

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
            response = self.session.get(self.download_url, headers=headers, stream=True, timeout=10)
            response.raise_for_status()
        except RequestException as e:
            print(f"Error downloading chunk {chunk_index}: {e}")
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

    def _combine_chunks(self):
        if os.path.exists(self.output_path):
            os.remove(self.output_path)
        os.rename(self.temp_file, self.output_path)
        if os.path.exists(self.progress_file):
            os.remove(self.progress_file)

    def download(self):
        try:
            resume = self._load_progress()
            if resume:
                print(f"Resuming download...")
                # If resuming, we need the download URL but don't need to select a new stream
                if not self.download_url:
                    self.select_stream()
            else:
                self.select_stream()
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
                if self.progress_callback:
                    self.progress_callback(progress)
                else:
                    print(f"\rProgress: {progress:.2f}%", end='')
                
                current_time = time.time()
                if current_time - last_save_time >= 1:
                    self._save_progress()
                    last_save_time = current_time

            for thread in threads:
                thread.join()

            if not self.stopped.is_set():
                self._combine_chunks()
                print("\nDownload completed successfully.")
            else:
                print("\nDownload paused. Resume later.")

        except Exception as e:
            self.stopped.set()
            self._save_progress()
            print(f"\nDownload failed: {e}")

def main():
    url = input("Enter YouTube URL: ")
    output_path = input("Enter output file path (e.g., video.mp4): ")
    format_type = input("Enter format (video/audio): ").lower()

    downloader = YoutubeDownloader(url, output_path, format_type=format_type)
    try:
        downloader.download()
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    main()
