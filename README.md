# YouTube channel 2: scheduled uploader (free)

Uploads your Drive videos to the new channel and makes YouTube publish them at
**9:00 AM, 4:00 PM and 6:00 PM IST** every day. Long videos and Shorts are told apart
automatically. GitHub only has to run once in a while; the posting time is set by YouTube.

## How it decides what to post
- Scans the Drive folder(s) and every sub-folder.
- **Short** = vertical or square and 3 minutes or less. Everything else = normal video.
- Default plan: **step by step, whatever comes next.** Each of the 3 daily slots (9:00 AM,
  4:00 PM, 6:00 PM) takes the next unused video in order (folder by folder, then by file name),
  whether it is a Short or a long video. To force types instead, set the `SLOT_PLAN` variable,
  e.g. `09:00=video,16:00=short,18:00=short`.
- Title = the file name, cleaned up (so name files like `The Robot Who Learned To Cook.mp4`).
- Always keeps the next 2 days of slots filled, then stops. Safe to run twice a day.

## One-time setup
1. **Channel:** done. Verify your phone at youtube.com/verify so you can upload videos
   longer than 15 minutes.
2. **New Google Cloud project** (separate from channel 1, so it has its own free daily quota):
   console.cloud.google.com → New project `yt-channel2` → enable **YouTube Data API v3**
   and **Google Drive API** → Google Auth Platform:
   - Branding: app name `yt-channel2`, your Gmail as support + contact email,
     home page `https://pradeepm-dev-ai.github.io/`,
     privacy policy `https://pradeepm-dev-ai.github.io/privacy.html`,
     authorised domain `pradeepm-dev-ai.github.io`
   - Audience: **Publish app** (so the login never expires)
   - Clients: Create client → **Desktop app** → download JSON as `client_secret.json`
3. **Get the token:** put `client_secret.json` next to `get_refresh_token.py`, then run
   `pip install google-auth-oauthlib` and `python get_refresh_token.py client_secret.json`.
   When Google asks which account/channel, **choose the NEW channel**. Tick both permissions.
   **If your Drive folder is shared with a different Gmail than the one that owns the channel:**
   run the script a second time, signing in with the Gmail that can open the folder, and keep
   that token as `DRIVE_REFRESH_TOKEN` (step 4). Use the same `client_secret.json` both times.
4. **GitHub:** new **private** repo `yt-channel2`. Upload `post_youtube.py`, `requirements.txt`,
   and create `.github/workflows/post.yml`. Do not upload `client_secret.json`.
   - Secrets: `DRIVE_FOLDER_IDS` (one or more folder IDs, comma separated),
     `YT_CLIENT_ID`, `YT_CLIENT_SECRET`, `YT_REFRESH_TOKEN`
     (only if Drive is on a different Gmail: also `DRIVE_CLIENT_ID`, `DRIVE_CLIENT_SECRET`
     = same values as the YT ones, and `DRIVE_REFRESH_TOKEN` from the second login)
   - Variables (all optional): `VIDEO_TITLE` (one title for every video; leave empty to use
     the file names), `DESCRIPTION_TEMPLATE` (use `{title}` where the title goes),
     `VIDEO_TAGS`, `VIDEO_LANGUAGE` (e.g. `hi`), `MADE_FOR_KIDS` (`true` or `false`),
     `SLOT_PLAN`, `CATEGORY_ID`, `DEFAULT_TITLE`, `CONTAINS_SYNTHETIC`
5. **Test:** Actions → *Schedule YouTube uploads (channel 2)* → **Run workflow** with
   *Dry run* ticked. The log lists what it would upload and when. Then run again with
   Dry run unticked. In YouTube Studio → Content you should see videos marked **Scheduled**.

## Good to know
- If a video shows as locked private with a notice about API verification, your API project is
  "unaudited". Apply via YouTube's "API Services audit and quota extension" form. Until then
  videos can't go public automatically.
- Realistic-looking AI people or events must carry YouTube's "altered or synthetic content"
  label. Set variable `CONTAINS_SYNTHETIC` to `true` if that applies to your videos.
- If an upload fails, the log shows the reason. A broken file would block the queue,
  so move it out of the Drive folder.
- Quota: 3 uploads a day uses about 4,800 of the free 10,000 daily units.
