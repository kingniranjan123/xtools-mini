import threading
import time
import os
import random
from instagrapi import Client
from modules.db import get_db

def run_poster_daemon(app_context_fetcher):
    """
    Background Daemon that wakes up every X minutes,
    checks if poster is enabled, and publishes the oldest unpublished
    processed reel using instagrapi.
    app_context_fetcher should be a callable returning a db connection and settings dict.
    """
    while True:
        try:
            db, settings = app_context_fetcher()
            
            enabled = settings.get('poster_enabled') == '1'
            if not enabled:
                time.sleep(60)
                continue
                
            interval_mins = int(settings.get('poster_interval', '10'))
            
            # Check for unpublished reels
            # We look for reels that are fully downloaded (status='ok')
            # and haven't been posted yet (create a posted flag / or just use a timestamp)
            row = db.execute("SELECT * FROM reels WHERE status='ok' AND is_posted=0 ORDER BY created_at ASC LIMIT 1").fetchone()
            
            if row:
                file_path = row['file_path']
                if file_path and os.path.exists(file_path):
                    user = settings.get('publisher_user')
                    pwd = settings.get('publisher_pass')
                    
                    if user and pwd:
                        print(f"[Poster Daemon] Authenticating as {user}...")
                        client = Client()
                        # Optional: Use a proxy or specific settings here if needed
                        client.login(user, pwd)
                        
                        desc = settings.get('poster_desc', '')
                        tags = settings.get('poster_tags', '')
                        caption = f"{desc}\n\n{tags}".strip()
                        
                        print(f"[Poster Daemon] Uploading {file_path} to Instagram...")
                        media = client.clip_upload(file_path, caption)
                        
                        if media:
                            print(f"[Poster Daemon] Successfully posted {row['id']}")
                            # Mark as posted
                            db.execute("UPDATE reels SET is_posted=1 WHERE id=?", (row['id'],))
                            db.commit()
                        else:
                            print(f"[Poster Daemon] Upload failed for {row['id']}")
                else:
                    # File missing, mark as posted/skipped to avoid infinite loops
                    db.execute("UPDATE reels SET is_posted=1 WHERE id=?", (row['id'],))
                    db.commit()

        except Exception as e:
            print(f"[Poster Daemon] Error: {e}")
            
        # Sleep for the configured interval
        # Using a fallback of 10 if parsing fails
        sleep_secs = interval_mins * 60
        time.sleep(sleep_secs)
