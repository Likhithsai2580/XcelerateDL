import argparse
import os
import sys
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

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Handle startup and shutdown events."""
    # Startup
    resumed_count = await download_manager.initialize()
    print(f"Server started! Resumed {resumed_count} downloads.")
    
    yield
    
    # Shutdown
    await download_manager.save_downloads()
    print("Server shutting down, download state saved.")

# Create FastAPI app
app = FastAPI(
    title="XcelerateDL - Download Manager API",
    description="An API for managing file downloads",
    version="1.0.0",
    lifespan=lifespan
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
    return {
        "name": "XcelerateDL API",
        "version": "1.0.0",
        "docs_url": "/docs"
    }

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
    return JSONResponse(
        status_code=500, content={"detail": f"An unexpected error occurred: {str(exc)}"}
    )

# Add shutdown endpoint
@app.post("/shutdown")
async def shutdown_server(background_tasks: BackgroundTasks):
    """Shutdown the server gracefully"""
    async def shutdown_app():
        # Save the download state
        await download_manager.save_downloads()
        # Wait a bit to allow this response to be sent
        import asyncio
        await asyncio.sleep(1)
        # Exit the process
        import os
        os._exit(0)
    
    # Schedule the shutdown to happen after response is sent
    background_tasks.add_task(shutdown_app)
    return {"message": "Server shutting down..."}

def start_api_server(host="0.0.0.0", port=8000, reload=False):
    """Start the API server."""
    global server_running
    
    if server_running:
        print("API server is already running.")
        return
        
    import uvicorn
    server_running = True
    uvicorn.run("app.main:app", host=host, port=port, reload=reload)

def main():
    """Entry point for the application."""
    parser = argparse.ArgumentParser(description="XcelerateDL - Fast Download Manager")
    parser.add_argument("--gui", action="store_true", help="Start the GUI version")
    parser.add_argument("--api-only", action="store_true", help="Start only the API server")
    parser.add_argument("--port", type=int, default=8000, help="API server port (default: 8000)")
    parser.add_argument("--host", type=str, default="0.0.0.0", help="API server host (default: 0.0.0.0)")
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
