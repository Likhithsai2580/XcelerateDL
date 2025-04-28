// Global state
let downloads = {};
let selectedDownloads = new Set();
let isRefreshing = false;
let lastRefreshTime = 0;
const REFRESH_THROTTLE_MS = 500; // Lowering from default to update more frequently
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
            
            // Set up scheduling functionality
            setupSchedulingOptions();
            
            // Load notification settings
            loadNotificationSettings();
            
            // Setup context menu
            setupContextMenu();
            
            // Attach all event handlers
            attachEventHandlers();
            
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

// Setup scheduling functionality
function setupSchedulingOptions() {
    // Handle "Schedule for later" checkbox in the new download form
    const enableScheduleCheckbox = document.getElementById('enable-schedule');
    const scheduleOptions = document.querySelector('.schedule-options');
    
    if (enableScheduleCheckbox && scheduleOptions) {
        enableScheduleCheckbox.addEventListener('change', function() {
            scheduleOptions.style.display = this.checked ? 'block' : 'none';
            
            // Set default datetime to current time + 1 hour if checked
            if (this.checked) {
                const dateInput = document.getElementById('schedule-datetime');
                if (dateInput) {
                    const now = new Date();
                    now.setHours(now.getHours() + 1);
                    
                    // Format date in yyyy-MM-ddThh:mm format
                    const year = now.getFullYear();
                    const month = String(now.getMonth() + 1).padStart(2, '0');
                    const day = String(now.getDate()).padStart(2, '0');
                    const hours = String(now.getHours()).padStart(2, '0');
                    const minutes = String(now.getMinutes()).padStart(2, '0');
                    
                    dateInput.value = `${year}-${month}-${day}T${hours}:${minutes}`;
                }
                
                // Scroll schedule options into view with a slight delay to ensure display has updated
                setTimeout(() => {
                    scheduleOptions.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
                }, 100);
            }
        });
    }
    
    // Handle recurrence option changes in new download form
    const recurrenceSelect = document.getElementById('schedule-recurrence');
    const daysOfWeekDiv = document.querySelector('.days-of-week');
    const dayOfMonthDiv = document.querySelector('.day-of-month');
    
    if (recurrenceSelect) {
        recurrenceSelect.addEventListener('change', function() {
            // Only show days of week selection for weekly recurrence
            if (daysOfWeekDiv) {
                daysOfWeekDiv.style.display = this.value === 'weekly' ? 'block' : 'none';
            }
            
            // Only show day of month selection for monthly recurrence
            if (dayOfMonthDiv) {
                dayOfMonthDiv.style.display = this.value === 'monthly' ? 'block' : 'none';
            }
        });
    }
    
    // Handle recurrence option changes in schedule modal
    const modalRecurrenceSelect = document.getElementById('schedule-modal-recurrence');
    const modalDaysOfWeekDiv = document.getElementById('modal-days-of-week');
    const modalDayOfMonthDiv = document.getElementById('modal-day-of-month');
    
    if (modalRecurrenceSelect) {
        modalRecurrenceSelect.addEventListener('change', function() {
            // Only show days of week selection for weekly recurrence
            if (modalDaysOfWeekDiv) {
                modalDaysOfWeekDiv.style.display = this.value === 'weekly' ? 'block' : 'none';
            }
            
            // Only show day of month selection for monthly recurrence
            if (modalDayOfMonthDiv) {
                modalDayOfMonthDiv.style.display = this.value === 'monthly' ? 'block' : 'none';
            }
        });
    }
    
    // Setup schedule download form submission
    const scheduleForm = document.getElementById('schedule-download-form');
    if (scheduleForm) {
        scheduleForm.addEventListener('submit', async function(e) {
            e.preventDefault();
            
            const downloadId = document.getElementById('schedule-download-id').value;
            const scheduledTimeInput = document.getElementById('schedule-modal-datetime').value;
            const recurrence = document.getElementById('schedule-modal-recurrence').value;
            const bandwidthAllocation = parseInt(document.getElementById('schedule-modal-bandwidth').value);
            const retryOnFailure = document.getElementById('schedule-modal-retry').checked;
            const maxRetries = parseInt(document.getElementById('schedule-modal-max-retries').value);
            const retryDelay = parseInt(document.getElementById('schedule-modal-retry-delay').value);
            const notifyOnStart = document.getElementById('schedule-modal-notify').checked;
            const priorityBoost = document.getElementById('schedule-modal-priority-boost').checked;
            
            // Convert local datetime to ISO format for API
            let scheduledTime = null;
            if (scheduledTimeInput) {
                try {
                    // Create date object from input and convert to ISO string with timezone info
                    const dateObj = new Date(scheduledTimeInput);
                    if (!isNaN(dateObj.getTime())) {
                        // The toISOString() method converts to UTC time automatically
                        scheduledTime = dateObj.toISOString();
                        
                        // Enhanced timezone debugging
                        console.log('=== SCHEDULE MODAL TIMEZONE DEBUGGING ===');
                        console.log(`Original input: ${scheduledTimeInput}`);
                        console.log(`Local date object: ${dateObj.toString()}`);
                        console.log(`Converted to UTC ISO: ${scheduledTime}`);
                        console.log(`Local timezone offset: ${dateObj.getTimezoneOffset()} minutes`);
                        console.log(`Browser timezone: ${Intl.DateTimeFormat().resolvedOptions().timeZone}`);
                        console.log('======================================');
                    } else {
                        showNotification('Invalid date format. Please check the date and time.', 'error');
                        return;
                    }
                } catch (err) {
                    console.error('Date parsing error:', err);
                    showNotification('Invalid date format. Please check the date and time.', 'error');
                    return;
                }
            } else {
                showNotification('Please select a date and time.', 'error');
                return;
            }
            
            // Get selected days of week for weekly recurrence
            let daysOfWeek = null;
            if (recurrence === 'weekly') {
                daysOfWeek = [];
                document.querySelectorAll('#modal-days-of-week input[name="days_of_week"]:checked').forEach(checkbox => {
                    daysOfWeek.push(parseInt(checkbox.value));
                });
                
                // Validate at least one day is selected
                if (daysOfWeek.length === 0) {
                    showNotification('Please select at least one day of the week.', 'error');
                    return;
                }
            }
            
            // Get day of month for monthly recurrence
            let dayOfMonth = null;
            if (recurrence === 'monthly') {
                dayOfMonth = parseInt(document.getElementById('modal-day-of-month-input').value || '1');
                
                // Validate day of month
                if (isNaN(dayOfMonth) || dayOfMonth < 1 || dayOfMonth > 31) {
                    showNotification('Please enter a valid day of the month (1-31).', 'error');
                    return;
                }
            }
            
            // Create schedule data
            const scheduleData = {
                scheduled_time: scheduledTime,
                bandwidth_allocation: bandwidthAllocation,
                retry_on_failure: retryOnFailure,
                max_schedule_retries: maxRetries,
                retry_delay_minutes: retryDelay,
                notify_on_start: notifyOnStart,
                priority_boost: priorityBoost
            };
            
            // Add recurrence data if selected
            if (recurrence) {
                scheduleData.recurrence = recurrence;
                if (recurrence === 'weekly' && daysOfWeek) {
                    scheduleData.days_of_week = daysOfWeek;
                } else if (recurrence === 'monthly' && dayOfMonth) {
                    scheduleData.day_of_month = dayOfMonth;
                }
            }
            
            // Call API to schedule the download
            try {
                showLoadingOverlay(); // Show loading while API call is in progress
                const result = await eel.schedule_download(downloadId, scheduleData)();
                hideLoadingOverlay();
                
                if (result.error) {
                    showNotification(`Error: ${result.error}`, 'error');
                } else {
                    // Update the download in the UI
                    downloads[downloadId] = result;
                    renderDownloads();
                    showNotification('Download scheduled successfully!', 'success');
                    closeModal('schedule-download-modal');
                }
            } catch (error) {
                hideLoadingOverlay();
                console.error('Error scheduling download:', error);
                showNotification('Failed to schedule download. Please try again.', 'error');
            }
        });
    }
    
    // Update the new download form to handle scheduling
    const newDownloadForm = document.getElementById('new-download-form');
    if (newDownloadForm) {
        const originalSubmitHandler = newDownloadForm.onsubmit;
        newDownloadForm.onsubmit = async function(e) {
            e.preventDefault();
            
            // Get form data
            const formData = new FormData(newDownloadForm);
            const downloadData = {
                url: formData.get('url'),
                filename: formData.get('filename') || null,
                save_path: formData.get('save_path') || null,
                category: formData.get('category') || null,
                is_youtube: false,
                priority: parseInt(formData.get('priority') || '2'),
                max_speed: parseInt(formData.get('max_speed') || '0'),
                max_retries: parseInt(formData.get('max_retries') || '3')
            };
            
            // Handle YouTube options
            if (detectYouTubeUrl(downloadData.url)) {
                downloadData.is_youtube = true;
                downloadData.youtube_type = formData.get('youtube_type') || 'video';
            }
            
            // Handle scheduling if enabled
            if (formData.get('enable_schedule')) {
                const scheduledTimeInput = formData.get('scheduled_time');
                if (scheduledTimeInput) {
                    try {
                        // Create date object from input and convert to ISO string with timezone info
                        const dateObj = new Date(scheduledTimeInput);
                        if (!isNaN(dateObj.getTime())) {
                            downloadData.schedule = {
                                scheduled_time: dateObj.toISOString(), // Ensures UTC timezone
                                bandwidth_allocation: parseInt(formData.get('bandwidth_allocation') || '100')
                            };
                            
                            // Enhanced timezone debugging
                            console.log('=== NEW DOWNLOAD TIMEZONE DEBUGGING ===');
                            console.log(`Original input: ${scheduledTimeInput}`);
                            console.log(`Local date object: ${dateObj.toString()}`);
                            console.log(`Converted to UTC ISO: ${downloadData.schedule.scheduled_time}`);
                            console.log(`Local timezone offset: ${dateObj.getTimezoneOffset()} minutes`);
                            console.log(`Browser timezone: ${Intl.DateTimeFormat().resolvedOptions().timeZone}`);
                            console.log('======================================');
                            
                            const recurrence = formData.get('recurrence');
                            if (recurrence) {
                                downloadData.schedule.recurrence = recurrence;
                                
                                // Get days of week for weekly recurrence
                                if (recurrence === 'weekly') {
                                    const daysOfWeek = [];
                                    document.querySelectorAll('.days-checkboxes input[name="days_of_week"]:checked').forEach(checkbox => {
                                        daysOfWeek.push(parseInt(checkbox.value));
                                    });
                                    
                                    if (daysOfWeek.length === 0) {
                                        showNotification('Please select at least one day of the week.', 'error');
                                        return;
                                    }
                                    
                                    downloadData.schedule.days_of_week = daysOfWeek;
                                }
                                
                                // Get day of month for monthly recurrence
                                if (recurrence === 'monthly') {
                                    const dayOfMonth = parseInt(document.getElementById('day-of-month-input').value || '1');
                                    
                                    if (isNaN(dayOfMonth) || dayOfMonth < 1 || dayOfMonth > 31) {
                                        showNotification('Please enter a valid day of the month (1-31).', 'error');
                                        return;
                                    }
                                    
                                    downloadData.schedule.day_of_month = dayOfMonth;
                                }
                            }
                        } else {
                            showNotification('Invalid date format. Please check the date and time.', 'error');
                            return;
                        }
                    } catch (err) {
                        console.error('Date parsing error:', err);
                        showNotification('Invalid date format. Please check the date and time.', 'error');
                        return;
                    }
                } else {
                    showNotification('Please select a date and time for scheduling.', 'warning');
                    return;
                }
            }
            
            // Add the download
            try {
                const result = await eel.add_download(downloadData)();
                if (result.error) {
                    showNotification(`Error: ${result.error}`, 'error');
                } else {
                    // Add the new download to our list
                    downloads[result.id] = result;
                    renderDownloads();
                    showNotification('Download added successfully!', 'success');
                    closeModal('new-download-modal');
                    newDownloadForm.reset();
                }
            } catch (error) {
                console.error('Error adding download:', error);
                showNotification('Failed to add download.', 'error');
            }
        };
    }
}

// Open schedule download modal for a specific download
function openScheduleModal(downloadId) {
    const download = downloads[downloadId];
    if (!download) return;
    
    // Set download ID and name
    document.getElementById('schedule-download-id').value = downloadId;
    document.getElementById('schedule-download-name').value = download.filename;
    
    // Set default date time (current time + 1 hour)
    const defaultDate = new Date();
    defaultDate.setHours(defaultDate.getHours() + 1);
    
    // Format date in yyyy-MM-ddThh:mm format
    const year = defaultDate.getFullYear();
    const month = String(defaultDate.getMonth() + 1).padStart(2, '0');
    const day = String(defaultDate.getDate()).padStart(2, '0');
    const hours = String(defaultDate.getHours()).padStart(2, '0');
    const minutes = String(defaultDate.getMinutes()).padStart(2, '0');
    
    document.getElementById('schedule-modal-datetime').value = `${year}-${month}-${day}T${hours}:${minutes}`;
    
    // Reset form selections
    document.getElementById('schedule-modal-recurrence').value = '';
    document.getElementById('modal-days-of-week').style.display = 'none';
    
    // Hide day of month for monthly recurrence initially
    const dayOfMonthDiv = document.getElementById('modal-day-of-month');
    if (dayOfMonthDiv) {
        dayOfMonthDiv.style.display = 'none';
    }
    
    // Set default values for new options
    document.getElementById('schedule-modal-bandwidth').value = '100';
    document.getElementById('schedule-modal-retry').checked = true;
    document.getElementById('schedule-modal-max-retries').value = '3';
    document.getElementById('schedule-modal-retry-delay').value = '10';
    document.getElementById('schedule-modal-notify').checked = true;
    document.getElementById('schedule-modal-priority-boost').checked = false;
    
    // Set default day of month to current day
    document.getElementById('modal-day-of-month-input').value = String(defaultDate.getDate());
    
    // Uncheck all days
    document.querySelectorAll('#modal-days-of-week input[type="checkbox"]').forEach(checkbox => {
        checkbox.checked = false;
    });
    
    // If the download already has schedule settings, populate the form with them
    if (download.schedule) {
        try {
            // Set scheduled time if available
            if (download.schedule.scheduled_time) {
                const scheduledDate = new Date(download.schedule.scheduled_time);
                const scheduledYear = scheduledDate.getFullYear();
                const scheduledMonth = String(scheduledDate.getMonth() + 1).padStart(2, '0');
                const scheduledDay = String(scheduledDate.getDate()).padStart(2, '0');
                const scheduledHours = String(scheduledDate.getHours()).padStart(2, '0');
                const scheduledMinutes = String(scheduledDate.getMinutes()).padStart(2, '0');
                document.getElementById('schedule-modal-datetime').value = 
                    `${scheduledYear}-${scheduledMonth}-${scheduledDay}T${scheduledHours}:${scheduledMinutes}`;
            }
            
            // Set recurrence
            if (download.schedule.recurrence) {
                document.getElementById('schedule-modal-recurrence').value = download.schedule.recurrence;
                
                // Show days of week selector if weekly recurrence
                if (download.schedule.recurrence === 'weekly' && download.schedule.days_of_week) {
                    document.getElementById('modal-days-of-week').style.display = 'block';
                    
                    // Check the appropriate days
                    download.schedule.days_of_week.forEach(day => {
                        const checkbox = document.getElementById(`modal-day-${day}`);
                        if (checkbox) checkbox.checked = true;
                    });
                }
                
                // Show day of month selector if monthly recurrence
                if (download.schedule.recurrence === 'monthly') {
                    if (dayOfMonthDiv) {
                        dayOfMonthDiv.style.display = 'block';
                    }
                    
                    // Set day of month
                    if (download.schedule.day_of_month) {
                        document.getElementById('modal-day-of-month-input').value = download.schedule.day_of_month;
                    }
                }
            }
            
            // Set bandwidth allocation
            if (download.schedule.bandwidth_allocation) {
                document.getElementById('schedule-modal-bandwidth').value = download.schedule.bandwidth_allocation;
            }
            
            // Set retry settings
            if (download.schedule.retry_on_failure !== undefined) {
                document.getElementById('schedule-modal-retry').checked = download.schedule.retry_on_failure;
            }
            
            if (download.schedule.max_schedule_retries) {
                document.getElementById('schedule-modal-max-retries').value = download.schedule.max_schedule_retries;
            }
            
            if (download.schedule.retry_delay_minutes) {
                document.getElementById('schedule-modal-retry-delay').value = download.schedule.retry_delay_minutes;
            }
            
            // Set notification settings
            if (download.schedule.notify_on_start !== undefined) {
                document.getElementById('schedule-modal-notify').checked = download.schedule.notify_on_start;
            }
            
            // Set priority boost
            if (download.schedule.priority_boost !== undefined) {
                document.getElementById('schedule-modal-priority-boost').checked = download.schedule.priority_boost;
            }
            
        } catch (error) {
            console.error('Error populating schedule form:', error);
            // Continue with default values if error
        }
    }
    
    // Open the modal
    openModal('schedule-download-modal');
}

// Setup context menu for downloads
function setupContextMenu() {
    // Add right-click event listener to download items
    document.addEventListener('click', function(e) {
        // Close any open context menus when clicking elsewhere
        const openMenus = document.querySelectorAll('.context-menu, .empty-space-context-menu');
        openMenus.forEach(menu => menu.remove());
    });
    
    // Listen for right clicks on download items or empty space
    document.addEventListener('contextmenu', function(e) {
        // Check if clicked on a download item
        const downloadItem = e.target.closest('.download-item');
        const downloadList = e.target.closest('.download-list-body');
        
        if (downloadItem) {
            e.preventDefault();
            
            // Get download ID
            const downloadId = downloadItem.getAttribute('data-id');
            if (!downloadId) return;
            
            // Create context menu
            createDownloadContextMenu(downloadId, e.clientX, e.clientY);
        } 
        // Check if clicked on empty space in download list
        else if (downloadList) {
            e.preventDefault();
            
            // Create empty space context menu
            createEmptySpaceContextMenu(e.clientX, e.clientY);
        }
    });
}

// Create context menu for empty space
function createEmptySpaceContextMenu(x, y) {
    // Remove any existing context menus
    const existingMenus = document.querySelectorAll('.context-menu, .empty-space-context-menu');
    existingMenus.forEach(menu => menu.remove());
    
    // Create menu element
    const menu = document.createElement('div');
    menu.className = 'empty-space-context-menu';
    
    // Add menu items
    let menuContent = `
        <div class="context-menu-section">
            <div class="empty-space-menu-item" data-action="new-download">
                <i class="fa-solid fa-plus"></i> Add New Download
            </div>
            <div class="empty-space-menu-separator"></div>
            <div class="empty-space-menu-item" data-action="import-list">
                <i class="fa-solid fa-file-import"></i> Import Download List...
            </div>
            <div class="empty-space-menu-item" data-action="export-list">
                <i class="fa-solid fa-file-export"></i> Export Download List
            </div>
            <div class="empty-space-menu-separator"></div>
            <div class="empty-space-menu-item" data-action="select-all">
                <i class="fa-solid fa-check-double"></i> Select All
            </div>
            <div class="empty-space-menu-item" data-action="select-none">
                <i class="fa-solid fa-xmark"></i> Select None
            </div>
            <div class="empty-space-menu-separator"></div>
            <div class="empty-space-menu-item" data-action="settings">
                <i class="fa-solid fa-gear"></i> Settings
            </div>
            <div class="empty-space-menu-item" data-action="refresh">
                <i class="fa-solid fa-arrows-rotate"></i> Refresh
            </div>
        </div>
    `;
    
    // Set menu content
    menu.innerHTML = menuContent;
    
    // Add event listeners for menu items
    menu.querySelectorAll('.empty-space-menu-item').forEach(item => {
        item.addEventListener('click', handleEmptySpaceMenuAction);
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

// Function to handle file selection for imports
async function selectFileForImport() {
    try {
        const result = await eel.show_file_open_dialog(
            "Select file to import",
            [
                ['JSON Files', '*.json'],
                ['CSV Files', '*.csv'],
                ['Text Files', '*.txt'],
                ['All Files', '*.*']
            ]
        )();
        
        if (!result.success) {
            throw new Error(result.error || 'File selection canceled');
        }
        
        return result.file_path;
    } catch (error) {
        console.error('Error in file selection:', error);
        throw error;
    }
}

// Function to handle file selection for exports
async function selectFileForExport() {
    try {
        const result = await eel.show_file_save_dialog(
            "Save download list as",
            ".json",
            [
                ['JSON Files', '*.json'],
                ['All Files', '*.*']
            ],
            null,
            "download_list.json"
        )();
        
        if (!result.success) {
            throw new Error(result.error || 'File selection canceled');
        }
        
        return result.file_path;
    } catch (error) {
        console.error('Error in file selection:', error);
        throw error;
    }
}

// Handle empty space context menu item clicks
function handleEmptySpaceMenuAction(e) {
    const action = this.getAttribute('data-action');
    
    // Close the menu
    const menu = this.closest('.empty-space-context-menu');
    if (menu) menu.remove();
    
    // Handle different actions
    switch (action) {
        case 'new-download':
            openNewDownloadModal();
            break;
        case 'import-list':
            // Import download list
            (async () => {
                try {
                    showLoadingOverlay();
                    const filePath = await selectFileForImport();
                    const result = await eel.import_download_list(filePath)();
                    
                    if (result.error) {
                        showNotification(`Import failed: ${result.error}`, 'error');
                    } else {
                        showNotification(result.message, 'success');
                        // Refresh download list
                        await updateDownloads(true);
                    }
                } catch (error) {
                    console.error('Error importing download list:', error);
                    if (error !== 'File selection canceled') {
                        showNotification('Failed to import download list.', 'error');
                    }
                } finally {
                    hideLoadingOverlay();
                }
            })();
            break;
        case 'export-list':
            // Export download list
            (async () => {
                try {
                    showLoadingOverlay();
                    const filePath = await selectFileForExport();
                    const result = await eel.export_download_list(filePath)();
                    
                    if (result.error) {
                        showNotification(`Export failed: ${result.error}`, 'error');
                    } else {
                        showNotification(result.message, 'success');
                    }
                } catch (error) {
                    console.error('Error exporting download list:', error);
                    if (error.message !== 'Export canceled') {
                        showNotification('Failed to export download list.', 'error');
                    }
                } finally {
                    hideLoadingOverlay();
                }
            })();
            break;
        case 'settings':
            // Open settings dialog
            openSettingsModal();
            break;
        case 'refresh':
            // Refresh download list
            updateDownloads(true);
            break;
        case 'select-all':
            handleSelectAll(e);
            break;
        case 'select-none':
            handleSelectNone(e);
            break;
    }
}

// Create context menu for download items
function createDownloadContextMenu(downloadId, x, y) {
    // Remove any existing context menus
    const existingMenus = document.querySelectorAll('.context-menu, .empty-space-context-menu');
    existingMenus.forEach(menu => menu.remove());
    
    // Get download data
    const download = downloads[downloadId];
    if (!download) return;
    
    // Create menu element
    const menu = document.createElement('div');
    menu.className = 'context-menu';
    
    // Add menu items based on download status
    let menuContent = '<div class="context-menu-section">';
    
    // Status-specific primary actions
    if (download.status === 'downloading') {
        menuContent += `
            <div class="context-menu-item primary-action" data-action="pause" data-id="${downloadId}">
                <i class="fa-solid fa-pause"></i> Pause
            </div>
        `;
    } else if (download.status === 'paused' || download.status === 'queued' || download.status === 'failed') {
        menuContent += `
            <div class="context-menu-item primary-action" data-action="resume" data-id="${downloadId}">
                <i class="fa-solid fa-play"></i> Resume
            </div>
        `;
    } else if (download.status === 'completed') {
        menuContent += `
            <div class="context-menu-item primary-action" data-action="open" data-id="${downloadId}">
                <i class="fa-solid fa-folder-open"></i> Open File
            </div>
        `;
    }
    
    menuContent += `<div class="empty-space-menu-separator"></div>`;
    
    // Common actions for all downloads
    menuContent += `
        <div class="context-menu-item" data-action="settings" data-id="${downloadId}">
            <i class="fa-solid fa-gear"></i> Settings
        </div>
    `;
    
    // Additional status-specific actions
    if (download.status === 'paused' || download.status === 'queued' || download.status === 'failed') {
        menuContent += `
            <div class="context-menu-item" data-action="schedule" data-id="${downloadId}">
                <i class="fa-solid fa-calendar"></i> Schedule
            </div>
        `;
    }
    
    // Copy URL
    menuContent += `
        <div class="context-menu-item" data-action="copy-url" data-id="${downloadId}">
            <i class="fa-solid fa-copy"></i> Copy URL
        </div>
    `;
    
    // Add file location (for completed downloads)
    if (download.status === 'completed') {
        menuContent += `
            <div class="context-menu-item" data-action="open-location" data-id="${downloadId}">
                <i class="fa-solid fa-folder"></i> Open File Location
            </div>
        `;
    }
    
    // Add separator
    menuContent += `<div class="context-menu-separator"></div>`;
    
    // Delete options
    menuContent += `
        <div class="context-menu-item danger-action" data-action="delete" data-id="${downloadId}">
            <i class="fa-solid fa-trash"></i> Delete
        </div>
        <div class="context-menu-item danger-action" data-action="delete-file" data-id="${downloadId}">
            <i class="fa-solid fa-trash-alt"></i> Delete with File
        </div>
    `;
    
    menuContent += '</div>'; // Close section
    
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

// Show custom delete confirmation dialog
function showDeleteConfirmation(downloadId, withFile = false) {
    // Get download info to display filename
    const download = downloads[downloadId];
    if (!download) return;

    // Create dialog elements
    const dialog = document.createElement('div');
    dialog.className = 'delete-dialog';
    
    const dialogContent = `
        <div class="delete-dialog-content">
            <div class="delete-dialog-header">
                <i class="fa-solid fa-triangle-exclamation"></i>
                <h3>Confirm Delete</h3>
            </div>
            <div class="delete-dialog-body">
                <p><strong>${withFile ? 
                    'Are you sure you want to delete this download AND the associated file?' : 
                    'Are you sure you want to delete this download?'}</strong></p>
                <p>File: <span class="filename">${download.filename}</span></p>
                <p class="text-dim">${withFile ? 
                    'This action will permanently remove the downloaded file from your system.' : 
                    'The downloaded file will remain on your system.'}</p>
            </div>
            <div class="delete-dialog-footer">
                <button class="delete-dialog-btn delete-dialog-btn-cancel">
                    <i class="fa-solid fa-times"></i>Cancel
                </button>
                <button class="delete-dialog-btn delete-dialog-btn-delete">
                    <i class="fa-solid fa-trash"></i>Delete
                </button>
            </div>
        </div>
    `;
    
    dialog.innerHTML = dialogContent;
    document.body.appendChild(dialog);
    
    // Add event listeners
    const cancelBtn = dialog.querySelector('.delete-dialog-btn-cancel');
    const deleteBtn = dialog.querySelector('.delete-dialog-btn-delete');
    
    // Add click outside to close
    dialog.addEventListener('click', (e) => {
        if (e.target === dialog) {
            dialog.remove();
        }
    });
    
    // Add escape key to close
    document.addEventListener('keydown', function escapeHandler(e) {
        if (e.key === 'Escape') {
            dialog.remove();
            document.removeEventListener('keydown', escapeHandler);
        }
    });
    
    cancelBtn.addEventListener('click', () => {
        dialog.remove();
    });
    
    deleteBtn.addEventListener('click', async () => {
        // Show loading state
        deleteBtn.disabled = true;
        deleteBtn.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i>Deleting...';
        
        try {
            const success = await eel.delete_download(downloadId, withFile)();
            if (success) {
                delete downloads[downloadId];
                renderDownloads();
                showNotification(`Download ${withFile ? 'and file ' : ''}deleted successfully.`, 'success');
            } else {
                showNotification(`Failed to delete download${withFile ? ' and file' : ''}.`, 'error');
            }
        } catch (error) {
            console.error(`Error deleting download${withFile ? ' and file' : ''}:`, error);
            showNotification(`Failed to delete download${withFile ? ' and file' : ''}.`, 'error');
        } finally {
            dialog.remove();
        }
    });
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
        await updateDownloads();
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

// Setup toolbar action buttons
function setupToolbarButtons() {
    // Get all toolbar buttons with data-action attribute
    const actionButtons = document.querySelectorAll('.toolbar-button[data-action]');
    
    // Add event listeners to each button
    actionButtons.forEach(button => {
        const action = button.dataset.action;
        
        // Remove any existing listeners to prevent duplicates
        button.removeEventListener('click', handleToolbarAction);
        
        // Add the event listener
        button.addEventListener('click', handleToolbarAction);
    });
}

// Handle toolbar button clicks
async function handleToolbarAction(e) {
    const action = this.dataset.action;
    
    // Skip if button is disabled
    if (this.disabled || this.classList.contains('disabled')) {
        console.log(`Button ${action} is disabled`);
        return;
    }
    
    console.log(`Toolbar action: ${action}`);
    
    // Process different actions
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
            
        case 'stop-all':
            await pauseAll();
            break;
            
        case 'start-queue':
            // TODO: Implement start queue functionality
            showNotification('Starting download queue', 'info');
            break;
            
        case 'stop-queue':
            // TODO: Implement stop queue functionality
            showNotification('Stopping download queue', 'info');
            break;
            
        default:
            console.log(`Unhandled action: ${action}`);
    }
    
    // Refresh the downloads list after action
    await updateDownloads(true);
}

// Updated version of attachEventHandlers to include the settings button
function attachEventHandlers() {
    // Set up modal buttons
    document.querySelector('.new-download-btn')?.addEventListener('click', () => openModal('new-download-modal'));
    document.querySelector('.start-downloading-btn')?.addEventListener('click', () => openModal('new-download-modal'));
    
    // Set up select-all checkbox
    const selectAllCheckbox = document.getElementById('select-all');
    if (selectAllCheckbox) {
        selectAllCheckbox.addEventListener('change', handleSelectAll);
    }

    // Get all close modal buttons
    document.querySelectorAll('.close-modal').forEach(button => {
        button.addEventListener('click', function() {
            // Find the closest modal parent and get its ID
            const modal = this.closest('.modal');
            if (modal) {
                closeModal(modal.id);
            }
        });
    });

    // Close modals when clicking outside the modal content
    document.querySelectorAll('.modal').forEach(modal => {
        modal.addEventListener('click', function(e) {
            // Only close if clicking directly on the modal background, not on the content
            if (e.target === this) {
                closeModal(this.id);
            }
        });
    });
    
    // Set up toolbar buttons
    setupToolbarButtons();
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

// Utility function to normalize URLs for comparison
function normalizeUrl(url) {
    if (!url) return '';
    
    try {
        // Create URL object
        const urlObj = new URL(url);
        
        // Remove 'www.' from hostname if present
        let hostname = urlObj.hostname;
        if (hostname.startsWith('www.')) {
            hostname = hostname.substring(4);
        }
        
        // Normalize path - ensure trailing slash consistency
        let path = urlObj.pathname;
        if (path === '') {
            path = '/';
        } else if (path.endsWith('/') && path !== '/') {
            path = path.slice(0, -1);
        }
        
        // Get query parameters and sort them
        const queryParams = {};
        for (const [key, value] of urlObj.searchParams.entries()) {
            if (!queryParams[key]) {
                queryParams[key] = [];
            }
            queryParams[key].push(value);
        }
        
        // Build normalized query string
        const queryKeys = Object.keys(queryParams).sort();
        const queryParts = [];
        for (const key of queryKeys) {
            for (const value of queryParams[key].sort()) {
                queryParts.push(`${encodeURIComponent(key)}=${encodeURIComponent(value)}`);
            }
        }
        const normalizedQuery = queryParts.join('&');
        
        // Rebuild URL without protocol (scheme)
        return `//${hostname}${path}${normalizedQuery ? '?' + normalizedQuery : ''}`;
    } catch (e) {
        console.error('Error normalizing URL:', e);
        return url.trim();
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
            throw new Error("Empty response from get_downloads");
        }
        
        if (newDownloads.error) {
            console.warn("Error occurred:", newDownloads.error);
            isRefreshing = false;
            throw new Error(newDownloads.error || "Failed to get downloads");
        }
        
        // Create a new object to store the updated downloads
        let updatedDownloads = {};
        
        // Create maps to track downloads by URL for duplicate detection
        const downloadsByUrl = new Map();
        
        // First, process existing downloads in our current state
        for (const id of Object.keys(downloads)) {
            const download = downloads[id];
            if (download.url) {
                const normalizedUrl = normalizeUrl(download.url);
                if (!downloadsByUrl.has(normalizedUrl)) {
                    downloadsByUrl.set(normalizedUrl, []);
                }
                downloadsByUrl.get(normalizedUrl).push({id, download});
            }
        }
        
        // Process all new downloads from the API
        for (const id of Object.keys(newDownloads)) {
            const newDownload = newDownloads[id];
            
            // Add to the normalized URL map
            if (newDownload.url) {
                const normalizedUrl = normalizeUrl(newDownload.url);
                if (!downloadsByUrl.has(normalizedUrl)) {
                    downloadsByUrl.set(normalizedUrl, []);
                }
                downloadsByUrl.get(normalizedUrl).push({id, download: newDownload, isNew: true});
            }
            
            // Add to updated downloads
            updatedDownloads[id] = newDownload;
        }
        
        // Track any downloads that disappeared but were active
        for (const id of Object.keys(downloads)) {
            // Skip if this download is already in the updated list
            if (updatedDownloads[id]) continue;
            
            const download = downloads[id];
            
            // If a download is active (downloading/paused) but disappeared, check if we found it with a different ID
            if (['downloading', 'paused', 'queued'].includes(download.status)) {
                let foundDuplicate = false;
                
                // Check if we have this URL with a different ID in the new downloads
                if (download.url) {
                    const normalizedUrl = normalizeUrl(download.url);
                    const urlDownloads = downloadsByUrl.get(normalizedUrl) || [];
                    
                    for (const {id: otherId, download: otherDownload, isNew} of urlDownloads) {
                        // If this is a new download with the same URL
                        if (isNew && id !== otherId) {
                            console.log(`Found download ${otherId} with same URL as disappeared download ${id}`);
                            foundDuplicate = true;
                            // Don't add the disappeared download to updatedDownloads
                            break;
                        }
                    }
                }
                
                // If we didn't find a duplicate, preserve the download with an error state
                if (!foundDuplicate) {
                    console.warn(`Download ${id} disappeared while in ${download.status} state, preserving in UI as failed`);
                    updatedDownloads[id] = {
                        ...download,
                        status: 'failed',
                        speed: 0,
                        time_left: 0,
                        error_message: 'Connection to download manager lost'
                    };
                    
                    // Try to recover the download asynchronously if it was in downloading state
                    if (download.status === 'downloading') {
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
        }

        // Check if anything has changed that would require a re-render
        let needsRerender = forceRefresh || 
                           JSON.stringify(Object.keys(downloads).sort()) !== JSON.stringify(Object.keys(updatedDownloads).sort());
        
        // If structure hasn't changed, check if any properties have changed
        if (!needsRerender) {
            for (const id of Object.keys(updatedDownloads)) {
                if (downloads[id] && (
                    downloads[id].status !== updatedDownloads[id].status ||
                    downloads[id].progress !== updatedDownloads[id].progress ||
                    downloads[id].speed !== updatedDownloads[id].speed
                )) {
                    needsRerender = true;
                    break;
                }
            }
        }
        
        // Update the global downloads object
        downloads = updatedDownloads;
        
        // Either do a full re-render or just update progress
        if (needsRerender) {
            renderDownloads();
        } else {
            updateDownloadProgress();
        }
        
        // Update status bar with latest data
        updateStatusBar();
        
        return true;
    } catch (error) {
        console.error('Error fetching downloads:', error);
        // Don't clear the downloads object on error to maintain state
        // Re-throw the error so it can be caught by callers
        throw error;
    } finally {
        isRefreshing = false;
    }
}

// Add a manual refresh button handler
function setupRefreshButton() {
    const refreshButton = document.getElementById('refresh-button');
    if (refreshButton) {
        refreshButton.addEventListener('click', async () => {
            // Disable the button and show loading state
            refreshButton.disabled = true;
            refreshButton.classList.add('loading');
            
            // Add a spinner icon while refreshing
            const originalContent = refreshButton.innerHTML;
            refreshButton.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i><span>Refreshing...</span>';
            
            showNotification('Refreshing downloads...', 'info');
            
            try {
                // Attempt to update downloads with forced refresh
                await updateDownloads(true);
                showNotification('Downloads refreshed successfully', 'success');
            } catch (error) {
                console.error('Error refreshing downloads:', error);
                showNotification('Failed to refresh downloads. Please try again.', 'error');
            } finally {
                // Re-enable button and restore original content
                refreshButton.disabled = false;
                refreshButton.classList.remove('loading');
                refreshButton.innerHTML = originalContent;
            }
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
        
        // Create download items
        const statusIcon = download.status === 'completed' ? 'fa-check-circle' :
                          download.status === 'downloading' ? 'fa-circle-notch fa-spin' :
                          download.status === 'paused' ? 'fa-pause-circle' :
                          download.status === 'queued' ? 'fa-clock' :
                          download.status === 'scheduled' ? 'fa-calendar-alt' :
                          'fa-exclamation-circle';
        
        const statusClass = download.status === 'completed' ? 'status-completed' :
                           download.status === 'downloading' ? 'status-downloading' :
                           download.status === 'paused' ? 'status-paused' :
                           download.status === 'queued' ? 'status-queued' :
                           download.status === 'scheduled' ? 'status-scheduled' :
                           'status-failed';
        
        const statusText = download.status === 'completed' ? 'Completed' :
                          download.status === 'downloading' ? 'Downloading' :
                          download.status === 'paused' ? 'Paused' :
                          download.status === 'queued' ? 'Queued' :
                          download.status === 'scheduled' ? 'Scheduled' :
                          'Failed';
                          
        // Format scheduled time for display if applicable
        let scheduleInfo = '';
        if (download.status === 'scheduled' && download.schedule && download.schedule.scheduled_time) {
            const scheduleDate = new Date(download.schedule.scheduled_time);
            const formattedDate = scheduleDate.toLocaleDateString();
            const formattedTime = scheduleDate.toLocaleTimeString([], {hour: '2-digit', minute:'2-digit'});
            
            scheduleInfo = `<div class="schedule-info">${formattedDate} ${formattedTime}</div>`;
            if (download.schedule.recurrence) {
                const recurrenceText = download.schedule.recurrence === 'daily' ? 'Daily' : 
                                      download.schedule.recurrence === 'weekly' ? 'Weekly' : '';
                if (recurrenceText) {
                    scheduleInfo += `<div class="recurrence-info">${recurrenceText}</div>`;
                }
            }
        }
        
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
                    <span class="status-badge ${statusClass}">
                        <i class="fa-solid ${statusIcon}"></i>
                        <span class="status-text">${statusText}</span>
                        <div class="progress-bar-container">
                            <div class="progress-bar ${progressBarClass}" style="width: ${progress}%"></div>
                        </div>
                    </span>
                </div>
                <div class="item-cell item-speed">${formatSpeed(download.speed)}</div>
                <div class="item-cell item-time">${formatTimeLeft(download.time_left)}</div>
                <div class="item-cell item-date">${formatDate(download.date_added)}</div>
                <div class="item-cell item-schedule">${scheduleInfo}</div>
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

// Function to detect if a URL is a YouTube video
function detectYouTubeUrl(url) {
    if (!url) return false;
    
    try {
        // Normalize the URL first
        const normalizedUrl = normalizeUrl(url);
        
        // Check for various YouTube domain patterns
        const youtubePatterns = [
            '//youtube.com/watch',
            '//youtube.com/shorts/',
            '//youtube.com/v/',
            '//youtube.com/embed/',
            '//youtu.be/',
            '//youtube.com/playlist',
            '//music.youtube.com/watch',
            '//gaming.youtube.com/watch'
        ];
        
        // Check if normalized URL matches any YouTube patterns
        for (const pattern of youtubePatterns) {
            if (normalizedUrl.includes(pattern)) {
                return true;
            }
        }
        
        return false;
    } catch (e) {
        console.error('Error detecting YouTube URL:', e);
        
        // Fallback to simpler detection if normalization fails
        return url.includes('youtube.com/watch') || 
               url.includes('youtu.be/') ||
               url.includes('youtube.com/shorts/');
    }
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
        showNotification("No downloads selected to pause", "warning");
        return;
    }

    let successCount = 0;
    let failedCount = 0;
    const errors = [];
    
    for (const id of selectedDownloads) {
        try {
            console.log(`Attempting to pause download ${id}`);
            const result = await eel.pause_download(id)();
            
            // Check for error responses
            if (result && result.error) {
                console.error(`Error pausing download ${id}:`, result.error);
                failedCount++;
                errors.push(`Failed to pause download ${id}: ${result.error}`);
            } else {
                successCount++;
                console.log(`Successfully paused download ${id}`);
                
                // Update download in local cache
                if (downloads[id]) {
                    downloads[id] = result;
                }
            }
        } catch (error) {
            console.error(`Error pausing download ${id}:`, error);
            failedCount++;
            errors.push(`Failed to pause download ${id}: ${error.message || "Unknown error"}`);
        }
    }
    
    // Show notifications based on results
    if (successCount > 0) {
        showNotification(`Paused ${successCount} download(s)`, 'success');
    }
    
    if (failedCount > 0) {
        console.error(`Failed to pause ${failedCount} downloads:`, errors);
        showNotification(`Failed to pause ${failedCount} download(s). Check console for details.`, 'error');
    }
    
    // Refresh the downloads list
    await updateDownloads(true);
}

async function deleteSelectedDownloads() {
    if (selectedDownloads.size === 0) {
        console.warn("No downloads selected to delete");
        showNotification("No downloads selected to delete", "warning");
        return;
    }
    
    // Create a delete confirmation dialog for batch deletion
    const dialog = document.createElement('div');
    dialog.className = 'delete-dialog';
    
    const selectedCount = selectedDownloads.size;
    const selectedFiles = [...selectedDownloads].map(id => downloads[id]?.filename || 'Unknown file').slice(0, 3);
    const hasMore = selectedCount > 3;
    
    const filesList = selectedFiles.map(name => `<li>${name}</li>`).join('');
    const moreText = hasMore ? `<li>...and ${selectedCount - 3} more</li>` : '';
    
    const dialogContent = `
        <div class="delete-dialog-content">
            <div class="delete-dialog-header">
                <i class="fa-solid fa-triangle-exclamation"></i>
                <h3>Confirm Delete</h3>
            </div>
            <div class="delete-dialog-body">
                <p><strong>Are you sure you want to delete ${selectedCount} selected download${selectedCount > 1 ? 's' : ''}?</strong></p>
                <p>Selected files:</p>
                <ul class="file-list">
                    ${filesList}
                    ${moreText}
                </ul>
                <p class="text-dim">The downloaded files will remain on your system.</p>
            </div>
            <div class="delete-dialog-footer">
                <button class="delete-dialog-btn delete-dialog-btn-cancel">
                    <i class="fa-solid fa-times"></i>Cancel
                </button>
                <button class="delete-dialog-btn delete-dialog-btn-delete">
                    <i class="fa-solid fa-trash"></i>Delete
                </button>
            </div>
        </div>
    `;
    
    dialog.innerHTML = dialogContent;
    document.body.appendChild(dialog);
    
    // Add event listeners
    const cancelBtn = dialog.querySelector('.delete-dialog-btn-cancel');
    const deleteBtn = dialog.querySelector('.delete-dialog-btn-delete');
    
    // Add click outside to close
    dialog.addEventListener('click', (e) => {
        if (e.target === dialog) {
            dialog.remove();
        }
    });
    
    // Add escape key to close
    document.addEventListener('keydown', function escapeHandler(e) {
        if (e.key === 'Escape') {
            dialog.remove();
            document.removeEventListener('keydown', escapeHandler);
        }
    });
    
    cancelBtn.addEventListener('click', () => {
        dialog.remove();
    });
    
    deleteBtn.addEventListener('click', async () => {
        // Show loading state
        deleteBtn.disabled = true;
        deleteBtn.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i>Deleting...';
        
        let successCount = 0;
        let failedCount = 0;
        const toDelete = [...selectedDownloads]; // Make a copy of the selected IDs
        
        for (const id of toDelete) {
            try {
                console.log(`Attempting to delete download ${id}`);
                const result = await eel.delete_download(id, false)();
                
                if (result === true) {
                    successCount++;
                    selectedDownloads.delete(id); // Remove from selection
                    console.log(`Successfully deleted download ${id}`);
                    
                    // Remove from local cache
                    delete downloads[id];
                } else {
                    failedCount++;
                    console.error(`Failed to delete download ${id}`);
                }
            } catch (error) {
                failedCount++;
                console.error(`Error deleting download ${id}:`, error);
            }
        }
        
        // Update selection UI
        updateSelectionUI();
        
        // Show notifications based on results
        if (successCount > 0) {
            showNotification(`Deleted ${successCount} download(s)`, 'success');
        }
        
        if (failedCount > 0) {
            showNotification(`Failed to delete ${failedCount} download(s)`, 'error');
        }
        
        // Refresh the downloads list
        await updateDownloads(true);
        
        // Close the dialog
        dialog.remove();
    });
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
    const modal = document.getElementById(modalId);
    if (modal) {
        modal.classList.add('active');
        document.body.style.overflow = 'hidden'; // Prevent background scrolling
    }
}

function closeModal(modalId) {
    const modal = document.getElementById(modalId);
    if (modal) {
        modal.classList.remove('active');
        document.body.style.overflow = ''; // Restore scrolling
    }
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

// Handle select none action
function handleSelectNone() {
    // Clear selected downloads
    selectedDownloads.clear();
    
    // Update selection state in UI
    const checkboxes = document.querySelectorAll('.download-checkbox input[type="checkbox"]');
    checkboxes.forEach(checkbox => {
        checkbox.checked = false;
    });
    
    // Update select all checkbox
    const selectAllCheckbox = document.getElementById('select-all');
    if (selectAllCheckbox) {
        selectAllCheckbox.checked = false;
        selectAllCheckbox.indeterminate = false;
    }
    
    // Update selection UI (toolbar buttons etc.)
    updateSelectionUI();
}

// Copy text to clipboard
async function copyToClipboard(text) {
    try {
        await navigator.clipboard.writeText(text);
        return true;
    } catch (err) {
        console.error('Failed to copy text: ', err);
        
        // Fallback for browsers that don't support clipboard API
        const textArea = document.createElement('textarea');
        textArea.value = text;
        textArea.style.position = 'fixed';
        textArea.style.left = '-999999px';
        textArea.style.top = '-999999px';
        document.body.appendChild(textArea);
        textArea.focus();
        textArea.select();
        
        try {
            document.execCommand('copy');
            document.body.removeChild(textArea);
            return true;
        } catch (err) {
            document.body.removeChild(textArea);
            console.error('Failed to copy text using fallback: ', err);
            return false;
        }
    }
}

// Open file location
async function openFileLocation(downloadId) {
    try {
        const download = downloads[downloadId];
        if (!download) {
            showNotification('Download information not found.', 'error');
            return;
        }
        
        // Use Eel to open the directory
        const result = await eel.open_file_location(download.save_path)();
        
        if (!result.success) {
            showNotification(`Error: ${result.error || 'Failed to open location'}`, 'error');
        }
    } catch (error) {
        console.error('Error opening file location:', error);
        showNotification('Failed to open file location.', 'error');
    }
}