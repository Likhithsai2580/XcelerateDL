# XcelerateDL Troubleshooting Guide

This guide addresses common issues you might encounter while using XcelerateDL and provides solutions.

## Installation Issues

### Python Version Compatibility

**Issue**: Error message about incompatible Python version.

**Solution**:
- Ensure you're using Python 3.8 or higher
- Check your Python version with:
  ```bash
  python --version
  ```
- If needed, [download and install a compatible Python version](https://www.python.org/downloads/)

### Missing Dependencies

**Issue**: Error messages about missing modules during startup.

**Solution**:
- Reinstall all dependencies:
  ```bash
  pip install -e .
  ```
- Check for specific errors in the terminal output and install missing packages individually

### ffmpeg Not Found

**Issue**: YouTube downloads fail with errors about missing ffmpeg.

**Solution**:
- **Windows**: 
  1. Download ffmpeg from [ffmpeg.org](https://ffmpeg.org/download.html)
  2. Extract the files to a folder (e.g., `C:\ffmpeg`)
  3. Add the bin folder to your PATH environment variable
  4. Restart your terminal and application

- **macOS**:
  ```bash
  brew install ffmpeg
  ```

- **Linux**:
  ```bash
  sudo apt update
  sudo apt install ffmpeg  # For Debian/Ubuntu
  # OR
  sudo yum install ffmpeg  # For CentOS/RHEL
  ```

- Verify installation with:
  ```bash
  ffmpeg -version
  ```

## Startup Issues

### Port Already in Use

**Issue**: Error "Address already in use" when starting the server.

**Solution**:
- Change the port:
  ```bash
  python -m app.main --port 8080
  ```
- Alternatively, find and close the process using the default port (8000)
  - **Windows**: 
    ```
    netstat -ano | findstr :8000
    taskkill /PID <PID> /F
    ```
  - **macOS/Linux**: 
    ```
    lsof -i :8000
    kill -9 <PID>
    ```

### GUI Not Starting

**Issue**: GUI mode fails to start.

**Solution**:
- Check for any error messages in the terminal
- Ensure all dependencies are installed:
  ```bash
  pip install -e .
  ```
- Try running in API-only mode first to verify the backend works:
  ```bash
  python -m app.main --api-only
  ```
- Check if Chrome/Chromium is installed (required by Eel)

## Download Issues

### Downloads Not Starting

**Issue**: Downloads stay in "queued" status and never start.

**Solution**:
- Check your bandwidth settings, especially `max_concurrent_downloads`
- Verify you have internet connectivity
- Check if there are higher priority downloads consuming all slots
- Try pausing and resuming the download
- Restart the application

### Slow Download Speeds

**Issue**: Downloads are slower than expected.

**Solution**:
- Check your bandwidth settings in the application
- Verify your internet connection speed with an external speed test
- Try adjusting the bandwidth allocation mode
- Consider reducing the number of concurrent downloads
- Check if downloads are throttled due to peak hours settings

### Downloads Failing Consistently

**Issue**: Downloads immediately fail or fail after a short time.

**Solution**:
- Check if the download URL is valid and accessible
- Verify your internet connection
- Check available disk space
- Look for error messages in the terminal output
- Try increasing the `max_retries` setting
- For very large files, try enabling chunked downloading

### Downloads Stuck at Specific Percentage

**Issue**: Download progress stalls at a specific percentage.

**Solution**:
- Try pausing and resuming the download
- Check for internet connectivity issues
- Restart the application
- Delete the partial download and try again
- Check if the server supports resume functionality

## YouTube Download Issues

### YouTube Downloads Fail

**Issue**: YouTube downloads fail with errors.

**Solution**:
- Ensure ffmpeg is properly installed (see above)
- Check if the video is available (not private, deleted, or geo-restricted)
- Update yt-dlp to the latest version:
  ```bash
  pip install -U yt-dlp
  ```
- Try downloading audio only to see if that works
- Check terminal output for specific yt-dlp errors
- Try a different video to determine if it's a general or specific issue

### Audio Extraction Problems

**Issue**: Audio extraction from YouTube videos fails.

**Solution**:
- Verify ffmpeg is installed and working
- Try a different video format
- Check terminal output for ffmpeg-specific errors
- Ensure you have sufficient disk space
- Try downloading the complete video and then extract audio separately

### Video Quality Issues

**Issue**: YouTube videos download in lower quality than expected.

**Solution**:
- Check if higher quality versions are available (some videos have limited formats)
- Modify format selection preference in the configuration
- For specific videos, try using custom YouTube download options

## WebSocket Issues

### Real-time Updates Not Working

**Issue**: Download progress doesn't update in real time.

**Solution**:
- Check browser console for WebSocket errors
- Try refreshing the page
- Ensure no firewall or proxy is blocking WebSocket connections
- Try a different browser
- Restart the application

### WebSocket Disconnecting

**Issue**: Frequent "Connection lost" messages.

**Solution**:
- Check network stability
- Try reducing the number of WebSocket messages (e.g., by adjusting progress update frequency)
- Add reconnection logic to the client
- Check if the server is under heavy load

## File Organization Issues

### Files Downloaded to Wrong Location

**Issue**: Files are saved in unexpected locations.

**Solution**:
- Check the default download directory setting
- Verify category-based organization is working properly
- Check if custom save paths are being respected
- Ensure you have write permissions for the target directories
- Look for error messages in the application logs

### Incorrect File Categorization

**Issue**: Files are categorized incorrectly.

**Solution**:
- Check file extension against category mappings
- For YouTube downloads, ensure proper video/audio type selection
- Manually specify the category when adding downloads
- Consider adding custom category mappings

## Performance Issues

### High CPU Usage

**Issue**: Application uses excessive CPU resources.

**Solution**:
- Reduce the number of concurrent downloads
- Check for downloads stuck in a retry loop
- Consider lowering the bandwidth settings
- Close unnecessary browser tabs if using the web UI
- Restart the application

### High Memory Usage

**Issue**: Application consumes too much memory.

**Solution**:
- Limit the number of concurrent downloads
- For very large files, ensure chunked downloading is enabled
- Close completed downloads from the UI
- Restart the application periodically
- Consider using the API-only mode which typically uses less memory

### Application Becomes Unresponsive

**Issue**: UI freezes or application stops responding.

**Solution**:
- Check terminal output for errors or exceptions
- Reduce the number of active downloads
- Consider using API-only mode with a separate client
- Close and restart the application
- Check system resources (CPU, memory, disk space)

## Scheduled Download Issues

### Scheduled Downloads Not Starting

**Issue**: Downloads don't start at the scheduled time.

**Solution**:
- Verify system time is accurate
- Check scheduler settings, particularly `scheduler_check_interval`
- Ensure the application was running at the scheduled time
- Check for error messages in the logs
- Verify recurrence settings for recurring schedules
- Try rescheduling the download

### Peak Hours Settings Not Working

**Issue**: Downloads don't respect peak hours settings.

**Solution**:
- Verify peak hours settings are enabled
- Check the current time against peak hours range
- Ensure downloads have the `pause_on_peak_hours` option enabled
- Try adjusting peak hours times to ensure they're correctly set

### Recurring Downloads Not Working

**Issue**: Recurring downloads don't repeat as expected.

**Solution**:
- Check recurrence type (`daily`, `weekly`, `monthly`)
- For weekly downloads, verify `days_of_week` settings
- For monthly downloads, ensure `day_of_month` is valid
- Verify the application is running when the recurrence should trigger
- Check logs for scheduled task execution

## Advanced Troubleshooting

### Enabling Debug Logging

For more detailed troubleshooting, enable debug logging:

1. Create a file named `logging.conf` in the project root with:
```ini
[loggers]
keys=root

[handlers]
keys=consoleHandler,fileHandler

[formatters]
keys=simpleFormatter

[logger_root]
level=DEBUG
handlers=consoleHandler,fileHandler

[handler_consoleHandler]
class=StreamHandler
level=INFO
formatter=simpleFormatter
args=(sys.stdout,)

[handler_fileHandler]
class=FileHandler
level=DEBUG
formatter=simpleFormatter
args=('xceleratedl.log', 'w')

[formatter_simpleFormatter]
format=%(asctime)s - %(name)s - %(levelname)s - %(message)s
```

2. Start the application with logging configuration:
```bash
python -m app.main --log-config logging.conf
```

3. Check the generated log file for detailed information.

### Restoring Default Settings

If settings are causing issues, reset to defaults:

1. Stop the application
2. Delete or rename these files:
   - `downloads/download_data.json`
   - `downloads/bandwidth_settings.json`
3. Restart the application

### Cleaning Up Corrupted Downloads

If you have problematic partial downloads:

1. Stop the application
2. Run the cleanup script:
   ```bash
   # Windows
   delete_downloads.bat
   
   # macOS/Linux
   rm -rf downloads/*
   ```
3. Restart the application

### Diagnostic Commands

Use these commands to diagnose common issues:

```bash
# Check Python version
python --version

# Check installed packages
pip list

# Verify ffmpeg installation
ffmpeg -version

# Check disk space
# Windows
dir
# macOS/Linux
df -h

# Test internet connection
ping example.com

# Check port usage
# Windows
netstat -ano | findstr :8000
# macOS/Linux
lsof -i :8000
```

## Getting Help

If you've tried the solutions above and still have issues:

1. Check the [GitHub Issues](https://github.com/yourusername/XcelerateDL/issues) page for similar problems
2. Open a new issue with:
   - Detailed description of the problem
   - Steps to reproduce
   - System information (OS, Python version)
   - Relevant log output
   - Screenshots if applicable
