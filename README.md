# XcelerateDL

A powerful and feature-rich download manager built with Python and PyQt5, featuring multi-threaded downloads and resume capability.

---
![Dark Mode](https://github.com/Likhithsai2580/XcelerateDL/blob/main/imgs/home-dark.png?raw=true)

---
![Light Mode](https://github.com/Likhithsai2580/XcelerateDL/blob/main/imgs/home-light.png?raw=true)

---

## Features

- Multi-threaded HTTP downloads with configurable thread count
- YouTube video downloads (Testing Phase)
  - Support for various quality options
  - Audio-only download capability
- Smart download resume system
  - Automatic progress saving
  - Chunk-based download recovery
- Modern GUI interface
  - Dark/Light theme switching
  - Progress tracking for each download
  - Real-time speed monitoring
- Download management
  - Pause/Resume functionality
  - Multiple simultaneous downloads
  - Queue management

## Requirements

### System Requirements
- Python 3.7 or higher
- 2GB RAM minimum
- Windows/Linux/MacOS compatible

### Python Dependencies
```bash
pip install PyQt5 requests pytubefix yt-dlp colorlog
```

## Installation

1. Clone the repository or download the source code
2. Install required dependencies:
```bash
pip install -r requirements.txt
```
3. Ensure all files are in the same directory:
   - gui.py
   - idm.py
   - style.qss
   - resources/ (folder with icons)

## Detailed Usage

### HTTP Downloads
1. Launch the application:
```bash
python gui.py
```
2. Switch to HTTP tab
3. Enter URL(s):
   - Paste URL in input field
   - Click "Add URL" for each link
   - Multiple URLs can be queued
4. Select download location
5. Click "Download All"
6. Monitor progress in real-time

### YouTube Downloads (Testing)
1. Switch to YouTube tab
2. Enter YouTube URL
3. Choose download options:
   - Video quality selection
   - Format selection
4. Select destination folder
5. Start download

### Resume Downloads
1. Go to Resume Downloads tab
2. View interrupted downloads
3. Select downloads to resume
4. Click "Resume Selected"

## Advanced Features

- **Multi-threading**: Configurable thread count for faster downloads
- **Chunk Management**: Smart download chunking for better reliability
- **Progress Recovery**: Saves download state for power/network failures
- **Theme Support**: Built-in dark/light theme switching
- **Resource Management**: Efficient memory and bandwidth usage

## Architecture

### Components
- `gui.py`: PyQt5-based GUI implementation
  - Download management interface
  - Progress monitoring
  - Theme management
- `idm.py`: Core download engine
  - HTTP download implementation
  - YouTube download integration
  - Resume system implementation

### Technical Details
- Uses PyQt5 for GUI rendering
- Implements multi-threading for parallel downloads
- Employs chunk-based downloading for better reliability
- Integrated YouTube download support via yt-dlp
- Progress tracking with JSON-based state management

## Known Issues

1. YouTube downloads are in testing phase
   - Some formats may not download correctly
   - Audio extraction might be unstable
2. HTTP downloads require range request support
3. Large files might require significant memory

## Contributing

1. Fork the repository
2. Create feature branch
3. Commit changes
4. Submit pull request

## License

MIT License - Feel free to use and modify as needed.

## Support

- Report issues on GitHub
- Check documentation for common problems
- Contact developers for major concerns

## Future Plans

- Enhanced YouTube support
- Improved download scheduling
- Bandwidth control features
- Browser integration
- Custom theme support

## Detailed Explanations and Examples for HTTP and YouTube Downloads

### HTTP Downloads

#### Example
1. Launch the application:
```bash
python gui.py
```
2. Switch to the HTTP tab.
3. Enter the URL(s) you want to download:
   - Paste the URL in the input field.
   - Click "Add URL" for each link.
   - Multiple URLs can be queued.
4. Select the download location.
5. Click "Download All".
6. Monitor the progress in real-time.

#### Detailed Explanation
- **Multi-threaded Downloads**: The application uses multiple threads to download different parts of the file simultaneously, which speeds up the download process.
- **Progress Tracking**: The progress of each download is tracked and displayed in real-time.
- **Pause/Resume Functionality**: Downloads can be paused and resumed at any time. The application saves the progress and resumes from where it left off.

### YouTube Downloads

#### Example
1. Switch to the YouTube tab.
2. Enter the YouTube URL.
3. Choose the download options:
   - Select the video quality.
   - Choose the format (video or audio).
4. Select the destination folder.
5. Start the download.

#### Detailed Explanation
- **Video Quality Selection**: Users can choose the quality of the video they want to download.
- **Format Selection**: Users can choose to download the video or just the audio.
- **Progress Tracking**: The progress of the download is tracked and displayed in real-time.
- **Pause/Resume Functionality**: Downloads can be paused and resumed at any time. The application saves the progress and resumes from where it left off.

## Detailed Installation Steps, Troubleshooting Tips, and System Requirements

### Installation Steps
1. Clone the repository or download the source code.
2. Install the required dependencies:
```bash
pip install -r requirements.txt
```
3. Ensure all files are in the same directory:
   - gui.py
   - idm.py
   - style.qss
   - resources/ (folder with icons)

### Troubleshooting Tips
- **Dependency Issues**: Ensure all dependencies are installed correctly. Use `pip list` to check installed packages.
- **Permission Issues**: Run the application with appropriate permissions. On Windows, try running as an administrator.
- **Network Issues**: Check your internet connection. Ensure that the URLs you are trying to download are accessible.

### System Requirements
- **Operating System**: Windows, Linux, or MacOS
- **Python Version**: Python 3.7 or higher
- **Memory**: Minimum 2GB RAM

## In-depth Technical Details and Diagrams

### Architecture

#### Components
- **gui.py**: PyQt5-based GUI implementation
  - Download management interface
  - Progress monitoring
  - Theme management
- **idm.py**: Core download engine
  - HTTP download implementation
  - YouTube download integration
  - Resume system implementation

#### Technical Details
- **GUI Rendering**: Uses PyQt5 for rendering the graphical user interface.
- **Multi-threading**: Implements multi-threading for parallel downloads.
- **Chunk-based Downloading**: Employs chunk-based downloading for better reliability.
- **YouTube Download Support**: Integrated YouTube download support via yt-dlp.
- **Progress Tracking**: Tracks progress with JSON-based state management.

### Diagrams
#### Architecture Diagram
```plaintext
+------------------+       +------------------+
|                  |       |                  |
|     gui.py       |<----->|     idm.py       |
|                  |       |                  |
+------------------+       +------------------+
```

## Guidelines for Contributing, Code of Conduct, and Instructions for Submitting Pull Requests

### Contributing Guidelines
1. Fork the repository.
2. Create a feature branch.
3. Commit your changes.
4. Submit a pull request.

### Code of Conduct
- **Respect**: Treat everyone with respect. Harassment and abuse are not tolerated.
- **Collaboration**: Work together to achieve common goals. Share knowledge and help others.
- **Integrity**: Be honest and transparent in your contributions.

### Instructions for Submitting Pull Requests
1. Ensure your code follows the project's coding standards.
2. Write clear and concise commit messages.
3. Provide a detailed description of your changes in the pull request.
4. Address any feedback or comments from reviewers.

## License

MIT License - Feel free to use and modify as needed.

## Support

- Report issues on GitHub.
- Check the documentation for common problems.
- Contact the developers for major concerns.

## Future Plans

- Enhanced YouTube support.
- Improved download scheduling.
- Bandwidth control features.
- Browser integration.
- Custom theme support.
