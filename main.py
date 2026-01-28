from fastapi import FastAPI, Request, HTTPException, Form, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from fastapi.responses import RedirectResponse, StreamingResponse, JSONResponse
from pathlib import Path
from typing import List, Optional
import shutil
import zipfile
import io
from datetime import datetime, timedelta
import re
import ffmpeg
import tempfile
import asyncio
from concurrent.futures import ThreadPoolExecutor
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
import logging
from contextlib import asynccontextmanager
from merge_helper import merge_videos_fast

# Configure logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# Initialize APScheduler
scheduler = AsyncIOScheduler()

# Thread pool for running ffmpeg in background
executor = ThreadPoolExecutor(max_workers=2)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Lifespan context manager for FastAPI application.
    Handles startup and shutdown events.
    """
    # Startup: Start the scheduler
    scheduler.add_job(
        merge_today_videos_job,
        trigger=CronTrigger(hour=18, minute=0),  # Run at 18:00 (6 PM) every day
        id="merge_today_videos",
        name="Merge Today's Videos",
        replace_existing=True,
    )

    scheduler.start()
    logger.info("🚀 Scheduler started - Video merge job will run daily at 6 PM (18:00)")

    yield  # Application is running

    # Shutdown: Stop the scheduler
    scheduler.shutdown()
    logger.info("🛑 Scheduler stopped")


# Create FastAPI app with lifespan
app = FastAPI(lifespan=lifespan)

# Configure CORS to allow all origins
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allow all origins
    allow_credentials=True,
    allow_methods=["*"],  # Allow all methods
    allow_headers=["*"],  # Allow all headers
)

app.mount("/static", StaticFiles(directory="n8n_ffmpeg"), name="static")
app.mount("/yt", StaticFiles(directory="yt"), name="yt")


templates = Jinja2Templates(directory="templates")

STATICFILES_DIR = Path("n8n_ffmpeg")


# Scheduled job function
async def merge_today_videos_job():
    """
    Scheduled job that runs at 6 PM to merge today's videos.
    This is the same logic as the API endpoint but runs automatically.
    """
    try:
        logger.info("Starting scheduled video merge job...")

        # Use current date (will be called at 6 PM to merge today's videos)
        current_date = datetime.now()
        today_str = current_date.strftime("%Y-%m-%d")

        # Pattern to match date in filename (YYYY-MM-DD)
        date_pattern = re.compile(r"(\d{4}-\d{2}-\d{2})")

        # Get all video files from today
        if not STATICFILES_DIR.exists():
            logger.error("n8n_ffmpeg folder not found")
            return

        video_files = []
        video_extensions = {".mp4", ".avi", ".mov", ".mkv", ".flv", ".wmv", ".webm"}

        # Find all video files from today
        for item in STATICFILES_DIR.rglob("*"):
            if item.is_file() and item.suffix.lower() in video_extensions:
                match = date_pattern.search(item.name)
                if match and match.group(1) == today_str:
                    video_files.append(item)

        if not video_files:
            logger.warning(f"No video files found for {today_str}")
            return

        # Sort files by name to ensure consistent order
        video_files.sort(key=lambda x: x.name)

        logger.info(f"Found {len(video_files)} videos to merge for {today_str}")

        # Generate output filename
        output_filename = f"{today_str}.mp4"
        output_path = STATICFILES_DIR / output_filename

        # Try FAST merge first (codec copy - no re-encoding)
        # This is 10-50x faster but only works if all videos have same format
        loop = asyncio.get_event_loop()
        logger.info(
            f"Attempting FAST merge (codec copy) for {len(video_files)} videos..."
        )
        result = await loop.run_in_executor(
            executor, merge_videos_fast, video_files, output_path
        )

        # If fast merge failed, fall back to slow merge with re-encoding
        if result["status"] == "error":
            logger.warning(f"Fast merge failed: {result['message']}")
            logger.info("Falling back to slow merge with re-encoding...")
            result = await loop.run_in_executor(
                executor, merge_videos_sync, video_files, output_path
            )

        if result["status"] == "success":
            logger.info(
                f"✅ Successfully merged {len(video_files)} videos into {output_filename}"
            )
            logger.info(f"   Output size: {result['output_size_mb']} MB")
            logger.info(f"   Method: {result['message']}")
        else:
            logger.error(f"❌ Failed to merge videos: {result['message']}")

    except Exception as e:
        logger.error(f"❌ Error in scheduled merge job: {str(e)}", exc_info=True)


@app.get("/")
async def home(request: Request):
    """Home page with file listing"""
    try:
        items = []
        if STATICFILES_DIR.exists():
            for item in STATICFILES_DIR.iterdir():
                items.append(
                    {
                        "name": item.name,
                        "type": "📁" if item.is_dir() else "📄",
                        "is_dir": item.is_dir(),
                        "size": f"{item.stat().st_size / 1024:.2f} KB"
                        if item.is_file()
                        else "-",
                        "path": item.name,
                    }
                )

        return templates.TemplateResponse(
            "file_list.html",
            {
                "request": request,
                "items": sorted(items, key=lambda x: (not x["is_dir"], x["name"])),
                "title": "Static Files Browser",
                "current_path": None,
            },
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/folder/{path:path}")
async def browse_folder(request: Request, path: str):
    """Browse a specific folder"""
    try:
        target_path = STATICFILES_DIR / path
        target_path = target_path.resolve()

        if not str(target_path).startswith(str(STATICFILES_DIR.resolve())):
            raise HTTPException(status_code=403, detail="Access denied")

        if not target_path.exists() or not target_path.is_dir():
            raise HTTPException(status_code=404, detail="Folder not found")

        items = []
        for item in target_path.iterdir():
            items.append(
                {
                    "name": item.name,
                    "type": "📁" if item.is_dir() else "📄",
                    "is_dir": item.is_dir(),
                    "size": f"{item.stat().st_size / 1024:.2f} KB"
                    if item.is_file()
                    else "-",
                    "path": str(item.relative_to(STATICFILES_DIR)),
                }
            )

        return templates.TemplateResponse(
            "file_list.html",
            {
                "request": request,
                "items": sorted(items, key=lambda x: (not x["is_dir"], x["name"])),
                "title": f"Browsing: {path}",
                "current_path": path,
            },
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/delete")
async def delete_item(path: str = Form(...)):
    """Delete a single file or folder"""
    try:
        target_path = STATICFILES_DIR / path
        target_path = target_path.resolve()

        if not str(target_path).startswith(str(STATICFILES_DIR.resolve())):
            raise HTTPException(status_code=403, detail="Access denied")

        if not target_path.exists():
            raise HTTPException(status_code=404, detail="File not found")

        if target_path.is_dir():
            shutil.rmtree(target_path)
        else:
            target_path.unlink()

        parent = str(Path(path).parent)
        if parent == ".":
            return RedirectResponse(url="/", status_code=303)
        else:
            return RedirectResponse(url=f"/folder/{parent}", status_code=303)

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/delete-multiple")
async def delete_multiple(request: Request, selected_files: List[str] = Form(...)):
    """Delete multiple selected files/folders"""
    try:
        deleted_count = 0
        errors = []

        for file_path in selected_files:
            try:
                target_path = STATICFILES_DIR / file_path
                target_path = target_path.resolve()

                if not str(target_path).startswith(str(STATICFILES_DIR.resolve())):
                    errors.append(f"{file_path}: Access denied")
                    continue

                if not target_path.exists():
                    errors.append(f"{file_path}: Not found")
                    continue

                if target_path.is_dir():
                    shutil.rmtree(target_path)
                else:
                    target_path.unlink()

                deleted_count += 1

            except Exception as e:
                errors.append(f"{file_path}: {str(e)}")

        if selected_files:
            parent = str(Path(selected_files[0]).parent)
            redirect_url = "/" if parent == "." else f"/folder/{parent}"
        else:
            redirect_url = "/"

        return RedirectResponse(url=redirect_url, status_code=303)

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/download-multiple")
async def download_multiple(selected_files: List[str] = Form(...)):
    """Download multiple selected files/folders as a ZIP archive"""
    try:
        if not selected_files:
            raise HTTPException(status_code=400, detail="No files selected")

        # Create an in-memory ZIP file
        zip_buffer = io.BytesIO()

        with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
            for file_path in selected_files:
                target_path = STATICFILES_DIR / file_path
                target_path = target_path.resolve()

                # Security check
                if not str(target_path).startswith(str(STATICFILES_DIR.resolve())):
                    continue

                if not target_path.exists():
                    continue

                # Add file or folder to ZIP
                if target_path.is_file():
                    # Add single file
                    zip_file.write(target_path, arcname=file_path)
                elif target_path.is_dir():
                    # Add all files in directory recursively
                    for item in target_path.rglob("*"):
                        if item.is_file():
                            arcname = str(item.relative_to(STATICFILES_DIR))
                            zip_file.write(item, arcname=arcname)

        # Prepare the ZIP for download
        zip_buffer.seek(0)

        # Generate filename with timestamp
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"n8n_files_{timestamp}.zip"

        return StreamingResponse(
            io.BytesIO(zip_buffer.getvalue()),
            media_type="application/zip",
            headers={"Content-Disposition": f"attachment; filename={filename}"},
        )

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/yt/files")
async def list_yt_files():
    """List all files in yt folder and return as JSON"""
    try:
        yt_dir = Path("yt")

        if not yt_dir.exists():
            return JSONResponse(
                content={
                    "status": "error",
                    "message": "yt folder not found",
                    "files": [],
                },
                status_code=404,
            )

        files = []

        # Recursively get all files in yt folder
        for item in yt_dir.rglob("*"):
            if item.is_file():
                # Get relative path from yt folder
                relative_path = str(item.relative_to(yt_dir))
                size_bytes = item.stat().st_size

                files.append(
                    {
                        # Convert Windows paths to forward slash
                        "name": relative_path.replace("\\", "/"),
                        "size": size_bytes,
                        "size_kb": round(size_bytes / 1024, 2),
                        "size_mb": round(size_bytes / 1024 / 1024, 2),
                    }
                )

        # Sort by name
        files.sort(key=lambda x: x["name"])

        return JSONResponse(
            content={"status": "success", "total_files": len(files), "files": files}
        )

    except Exception as e:
        return JSONResponse(
            content={"status": "error", "message": str(e), "files": []}, status_code=500
        )


@app.post("/api/yt/files")
async def upload_file_to_yt(file: UploadFile = File(...)):
    """Upload a file to the yt folder"""
    try:
        yt_dir = Path("yt")

        # Create yt folder if it doesn't exist
        yt_dir.mkdir(exist_ok=True)

        # Validate filename
        if not file.filename:
            return JSONResponse(
                content={"status": "error", "message": "No filename provided"},
                status_code=400,
            )

        # Create target file path
        target_path = yt_dir / file.filename

        # Write file to disk
        with open(target_path, "wb") as buffer:
            content = await file.read()
            buffer.write(content)

        # Get file info
        file_size = target_path.stat().st_size

        return JSONResponse(
            content={
                "status": "success",
                "message": "File uploaded successfully",
                "file": {
                    "name": file.filename,
                    "size": file_size,
                    "size_kb": round(file_size / 1024, 2),
                    "size_mb": round(file_size / 1024 / 1024, 2),
                },
            },
            status_code=201,
        )

    except Exception as e:
        return JSONResponse(
            content={"status": "error", "message": str(e)}, status_code=500
        )


@app.get("/api/yt/files/{filename:path}")
async def get_file_url(request: Request, filename: str):
    """Get the URL for a file in the yt folder"""
    try:
        yt_dir = Path("yt")
        target_path = yt_dir / filename
        target_path = target_path.resolve()

        # Security check - ensure path is within yt directory
        if not str(target_path).startswith(str(yt_dir.resolve())):
            return JSONResponse(
                content={"status": "error", "message": "Access denied"}, status_code=403
            )

        # Check if file exists
        if not target_path.exists() or not target_path.is_file():
            return JSONResponse(
                content={"status": "error", "message": "File not found"},
                status_code=404,
            )

        # Construct the URL
        base_url = str(request.base_url).rstrip("/")
        file_url = f"{base_url}/yt/{filename}"

        # Get file info
        file_size = target_path.stat().st_size

        return JSONResponse(
            content={
                "status": "success",
                "file": {
                    "name": filename,
                    "url": file_url,
                    "size": file_size,
                    "size_kb": round(file_size / 1024, 2),
                    "size_mb": round(file_size / 1024 / 1024, 2),
                },
            }
        )

    except Exception as e:
        return JSONResponse(
            content={"status": "error", "message": str(e)}, status_code=500
        )


@app.delete("/api/yt/files/{filename:path}")
async def delete_file_from_yt(filename: str):
    """Delete a file from the yt folder"""
    try:
        yt_dir = Path("yt")
        target_path = yt_dir / filename
        target_path = target_path.resolve()

        # Security check - ensure path is within yt directory
        if not str(target_path).startswith(str(yt_dir.resolve())):
            return JSONResponse(
                content={"status": "error", "message": "Access denied"}, status_code=403
            )

        # Check if file exists
        if not target_path.exists():
            return JSONResponse(
                content={"status": "error", "message": "File not found"},
                status_code=404,
            )

        # Only delete files, not directories
        if not target_path.is_file():
            return JSONResponse(
                content={"status": "error", "message": "Cannot delete directories"},
                status_code=400,
            )

        # Delete the file
        target_path.unlink()

        return JSONResponse(
            content={
                "status": "success",
                "message": f"File '{filename}' deleted successfully",
            }
        )

    except Exception as e:
        return JSONResponse(
            content={"status": "error", "message": str(e)}, status_code=500
        )


@app.get("/api/files/yesterday")
async def get_yesterday_files(date_now: Optional[str] = None):
    """
    Get all files from n8n_ffmpeg folder that have yesterday's date in their filename.

    Args:
        date_now: Optional date string in format YYYY-MM-DD (defaults to today)

    Returns:
        JSON response with list of files from yesterday

    Example filenames:
        - news_2026-01-26_13-00-22.mp4
        - sanook_news_2026-01-26_09-05-24.mp4
        - sports_news_2026-01-26_09-03-22.mp4
    """
    try:
        # Parse the current date or use today
        if date_now:
            try:
                current_date = datetime.strptime(date_now, "%Y-%m-%d")
            except ValueError:
                return JSONResponse(
                    content={
                        "status": "error",
                        "message": "Invalid date format. Use YYYY-MM-DD",
                    },
                    status_code=400,
                )
        else:
            current_date = datetime.now()

        # Calculate yesterday's date
        yesterday = current_date - timedelta(days=1)
        yesterday_str = yesterday.strftime("%Y-%m-%d")

        # Pattern to match date in filename (YYYY-MM-DD)
        date_pattern = re.compile(r"(\d{4}-\d{2}-\d{2})")

        # Get all files from n8n_ffmpeg folder
        if not STATICFILES_DIR.exists():
            return JSONResponse(
                content={
                    "status": "error",
                    "message": "n8n_ffmpeg folder not found",
                    "files": [],
                },
                status_code=404,
            )

        yesterday_files = []

        # Recursively search for files
        for item in STATICFILES_DIR.rglob("*"):
            if item.is_file():
                # Extract date from filename
                match = date_pattern.search(item.name)
                if match:
                    file_date = match.group(1)

                    # Check if the date matches yesterday
                    if file_date == yesterday_str:
                        relative_path = str(item.relative_to(STATICFILES_DIR))
                        file_size = item.stat().st_size

                        yesterday_files.append(
                            {
                                "name": item.name,
                                "path": relative_path.replace("\\", "/"),
                                "date": file_date,
                                "size": file_size,
                                "size_kb": round(file_size / 1024, 2),
                                "size_mb": round(file_size / 1024 / 1024, 2),
                                "modified": datetime.fromtimestamp(
                                    item.stat().st_mtime
                                ).strftime("%Y-%m-%d %H:%M:%S"),
                            }
                        )

        # Sort by filename
        yesterday_files.sort(key=lambda x: x["name"])

        return JSONResponse(
            content={
                "status": "success",
                "current_date": current_date.strftime("%Y-%m-%d"),
                "yesterday_date": yesterday_str,
                "total_files": len(yesterday_files),
                "files": yesterday_files,
            }
        )

    except Exception as e:
        return JSONResponse(
            content={"status": "error", "message": str(e), "files": []}, status_code=500
        )


# Thread pool for running ffmpeg in background
executor = ThreadPoolExecutor(max_workers=2)


def merge_videos_sync(video_files: List[Path], output_path: Path) -> dict:
    """
    Synchronous function to merge multiple video files using ffmpeg.
    This will be run in a thread pool to avoid blocking the async event loop.

    Args:
        video_files: List of video file paths to merge
        output_path: Path where the merged video will be saved

    Returns:
        dict with status and message
    """
    try:
        # Create a temporary file list for ffmpeg concat
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", delete=False, encoding="utf-8"
        ) as f:
            concat_file = f.name
            for video_file in video_files:
                # Write absolute path with forward slashes and escape special characters
                file_path = str(video_file.absolute()).replace("\\", "/")
                f.write(f"file '{file_path}'\n")

        try:
            # Use ffmpeg to merge and convert videos to 1920x1080 landscape
            # Scale vertical videos (1080x1920) to horizontal (1920x1080) with black bars
            # Using 'ultrafast' preset for much faster encoding (important when merging many videos)
            (
                ffmpeg.input(concat_file, format="concat", safe=0)
                .output(
                    str(output_path),
                    vf="scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2:black",
                    # Video codec settings
                    vcodec="libx264",
                    # Encoding speed (ultrafast = fastest encoding, larger file size)
                    preset="ultrafast",
                    crf=23,  # Quality (18-28, lower = better quality)
                    # Audio codec settings
                    acodec="aac",
                    audio_bitrate="128k",
                    # Other settings
                    loglevel="error",
                )
                .overwrite_output()
                .run(capture_stdout=True, capture_stderr=True)
            )

            return {
                "status": "success",
                "message": f"Successfully merged {len(video_files)} videos",
                "output_file": output_path.name,
                "output_size": output_path.stat().st_size,
                "output_size_mb": round(output_path.stat().st_size / 1024 / 1024, 2),
            }

        finally:
            # Clean up temporary concat file
            Path(concat_file).unlink(missing_ok=True)

    except ffmpeg.Error as e:
        error_message = e.stderr.decode() if e.stderr else str(e)
        return {"status": "error", "message": f"FFmpeg error: {error_message}"}
    except Exception as e:
        return {"status": "error", "message": f"Unexpected error: {str(e)}"}


@app.get("/api/files/merge-today")
async def merge_today_videos(date_now: Optional[str] = None):
    """
    Merge all video files from today into a single video file.

    Args:
        date_now: Optional date string in format YYYY-MM-DD (defaults to today)

    Returns:
        JSON response with merge status and output file info

    Note:
        - Videos will be merged in alphabetical order by filename
        - Output file will be saved as: YYYY-MM-DD.mp4
        - This uses ffmpeg to merge and convert videos to 1920x1080 landscape
    """
    try:
        # Parse the current date or use today
        if date_now:
            try:
                current_date = datetime.strptime(date_now, "%Y-%m-%d")
            except ValueError:
                return JSONResponse(
                    content={
                        "status": "error",
                        "message": "Invalid date format. Use YYYY-MM-DD",
                    },
                    status_code=400,
                )
        else:
            current_date = datetime.now()

        # Use today's date
        today_str = current_date.strftime("%Y-%m-%d")

        # Pattern to match date in filename (YYYY-MM-DD)
        date_pattern = re.compile(r"(\d{4}-\d{2}-\d{2})")

        # Get all video files from today
        if not STATICFILES_DIR.exists():
            return JSONResponse(
                content={"status": "error", "message": "n8n_ffmpeg folder not found"},
                status_code=404,
            )

        video_files = []
        video_extensions = {".mp4", ".avi", ".mov", ".mkv", ".flv", ".wmv", ".webm"}

        # Find all video files from today
        for item in STATICFILES_DIR.rglob("*"):
            if item.is_file() and item.suffix.lower() in video_extensions:
                match = date_pattern.search(item.name)
                if match and match.group(1) == today_str:
                    video_files.append(item)

        if not video_files:
            return JSONResponse(
                content={
                    "status": "error",
                    "message": f"No video files found for {today_str}",
                },
                status_code=404,
            )

        # Sort files by name to ensure consistent order
        video_files.sort(key=lambda x: x.name)

        output_filename = f"{today_str}.mp4"
        output_path = STATICFILES_DIR / output_filename

        # Try FAST merge first (codec copy - no re-encoding)
        # This is 10-50x faster but only works if all videos have same format
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            executor, merge_videos_fast, video_files, output_path
        )

        # If fast merge failed, fall back to slow merge with re-encoding
        if result["status"] == "error":
            result = await loop.run_in_executor(
                executor, merge_videos_sync, video_files, output_path
            )

        if result["status"] == "success":
            return JSONResponse(
                content={
                    "status": "success",
                    "message": result["message"],
                    "today_date": today_str,
                    "input_files": [f.name for f in video_files],
                    "total_input_files": len(video_files),
                    "output_file": result["output_file"],
                    "output_size": result["output_size"],
                    "output_size_mb": result["output_size_mb"],
                    "output_url": f"/static/{result['output_file']}",
                }
            )
        else:
            return JSONResponse(
                content={"status": "error", "message": result["message"]},
                status_code=500,
            )

    except Exception as e:
        return JSONResponse(
            content={"status": "error", "message": str(e)}, status_code=500
        )
