# Getting Started with XcelerateDL

This guide will help you get up and running with XcelerateDL quickly.

## Requirements

Before installing XcelerateDL, ensure you have the following prerequisites:

- **Python 3.8+** (Python 3.13 recommended)
- **ffmpeg** (required for YouTube downloads)
- 50MB free disk space for the application (plus additional space for downloads)

## Installation

### Step 1: Clone the Repository

```bash
git clone https://github.com/Likhithsai2580/XcelerateDL.git
cd XcelerateDL
```

### Step 2: Set Up a Virtual Environment

It's recommended to use a virtual environment to avoid conflicts with other Python packages:

```bash
# Create a virtual environment
python -m venv venv

# Activate the virtual environment
# On Windows:
venv\Scripts\activate

# On macOS/Linux:
source venv/bin/activate
```

### Step 3: Install Dependencies

Install XcelerateDL and its dependencies:

```bash
pip install -e .
```

### Step 4: Install ffmpeg (for YouTube Downloads)

ffmpeg is required for YouTube download functionality:

#### Windows
1. Download ffmpeg from [ffmpeg.org](https://ffmpeg.org/download.html) or [gyan.dev](https://www.gyan.dev/ffmpeg/builds/)
2. Extract the files to a folder on your system
3. Add the bin folder to your PATH environment variable

#### macOS
```bash
brew install ffmpeg
```

#### Linux (Debian/Ubuntu)
```bash
sudo apt update
sudo apt install ffmpeg
```

#### Linux (CentOS/RHEL)
```bash
sudo yum install epel-release
sudo yum install ffmpeg
```

## Running XcelerateDL

XcelerateDL offers two operation modes:

### GUI Mode

For a complete desktop experience with a user-friendly interface:

```bash
python -m app.main --gui
```

### API/Web Mode

For headless operation or to use the web interface:

```bash
# Default configuration (host: 0.0.0.0, port: 8000)
python -m app.main

# Custom host and port
python -m app.main --api-only --host 127.0.0.1 --port 8080
```

After starting in API mode, access the web interface at `http://localhost:8000` (or your custom host/port).

## First Run

On first run, XcelerateDL will:

1. Create necessary directories for downloads and settings
2. Initialize with default configuration values
3. Prepare the download queue system

### Default Folders

- Downloaded files are saved to the `downloads` directory by default
- Download state is saved in `downloads/download_data.json`
- Bandwidth settings are saved in `downloads/bandwidth_settings.json`

## Verifying Installation

To verify your installation is working correctly:

1. Start XcelerateDL in either GUI or API mode
2. For GUI mode: The application window should open
3. For API mode: Navigate to `http://localhost:8000` in your browser
4. Try adding a small test download to confirm functionality

## Next Steps

Now that you have XcelerateDL up and running, you can:

- Learn how to use all the features in the [User Guide](user-guide.md)
- Explore the [API Reference](api-reference.md) for programmatic usage
- Understand the [application architecture](architecture.md)
- Customize XcelerateDL to your needs using the configuration options
