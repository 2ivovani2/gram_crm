# Gramly Welcome tutorials

Five short product tutorials rendered with the official Remotion toolchain.
The compositions use the same labels and visual language as the Mini App and
produce the MP4 and poster assets served by the public Django application.

```bash
cd tools/welcome-videos
npm ci
npm run studio
npm run render
```

`public/cubigator-wave.webp` is the greeting sticker from the public Telegram
set [Cubigator](https://t.me/addstickers/crocosaurus). `build_avatar.py` takes a
recognisable frame from that original asset and builds the owner-bot profile
image without redrawing the mascot.

Rendered assets are written to `services/crm/static/learning`. The database
migration publishes their canonical `https://gramly.tech/static/learning/...`
URLs for the Mini App; the landing uses the same files through Django static.
