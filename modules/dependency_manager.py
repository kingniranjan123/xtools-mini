import os
import sys
import json
import shutil
import urllib.request
import zipfile
import subprocess
import threading
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEP_DIR = os.path.join(BASE_DIR, '_dependencies')

# Define paths
FFMPEG_DIR = os.path.join(DEP_DIR, 'ffmpeg')
FFMPEG_BIN_DIR = os.path.join(FFMPEG_DIR, 'bin')

# FFMPEG Github release URL (BtbN windows master build GPL)
FFMPEG_DL_URL = "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip"

def get_ffmpeg_version():
    """Returns the ffmpeg version string or None if not found."""
    try:
        out = subprocess.check_output(['ffmpeg', '-version'], stderr=subprocess.STDOUT, text=True)
        return out.splitlines()[0]
    except Exception:
        return None

def get_ytdlp_version():
    """Returns the yt-dlp version string or None if not found."""
    try:
        import yt_dlp
        return yt_dlp.version.__version__
    except ImportError:
        try:
            out = subprocess.check_output(['yt-dlp', '--version'], stderr=subprocess.STDOUT, text=True)
            return out.strip()
        except Exception:
            return None

def check_system_status():
    """Gather diagnostic info about system readiness."""
    import sqlite3
    db_path = os.path.join(BASE_DIR, 'reels_db.sqlite')
    db_size = os.path.getsize(db_path) if os.path.exists(db_path) else 0

    from modules.cuda_check import detect_cuda
    cuda_info = detect_cuda()

    ffmpeg_ver = get_ffmpeg_version()
    ytdlp_ver = get_ytdlp_version()

    return {
        'ffmpeg': {
            'installed': bool(ffmpeg_ver),
            'version': ffmpeg_ver,
            'is_local': os.path.isdir(FFMPEG_BIN_DIR) and bool(shutil.which('ffmpeg'))
        },
        'ytdlp': {
            'installed': bool(ytdlp_ver),
            'version': ytdlp_ver
        },
        'cuda': {
            'available': cuda_info['available'],
            'device': cuda_info.get('device', 'None'),
            'encoder': cuda_info.get('encoder', 'libx264')
        },
        'database': {
            'size_mb': round(db_size / (1024 * 1024), 2)
        }
    }

def init_runtime_path():
    """Inject local ffmpeg into os.environ['PATH'] so subprocesses find it natively."""
    if os.path.isdir(FFMPEG_BIN_DIR):
        current_path = os.environ.get('PATH', '')
        if FFMPEG_BIN_DIR not in current_path:
            os.environ['PATH'] = FFMPEG_BIN_DIR + os.pathsep + current_path
            print(f"[Dependency Manager] Added local FFmpeg to PATH: {FFMPEG_BIN_DIR}")


def install_ffmpeg_thread(callback):
    """Downloads and extracts ffmpeg strictly to _dependencies/ffmpeg/bin"""
    try:
        os.makedirs(DEP_DIR, exist_ok=True)
        zip_path = os.path.join(DEP_DIR, 'ffmpeg.zip')
        
        callback('status', 'Downloading FFmpeg from BtbN Releases...')
        urllib.request.urlretrieve(FFMPEG_DL_URL, zip_path)
        
        callback('status', 'Extracting FFmpeg...')
        
        # Cleanup old dir if exists
        if os.path.exists(FFMPEG_DIR):
            shutil.rmtree(FFMPEG_DIR, ignore_errors=True)
        os.makedirs(FFMPEG_DIR, exist_ok=True)
        
        with zipfile.ZipFile(zip_path, 'r') as zf:
            zf.extractall(DEP_DIR)
            
        # The zip extracts as 'ffmpeg-master-latest-win64-gpl'. Let's rename it.
        extracted_folder = os.path.join(DEP_DIR, "ffmpeg-master-latest-win64-gpl")
        if os.path.isdir(extracted_folder):
            shutil.move(extracted_folder, os.path.join(DEP_DIR, 'ffmpeg_temp'))
            shutil.move(os.path.join(DEP_DIR, 'ffmpeg_temp'), FFMPEG_DIR)
            
        # Delete zip
        try: os.remove(zip_path)
        except: pass
        
        # Refresh path
        init_runtime_path()
        
        callback('status', 'FFmpeg installed successfully!')
        callback('done', True)
        
    except Exception as e:
        callback('error', str(e))
        callback('done', False)

def upgrade_ytdlp_thread(callback):
    """Upgrades yt-dlp via pip"""
    try:
        callback('status', 'Upgrading yt-dlp via pip...')
        # Run pip install --upgrade yt-dlp
        cmd = [sys.executable, '-m', 'pip', 'install', '--upgrade', 'yt-dlp']
        result = subprocess.run(cmd, capture_output=True, text=True)
        
        if result.returncode == 0:
            callback('status', 'yt-dlp upgraded successfully!')
            callback('done', True)
        else:
            callback('error', result.stderr)
            callback('done', False)
    except Exception as e:
        callback('error', str(e))
        callback('done', False)
