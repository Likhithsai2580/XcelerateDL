# XcelerateDL

A powerful and feature-rich download manager built with Python and PyQt5, featuring multi-threaded downloads and resume capability.

![Dark Mode](https://github.com/Likhithsai2580/XcelerateDL/blob/main/imgs/home-dark.png?raw=true)

![Light Mode](https://github.com/Likhithsai2580/XcelerateDL/blob/main/imgs/home-light.png?raw=true)

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
