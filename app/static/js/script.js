// Global state
let downloads = {};
let selectedDownloads = new Set();
let isRefreshing = false;
let lastRefreshTime = 0;
const REFRESH_THROTTLE_MS = 1000; // Lowering from default to update more frequently
// Add current filter state
let currentFilter = {
    category: 'all',
    status: null
};
// Add notification settings (default values)
let notificationSettings = {
    notify_completion: true,
    notify_failure: true,
    notify_progress: false
};

// Load notification settings from localStorage
function loadNotificationSettings() {
    const savedSettings = localStorage.getItem('notificationSettings');
    if (savedSettings) {
        try {
            notificationSettings = JSON.parse(savedSettings);
            
            // Set form checkboxes if settings modal exists
            document.getElementById('settings-notify-completion').checked = notificationSettings.notify_completion;
            document.getElementById('settings-notify-failure').checked = notificationSettings.notify_failure;
            document.getElementById('settings-notify-progress').checked = notificationSettings.notify_progress;
        } catch (e) {
            console.error('Error loading notification settings:', e);
        }
    }
}

// Save notification settings to localStorage
function saveNotificationSettings() {
    localStorage.setItem('notificationSettings', JSON.stringify(notificationSettings));
}

// Wait for Eel to be ready
window.addEventListener('load', function() {
    // Show loading indicator
    showLoadingOverlay();
    
    // Initialize by fetching downloads immediately
    updateDownloads(true)
        .then(() => {
            // Hide loading indicator after initial load
            hideLoadingOverlay();
            
            // Set up periodic updates with a reasonable interval
            setInterval(() => updateDownloads(false), 2000);
            
            // Set up advanced settings collapsible sections
            setupCollapsibleSections();
            
            // Load notification settings
            loadNotificationSettings();
            
            // Setup context menu
            setupContextMenu();
            
            // Initialize notification permission
            initializeNotifications();
        })
        .catch(error => {
            console.error('Error during initial load:', error);
            hideLoadingOverlay();
            showNotification('Failed to load downloads. Check if the server is running.', 'error');
        });
});

// Function to initialize notifications and request permission if needed
function initializeNotifications() {
    if (!('Notification' in window)) {
        console.log('This browser does not support desktop notifications');
        return;
    }
    
    if (Notification.permission !== 'granted' && Notification.permission !== 'denied') {
        Notification.requestPermission();
    }
}

// Function to show a desktop notification
function showDesktopNotification(title, message, type = 'info') {
    // Check if browser supports notifications and permission is granted
    if (!('Notification' in window) || Notification.permission !== 'granted') {
        return;
    }
    
    // Create notification
    const notification = new Notification(title, {
        body: message,
        icon: type === 'success' ? '/static/images/success-icon.png' : 
              type === 'error' ? '/static/images/error-icon.png' : 
              '/static/images/notification-icon.png'
    });
    
    // Auto-close after 5 seconds
    setTimeout(() => notification.close(), 5000);
    
    // Add click handler to focus window
    notification.onclick = function() {
        window.focus();
        this.close();
    };
}

// Setup collapsible sections
function setupCollapsibleSections() {
    document.querySelectorAll('.collapsible-header').forEach(header => {
        header.addEventListener('click', function() {
            // Toggle active class
            this.classList.toggle('active');
            
            // Find the content section
            const content = this.nextElementSibling;
            if (content && content.classList.contains('collapsible-content')) {
                // Toggle display
                if (content.style.display === 'none' || !content.style.display) {
                    content.style.display = 'block';
                } else {
                    content.style.display = 'none';
                }
            }
        });
    });
}

// Setup context menu for downloads
function setupContextMenu() {
    // Add right-click event listener to download items
    document.addEventListener('click', function(e) {
        // Close any open context menus when clicking elsewhere
        const openMenus = document.querySelectorAll('.context-menu');
        openMenus.forEach(menu => menu.remove());
    });
    
    // Listen for right clicks on download items
    document.addEventListener('contextmenu', function(e) {
        // Check if clicked on a download item
        const downloadItem = e.target.closest('.download-item');
        if (downloadItem) {
            e.preventDefault();
            
            // Get download ID
            const downloadId = downloadItem.getAttribute('data-id');
            if (!downloadId) return;
            
            // Create context menu
            createDownloadContextMenu(downloadId, e.clientX, e.clientY);
        }
    });
}

// Create context menu for download items
function createDownloadContextMenu(downloadId, x, y) {
    // Remove any existing context menus
    const existingMenus = document.querySelectorAll('.context-menu');
    existingMenus.forEach(menu => menu.remove());
    
    // Get download data
    const download = downloads[downloadId];
    if (!download) return;
    
    // Create menu element
    const menu = document.createElement('div');
    menu.className = 'context-menu';
    
    // Add menu items based on download status
    let menuContent = '';
    
    // Common actions for all downloads
    menuContent += `
        <div class="context-menu-item" data-action="settings" data-id="${downloadId}">
            <i class="fa-solid fa-gear"></i> Settings
        </div>
    `;
    
    // Add status-specific actions
    if (download.status === 'downloading') {
        menuContent += `
            <div class="context-menu-item" data-action="pause" data-id="${downloadId}">
                <i class="fa-solid fa-pause"></i> Pause
            </div>
        `;
    } else if (download.status === 'paused' || download.status === 'queued' || download.status === 'failed') {
        menuContent += `
            <div class="context-menu-item" data-action="resume" data-id="${downloadId}">
                <i class="fa-solid fa-play"></i> Resume
            </div>
        `;
    }
    
    if (download.status === 'completed') {
        menuContent += `
            <div class="context-menu-item" data-action="open" data-id="${downloadId}">
                <i class="fa-solid fa-folder-open"></i> Open File
            </div>
        `;
    }
    
    // Add separator
    menuContent += `<div class="context-menu-separator"></div>`;
    
    // Delete options
    menuContent += `
        <div class="context-menu-item" data-action="delete" data-id="${downloadId}">
            <i class="fa-solid fa-trash"></i> Delete
        </div>
        <div class="context-menu-item" data-action="delete-file" data-id="${downloadId}">
            <i class="fa-solid fa-trash-alt"></i> Delete with File
        </div>
    `;
    
    // Set menu content
    menu.innerHTML = menuContent;
    
    // Add event listeners for menu items
    menu.querySelectorAll('.context-menu-item').forEach(item => {
        item.addEventListener('click', handleContextMenuAction);
    });
    
    // Position the menu
    menu.style.left = `${x}px`;
    menu.style.top = `${y}px`;
    
    // Add to document
    document.body.appendChild(menu);
    
    // Adjust position if menu is outside viewport
    const menuRect = menu.getBoundingClientRect();
    if (menuRect.right > window.innerWidth) {
        menu.style.left = `${window.innerWidth - menuRect.width - 10}px`;
    }
    if (menuRect.bottom > window.innerHeight) {
        menu.style.top = `${window.innerHeight - menuRect.height - 10}px`;
    }
}

// Handle context menu item clicks
async function handleContextMenuAction(e) {
    const action = this.getAttribute('data-action');
    const downloadId = this.getAttribute('data-id');
    
    // Close the menu
    const menu = this.closest('.context-menu');
    if (menu) menu.remove();
    
    // Handle different actions
    switch (action) {
        case 'settings':
            openDownloadSettings(downloadId);
            break;
        case 'pause':
            await eel.pause_download(downloadId)();
            await updateDownloads(true);
            break;
        case 'resume':
            await eel.resume_download(downloadId)();
            await updateDownloads(true);
            break;
        case 'open':
            try {
                await eel.open_download(downloadId)();
            } catch (error) {
                console.error('Error opening file:', error);
                showNotification('Failed to open file', 'error');
            }
            break;
        case 'delete':
            if (confirm('Are you sure you want to delete this download?')) {
                await eel.delete_download(downloadId, false)();
                await updateDownloads(true);
            }
            break;
        case 'delete-file':
            if (confirm('Are you sure you want to delete this download and its file?')) {
                await eel.delete_download(downloadId, true)();
                await updateDownloads(true);
            }
            break;
    }
}

// Open download settings modal
function openDownloadSettings(downloadId) {
    const download = downloads[downloadId];
    if (!download) return;
    
    // Populate the form
    document.getElementById('settings-download-id').value = downloadId;
    document.getElementById('settings-download-name').value = download.filename;
    document.getElementById('settings-priority').value = download.priority || 2;
    document.getElementById('settings-max-speed').value = download.max_speed || 0;
    document.getElementById('settings-max-retries').value = download.max_retries || 3;
    
    // Show the modal
    openModal('download-settings-modal');
    
    // Handle form submission
    const form = document.getElementById('download-settings-form');
    form.onsubmit = async function(e) {
        e.preventDefault();
        
        // Get form values
        const priority = parseInt(document.getElementById('settings-priority').value);
        const maxSpeed = parseInt(document.getElementById('settings-max-speed').value);
        const maxRetries = parseInt(document.getElementById('settings-max-retries').value);
        
        // Update notification settings
        notificationSettings.notify_completion = document.getElementById('settings-notify-completion').checked;
        notificationSettings.notify_failure = document.getElementById('settings-notify-failure').checked;
        notificationSettings.notify_progress = document.getElementById('settings-notify-progress').checked;
        
        // Save notification settings
        saveNotificationSettings();
        
        try {
            // Call API to update download settings
            await eel.update_download_settings(downloadId, priority, maxSpeed * 1024, maxRetries)();
            showNotification('Download settings updated', 'success');
            closeModal('download-settings-modal');
            await updateDownloads(true);
        } catch (error) {
            console.error('Error updating download settings:', error);
            showNotification('Failed to update download settings', 'error');
        }
    };
}

// Expose function to receive notifications from backend
eel.expose(receiveNotification);
function receiveNotification(notification) {
    console.log('Received notification:', notification);
    
    // Check notification type
    const notificationType = notification.notification_type || 'info';
    const title = notification.title || 'Notification';
    const message = notification.message || '';
    
    // Show in-app notification
    showNotification(message, notificationType);
    
    // Check if we should show desktop notification based on settings
    const isCompletionNotification = title.includes('completed');
    const isFailureNotification = title.includes('failed');
    const isProgressNotification = message.includes('50%');
    
    if ((isCompletionNotification && notificationSettings.notify_completion) ||
        (isFailureNotification && notificationSettings.notify_failure) ||
        (isProgressNotification && notificationSettings.notify_progress)) {
            
        // Show desktop notification
        showDesktopNotification(title, message, notificationType);
    }
}

// Add this to the existing add_download function
document.getElementById('new-download-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    
    // Show loading indicator
    showLoadingOverlay();
    
    // Get form data
    const formData = new FormData(e.target);
    const downloadData = {
        url: formData.get('url'),
        filename: formData.get('filename') || null,
        save_path: formData.get('save_path') || null,
        category: formData.get('category') || null
    };
    
    // Check if it's a YouTube URL
    const isYoutube = detectYouTubeUrl(downloadData.url);
    if (isYoutube) {
        downloadData.is_youtube = true;
        downloadData.youtube_type = formData.get('youtube_type') || 'video';
    }
    
    // Get advanced options
    downloadData.priority = parseInt(formData.get('priority') || '2');
    
    // Convert KB/s to B/s for max_speed
    const maxSpeedKB = parseInt(formData.get('max_speed') || '0');
    downloadData.max_speed = maxSpeedKB > 0 ? maxSpeedKB * 1024 : null;
    
    downloadData.max_retries = parseInt(formData.get('max_retries') || '3');
    
    try {
        // Add download
        const result = await eel.add_download(downloadData)();
        
        // Check for error
        if (result && result.error) {
            hideLoadingOverlay();
            showNotification(`Error: ${result.error}`, 'error');
            return;
        }
        
        // Success - close modal and refresh
        closeModal('new-download-modal');
        e.target.reset();
        showNotification(`Added download: ${result.filename || 'Unknown file'}`, 'success');
        await updateDownloads(true);
        hideLoadingOverlay();
    } catch (error) {
        console.error('Error adding download:', error);
        hideLoadingOverlay();
        showNotification(`Failed to add download: ${error.message || 'Unknown error'}`, 'error');
    }
});

// Expose the eel function for updating download settings
eel.expose(update_download_settings);
async function update_download_settings(downloadId, priority, maxSpeed, maxRetries) {
    try {
        const response = await fetch(`/api/downloads/${downloadId}/settings`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({
                priority: priority,
                max_speed: maxSpeed,
                max_retries: maxRetries
            })
        });
        
        if (!response.ok) {
            throw new Error(`Server returned ${response.status}: ${response.statusText}`);
        }
        
        return await response.json();
    } catch (error) {
        console.error('Error updating download settings:', error);
        throw error;
    }
}

// Add event handler for settings button in toolbar
function setupSettingsButton() {
    const settingsButton = document.querySelector('.toolbar-button[data-action="settings"]');
    if (settingsButton) {
        settingsButton.addEventListener('click', () => {
            // Check if a download is selected
            if (selectedDownloads.size === 1) {
                // Open settings for the selected download
                const downloadId = [...selectedDownloads][0];
                openDownloadSettings(downloadId);
            } else if (selectedDownloads.size > 1) {
                showNotification('Please select only one download to edit settings', 'warning');
            } else {
                showNotification('Please select a download to edit settings', 'warning');
            }
        });
    }
}

// Updated version of attachEventHandlers to include the settings button
function attachEventHandlers() {
    // Set up toolbar action buttons
    document.querySelectorAll('.toolbar-button[data-action]').forEach(button => {
        // Remove existing event listener first to prevent duplicates
        button.replaceWith(button.cloneNode(true));
        
        // Get the new element after cloning
        const newButton = document.querySelector(`.toolbar-button[data-action="${button.dataset.action}"]`);
        if (!newButton) return;
        
        // Add new event listener
        newButton.addEventListener('click', async (e) => {
            const action = e.currentTarget.dataset.action;
            
            try {
                switch (action) {
                    case 'resume':
                        await resumeSelectedDownloads();
                        break;
                    case 'pause':
                        await pauseSelectedDownloads();
                        break;
                    case 'delete':
                        await deleteSelectedDownloads();
                        break;
                    case 'start-queue':
                        await resumeAll();
                        break;
                    case 'stop-queue':
                    case 'stop-all':
                        await pauseAll();
                        break;
                    case 'settings':
                        if (selectedDownloads.size === 1) {
                            // Open settings for the selected download
                            const downloadId = [...selectedDownloads][0];
                            openDownloadSettings(downloadId);
                        } else if (selectedDownloads.size > 1) {
                            showNotification('Please select only one download to edit settings', 'warning');
                        } else {
                            showNotification('Please select a download to edit settings', 'warning');
                        }
                        break;
                }
                await updateDownloads(true);
            } catch (error) {
                console.error('Error performing action:', error);
                showNotification('Failed to perform action. Please try again.', 'error');
            }
        });
    });
    
    // Set up modal buttons
    document.querySelector('.new-download-btn')?.addEventListener('click', () => openModal('new-download-modal'));
    document.querySelector('.start-downloading-btn')?.addEventListener('click', () => openModal('new-download-modal'));
    document.querySelectorAll('.close-modal').forEach(button => {
        button.addEventListener('click', () => {
            const modal = button.closest('.modal');
            if (modal) closeModal(modal.id);
        });
    });
    
    // Set up select-all checkbox
    const selectAllCheckbox = document.getElementById('select-all');
    if (selectAllCheckbox) {
        selectAllCheckbox.addEventListener('change', handleSelectAll);
    }
}

// Show loading overlay
function showLoadingOverlay() {
    let overlay = document.querySelector('.loading-overlay');
    if (!overlay) {
        overlay = document.createElement('div');
        overlay.className = 'loading-overlay';
        overlay.innerHTML = `
            <div class="loading-spinner"></div>
            <div class="loading-text">Loading downloads...</div>
        `;
        document.body.appendChild(overlay);
    }
    overlay.style.display = 'flex';
}

// Hide loading overlay
function hideLoadingOverlay() {
    const overlay = document.querySelector('.loading-overlay');
    if (overlay) {
        overlay.style.display = 'none';
    }
}

// Update downloads list - with throttling to prevent UI freezing
async function updateDownloads(forceRefresh = false) {
    const now = Date.now();
    
    // If we're already refreshing or if it's too soon since the last refresh (unless forced)
    if (isRefreshing || (!forceRefresh && now - lastRefreshTime < REFRESH_THROTTLE_MS)) {
        return;
    }
    
    isRefreshing = true;
    lastRefreshTime = now;
    
    try {
        // Call Eel function and wait for response
        const newDownloads = await eel.get_downloads()();
        
        // Check if we got an error or empty response
        if (!newDownloads) {
            console.warn("Empty response from get_downloads");
            isRefreshing = false;
            return;
        }
        
        if (newDownloads.error) {
            console.warn("Error occurred:", newDownloads.error);
            isRefreshing = false;
            return;
        }
        
        // Check if downloads have changed
        const downloadsChanged = JSON.stringify(Object.keys(downloads).sort()) !== 
                               JSON.stringify(Object.keys(newDownloads).sort());
        
        // Check if any download properties have changed
        let propertiesChanged = downloadsChanged;
        if (!propertiesChanged) {
            for (const id of Object.keys(downloads)) {
                if (newDownloads[id] && (
                    downloads[id].status !== newDownloads[id].status ||
                    downloads[id].progress !== newDownloads[id].progress ||
                    downloads[id].speed !== newDownloads[id].speed
                )) {
                    propertiesChanged = true;
                    break;
                }
            }
        }
        
        // Keep track of downloads that are in progress locally but missing from the API response
        for (const id of Object.keys(downloads)) {
            // If a download is active (downloading/paused) but suddenly disappears from API, keep it in the UI with an error state
            if (!newDownloads[id] && ['downloading', 'paused', 'queued'].includes(downloads[id].status)) {
                console.warn(`Download ${id} disappeared while in ${downloads[id].status} state, preserving in UI as failed`);
                newDownloads[id] = {
                    ...downloads[id],
                    status: 'failed',
                    speed: 0,
                    time_left: 0,
                    error_message: 'Connection to download manager lost'
                };
                propertiesChanged = true;
                
                // Try to recover the download asynchronously if it was in downloading state
                if (downloads[id].status === 'downloading') {
                    console.log(`Attempting to recover download ${id}`);
                    setTimeout(async () => {
                        try {
                            // Try to get specific download info or resume it
                            const result = await eel.resume_download(id)();
                            if (result && !result.error) {
                                console.log(`Successfully recovered download ${id}`);
                                await updateDownloads(true);
                            }
                        } catch (e) {
                            console.error(`Failed to recover download ${id}:`, e);
                        }
                    }, 2000); // Wait 2 seconds before attempting recovery
                }
            }
        }
        
        // Update the global downloads object
        downloads = newDownloads;
        
        // Either do a full re-render or just update progress
        if (downloadsChanged || propertiesChanged) {
            renderDownloads();
        } else {
            updateDownloadProgress();
        }
    } catch (error) {
        console.error('Error fetching downloads:', error);
        // Don't clear the downloads object on error to maintain state
    } finally {
        isRefreshing = false;
    }
}

// Add a manual refresh button handler
function setupRefreshButton() {
    const refreshButton = document.getElementById('refresh-button');
    if (refreshButton) {
        refreshButton.addEventListener('click', async () => {
            showNotification('Refreshing downloads...', 'info');
            await updateDownloads(true);
            showNotification('Downloads refreshed', 'success');
        });
    }
}

// Initialize event listeners once DOM is loaded
document.addEventListener('DOMContentLoaded', () => {
    console.log('DOM fully loaded');
    
    // Load downloads when the page loads
    setupDownloadsRefresh();
    
    // Initialize sidebar menu
    initializeSidebarMenu();
    
    // Set up refresh button
    setupRefreshButton();
    
    // Attach all event handlers for buttons and checkboxes
    attachEventHandlers();
    
    // Set up YouTube URL detection
    const urlInput = document.getElementById('download-url');
    const youtubeOptions = document.querySelector('.youtube-options');
    const categorySelect = document.getElementById('download-category');
    
    urlInput?.addEventListener('input', function() {
        const isYouTubeUrl = detectYouTubeUrl(this.value);
        
        // Show/hide YouTube options
        if (youtubeOptions) {
            youtubeOptions.style.display = isYouTubeUrl ? 'block' : 'none';
        }
        
        // Auto-select YouTube category if it's a YouTube URL
        if (isYouTubeUrl && categorySelect) {
            categorySelect.value = 'youtube';
        }
    });
    
    // Category change handler
    categorySelect?.addEventListener('change', function() {
        if (youtubeOptions) {
            youtubeOptions.style.display = this.value === 'youtube' ? 'block' : 'none';
        }
    });
});

// Update progress bars without re-rendering the entire list
function updateDownloadProgress() {
    for (const [id, download] of Object.entries(downloads)) {
        const downloadItem = document.querySelector(`.download-item[data-id="${id}"]`);
        if (!downloadItem) continue;
        
        const progress = download.progress || 0;
        
        // Update progress bar with smoother transition
        const progressBar = downloadItem.querySelector('.progress-bar');
        if (progressBar) {
            // Only update progress if it's meaningful to avoid random fluctuations
            const currentWidth = parseFloat(progressBar.style.width) || 0;
            const difference = Math.abs(progress - currentWidth);
            
            // Only update if the change is significant (more than 0.5%) or for specific states
            if (difference > 0.5 || download.status === 'completed' || download.status === 'failed') {
                progressBar.style.width = `${progress}%`;
            }
            
            // Update progress bar class based on status
            progressBar.className = 'progress-bar';
            if (download.status === 'downloading') progressBar.classList.add('progress-downloading');
            else if (download.status === 'paused') progressBar.classList.add('progress-paused');
            else if (download.status === 'completed') progressBar.classList.add('progress-completed');
            else if (download.status === 'failed') progressBar.classList.add('progress-failed');
        }
        
        // Update speed with improved formatting
        const speedElement = downloadItem.querySelector('.item-speed');
        if (speedElement && download.speed !== undefined) {
            const speedFormatted = formatSpeed(download.speed);
            // Only update if the speed has changed to avoid flickering
            if (speedElement.innerHTML !== speedFormatted) {
                speedElement.innerHTML = speedFormatted;
            }
        }
        
        // Update time left
        const timeElement = downloadItem.querySelector('.item-time');
        if (timeElement) {
            timeElement.textContent = formatTimeLeft(download.time_left);
        }
        
        // Update status
        const statusElement = downloadItem.querySelector('.status-badge');
        if (statusElement) {
            statusElement.className = `status-badge ${download.status}`;
            statusElement.querySelector('.status-text').textContent = download.status;
        }
        
        // Update size downloaded
        const sizeElement = downloadItem.querySelector('.item-size');
        if (sizeElement) {
            const totalSize = formatSize(download.size);
            const downloadedSize = formatSize(download.downloaded || download.size_downloaded || 0);
            sizeElement.textContent = `${downloadedSize} / ${totalSize}`;
        }
    }
    
    // Update status bar
    updateStatusBar();
}

// Handle "Select All" checkbox
function handleSelectAll(e) {
    const isChecked = e.target.checked;
    const checkboxes = document.querySelectorAll('.download-item input[type="checkbox"]');
    
    checkboxes.forEach(checkbox => {
        checkbox.checked = isChecked;
        const downloadId = checkbox.closest('.download-item').dataset.id;
        
        if (isChecked) {
            selectedDownloads.add(downloadId);
    } else {
            selectedDownloads.delete(downloadId);
        }
    });
    
    // Update UI based on selection
    updateSelectionUI();
}

// Handle individual checkbox changes
function handleCheckboxChange(e) {
    const checkbox = e.target;
    const downloadId = checkbox.closest('.download-item').dataset.id;
    
    if (checkbox.checked) {
        selectedDownloads.add(downloadId);
    } else {
        selectedDownloads.delete(downloadId);
        // Uncheck "select all" if any item is unchecked
        document.getElementById('select-all').checked = false;
    }
    
    // Update UI based on selection
    updateSelectionUI();
}

// Update UI based on selection state
function updateSelectionUI() {
    const hasSelection = selectedDownloads.size > 0;
    
    // Enable/disable action buttons based on selection
    document.querySelectorAll('.toolbar-button[data-action]').forEach(button => {
        const action = button.dataset.action;
        if (['resume', 'pause', 'delete'].includes(action)) {
            button.disabled = !hasSelection;
            button.classList.toggle('disabled', !hasSelection);
        }
    });
}

// Render downloads in the UI
function renderDownloads() {
    const container = document.getElementById('downloads-container');
    
    // If container doesn't exist yet, exit early
    if (!container) {
        console.warn("Downloads container not found in DOM");
        return;
    }
    
    // Handle empty downloads list
    if (!downloads || Object.keys(downloads).length === 0) {
        container.innerHTML = `
            <div class="empty-list-message">
                <i class="fa-solid fa-cloud-arrow-down fa-3x"></i>
                <p>Your download list is empty</p>
                <button class="btn-primary start-downloading-btn">
                    <i class="fa-solid fa-plus"></i> Add New Download
                </button>
            </div>
        `;
        
        // Re-add event listeners
        attachEventHandlers();
        
        // Update select-all checkbox
        const selectAllCheckbox = document.getElementById('select-all');
        if (selectAllCheckbox) {
            selectAllCheckbox.checked = false;
            selectAllCheckbox.disabled = true;
        }
        
        // Clear selection and update UI
        selectedDownloads.clear();
        updateSelectionUI();
        return;
    }

    // Enable the select-all checkbox when we have downloads
const selectAllCheckbox = document.getElementById('select-all');
    if (selectAllCheckbox) {
        selectAllCheckbox.disabled = false;
    }

    let html = '';
    for (const [id, download] of Object.entries(downloads)) {
        const isChecked = selectedDownloads.has(id) ? 'checked' : '';
        const progress = download.progress || 0;
        
        // Colorize progress bar based on status
        let progressBarClass = '';
        if (download.status === 'downloading') progressBarClass = 'progress-downloading';
        else if (download.status === 'paused') progressBarClass = 'progress-paused';
        else if (download.status === 'completed') progressBarClass = 'progress-completed';
        else if (download.status === 'failed') progressBarClass = 'progress-failed';
        
        // Handle optional file extension icon
        const fileExtension = (download.filename || '').split('.').pop().toLowerCase();
        const categoryIcon = getCategoryIconByExtension(fileExtension) || getCategoryIcon(download.category);
        
        html += `
            <div class="download-item" data-id="${id}">
                <div class="item-checkbox">
                    <input type="checkbox" ${isChecked}>
                </div>
                <div class="item-cell item-name">
                    <i class="fa-solid ${categoryIcon}"></i>
                    ${download.filename || 'Unknown'}
                </div>
                <div class="item-cell item-size">${formatSize(download.downloaded)} / ${formatSize(download.size)}</div>
                <div class="item-cell item-status">
                    <span class="status-badge ${download.status}">
                        <span class="status-text">${download.status}</span>
                        <div class="progress-bar-container">
                            <div class="progress-bar ${progressBarClass}" style="width: ${progress}%"></div>
                        </div>
                    </span>
                </div>
                <div class="item-cell item-speed">${formatSpeed(download.speed)}</div>
                <div class="item-cell item-time">${formatTimeLeft(download.time_left)}</div>
                <div class="item-cell item-date">${formatDate(download.date_added)}</div>
            </div>
        `;
    }
    container.innerHTML = html;
    
    // Add event listeners to checkboxes
    document.querySelectorAll('.download-item input[type="checkbox"]').forEach(checkbox => {
        checkbox.addEventListener('change', handleCheckboxChange);
    });
    
    // Update the "select all" checkbox state
    if (selectAllCheckbox) {
        const allCheckboxes = document.querySelectorAll('.download-item input[type="checkbox"]');
        const allChecked = allCheckboxes.length > 0 && Array.from(allCheckboxes).every(cb => cb.checked);
        selectAllCheckbox.checked = allChecked;
    }
    
    // Re-attach all event handlers
    attachEventHandlers();
    
    // Update status bar and selection UI
    updateStatusBar();
    updateSelectionUI();
    
    // Apply current filters
    applyFilters();
}

// Function to detect YouTube URLs
function detectYouTubeUrl(url) {
    if (!url) return false;
    const youtubeRegex = /^(https?:\/\/)?(www\.)?(youtube\.com|youtu\.be)\/.*$/i;
    return youtubeRegex.test(url);
}

// Handle new download form submission
document.getElementById('new-download-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const formData = new FormData(e.target);
    
    // Check if it's a YouTube URL
    const url = formData.get('url');
    const isYouTube = detectYouTubeUrl(url);
    const category = formData.get('category');
    
    try {
        // Show processing message for YouTube downloads
        if (isYouTube || category === 'youtube') {
            showNotification('Processing YouTube URL, this may take a moment...', 'info');
        }
        
        // Prepare download request
        const downloadData = {
            url: url,
            filename: formData.get('filename') || null,
            save_path: formData.get('save_path') || null,
            category: category || null,
        };
        
        // Add YouTube-specific data if it's a YouTube URL
        if (isYouTube || category === 'youtube') {
            downloadData.is_youtube = true;
            downloadData.youtube_type = formData.get('youtube_type') || 'video';
        }
        
        // Call Eel function and wait for response
        const response = await eel.add_download(downloadData)();
        
        // Check for errors
        if (response.error) {
            console.error('Error adding download:', response.error);
            showNotification(`Failed to add download: ${response.error}`, 'error');
            return;
        }
        
        closeModal('new-download-modal');
        e.target.reset();
        
        // Show success notification
        if (isYouTube || category === 'youtube') {
            showNotification('YouTube download added successfully! Processing will begin shortly.', 'success');
        } else {
            showNotification('Download added successfully!', 'success');
        }
        
        await updateDownloads();
    } catch (error) {
        console.error('Error adding download:', error);
        showNotification(`Failed to add download: ${error.message || 'Unknown error'}`, 'error');
    }
});

// Update functions that handle API responses
async function resumeSelectedDownloads() {
    if (selectedDownloads.size === 0) {
        console.warn("No downloads selected to resume");
        showNotification("No downloads selected to resume", "warning");
        return;
    }
    
    let successCount = 0;
    let failedCount = 0;
    const errors = [];
    
    for (const id of selectedDownloads) {
        try {
            console.log(`Attempting to resume download ${id}`);
            const result = await eel.resume_download(id)();
            
            // Check for error responses
            if (result && result.error) {
                console.error(`Error resuming download ${id}:`, result.error);
                failedCount++;
                errors.push(`Failed to resume download ${id}: ${result.error}`);
            } else {
                successCount++;
                console.log(`Successfully resumed download ${id}`);
            }
    } catch (error) {
            console.error(`Error resuming download ${id}:`, error);
            failedCount++;
            errors.push(`Failed to resume download ${id}: ${error.message || "Unknown error"}`);
        }
    }
    
    // Show notifications based on results
    if (successCount > 0) {
        showNotification(`Resumed ${successCount} download(s)`, 'success');
    }
    
    if (failedCount > 0) {
        console.error(`Failed to resume ${failedCount} downloads:`, errors);
        showNotification(`Failed to resume ${failedCount} download(s). Check console for details.`, 'error');
    }
    
    // Refresh the downloads list
    await updateDownloads(true);
}

async function pauseSelectedDownloads() {
    if (selectedDownloads.size === 0) {
        console.warn("No downloads selected to pause");
        return;
    }

    let successCount = 0;
    for (const id of selectedDownloads) {
        try {
            const result = await eel.pause_download(id)();
            
            // Check for error responses
            if (result && result.error) {
                console.error(`Error pausing download ${id}:`, result.error);
            } else {
                successCount++;
                console.log(`Successfully paused download ${id}`);
            }
        } catch (error) {
            console.error(`Error pausing download ${id}:`, error);
        }
    }
    
    // Show a notification if successful
    if (successCount > 0) {
        showNotification(`Paused ${successCount} download(s)`, 'success');
    }
}

async function deleteSelectedDownloads() {
    if (selectedDownloads.size === 0) {
        console.warn("No downloads selected to delete");
        return;
    }
    
    if (confirm('Are you sure you want to delete the selected downloads?')) {
        let successCount = 0;
        const toDelete = [...selectedDownloads]; // Make a copy of the selected IDs
        
        for (const id of toDelete) {
            try {
                const result = await eel.delete_download(id, false)();
                if (result === true) {
                    successCount++;
                    selectedDownloads.delete(id); // Remove from selection
                    console.log(`Successfully deleted download ${id}`);
                } else {
                    console.error(`Failed to delete download ${id}`);
                }
            } catch (error) {
                console.error(`Error deleting download ${id}:`, error);
            }
        }
        
        // Show a notification if successful
        if (successCount > 0) {
            showNotification(`Deleted ${successCount} download(s)`, 'success');
            await updateDownloads(); // Refresh the downloads list
        }
    }
}

function getSelectedDownloads() {
    return Array.from(selectedDownloads);
}

// Utility functions
function getCategoryIcon(category) {
    const icons = {
        compressed: 'fa-file-zipper',
        programs: 'fa-microchip',
        videos: 'fa-film',
        music: 'fa-music',
        pictures: 'fa-image',
        documents: 'fa-file-lines',
        youtube: 'fa-youtube',
        other: 'fa-file'
    };
    return icons[category] || 'fa-file';
}

function formatSize(bytes) {
    if (!bytes) return '0 B';
    const units = ['B', 'KB', 'MB', 'GB', 'TB'];
    const i = Math.floor(Math.log(bytes) / Math.log(1024));
    return `${(bytes / Math.pow(1024, i)).toFixed(2)} ${units[i]}`;
}

function formatSpeed(bytesPerSec) {
    if (!bytesPerSec || bytesPerSec < 0 || bytesPerSec === undefined) return '0 B/s';
    
    // Format with appropriate units
    const units = ['B/s', 'KB/s', 'MB/s', 'GB/s'];
    const i = Math.floor(Math.log(Math.max(1, bytesPerSec)) / Math.log(1024));
    const value = (bytesPerSec / Math.pow(1024, i)).toFixed(2);
    
    // Add color based on speed
    let speedClass = 'speed-low';
    if (i >= 2) speedClass = 'speed-high'; // MB/s or higher
    else if (i >= 1) speedClass = 'speed-medium'; // KB/s
    
    return `<span class="${speedClass}">${value} ${units[Math.min(i, units.length - 1)]}</span>`;
}

function formatTimeLeft(seconds) {
    if (!seconds) return '--:--';
    const hours = Math.floor(seconds / 3600);
    const minutes = Math.floor((seconds % 3600) / 60);
    const secs = Math.floor(seconds % 60);
    if (hours > 0) {
        return `${hours}:${minutes.toString().padStart(2, '0')}:${secs.toString().padStart(2, '0')}`;
    }
    return `${minutes}:${secs.toString().padStart(2, '0')}`;
}

function formatDate(timestamp) {
    if (!timestamp) return '';
    return new Date(timestamp * 1000).toLocaleString();
}

function updateStatusBar() {
    const totalDownloads = Object.keys(downloads).length;
    const activeDownloads = Object.values(downloads).filter(d => d.status === 'downloading').length;
    
    // Calculate total speed with safety check
    const totalSpeed = Object.values(downloads)
        .filter(d => d.status === 'downloading' && typeof d.speed === 'number')
        .reduce((sum, d) => sum + (d.speed || 0), 0);

    // Update UI elements if they exist
    const totalElement = document.getElementById('total-downloads');
    if (totalElement) totalElement.textContent = totalDownloads;
    
    const activeElement = document.getElementById('active-downloads');
    if (activeElement) activeElement.textContent = activeDownloads;
    
    // Format speed for display in status bar
    const speedElement = document.getElementById('download-speed');
    if (speedElement) {
        // Get formatted speed and strip HTML tags for status bar display
        const formattedSpeedHtml = formatSpeed(totalSpeed);
        const plainTextSpeed = formattedSpeedHtml.replace(/<\/?span[^>]*>/g, '');
        speedElement.textContent = plainTextSpeed;
    }
}

// Modal handling
function openModal(modalId) {
    document.getElementById(modalId).style.display = 'flex';
}

function closeModal(modalId) {
    document.getElementById(modalId).style.display = 'none';
}

// Get category icon based on file extension
function getCategoryIconByExtension(ext) {
    if (!ext) return null;
    
    const iconMap = {
        // Compressed files
        'zip': 'fa-file-zipper',
        'rar': 'fa-file-zipper',
        '7z': 'fa-file-zipper',
        'tar': 'fa-file-zipper',
        'gz': 'fa-file-zipper',
        
        // Executable files
        'exe': 'fa-microchip',
        'msi': 'fa-microchip',
        'dmg': 'fa-microchip',
        'app': 'fa-microchip',
        
        // Documents
        'pdf': 'fa-file-pdf',
        'doc': 'fa-file-word',
        'docx': 'fa-file-word',
        'xls': 'fa-file-excel',
        'xlsx': 'fa-file-excel',
        'ppt': 'fa-file-powerpoint',
        'pptx': 'fa-file-powerpoint',
        'txt': 'fa-file-lines',
        
        // Media files
        'mp3': 'fa-file-audio',
        'wav': 'fa-file-audio',
        'ogg': 'fa-file-audio',
        'mp4': 'fa-file-video',
        'avi': 'fa-file-video',
        'mkv': 'fa-file-video',
        'mov': 'fa-file-video',
        'jpg': 'fa-file-image',
        'jpeg': 'fa-file-image',
        'png': 'fa-file-image',
        'gif': 'fa-file-image',
        'webp': 'fa-file-image',
        
        // Other common files
        'iso': 'fa-compact-disc',
        'torrent': 'fa-magnet'
    };
    
    return iconMap[ext.toLowerCase()] || null;
}

// Utility function for showing notifications
function showNotification(message, type = 'info') {
    // Create notification element if it doesn't exist
    let notificationContainer = document.querySelector('.notification-container');
    if (!notificationContainer) {
        notificationContainer = document.createElement('div');
        notificationContainer.className = 'notification-container';
        document.body.appendChild(notificationContainer);
    }
    
    // Create notification
    const notification = document.createElement('div');
    notification.className = `notification notification-${type}`;
    notification.innerHTML = `
        <span class="notification-message">${message}</span>
        <button class="notification-close">&times;</button>
    `;
    
    // Add to container
    notificationContainer.appendChild(notification);
    
    // Add close button functionality
    const closeBtn = notification.querySelector('.notification-close');
    closeBtn.addEventListener('click', () => {
        notification.classList.add('notification-hiding');
        setTimeout(() => notification.remove(), 300);
    });
    
    // Auto-remove after 3 seconds
    setTimeout(() => {
        if (notification.parentNode) {
            notification.classList.add('notification-hiding');
            setTimeout(() => notification.remove(), 300);
        }
    }, 3000);
}

// Function to initialize the sidebar menu
function initializeSidebarMenu() {
    // Status filters (All Downloads, Finished, In Progress)
    document.querySelectorAll('.category-item[data-status]').forEach(item => {
        item.addEventListener('click', function() {
            // Remove active class from all status items
            document.querySelectorAll('.category-item[data-status]').forEach(el => {
                el.classList.remove('active');
            });
            
            // Add active class to clicked item
            this.classList.add('active');
            
            // Update filter
            const status = this.getAttribute('data-status');
            currentFilter.status = status === 'all' ? null : status;
            
            // Apply filters and update UI
            applyFilters();
        });
    });
    
    // Category filters (compressed, programs, videos, etc.)
    document.querySelectorAll('.category-item[data-category]').forEach(item => {
        item.addEventListener('click', function() {
            // Remove active class from all category items
            document.querySelectorAll('.category-item[data-category]').forEach(el => {
                el.classList.remove('active');
            });
            
            // Add active class to clicked item
            this.classList.add('active');
            
            // Update filter
            currentFilter.category = this.getAttribute('data-category');
            
            // Apply filters and update UI
            applyFilters();
        });
    });
}

// Function to apply current filters to downloads
function applyFilters() {
    const downloadContainer = document.getElementById('downloads-container');
    const downloadItems = downloadContainer.querySelectorAll('.download-item');
    
    downloadItems.forEach(item => {
        const downloadId = item.getAttribute('data-id');
        const download = downloads[downloadId];
        let visible = true;
        
        // Apply category filter
        if (currentFilter.category && currentFilter.category !== 'all') {
            visible = download.category === currentFilter.category;
        }
        
        // Apply status filter
        if (visible && currentFilter.status) {
            if (currentFilter.status === 'completed') {
                visible = download.status === 'completed';
            } else if (currentFilter.status.includes(',')) {
                // Handle comma-separated status values (like "queued,downloading,paused")
                const statusArray = currentFilter.status.split(',');
                visible = statusArray.includes(download.status);
            } else {
                visible = download.status === currentFilter.status;
            }
        }
        
        // Show or hide item
        item.style.display = visible ? 'flex' : 'none';
    });
    
    // Check if there are any visible items
    const hasVisibleItems = Array.from(downloadItems).some(item => 
        item.style.display !== 'none'
    );
    
    // Show or hide empty message based on filtered results
    const emptyMessage = downloadContainer.querySelector('.empty-list-message');
    if (emptyMessage) {
        emptyMessage.style.display = hasVisibleItems ? 'none' : 'flex';
    }
}

// Set up periodic refresh with shorter interval
function setupDownloadsRefresh() {
    // Initial load
    updateDownloads(true);
    
    // Set up regular polling with a shorter interval (every 1 second)
    setInterval(() => updateDownloads(), 1000);
}

// Add helper functions for eel calls to resume and pause all downloads
async function resumeAll() {
    try {
        const result = await eel.resume_all()();
        if (result === true) {
            showNotification('All downloads resumed', 'success');
            await updateDownloads(true);
            return true;
        } else {
            showNotification('Failed to resume all downloads', 'warning');
            return false;
        }
    } catch (error) {
        console.error('Error resuming all downloads:', error);
        showNotification('Failed to resume all downloads', 'error');
        return false;
    }
}

async function pauseAll() {
    try {
        const result = await eel.pause_all()();
        if (result === true) {
            showNotification('All downloads paused', 'success');
            await updateDownloads(true);
            return true;
        } else {
            showNotification('Failed to pause all downloads', 'warning');
            return false;
        }
    } catch (error) {
        console.error('Error pausing all downloads:', error);
        showNotification('Failed to pause all downloads', 'error');
        return false;
    }
}

// Expose the eel function for opening downloads
eel.expose(open_download);
async function open_download(downloadId) {
    try {
        const response = await fetch(`/api/downloads/${downloadId}/open`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            }
        });
        
        if (!response.ok) {
            throw new Error(`Server returned ${response.status}: ${response.statusText}`);
        }
        
        const result = await response.json();
        if (result.success) {
            showNotification(`Opening file: ${result.message}`, 'success');
        } else {
            showNotification('Failed to open file', 'error');
        }
        return result;
    } catch (error) {
        console.error('Error opening file:', error);
        showNotification(`Error opening file: ${error.message}`, 'error');
        throw error;
    }
} 