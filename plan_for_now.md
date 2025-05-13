# Plan for Now: Next Implementable Features

Based on the `future_plans.md` and the current codebase structure, here are a few features that offer a good balance of usefulness and near-term implementability:

1.  **Enhanced Resume Robustness (from "Seamless Resume & Self-Healing")**
    *   **Why**: The system already has a mechanism for saving and resuming downloads (`download_manager.save_downloads()`, `download_manager.initialize()`). Enhancing this for greater robustness is a natural and high-value next step.
    *   **Implementation Idea**:
        *   More frequent or granular saving of download progress to minimize data loss on unexpected interruptions.
        *   Improved error handling during the resume process to better cope with partially corrupted state files or changed network conditions for the *same download source*.
        *   Ensure that all download parameters necessary for a successful resume are being saved and restored correctly.

2.  **Simple HTTP/HTTPS Fallback (a step towards "Adaptive Protocol Switching")**
    *   **Why**: Provides a simple, effective way to improve download success rates when one scheme (HTTP or HTTPS) fails for a given host.
    *   **Implementation Idea**:
        *   If a download initiated with an `http://` URL fails with a connection error or a specific HTTP error code, the `download_manager` could automatically retry the download using `https://` for the same host and path.
        *   Similarly, if an `https://` download fails (e.g., due to certificate issues that the user might want to bypass in certain contexts, or if the server unexpectedly serves on HTTP), consider an option or automatic retry on `http://`. (This direction needs careful consideration of security implications).

3.  **Basic Multi-Mirror Support (a step towards "Machine-Learned Mirror & Peer Selection" and "Seamless Resume from alternative sources")**
    *   **Why**: Allows users to provide multiple sources for the same file, increasing the chances of a successful and potentially faster download if one source is slow or unavailable. This is a foundational step before implementing more complex mirror selection logic.
    *   **Implementation Idea**:
        *   Modify the download task definition to accept a list of URLs (mirrors) instead of a single URL.
        *   The `download_manager` would attempt to download from the first URL in the list. If it fails (based on defined retry limits or specific error conditions), it moves to the next URL in the list.
        *   The UI/API would need to be updated to allow users to input multiple mirrors.

These features leverage the existing `download_manager` and FastAPI structure, focusing on enhancing current capabilities before venturing into more complex areas like new transfer protocols or advanced P2P mechanisms. 