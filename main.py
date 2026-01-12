from fastapi import FastAPI, Request, HTTPException, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from fastapi.responses import RedirectResponse, FileResponse, StreamingResponse, JSONResponse
from pathlib import Path
from typing import List
import shutil
import zipfile
import io
from datetime import datetime

app = FastAPI()

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


@app.get("/")
async def home(request: Request):
    """Home page with file listing"""
    try:

        items = []
        if STATICFILES_DIR.exists():
            for item in STATICFILES_DIR.iterdir():
                items.append({
                    "name": item.name,
                    "type": "📁" if item.is_dir() else "📄",
                    "is_dir": item.is_dir(),
                    "size": f"{item.stat().st_size / 1024:.2f} KB" if item.is_file() else "-",
                    "path": item.name
                })

        return templates.TemplateResponse(
            "file_list.html",
            {
                "request": request,
                "items": sorted(items, key=lambda x: (not x["is_dir"], x["name"])),
                "title": "Static Files Browser",
                "current_path": None
            }
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
            items.append({
                "name": item.name,
                "type": "📁" if item.is_dir() else "📄",
                "is_dir": item.is_dir(),
                "size": f"{item.stat().st_size / 1024:.2f} KB" if item.is_file() else "-",
                "path": str(item.relative_to(STATICFILES_DIR))
            })

        return templates.TemplateResponse(
            "file_list.html",
            {
                "request": request,
                "items": sorted(items, key=lambda x: (not x["is_dir"], x["name"])),
                "title": f"Browsing: {path}",
                "current_path": path
            }
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

        with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
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
                    for item in target_path.rglob('*'):
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
            headers={
                "Content-Disposition": f"attachment; filename={filename}"
            }
        )

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/yt/files")
async def list_yt_files():
    """List all files in yt folder and return as JSON"""
    try:
        yt_dir = Path("yt")

        if not yt_dir.exists():
            return JSONResponse(content={
                "status": "error",
                "message": "yt folder not found",
                "files": []
            }, status_code=404)

        files = []

        # Recursively get all files in yt folder
        for item in yt_dir.rglob('*'):
            if item.is_file():
                # Get relative path from yt folder
                relative_path = str(item.relative_to(yt_dir))
                size_bytes = item.stat().st_size

                files.append({
                    # Convert Windows paths to forward slash
                    "name": relative_path.replace("\\", "/"),
                    "size": size_bytes,
                    "size_kb": round(size_bytes / 1024, 2),
                    "size_mb": round(size_bytes / 1024 / 1024, 2)
                })

        # Sort by name
        files.sort(key=lambda x: x["name"])

        return JSONResponse(content={
            "status": "success",
            "total_files": len(files),
            "files": files
        })

    except Exception as e:
        return JSONResponse(content={
            "status": "error",
            "message": str(e),
            "files": []
        }, status_code=500)
