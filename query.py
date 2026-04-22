import sqlite3
import json

db = sqlite3.connect('reels_db.sqlite')
rows = db.execute("SELECT id, title, file_path FROM reels").fetchall()

output = []
for r in rows:
    # check for terms
    t = (r[1] or '').lower()
    if 'dancing princess' in t or 'gun crazy' in t or 'happy marriage' in t:
        output.append({
            'id': r[0],
            'title': r[1],
            'file_path': r[2]
        })

print(json.dumps(output, indent=2))
db.close()
