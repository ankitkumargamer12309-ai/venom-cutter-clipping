# VENOM CUTTER CLIPPING

[![Deploy to Render](https://render.com/images/deploy-to-render-button.svg)](https://render.com/deploy?repo=https://github.com/ankitkumargamer12309-ai/venom-cutter-clipping)

GitHub Pages frontend: https://ankitkumargamer12309-ai.github.io/venom-cutter-clipping/

## Project layout

- `index.html`, `login.html`, `clipping.html`: existing frontend pages.
- `backend-config.js`: backend base URL used by the clipping page.
- `api.py`: upload API and FFmpeg clip exporter.
- `Dockerfile`, `render.yaml`: cloud deployment configuration.

## Deploy the backend

1. In Render, create a new Blueprint and select this GitHub repository.
2. Review the `venom-cutter-api` service settings and deploy it.
3. After deployment, open `https://YOUR-SERVICE.onrender.com/api/health`. It should return JSON with `"ok": true`.
4. Edit `backend-config.js` in GitHub and set `window.VENOM_API_BASE` to the service's HTTPS base URL (no trailing slash). Save the change.
5. Wait for GitHub Pages to publish, then test with a small video.

The backend permits the GitHub Pages origin configured in `render.yaml`. For a different website address, update `ALLOWED_ORIGINS` in the Render service environment.

## Current backend scope

This first hosted backend accepts MP4, MOV, MKV, and WEBM uploads up to 100 MB and 10 minutes. It uses FFmpeg scene-change detection to suggest and export clips in 9:16, 16:9, or 1:1.

Automatic speech subtitles, semantic AI highlight ranking, facecam detection, Google sign-in, public user accounts, and permanent clip storage are **not enabled** in this starter backend. Generated clips are temporary and are automatically removed after about 45 minutes. The public service limits requests and processes one video at a time.

## Local run

Install FFmpeg, Python 3.11+, and the packages in `requirements.txt`, then run:

```bash
python -m pip install -r requirements.txt
python api.py
```

The API listens on `http://127.0.0.1:5001`.
