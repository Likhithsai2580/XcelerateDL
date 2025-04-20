import os

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.api.downloads import router as downloads_router
from app.services.downloader import download_manager
from app.services.ws_manager import manager as ws_manager

# Create FastAPI app
app = FastAPI(
    title="Download Manager API", description="An API for managing file downloads", version="1.0.0"
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

# Include routers
app.include_router(downloads_router)


@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    """Serve the main application UI"""
    return templates.TemplateResponse("index.html", {"request": request})


@app.get("/api")
async def api_root():
    return {
        "message": "Welcome to the Download Manager API. Go to /docs for the API documentation."
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


@app.on_event("startup")
async def startup_event():
    """Initialize the download manager and restore previous downloads"""
    resumed_count = await download_manager.initialize()
    print(f"Server started! Resumed {resumed_count} downloads.")


@app.on_event("shutdown")
async def shutdown_event():
    """Save the current download state before shutting down"""
    await download_manager.save_downloads()
    print("Server shutting down, download state saved.")


if __name__ == "__main__":
    import uvicorn

    # Determine the port from environment or default to 8000
    port = int(os.getenv("PORT", 8000))
    uvicorn.run("app.main:app", host="0.0.0.0", port=port, reload=True)
