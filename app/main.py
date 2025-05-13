import argparse
import asyncio
import os
import sys
import signal

sys.path.append(os.getcwd())
from contextlib import asynccontextmanager

from fastapi import BackgroundTasks, FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.api.downloads import router as downloads_router
from app.gui import start_gui
from app.services.downloader import download_manager
from app.services.ws_manager import manager as ws_manager

# Flag to track if server is already running
server_running = False
# Flag to track shutdown in progress
shutdown_in_progress = False


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Handle startup and shutdown events."""
    global shutdown_in_progress # Ensure we are using the global flag
    # Startup
    resumed_count = await download_manager.initialize()
    print(f"Server started! Resumed {resumed_count} downloads.")

    yield

    # Shutdown
    if shutdown_in_progress:
        print("Lifespan: Shutdown previously initiated (e.g., via /shutdown endpoint or prior signal).")
    else:
        # This block will run if shutdown is triggered by an external signal 
        # not previously handled by our /shutdown logic or signal_handler which sets the flag.
        print("Lifespan: Shutdown initiated by external signal to Uvicorn or other direct means.")
        shutdown_in_progress = True # Mark shutdown as in progress for consistency

    print("Lifespan: Starting application shutdown process...")

    # Shutdown scheduler first
    try:
        await download_manager.shutdown_scheduler()
    except Exception as e:
        print(f"Error during scheduler shutdown in lifespan: {e}")

    # Cancel any active downloads
    try:
        await download_manager.cancel_all_active_downloads()
    except Exception as e:
        print(f"Error canceling active downloads: {e}")

    # Save download state
    await download_manager.save_downloads()
    print("Server shutting down, download state saved.")


# Create FastAPI app
app = FastAPI(
    title="XcelerateDL - Download Manager API",
    description="An API for managing file downloads",
    version="1.0.0",
    lifespan=lifespan,
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount static files directory
app.mount("/static", StaticFiles(directory="app/static"), name="static")

# Set up templates
templates = Jinja2Templates(directory="app/templates")

# Include routers - without the /api prefix since the router already has its own prefix
app.include_router(downloads_router)


@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    """Serve the main application UI"""
    return templates.TemplateResponse("index.html", {"request": request})


@app.get("/api")
async def api_root():
    return {"name": "XcelerateDL API", "version": "1.0.0", "docs_url": "/docs"}


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket endpoint for real-time download updates"""
    await ws_manager.connect(websocket)

    try:
        # Send initial data to the client
        await download_manager.broadcast_all_downloads()

        # Keep the connection alive
        while True:
            # Wait for any message from the client (we don't do anything with it yet)
            data = await websocket.receive_text()

            # For future: we could handle client requests here
            if data == "ping":
                await websocket.send_text("pong")
    except WebSocketDisconnect:
        ws_manager.disconnect(websocket)


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Global exception handler for the API"""
    # Log the exception
    import traceback

    error_details = traceback.format_exc()
    error_message = str(exc)

    print(f"API Error: {error_message}")
    print(f"Details: {error_details}")

    # Return a more friendly error response
    return JSONResponse(
        status_code=500,
        content={
            "detail": "An unexpected error occurred while processing your request.",
            "error_type": exc.__class__.__name__,
            "message": error_message,
        },
    )


# Add shutdown endpoint
@app.post("/shutdown")
async def shutdown_server(background_tasks: BackgroundTasks):
    """Shutdown the server gracefully"""
    global shutdown_in_progress
    
    # Prevent multiple shutdown attempts
    if shutdown_in_progress:
        return {"message": "Shutdown already in progress..."}
    
    shutdown_in_progress = True

    async def shutdown_app():
        # First shutdown scheduler to ensure scheduled downloads are saved
        try:
            # Explicitly set the shutdown flag before calling shutdown
            download_manager._scheduler_shutdown = True

            # Cancel any running scheduler task
            if download_manager.scheduler_task:
                download_manager.scheduler_task.cancel()

            # Call the scheduler shutdown
            await download_manager.shutdown_scheduler()

            # Ensure the scheduler task is completely done
            await asyncio.sleep(1)

            # Double-check and force termination if needed
            if download_manager.scheduler_task and not download_manager.scheduler_task.done():
                try:
                    download_manager.scheduler_task.cancel()
                    await asyncio.wait_for(
                        asyncio.shield(download_manager.scheduler_task), timeout=2.0
                    )
                except (TimeoutError, asyncio.CancelledError):
                    pass
        except Exception as e:
            print(f"Error during scheduler shutdown: {e}")

        # Cancel active downloads
        try:
            await download_manager.cancel_all_active_downloads()
        except Exception as e:
            print(f"Error canceling active downloads: {e}")

        # Save the download state
        try:
            await download_manager.save_downloads()
            print("Download state saved successfully")
        except Exception as e:
            print(f"Error saving download state: {e}")

        # Wait a bit to allow this response to be sent
        try:
            await asyncio.sleep(1)
        except asyncio.CancelledError:
            pass

        # Signal the server to gracefully shut down
        print("Server shutting down now...")
        # Send SIGTERM to the current process for graceful shutdown
        signal.raise_signal(signal.SIGTERM)

    # Schedule the shutdown to happen after response is sent
    background_tasks.add_task(shutdown_app)
    return {"message": "Server shutting down..."}


def setup_signal_handlers():
    """Setup signal handlers for graceful shutdown"""
    
    def handle_shutdown_signal(sig, frame):
        global shutdown_in_progress
        if not shutdown_in_progress:
            print(f"Received shutdown signal {sig}, initiating graceful shutdown...")
            shutdown_in_progress = True
            # Let the ASGI server handle the graceful shutdown
            # We don't need to exit here as the server will do it
    
    # Register signal handlers
    signal.signal(signal.SIGINT, handle_shutdown_signal)
    signal.signal(signal.SIGTERM, handle_shutdown_signal)


def start_api_server(host="0.0.0.0", port=8000, reload=False):
    """Start the API server."""
    global server_running

    if server_running:
        print("API server is already running.")
        return

    import uvicorn
    
    # Setup signal handlers
    setup_signal_handlers()
    
    server_running = True

    # Set longer timeout for worker processes and use more workers
    uvicorn.run(
        "app.main:app",
        host=host,
        port=port,
        reload=reload,
        workers=2,
        timeout_keep_alive=120,  # Longer keep-alive timeout
        timeout_graceful_shutdown=30,  # More time for graceful shutdown
    )


def main():
    """Entry point for the application."""
    parser = argparse.ArgumentParser(description="XcelerateDL - Fast Download Manager")
    parser.add_argument("--gui", action="store_true", help="Start the GUI version")
    parser.add_argument("--api-only", action="store_true", help="Start only the API server")
    parser.add_argument("--port", type=int, default=8000, help="API server port (default: 8000)")
    parser.add_argument(
        "--host", type=str, default="0.0.0.0", help="API server host (default: 0.0.0.0)"
    )
    args = parser.parse_args()

    if args.gui:
        # Start the GUI version (which will also start the API server)
        start_gui()
    elif args.api_only or not args.gui:
        # Start just the API server
        print(f"Starting API server on {args.host}:{args.port}")
        start_api_server(host=args.host, port=args.port, reload=True)


if __name__ == "__main__":
    main()
