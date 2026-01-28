from pathlib import Path
from typing import List
import tempfile
import ffmpeg


def merge_videos_fast(video_files: List[Path], output_path: Path) -> dict:
    """
    SUPER FAST merge using codec copy (no re-encoding).

    This is 10-50x faster than re-encoding but requires all videos to have:
    - Same codec (e.g., all h264)
    - Same resolution (e.g., all 1920x1080)
    - Same frame rate (e.g., all 30fps)

    If videos have different formats, this will fail and you should use merge_videos_sync instead.

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
                # Write absolute path with forward slashes
                file_path = str(video_file.absolute()).replace("\\", "/")
                f.write(f"file '{file_path}'\n")

        try:
            # Use concat demuxer with codec copy (no re-encoding) - VERY FAST!
            (
                ffmpeg.input(concat_file, format="concat", safe=0)
                .output(
                    str(output_path),
                    c="copy",  # Copy codec - no re-encoding!
                    loglevel="error",
                )
                .overwrite_output()
                .run(capture_stdout=True, capture_stderr=True)
            )

            return {
                "status": "success",
                "message": f"Successfully merged {len(video_files)} videos (FAST mode - no re-encoding)",
                "output_file": output_path.name,
                "output_size": output_path.stat().st_size,
                "output_size_mb": round(output_path.stat().st_size / 1024 / 1024, 2),
            }

        finally:
            # Clean up temporary concat file
            Path(concat_file).unlink(missing_ok=True)

    except ffmpeg.Error as e:
        error_message = e.stderr.decode() if e.stderr else str(e)
        return {
            "status": "error",
            "message": f"FFmpeg fast merge error: {error_message}",
        }
    except Exception as e:
        return {"status": "error", "message": f"Unexpected error: {str(e)}"}
