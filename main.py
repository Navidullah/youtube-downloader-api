from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
import yt_dlp as youtube_dl
import io
import re
import httpx

app = FastAPI(title="YouTube Downloader API")

# Allow your website to access this API
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class VideoRequest(BaseModel):
    url: str
    format_id: str = None
    remove_watermark: bool = False  # New option

@app.get("/")
def root():
    return {"message": "YouTube Downloader API is running!"}

@app.post("/analyze")
async def analyze_video(request: VideoRequest):
    """Get video information with ALL available qualities"""
    try:
        ydl_opts = {
            'quiet': True,
            'no_warnings': True,
        }
        
        with youtube_dl.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(request.url, download=False)
            
            # Collect ALL video formats
            formats = []
            seen_qualities = set()
            
            for f in info.get('formats', []):
                # Get video quality info
                height = f.get('height')
                width = f.get('width')
                fps = f.get('fps')
                format_note = f.get('format_note')
                format_id = f.get('format_id')
                
                # Check if format has watermark (based on format_note)
                has_watermark = False
                if format_note and ('watermark' in format_note.lower() or 'watermarked' in format_note.lower()):
                    has_watermark = True
                
                # Determine quality label
                if height:
                    quality_label = f"{height}p"
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
                
                # Add watermark indicator
                if has_watermark:
                    quality_label += " (with watermark)"
                else:
                    quality_label += " (no watermark)"
                
                # Check if it has both video and audio
                has_video = f.get('vcodec') != 'none'
                has_audio = f.get('acodec') != 'none'
                
                # Skip formats without video
                if not has_video:
                    continue
                
                # Get file size
                filesize = f.get('filesize')
                if filesize:
                    file_size_mb = round(filesize / (1024 * 1024), 1)
                    size_text = f"{file_size_mb} MB"
                else:
                    size_text = "Unknown"
                
                # Create quality key for deduplication
                quality_key = f"{quality_label}_{has_watermark}"
                
                if quality_key not in seen_qualities:
                    seen_qualities.add(quality_key)
                    
                    formats.append({
                        'format_id': format_id,
                        'quality': quality_label,
                        'height': height,
                        'width': width,
                        'fps': fps,
                        'size': size_text,
                        'has_audio': has_audio,
                        'has_watermark': has_watermark,
                        'ext': f.get('ext', 'mp4')
                    })
            
            # Sort formats by height (highest first) and prioritize no-watermark
            formats.sort(key=lambda x: (x.get('height') or 0, not x.get('has_watermark')), reverse=True)
            
            # Get best thumbnail
            thumbnail = info.get('thumbnail', '')
            if not thumbnail and info.get('thumbnails'):
                thumbnail = info['thumbnails'][-1]['url']
            
            return {
                'title': info.get('title', 'Unknown'),
                'thumbnail': thumbnail,
                'duration': info.get('duration', 0),
                'author': info.get('uploader', 'Unknown'),
                'views': info.get('view_count', 0),
                'formats': formats
            }
            
    except Exception as e:
        print(f"Error: {e}")
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/download")
async def download_video(request: VideoRequest):
    """Download the video in selected quality with option to avoid watermarks"""
    try:
        # Build format selector based on user preference
        if request.remove_watermark:
            # Try to get formats without watermark first
            format_selector = 'bestvideo[format_note!~="watermarked"][ext=mp4]+bestaudio[ext=m4a]/bestvideo[ext=mp4]+bestaudio/best[ext=mp4]/best'
        elif request.format_id:
            format_selector = request.format_id
        else:
            format_selector = 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best'
        
        ydl_opts = {
            'format': format_selector,
            'quiet': True,
            'no_warnings': True,
        }
        
        with youtube_dl.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(request.url, download=False)
            
            # Get the video URL
            video_url = None
            
            if request.format_id and not request.remove_watermark:
                # Find the specific format
                for f in info.get('formats', []):
                    if f.get('format_id') == request.format_id:
                        video_url = f.get('url')
                        break
            
            if not video_url:
                # Get best quality URL
                for f in info.get('formats', []):
                    if f.get('vcodec') != 'none':
                        # If removing watermark, skip watermarked formats
                        if request.remove_watermark:
                            format_note = f.get('format_note', '')
                            if 'watermark' in format_note.lower() or 'watermarked' in format_note.lower():
                                continue
                        
                        if not video_url or (f.get('height') or 0) > (video_info_height or 0):
                            video_url = f.get('url')
                            video_info_height = f.get('height')
            
            if not video_url:
                video_url = info.get('url')
            
            if not video_url:
                raise HTTPException(status_code=404, detail="No video URL found")
            
            # Download the video
            async with httpx.AsyncClient() as client:
                response = await client.get(video_url)
                
                # Create filename
                safe_title = re.sub(r'[^a-zA-Z0-9]', '_', info.get('title', 'video'))
                watermark_suffix = "_no_watermark" if request.remove_watermark else ""
                filename = f"{safe_title}{watermark_suffix}.mp4"
                
                return StreamingResponse(
                    io.BytesIO(response.content),
                    media_type="video/mp4",
                    headers={"Content-Disposition": f"attachment; filename={filename}"}
                )
                
    except Exception as e:
        print(f"Download error: {e}")
        raise HTTPException(status_code=400, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)