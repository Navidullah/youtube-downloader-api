from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, JSONResponse
from pydantic import BaseModel
import yt_dlp as youtube_dl
import io
import re
import httpx
import os

app = FastAPI(title="YouTube Downloader API")

# CORS Middleware - Configured for your domain
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://www.shopyor.com",
        "https://shopyor.com",
        "https://youtube-downloader-api-1ppa.onrender.com",
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"],
)

class VideoRequest(BaseModel):
    url: str
    format_id: str = None
    remove_watermark: bool = False

# Get cookies file path from environment (optional)
COOKIES_FILE = os.environ.get("YOUTUBE_COOKIES_FILE", "")

def get_ydl_opts(remove_watermark=False, format_id=None):
    """Get yt-dlp options with anti-bot measures"""
    
    # Base options
    opts = {
        'quiet': True,
        'no_warnings': True,
        'ignoreerrors': True,
        'extract_flat': False,
    }
    
    # Add cookies if available
    if COOKIES_FILE and os.path.exists(COOKIES_FILE):
        opts['cookiefile'] = COOKIES_FILE
    
    # Add user agent to look like a real browser
    opts['user_agent'] = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
    
    # Add referer
    opts['referer'] = 'https://www.youtube.com/'
    
    # Format selection
    if remove_watermark:
        opts['format'] = 'bestvideo[format_note!~="watermarked"][ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best'
    elif format_id:
        opts['format'] = format_id
    else:
        opts['format'] = 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best'
    
    return opts

@app.get("/")
async def root():
    return JSONResponse(
        content={"message": "YouTube Downloader API is running!", "status": "active"},
        headers={"Access-Control-Allow-Origin": "*"}
    )

@app.options("/{path:path}")
async def options_handler(path: str):
    return JSONResponse(
        content={},
        headers={
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
            "Access-Control-Allow-Headers": "*",
        }
    )

@app.post("/analyze")
async def analyze_video(request: VideoRequest):
    """Get video information with ALL available qualities"""
    try:
        ydl_opts = get_ydl_opts()
        
        with youtube_dl.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(request.url, download=False)
            
            if not info:
                raise HTTPException(status_code=404, detail="Could not fetch video information")
            
            formats = []
            seen_qualities = set()
            
            for f in info.get('formats', []):
                height = f.get('height')
                format_note = f.get('format_note')
                format_id = f.get('format_id')
                fps = f.get('fps')
                
                # Check if format has watermark
                has_watermark = False
                if format_note and ('watermark' in format_note.lower() or 'watermarked' in format_note.lower()):
                    has_watermark = True
                
                # Determine quality label
                if height:
                    if height >= 2160:
                        quality_label = "4K (2160p)"
                    elif height >= 1440:
                        quality_label = "2K (1440p)"
                    elif height >= 1080:
                        quality_label = "1080p (Full HD)"
                    elif height >= 720:
                        quality_label = "720p (HD)"
                    elif height >= 480:
                        quality_label = "480p"
                    elif height >= 360:
                        quality_label = "360p"
                    elif height >= 240:
                        quality_label = "240p"
                    elif height >= 144:
                        quality_label = "144p"
                    else:
                        quality_label = f"{height}p"
                else:
                    quality_label = format_note or "Unknown"
                
                # Add fps for better quality indication
                if fps and fps >= 60 and height and height >= 1080:
                    quality_label += f" {fps}fps"
                
                # Add watermark indicator
                if has_watermark:
                    quality_label += " (with watermark)"
                else:
                    quality_label += " (no watermark)"
                
                # Skip formats without video
                if f.get('vcodec') == 'none':
                    continue
                
                # Get file size
                filesize = f.get('filesize')
                if filesize:
                    file_size_mb = round(filesize / (1024 * 1024), 1)
                    size_text = f"{file_size_mb} MB"
                else:
                    size_text = "Unknown"
                
                quality_key = f"{quality_label}_{has_watermark}_{height}"
                
                if quality_key not in seen_qualities:
                    seen_qualities.add(quality_key)
                    formats.append({
                        'format_id': format_id,
                        'quality': quality_label,
                        'height': height,
                        'size': size_text,
                        'has_watermark': has_watermark,
                        'fps': fps,
                    })
            
            # Sort formats by height (highest first) and prioritize no-watermark
            formats.sort(key=lambda x: (x.get('height') or 0, not x.get('has_watermark')), reverse=True)
            
            # Get best thumbnail
            thumbnail = info.get('thumbnail', '')
            if not thumbnail and info.get('thumbnails'):
                thumbnail = info['thumbnails'][-1]['url']
            
            return JSONResponse(
                content={
                    'title': info.get('title', 'Unknown'),
                    'thumbnail': thumbnail,
                    'duration': info.get('duration', 0),
                    'author': info.get('uploader', 'Unknown'),
                    'views': info.get('view_count', 0),
                    'formats': formats
                },
                headers={"Access-Control-Allow-Origin": "*"}
            )
            
    except Exception as e:
        error_msg = str(e)
        print(f"Error: {error_msg}")
        
        # Provide helpful error messages
        if "Sign in to confirm" in error_msg or "bot" in error_msg.lower():
            return JSONResponse(
                content={"detail": "YouTube is asking for verification. This is a temporary issue. Please try again in a few minutes or try a different video."},
                status_code=403,
                headers={"Access-Control-Allow-Origin": "*"}
            )
        elif "429" in error_msg or "rate" in error_msg.lower():
            return JSONResponse(
                content={"detail": "Too many requests. Please wait a moment and try again."},
                status_code=429,
                headers={"Access-Control-Allow-Origin": "*"}
            )
        else:
            return JSONResponse(
                content={"detail": error_msg},
                status_code=400,
                headers={"Access-Control-Allow-Origin": "*"}
            )

@app.post("/download")
async def download_video(request: VideoRequest):
    """Download the video in selected quality"""
    try:
        ydl_opts = get_ydl_opts(
            remove_watermark=request.remove_watermark,
            format_id=request.format_id
        )
        
        with youtube_dl.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(request.url, download=False)
            
            if not info:
                raise HTTPException(status_code=404, detail="Could not fetch video information")
            
            # Get video URL
            video_url = None
            video_info_height = 0
            
            if request.format_id and not request.remove_watermark:
                for f in info.get('formats', []):
                    if f.get('format_id') == request.format_id:
                        video_url = f.get('url')
                        break
            
            if not video_url:
                for f in info.get('formats', []):
                    if f.get('vcodec') != 'none':
                        if request.remove_watermark:
                            format_note = f.get('format_note', '')
                            if 'watermark' in format_note.lower() or 'watermarked' in format_note.lower():
                                continue
                        
                        if not video_url or (f.get('height') or 0) > video_info_height:
                            video_url = f.get('url')
                            video_info_height = f.get('height') or 0
            
            if not video_url:
                video_url = info.get('url')
            
            if not video_url:
                raise HTTPException(status_code=404, detail="No video URL found")
            
            # Download the video with timeout
            timeout = httpx.Timeout(60.0, connect=10.0)
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.get(video_url)
                
                if response.status_code != 200:
                    raise HTTPException(status_code=response.status_code, detail="Failed to download video")
                
                safe_title = re.sub(r'[^a-zA-Z0-9]', '_', info.get('title', 'video'))
                watermark_suffix = "_no_watermark" if request.remove_watermark else ""
                filename = f"{safe_title}{watermark_suffix}.mp4"
                
                return StreamingResponse(
                    io.BytesIO(response.content),
                    media_type="video/mp4",
                    headers={
                        "Content-Disposition": f"attachment; filename={filename}",
                        "Access-Control-Allow-Origin": "*",
                        "Content-Length": str(len(response.content)),
                    }
                )
                
    except Exception as e:
        error_msg = str(e)
        print(f"Download error: {error_msg}")
        
        if "Sign in to confirm" in error_msg or "bot" in error_msg.lower():
            return JSONResponse(
                content={"detail": "YouTube verification required. Please try again in a few minutes."},
                status_code=403,
                headers={"Access-Control-Allow-Origin": "*"}
            )
        elif "429" in error_msg:
            return JSONResponse(
                content={"detail": "Rate limited. Please wait and try again."},
                status_code=429,
                headers={"Access-Control-Allow-Origin": "*"}
            )
        else:
            return JSONResponse(
                content={"detail": error_msg},
                status_code=400,
                headers={"Access-Control-Allow-Origin": "*"}
            )

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)