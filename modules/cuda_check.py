"""CUDA availability probe"""
import subprocess, shutil

_cache = None

def detect_cuda() -> dict:
    global _cache
    if _cache is not None:
        return _cache

    result = {'available': False, 'device': None, 'encoder': 'libx264'}

    # 1. Check nvidia-smi
    if shutil.which('nvidia-smi'):
        try:
            out = subprocess.check_output(
                ['nvidia-smi', '--query-gpu=name', '--format=csv,noheader'],
                stderr=subprocess.DEVNULL, text=True, timeout=5
            ).strip()
            if out:
                result['device'] = out.splitlines()[0]
            else:
                _cache = result
                return _cache
        except Exception:
            _cache = result
            return _cache
    else:
        _cache = result
        return _cache

    # 2. Check if ffmpeg has h264_nvenc
    if not shutil.which('ffmpeg'):
        _cache = result
        return _cache

    try:
        out = subprocess.check_output(
            ['ffmpeg', '-encoders'],
            stderr=subprocess.STDOUT, text=True, timeout=10
        )
        if 'h264_nvenc' in out:
            result['available'] = True
            result['encoder']   = 'h264_nvenc'
    except Exception:
        pass

    _cache = result
    return _cache
