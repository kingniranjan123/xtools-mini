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
            # 3. Perform an actual test encode to verify NVENC is functioning
            try:
                subprocess.check_call(
                    ['ffmpeg', '-y', '-hwaccel', 'cuda', '-f', 'lavfi', '-i', 'color=c=black:s=128x128:d=1', '-c:v', 'h264_nvenc', '-f', 'null', '-'],
                    stderr=subprocess.DEVNULL, stdout=subprocess.DEVNULL, timeout=5
                )
                result['available'] = True
                result['encoder']   = 'h264_nvenc'
            except Exception:
                # NVENC failed to encode (driver issue or missing dependencies)
                result['available'] = False
                result['encoder'] = 'libx264'
                result['note'] = 'GPU detected but test encode failed. Check NVIDIA drivers.'
    except Exception:
        pass

    _cache = result
    return _cache
