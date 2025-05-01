# XcelerateDL Documentation

Welcome to the official documentation for XcelerateDL, a high-performance download manager with a FastAPI backend and intuitive user interface.

![XcelerateDL Logo](asset/image.png)

## Table of Contents

1. [Getting Started](getting-started.md)
2. [User Guide](user-guide.md)
3. [API Reference](api-reference.md)
4. [Architecture](architecture.md)
5. [Development Guide](development-guide.md)
6. [Troubleshooting](troubleshooting.md)

## Overview

XcelerateDL is a modern, high-performance download manager built with Python and FastAPI. It provides a powerful, asynchronous downloading engine capable of handling multiple concurrent downloads with intelligent bandwidth management. The application offers both a web interface and a desktop GUI mode, making it versatile for different use cases.

### Key Features

- **High-Performance Downloads**: Leverages asynchronous I/O and multi-threading for optimal download speeds
- **YouTube Integration**: Download videos or extract audio as MP3 using yt-dlp
- **Download Control**: Pause, resume, or cancel downloads at any time
- **Real-time Updates**: Monitor download progress via WebSockets
- **Automatic File Organization**: Auto-categorize downloads by file type (videos, music, documents, etc.)
- **Queue Management**: Easily manage download queue with priority settings
- **Persistent State**: Resume downloads after application restart
- **Dual Interface**: Use either the web UI or standalone desktop GUI
- **Bandwidth Management**: Intelligently allocate bandwidth across multiple downloads
- **Advanced Scheduling**: Schedule downloads with recurrence options and smart scheduling
- **Powerful Search**: Find downloads with advanced filtering options

## Quick Links

- [Installation Guide](getting-started.md#installation)
- [API Documentation](api-reference.md)
- [Bandwidth Management](user-guide.md#bandwidth-management)
- [Download Scheduling](user-guide.md#download-scheduling)
- [YouTube Downloads](user-guide.md#youtube-downloads)
- [Project Architecture](architecture.md)
