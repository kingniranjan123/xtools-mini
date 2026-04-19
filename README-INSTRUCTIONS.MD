# Nikethan Tools (xtools-mini) — Instructions & Prerequisites

This toolkit is designed for parallel YouTube downloading, Reel conversion, and Instagram metadata management. Please follow these prerequisites and instructions to ensure smooth operation on any laptop.

## 1. Software Prerequisites

Before running the application, ensure the following are installed:

*   **Python 3.10 or higher**: [Download from python.org](https://www.python.org/downloads/).
    *   *Note: During installation on Windows, make sure to check "Add Python to PATH".*
*   **FFmpeg**: This is required for merging high-quality video and audio.
    *   If you don't have FFmpeg installed on your system PATH, ensure there is an `ffmpeg` folder (containing `ffmpeg.exe`) inside the project root directory. The app will automatically detect it.

## 2. Browser & Cookie Prerequisites (Crucial for YouTube)

To bypass YouTube's "Sign in to confirm you are not a bot" restrictions, the software uses your browser's active login session.

*   **Action Required**: You **MUST** open your preferred web browser (Chrome, Edge, Firefox, or Brave) and log into your YouTube/Google account.
*   **Keep Browser Prepared**: You do not need to keep the browser open while downloading, but you must have logged in at least once so the cookies are saved locally.
*   **Selecting in UI**: In the YouTube Downloader or Reel Converter tabs, use the **"Bypass Restrictions (Cookies)"** dropdown to select the browser where you are logged in.

## 3. Installation & First Run

1.  **Download/Clone**: Download this repository or extract the ZIP file to your laptop.
2.  **Initialize**: Run the `start.bat` file. 
    *   On the first run, this will automatically install all necessary Python libraries (yt-dlp, Flask, etc.).
    *   It will then start the local server.
3.  **Open App**: Once the console says the server is running, open your browser and go to: `http://127.0.0.1:5000`

## 4. Moving to a New Laptop

If you are setting this up on a different machine:

1.  Clone the repository from GitHub.
2.  **Login to your browser** (Chrome/Edge/etc.) on the new machine with your YouTube account.
3.  (Optional) If you want to keep your download history, copy the `reels_db.sqlite` file from your old laptop to the new project folder.
4.  Run `start.bat`.

## 5. Troubleshooting "Bot Detection"

If you still see "Sign in to confirm you are not a bot" even with the browser selected:
1.  Open YouTube in that browser and watch any 5-second video to refresh the session.
2.  Ensure you aren't using an "Incognito" or "Guest" window.
3.  Ensure the browser isn't "Managed by your Organization" in a way that blocks cookie access.

---
*Created by Nikethan AI Agent - 2026*
