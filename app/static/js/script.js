// Global state
let selectedDownloads = new Set();
let currentFilter = {
    category: 'all',
    status: null
};
let downloads = [];
let websocket = null;
let reconnectAttempts = 0;
let reconnectTimeout = null;
const MAX_RECONNECT_ATTEMPTS = 5;
const RECONNECT_DELAY = 3000; // 3 seconds

// Utility functions for formatting
function formatSize(bytes) {
    if (bytes === null || bytes === undefined) return 'Unknown';
    if (bytes === 0) return '0 B';
    const units = ['B', 'KB', 'MB', 'GB', 'TB'];
    const i = Math.floor(Math.log(bytes) / Math.log(1024));
    return `${(bytes / Math.pow(1024, i)).toFixed(2)} ${units[i]}`;
}

function formatSpeed(bytesPerSecond) {
    if (bytesPerSecond === 0) return '0 B/s';
    return `${formatSize(bytesPerSecond)}/s`;
}

function formatTime(seconds) {
    if (seconds === null || seconds === undefined) return 'Unknown';
    if (seconds === 0) return '0s';

    const hours = Math.floor(seconds / 3600);
    const minutes = Math.floor((seconds % 3600) / 60);
    const secs = seconds % 60;

    if (hours > 0) {
        return `${hours}h ${minutes}m ${secs}s`;
    } else if (minutes > 0) {
        return `${minutes}m ${secs}s`;
    } else {
        return `${secs}s`;
    }
}

function formatDate(dateString) {
    const date = new Date(dateString);
    return date.toLocaleString();
}

// Show notifications
function showNotification(message, type = 'success') {
    // Create notification element if it doesn't exist
    let notification = document.querySelector('.notification');
    if (!notification) {
        notification = document.createElement('div');
        notification.className = 'notification';
        document.body.appendChild(notification);
    }

    // Set content and type
    notification.textContent = message;
    notification.className = `notification notification-${type}`;

    // Show notification
    notification.classList.add('show');

    // Hide after 3 seconds
    setTimeout(() => {
        notification.classList.remove('show');
    }, 3000);
}

// WebSocket Functions
function connectWebSocket() {
    if (websocket) {
        return; // Already connected
    }

    // Create WebSocket connection
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${protocol}//${window.location.host}/ws`;

    websocket = new WebSocket(wsUrl);

    websocket.onopen = function () {
        console.log('WebSocket connection established');
        reconnectAttempts = 0; // Reset reconnect attempts counter
        showNotification('Connected to real-time updates', 'info');

        // Check if there's a ping interval and clear it
        if (window.pingInterval) {
            clearInterval(window.pingInterval);
        }

        // Set up ping interval to keep connection alive
        window.pingInterval = setInterval(() => {
            if (websocket && websocket.readyState === WebSocket.OPEN) {
                websocket.send('ping');
            }
        }, 30000); // Send ping every 30 seconds
    };

    websocket.onmessage = function (event) {
        // Check if it's a ping response
        if (event.data === "pong") {
            console.log("Received pong");
            return;
        }

        // Parse the message as JSON
        try {
            const data = JSON.parse(event.data);
            handleWebSocketMessage(data);
        } catch (e) {
            console.error('Error parsing WebSocket message:', e);
        }
    };

    websocket.onclose = function () {
        console.log('WebSocket connection closed');
        websocket = null;

        // Clear ping interval
        if (window.pingInterval) {
            clearInterval(window.pingInterval);
            window.pingInterval = null;
        }

        // Attempt to reconnect
        if (reconnectAttempts < MAX_RECONNECT_ATTEMPTS) {
            reconnectAttempts++;
            console.log(`Attempting to reconnect (${reconnectAttempts}/${MAX_RECONNECT_ATTEMPTS})...`);

            // Clear any existing timeout
            if (reconnectTimeout) {
                clearTimeout(reconnectTimeout);
            }

            // Set timeout for reconnection
            reconnectTimeout = setTimeout(() => {
                connectWebSocket();
            }, RECONNECT_DELAY);
        } else {
            console.error('Maximum reconnection attempts reached. Please refresh the page.');
            showNotification('Connection lost. Please refresh the page.', 'error');
        }
    };

    websocket.onerror = function (error) {
        console.error('WebSocket error:', error);
    };
}

function handleWebSocketMessage(data) {
    switch (data.type) {
        case 'update':
            // Update a single download
            updateDownload(data.download);
            break;

        case 'new_download':
            // Add a new download
            addOrUpdateDownload(data.download);
            showNotification(`New download added: ${data.download.name}`, 'success');
            break;

        case 'delete_download':
            // Remove a download
            removeDownload(data.download_id);

            // Show appropriate notification based on the file deletion status
            if (data.download.file_error) {
                showNotification(`Download removed, but ${data.download.file_error}`, 'warning');
            } else if (data.download.file_deleted) {
                showNotification(`Download and file deleted: ${data.download.name}`, 'info');
            } else {
                showNotification(`Download removed: ${data.download.name}`, 'info');
            }
            break;

        case 'downloads_list':
            // Replace the entire downloads list
            downloads = data.downloads;
            renderDownloads();
            updateStats();
            break;

        default:
            console.log('Unknown message type:', data.type);
    }
}

function updateDownload(downloadData) {
    // Find the download in the current list
    const index = downloads.findIndex(d => d.id === downloadData.id);

    // If the download exists and matches our current filter, update it
    if (index !== -1) {
        downloads[index] = downloadData;

        // Update the UI for this specific download if it's rendered
        const downloadEl = document.querySelector(`.download-item[data-id="${downloadData.id}"]`);
        if (downloadEl) {
            updateDownloadElement(downloadEl, downloadData);
        } else {
            // If it's not rendered but should be based on current filters, re-render all
            renderDownloads();
        }

        // Update stats
        updateStats();
    } else {
        // If the download is not in our list but matches our filter, add it
        if (shouldShowDownload(downloadData)) {
            downloads.push(downloadData);
            renderDownloads();
            updateStats();
        }
    }
}

function addOrUpdateDownload(downloadData) {
    // Find if the download already exists
    const index = downloads.findIndex(d => d.id === downloadData.id);

    if (index !== -1) {
        // Update existing download
        downloads[index] = downloadData;
    } else {
        // Add new download
        downloads.push(downloadData);
    }

    // Only re-render if the download matches our current filter
    if (shouldShowDownload(downloadData)) {
        renderDownloads();
        updateStats();
    }
}

function removeDownload(downloadId) {
    // Find the download in the current list
    const index = downloads.findIndex(d => d.id === downloadId);

    if (index !== -1) {
        // Remove from downloads array
        downloads.splice(index, 1);

        // Remove from selected downloads
        selectedDownloads.delete(downloadId);

        // Update UI
        renderDownloads();
        updateStats();
    }
}

function shouldShowDownload(download) {
    // Check if the download matches the current category filter
    if (currentFilter.category && currentFilter.category !== 'all' &&
        download.category !== currentFilter.category) {
        return false;
    }

    // Check if the download matches the current status filter
    if (currentFilter.status) {
        // Handle comma-separated status values
        const statusValues = currentFilter.status.split(',');
        if (!statusValues.includes(download.status)) {
            return false;
        }
    }

    return true;
}

function updateDownloadElement(element, download) {
    // Update progress bar and percentage
    const progressBar = element.querySelector('.progress-fill');
    const progressPercentage = element.querySelector('.progress-percentage');
    if (progressBar && progressPercentage) {
        const progressPercent = download.progress !== undefined ? download.progress :
            (download.size ? (download.size_downloaded / download.size * 100) : 0);
        progressBar.style.width = `${progressPercent}%`;
        progressPercentage.textContent = `${Math.round(progressPercent)}%`;
    }

    // Update status
    const statusEl = element.querySelector('.download-status');
    if (statusEl) {
        statusEl.className = `download-cell download-status status-${download.status}`;
        statusEl.innerHTML = `<span class="status-indicator"></span>${download.status}`;
    }

    // Update speed
    const speedEl = element.querySelector('.download-speed');
    if (speedEl) {
        speedEl.textContent = formatSpeed(download.speed);
    }

    // Update time left
    const timeEl = element.querySelector('.download-time');
    if (timeEl) {
        timeEl.textContent = formatTime(download.time_left);
    }

    // Update class based on status
    element.className = `download-item status-${download.status}`;
    if (selectedDownloads.has(download.id)) {
        element.classList.add('selected');
    }
}

// DOM elements
const newDownloadBtn = document.querySelector('.new-download-btn');
const newDownloadModal = document.getElementById('new-download-modal');
const closeModalBtns = document.querySelectorAll('.close-modal');
const newDownloadForm = document.getElementById('new-download-form');
const downloadsContainer = document.getElementById('downloads-container');
const selectAllCheckbox = document.getElementById('select-all');
const categoryItems = document.querySelectorAll('.category-item');
const statusItems = document.querySelectorAll('[data-status]');
const actionButtons = document.querySelectorAll('.toolbar-button[data-action]');
const totalDownloadsEl = document.getElementById('total-downloads');
const activeDownloadsEl = document.getElementById('active-downloads');
const downloadSpeedEl = document.getElementById('download-speed');

// Event listeners
function openNewDownloadModal() {
    newDownloadModal.classList.add('active');
    setTimeout(() => {
        document.getElementById('download-url').focus();
    }, 100);
}

function closeNewDownloadModal() {
    newDownloadModal.classList.remove('active');
    newDownloadForm.reset();
}

newDownloadBtn.addEventListener('click', openNewDownloadModal);

closeModalBtns.forEach(btn => {
    btn.addEventListener('click', closeNewDownloadModal);
});

// Event delegation for the empty list "Add New Download" button
document.addEventListener('click', (event) => {
    if (event.target.closest('.start-downloading-btn')) {
        openNewDownloadModal();
    }
});

newDownloadForm.addEventListener('submit', async (event) => {
    event.preventDefault();
    const formData = new FormData(newDownloadForm);
    const requestData = {};

    for (const [key, value] of formData.entries()) {
        if (value) {
            requestData[key] = value;
        }
    }

    try {
        const loader = document.createElement('div');
        loader.className = 'form-loader';
        loader.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> Processing...';
        newDownloadForm.appendChild(loader);

        await addDownload(requestData);
        newDownloadForm.reset();
        closeNewDownloadModal();

        showNotification('Download added successfully!');
    } catch (error) {
        showNotification(`Failed to add download: ${error.message}`, 'error');
    } finally {
        const loader = document.querySelector('.form-loader');
        if (loader) loader.remove();
    }
});

selectAllCheckbox.addEventListener('change', () => {
    const checkboxes = document.querySelectorAll('.download-checkbox input');
    if (selectAllCheckbox.checked) {
        checkboxes.forEach(checkbox => {
            checkbox.checked = true;
            selectedDownloads.add(checkbox.dataset.id);
        });
    } else {
        checkboxes.forEach(checkbox => {
            checkbox.checked = false;
        });
        selectedDownloads.clear();
    }
    updateActionButtons();
});

categoryItems.forEach(item => {
    item.addEventListener('click', () => {
        categoryItems.forEach(i => i.classList.remove('active'));
        item.classList.add('active');
        currentFilter.category = item.dataset.category;

        // Don't fetch from API when using WebSockets, just re-render with filter
        renderDownloads();
        updateStats();
    });
});

// Status filtering
document.querySelectorAll('[data-status]').forEach(item => {
    item.addEventListener('click', () => {
        document.querySelectorAll('[data-status]').forEach(i => i.classList.remove('active'));
        item.classList.add('active');
        currentFilter.status = item.dataset.status;

        // Don't fetch from API when using WebSockets, just re-render with filter
        renderDownloads();
        updateStats();
    });
});

// Action buttons
actionButtons.forEach(button => {
    button.addEventListener('click', () => {
        const action = button.dataset.action;
        handleAction(action);
    });
});

// API functions
async function fetchDownloads() {
    try {
        let url = '/api/downloads';
        const params = new URLSearchParams();

        if (currentFilter.category && currentFilter.category !== 'all') {
            params.append('category', currentFilter.category);
        }

        if (currentFilter.status) {
            params.append('status', currentFilter.status);
        }

        if (params.toString()) {
            url += `?${params.toString()}`;
        }

        const response = await fetch(url);
        const data = await response.json();

        downloads = data.downloads;
        renderDownloads();
        updateStats();
    } catch (error) {
        console.error('Error fetching downloads:', error);
        showNotification('Failed to fetch downloads', 'error');
    }
}

async function addDownload(downloadData) {
    try {
        const response = await fetch('/api/downloads', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify(downloadData)
        });

        if (!response.ok) {
            throw new Error(`Server responded with ${response.status}`);
        }

        // We'll get the update via WebSocket now, no need to fetch again
    } catch (error) {
        console.error('Error adding download:', error);
        throw error;
    }
}

async function pauseDownload(id) {
    try {
        await fetch(`/api/downloads/${id}/pause`, {
            method: 'POST'
        });
        // Updates will come via WebSocket
    } catch (error) {
        console.error(`Error pausing download ${id}:`, error);
        showNotification('Failed to pause download', 'error');
    }
}

async function resumeDownload(id) {
    try {
        await fetch(`/api/downloads/${id}/resume`, {
            method: 'POST'
        });
        // Updates will come via WebSocket
    } catch (error) {
        console.error(`Error resuming download ${id}:`, error);
        showNotification('Failed to resume download', 'error');
    }
}

async function deleteDownload(id, deleteFile = false) {
    try {
        await fetch(`/api/downloads/${id}?delete_file=${deleteFile}`, {
            method: 'DELETE'
        });
        // The WebSocket will handle the UI update
    } catch (error) {
        console.error(`Error deleting download ${id}:`, error);
        showNotification('Failed to delete download', 'error');
    }
}

async function openDownloadedFile(id) {
    try {
        const response = await fetch(`/api/downloads/${id}/open`, {
            method: 'POST'
        });

        const result = await response.json();

        if (!response.ok) {
            throw new Error(result.detail || 'Failed to open file');
        }

        showNotification(result.message || 'Opening file...', 'info');
    } catch (error) {
        console.error(`Error opening download ${id}:`, error);
        showNotification(`Failed to open file: ${error.message}`, 'error');
    }
}

async function pauseAllDownloads() {
    try {
        await fetch('/api/downloads/pause-all', {
            method: 'POST'
        });
        // Updates will come via WebSocket
    } catch (error) {
        console.error('Error pausing all downloads:', error);
        showNotification('Failed to pause all downloads', 'error');
    }
}

async function resumeAllDownloads() {
    try {
        await fetch('/api/downloads/resume-all', {
            method: 'POST'
        });
        // Updates will come via WebSocket
    } catch (error) {
        console.error('Error resuming all downloads:', error);
        showNotification('Failed to resume all downloads', 'error');
    }
}

// Render functions
function renderDownloads() {
    // Filter downloads based on current filter
    const filteredDownloads = downloads.filter(download => shouldShowDownload(download));

    if (filteredDownloads.length === 0) {
        downloadsContainer.innerHTML = `
            <div class="empty-list-message">
                <i class="fa-solid fa-cloud-arrow-down fa-3x"></i>
                <p>Your download list is empty</p>
                <button class="btn-primary start-downloading-btn">
                    <i class="fa-solid fa-plus"></i> Add New Download
                </button>
            </div>
        `;
        return;
    }

    downloadsContainer.innerHTML = '';

    filteredDownloads.forEach(download => {
        const downloadEl = document.createElement('div');
        downloadEl.className = 'download-item';
        downloadEl.dataset.id = download.id;

        if (selectedDownloads.has(download.id)) {
            downloadEl.classList.add('selected');
        }

        // Add status class for styling
        downloadEl.classList.add(`status-${download.status}`);

        const progressPercent = download.progress !== undefined ? download.progress :
            (download.size ? (download.size_downloaded / download.size * 100) : 0);

        downloadEl.innerHTML = `
            <div class="download-checkbox">
                <input type="checkbox" data-id="${download.id}" ${selectedDownloads.has(download.id) ? 'checked' : ''}>
            </div>
            <div class="download-cell download-name">
                ${download.name}
                <div class="progress-container">
                    <div class="progress-bar">
                        <div class="progress-fill" style="width: ${progressPercent}%"></div>
                    </div>
                    <div class="progress-percentage">${Math.round(progressPercent)}%</div>
                </div>
            </div>
            <div class="download-cell download-size">${formatSize(download.size)}</div>
            <div class="download-cell download-status status-${download.status}">
                <span class="status-indicator"></span>
                ${download.status}
            </div>
            <div class="download-cell download-speed">${formatSpeed(download.speed)}</div>
            <div class="download-cell download-time">${formatTime(download.time_left)}</div>
            <div class="download-cell download-date">${formatDate(download.date_added)}</div>
        `;

        const checkbox = downloadEl.querySelector('input[type="checkbox"]');
        checkbox.addEventListener('change', () => {
            if (checkbox.checked) {
                selectedDownloads.add(download.id);
                downloadEl.classList.add('selected');
            } else {
                selectedDownloads.delete(download.id);
                downloadEl.classList.remove('selected');
            }
            updateActionButtons();
        });

        // Add action buttons for each download
        const actionsContainer = document.createElement('div');
        actionsContainer.className = 'download-actions';

        // Add appropriate action buttons based on status
        if (download.status === 'downloading') {
            actionsContainer.innerHTML = `
                <button class="action-btn pause-btn" data-id="${download.id}" title="Pause">
                    <i class="fa-solid fa-pause"></i>
                </button>
            `;
        } else if (download.status === 'paused') {
            actionsContainer.innerHTML = `
                <button class="action-btn resume-btn" data-id="${download.id}" title="Resume">
                    <i class="fa-solid fa-play"></i>
                </button>
            `;
        } else if (download.status === 'completed') {
            actionsContainer.innerHTML = `
                <button class="action-btn open-btn" data-id="${download.id}" title="Open file">
                    <i class="fa-solid fa-folder-open"></i>
                </button>
            `;
        }

        // Add delete button for all downloads
        actionsContainer.innerHTML += `
            <button class="action-btn delete-btn" data-id="${download.id}" title="Delete">
                <i class="fa-solid fa-trash"></i>
            </button>
        `;

        downloadEl.appendChild(actionsContainer);

        // Add double-click handler to open completed files
        if (download.status === 'completed') {
            downloadEl.addEventListener('dblclick', () => {
                openDownloadedFile(download.id);
            });
            downloadEl.style.cursor = 'pointer';
        }

        // Add event listeners for the action buttons
        const pauseBtn = downloadEl.querySelector('.pause-btn');
        if (pauseBtn) {
            pauseBtn.addEventListener('click', (e) => {
                e.stopPropagation();
                pauseDownload(download.id);
            });
        }

        const resumeBtn = downloadEl.querySelector('.resume-btn');
        if (resumeBtn) {
            resumeBtn.addEventListener('click', (e) => {
                e.stopPropagation();
                resumeDownload(download.id);
            });
        }

        const openBtn = downloadEl.querySelector('.open-btn');
        if (openBtn) {
            openBtn.addEventListener('click', (e) => {
                e.stopPropagation();
                openDownloadedFile(download.id);
            });
        }

        const deleteBtn = downloadEl.querySelector('.delete-btn');
        if (deleteBtn) {
            deleteBtn.addEventListener('click', (e) => {
                e.stopPropagation();
                if (confirm('Are you sure you want to delete this download?')) {
                    const deleteFile = confirm('Do you want to delete the downloaded file as well?');
                    deleteDownload(download.id, deleteFile);
                }
            });
        }

        downloadsContainer.appendChild(downloadEl);
    });
}

function updateStats() {
    // Filter downloads based on current filter for accurate stats
    const filteredDownloads = downloads.filter(download => shouldShowDownload(download));
    const totalDownloads = filteredDownloads.length;
    const activeDownloads = filteredDownloads.filter(d => d.status === 'downloading').length;
    const totalSpeed = filteredDownloads
        .filter(d => d.status === 'downloading')
        .reduce((sum, d) => sum + d.speed, 0);

    totalDownloadsEl.textContent = totalDownloads;
    activeDownloadsEl.textContent = activeDownloads;
    downloadSpeedEl.textContent = formatSpeed(totalSpeed);
}

function updateActionButtons() {
    const hasSelected = selectedDownloads.size > 0;
    const selectedItems = downloads.filter(d => selectedDownloads.has(d.id));
    const hasDownloading = selectedItems.some(d => d.status === 'downloading');
    const hasPaused = selectedItems.some(d => d.status === 'paused');

    document.querySelector('[data-action="resume"]').disabled = !hasPaused;
    document.querySelector('[data-action="pause"]').disabled = !hasDownloading;
    document.querySelector('[data-action="delete"]').disabled = !hasSelected;
}

// Handle actions
function handleAction(action) {
    switch (action) {
        case 'resume':
            if (selectedDownloads.size > 0) {
                selectedDownloads.forEach(id => resumeDownload(id));
            } else {
                resumeAllDownloads();
            }
            break;

        case 'pause':
            if (selectedDownloads.size > 0) {
                selectedDownloads.forEach(id => pauseDownload(id));
            } else {
                pauseAllDownloads();
            }
            break;

        case 'delete':
            if (selectedDownloads.size > 0) {
                if (confirm('Delete selected downloads?')) {
                    const deleteFile = confirm('Delete downloaded files as well?');
                    selectedDownloads.forEach(id => deleteDownload(id, deleteFile));
                }
            }
            break;

        case 'stop-all':
            pauseAllDownloads();
            break;

        case 'start-queue':
        case 'stop-queue':
        case 'queues':
        case 'settings':
            showNotification('This feature is coming soon!', 'info');
            break;
    }
}

// Add CSS for notification system
function addNotificationStyles() {
    const style = document.createElement('style');
    style.textContent = `
        :root {
            --primary-color: #6366f1;
            --primary-dark: #4f46e5;
            --primary-light: #818cf8;
            --primary-rgb: 99, 102, 241;
            
            --success-color: #22c55e;
            --success-rgb: 34, 197, 94;
            
            --warning-color: #f59e0b;
            --warning-rgb: 245, 158, 11;
            
            --error-color: #ef4444;
            --error-rgb: 239, 68, 68;
            
            --text-color: #f3f4f6;
            --text-dim: #9ca3af;
            --bg-color: #1e1e2e;
            --surface-color: #27293d;
        }
        
        .notification {
            position: fixed;
            bottom: -60px;
            left: 50%;
            transform: translateX(-50%);
            padding: 12px 24px;
            border-radius: 8px;
            color: white;
            font-weight: 500;
            box-shadow: 0 4px 12px rgba(0, 0, 0, 0.15);
            z-index: 1000;
            transition: bottom 0.3s ease;
            display: flex;
            align-items: center;
        }
        
        .notification.show {
            bottom: 20px;
        }
        
        .notification-success {
            background-color: var(--success-color);
        }
        
        .notification-error {
            background-color: var(--error-color);
        }
        
        .notification-warning {
            background-color: var(--warning-color);
        }
        
        .notification-info {
            background-color: var(--primary-color);
        }
        
        .form-loader {
            position: absolute;
            top: 0;
            left: 0;
            right: 0;
            bottom: 0;
            background: rgba(0, 0, 0, 0.7);
            display: flex;
            justify-content: center;
            align-items: center;
            border-radius: 0 0 12px 12px;
            font-size: 18px;
            color: white;
        }
        
        .form-loader i {
            margin-right: 10px;
        }
        
        .download-actions {
            display: flex;
            align-items: center;
            opacity: 0;
            transition: opacity 0.2s;
            margin-left: auto;
            padding-left: 10px;
        }
        
        .download-item:hover .download-actions {
            opacity: 1;
        }
        
        .action-btn {
            background: transparent;
            border: none;
            color: var(--text-color);
            font-size: 14px;
            width: 28px;
            height: 28px;
            border-radius: 4px;
            display: flex;
            align-items: center;
            justify-content: center;
            cursor: pointer;
            margin-left: 5px;
            transition: all 0.2s;
        }
        
        .action-btn:hover {
            background-color: rgba(255, 255, 255, 0.1);
        }
        
        .pause-btn:hover {
            color: var(--warning-color);
        }
        
        .resume-btn:hover {
            color: var(--success-color);
        }
        
        .open-btn:hover {
            color: var(--success-color);
        }
        
        .delete-btn:hover {
            color: var(--error-color);
        }
        
        .status-indicator {
            display: inline-block;
            width: 8px;
            height: 8px;
            border-radius: 50%;
            margin-right: 6px;
        }
        
        .status-downloading .status-indicator {
            background-color: var(--primary-light);
            box-shadow: 0 0 0 rgba(99, 102, 241, 0.4);
            animation: pulse 2s infinite;
        }
        
        .status-completed .status-indicator {
            background-color: var(--success-color);
        }
        
        .status-paused .status-indicator {
            background-color: var(--warning-color);
        }
        
        .status-failed .status-indicator {
            background-color: var(--error-color);
        }
        
        .status-queued .status-indicator {
            background-color: var(--text-dim);
        }
        
        .empty-list-message {
            display: flex;
            flex-direction: column;
            align-items: center;
            gap: 16px;
        }
        
        .empty-list-message i {
            color: var(--primary-light);
            opacity: 0.6;
        }
        
        .empty-list-message p {
            font-size: 18px;
            margin-bottom: 16px;
        }
        
        .progress-container {
            display: flex;
            align-items: center;
            width: 100%;
            gap: 8px;
        }
        
        .progress-bar {
            flex-grow: 1;
            height: 6px;
            background-color: rgba(255, 255, 255, 0.1);
            border-radius: 3px;
            overflow: hidden;
            position: relative;
        }
        
        .progress-fill {
            height: 100%;
            background-color: var(--primary-color);
            border-radius: 3px;
            transition: width 0.3s ease;
        }
        
        .progress-percentage {
            min-width: 45px;
            text-align: right;
            font-weight: 600;
            font-size: 14px;
            color: var(--primary-light);
        }
        
        .status-downloading .progress-percentage {
            animation: pulse-text 2s infinite;
        }
        
        @keyframes pulse-text {
            0% {
                opacity: 0.7;
            }
            50% {
                opacity: 1;
            }
            100% {
                opacity: 0.7;
            }
        }
        
        .status-completed .progress-percentage {
            color: var(--success-color);
        }
        
        .status-paused .progress-percentage {
            color: var(--warning-color);
        }
        
        .status-failed .progress-percentage {
            color: var(--error-color);
        }

        .download-item {
            display: flex;
            align-items: center;
            padding: 12px 16px;
            border-bottom: 1px solid rgba(255, 255, 255, 0.05);
            transition: background-color 0.2s;
        }

        .download-item:hover {
            background-color: rgba(255, 255, 255, 0.05);
        }

        .download-item.selected {
            background-color: rgba(var(--primary-rgb), 0.1);
        }

        .status-downloading .progress-fill {
            background: linear-gradient(90deg, 
                var(--primary-dark) 0%, 
                var(--primary-color) 50%, 
                var(--primary-light) 100%);
            background-size: 200% 100%;
            animation: gradient-shift 2s linear infinite;
        }

        @keyframes gradient-shift {
            0% {
                background-position: 100% 0;
            }
            100% {
                background-position: 0 0;
            }
        }

        .status-indicator {
            display: inline-block;
            width: 8px;
            height: 8px;
            border-radius: 50%;
            margin-right: 6px;
        }

        .status-downloading .status-indicator {
            background-color: var(--primary-light);
            box-shadow: 0 0 0 rgba(99, 102, 241, 0.4);
            animation: pulse 2s infinite;
        }

        @keyframes pulse {
            0% {
                box-shadow: 0 0 0 0 rgba(99, 102, 241, 0.7);
            }
            70% {
                box-shadow: 0 0 0 6px rgba(99, 102, 241, 0);
            }
            100% {
                box-shadow: 0 0 0 0 rgba(99, 102, 241, 0);
            }
        }
    `;
    document.head.appendChild(style);
}

// Initialize the app
document.addEventListener('DOMContentLoaded', () => {
    addNotificationStyles();
    connectWebSocket(); // Connect to WebSocket for real-time updates

    // Fallback to regular polling if WebSocket fails to connect after a few seconds
    setTimeout(() => {
        if (!websocket || websocket.readyState !== WebSocket.OPEN) {
            console.log('WebSocket not connected, falling back to polling');
            showNotification('Using standard updates mode', 'info');
            startAutoRefresh();
        }
    }, 5000);
});

// Auto-refresh downloads every 3 seconds (fallback if WebSocket is not available)
function startAutoRefresh() {
    fetchDownloads(); // Initial fetch
    setInterval(fetchDownloads, 1000);
} 