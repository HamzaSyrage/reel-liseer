# Cookies for downloads (admin only)

Instagram, YouTube, TikTok etc. block anonymous requests from datacenter IPs (Render included).
yt-dlp needs a logged-in browser session's cookies. They are stored **on the server only**, uploaded by
you through `/admin`; bot users never send or see cookies.

## 1. Use a throwaway account
A cookie file is a login. Create a separate Instagram/YouTube/TikTok account for the bot and export
cookies from that one. Never use your personal account.

## 2. Export cookies (Netscape `cookies.txt`)
1. Open a **private/incognito window** (important for YouTube: cookies from a normal tab get rotated
   and stop working within hours).
2. Log in to the site with the throwaway account. For YouTube, then open
   `https://www.youtube.com/robots.txt` in the same tab and leave that as the only tab.
3. Install an exporter extension and allow it in incognito:
   - Chrome/Edge: **Get cookies.txt LOCALLY**
   - Firefox: **cookies.txt**
4. Export:
   - **Per site (recommended):** on the site's tab choose *Export* for the current site → save as
     `instagram.txt`, `youtube.txt`, `tiktok.txt`, ...
   - **Universal:** use *Export all cookies* from the incognito window → one `cookies.txt`.
     The server keeps only the allowed domains (`COOKIE_DOMAINS`: youtube, google, instagram,
     facebook, tiktok, twitter/x, reddit) and discards everything else before saving.
5. Close the incognito window so the session isn't rotated.

Check the file starts with `# Netscape HTTP Cookie File` and has tab-separated lines.

## 3. Upload
1. On Render set `ADMIN_USERNAME` (default `admin`) and `ADMIN_PASSWORD` (long random: `openssl rand -base64 32`).
2. Open `https://<your-service>.onrender.com/admin` and log in with them.
3. Pick a label (`universal` or the site), choose the file (or paste its text), **Upload**.
4. Paste a public reel/video URL under **Test a link** to confirm it works.

Re-uploading a label replaces it. Delete removes it from the server.

CLI alternative:
```bash
curl -u "$ADMIN_USERNAME:$ADMIN_PASSWORD" -F slot=instagram -F file=@instagram.txt \
  https://<your-service>.onrender.com/admin/api/cookies
```

## 4. Persistence on Render
Render's free filesystem is wiped on every deploy/restart/spin-down. Pick one:
- **Persistent disk** (paid): mount at `/var/data`, set `COOKIES_DIR=/var/data/cookies`.
- **No disk:** `base64 -w0 cookies.txt` → put it in env var `COOKIES_B64`. It is loaded at startup when
  nothing is stored. (Uploads via `/admin` still work but last until the next restart.)

## 5. Maintenance
Cookies expire or get invalidated; when downloads start failing again with "login required"/"empty media
response", export fresh ones and re-upload. YouTube also needs a JS runtime: the `deno` package in
requirements provides it (the admin page shows which runtime was found).
