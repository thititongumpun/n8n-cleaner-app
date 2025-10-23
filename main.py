from fastapi import FastAPI, Request, HTTPException, Form
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from fastapi.responses import RedirectResponse
from pathlib import Path
from typing import List
import shutil

app = FastAPI()


app.mount("/static", StaticFiles(directory="n8n_ffmpeg"), name="static")


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
