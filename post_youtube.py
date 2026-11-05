"""Scheduled YouTube uploader (channel 2).

How it works
------------
Every run looks at the upcoming publish slots (default 09:00, 16:00, 18:00 IST for
the next 2 days). For each empty slot it takes the next unused video from your Drive
folder(s), uploads it as PRIVATE with a publish time, and YouTube itself makes it
public at exactly that time. GitHub can start late or skip a run and the posting
time does not move.

Long video vs Short is detected from the file: vertical/square and 3 minutes or less
= Short, anything else = normal video. (If Drive has not read the file's size yet, a
folder with "short" in its name is treated as Shorts.)
"""
import io
import json
import os
import re
import sys
import tempfile
from datetime import datetime, timedelta, timezone

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload

IST = timezone(timedelta(hours=5, minutes=30))  # India has no daylight saving
STATE_FILE = "state.json"
FOLDER_MIME = "application/vnd.google-apps.folder"
SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/drive.readonly",
]


def env(name, default=""):
    return os.environ.get(name) or default


def natural_key(text):
    """Sort 'Part 2' before 'Part 10' and '2.mp4' before '10.mp4'."""
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", text)]


# ---------- planning (pure functions, easy to test) ----------

def parse_plan(text):
    """'09:00=video,16:00=short,18:00=any' -> [(9, 0, 'video'), ...]"""
    plan = []
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        time_part, _, kind = part.partition("=")
        hh, mm = time_part.strip().split(":")
        kind = kind.strip().lower() or "any"
        if kind not in ("video", "short", "any"):
            raise ValueError(f"SLOT_PLAN type must be video, short or any (got '{kind}')")
        plan.append((int(hh), int(mm), kind))
    if not plan:
        raise ValueError("SLOT_PLAN is empty")
    return sorted(plan)


def upcoming_slots(now, plan, horizon_days, lead_minutes):
    """Publish slots (IST) between now+lead and now+horizon, oldest first."""
    earliest = now + timedelta(minutes=lead_minutes)
    cutoff = now + timedelta(days=horizon_days)
    today = now.astimezone(IST).date()
    slots = []
    for d in range(horizon_days + 2):
        day = today + timedelta(days=d)
        for hh, mm, kind in plan:
            dt = datetime(day.year, day.month, day.day, hh, mm, tzinfo=IST)
            if earliest < dt <= cutoff:
                slots.append((dt, kind))
    return sorted(slots)


def classify(f):
    meta = f.get("videoMediaMetadata") or {}
    w, h, dur = meta.get("width"), meta.get("height"), meta.get("durationMillis")
    if w and h and dur:
        seconds = int(dur) / 1000
        return "short" if (seconds <= 180 and int(h) >= int(w)) else "video"
    return "short" if "short" in f.get("folder", "").lower() else "video"


def pick(pending, kind):
    """Take the next video for a slot. Falls back to the other type if empty."""
    if kind == "any":
        pool = pending["video"] + pending["short"]
        if not pool:
            return None
        v = min(pool, key=lambda f: (natural_key(f["folder"]), natural_key(f["name"])))
    else:
        other = "short" if kind == "video" else "video"
        pool = pending[kind] or pending[other]
        if not pool:
            return None
        v = pool[0]
    pending[v["kind"]].remove(v)
    return v


def make_title(name, kind):
    fixed = env("VIDEO_TITLE")  # set this variable to use ONE title for every video
    base = fixed if fixed else re.sub(r"\.[A-Za-z0-9]{2,4}$", "", name)
    base = re.sub(r"[<>]", "", base if fixed else base.replace("_", " "))
    base = re.sub(r"\s+", " ", base).strip(" -")
    if not base or re.fullmatch(r"[\d\s.\-]+", base):
        base = env("DEFAULT_TITLE", "Funny AI Story")
    if kind == "short" and "#shorts" not in base.lower() and len(base) + 8 <= 100:
        base += " #Shorts"
    return base[:100]


# ---------- Google Drive ----------

def scan(drive, root_ids):
    """All videos under the given folders (sub-folders included)."""
    videos, seen = [], set()
    stack = []
    for rid in root_ids:
        name = drive.files().get(fileId=rid, fields="name", supportsAllDrives=True).execute()["name"]
        stack.append((rid, "/" + name))
    while stack:
        fid, path = stack.pop()
        if fid in seen:
            continue
        seen.add(fid)
        token = None
        while True:
            r = drive.files().list(
                q=f"'{fid}' in parents and trashed=false",
                fields="nextPageToken, files(id,name,mimeType,videoMediaMetadata(width,height,durationMillis))",
                pageSize=1000,
                pageToken=token,
                supportsAllDrives=True,
                includeItemsFromAllDrives=True,
            ).execute()
            for f in r["files"]:
                if f["mimeType"] == FOLDER_MIME:
                    stack.append((f["id"], f"{path}/{f['name']}"))
                elif f["mimeType"].startswith("video/"):
                    f["folder"] = path
                    f["kind"] = classify(f)
                    videos.append(f)
            token = r.get("nextPageToken")
            if not token:
                break
    videos.sort(key=lambda f: (natural_key(f["folder"]), natural_key(f["name"])))
    return videos


def download(drive, file_id, path):
    req = drive.files().get_media(fileId=file_id, supportsAllDrives=True)
    with io.FileIO(path, "wb") as fh:
        dl = MediaIoBaseDownload(fh, req, chunksize=16 * 1024 * 1024)
        done = False
        while not done:
            _, done = dl.next_chunk()


# ---------- state ----------

def load_state():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except FileNotFoundError:
        return {"used": [], "slots": {}}


def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


# ---------- YouTube ----------

def upload(yt, path, v, dt):
    title = make_title(v["name"], v["kind"])
    template = env(
        "DESCRIPTION_TEMPLATE",
        "{title}\n\n🔔 Subscribe to AI Storytelling Videos for new Hindi cartoon stories every day!\n"
        "👍 Like, share and tell us your favourite story in the comments.\n\n"
        "#HindiCartoon #HindiStories #FunnyCartoon #Storytelling #CartoonStories",
    )
    desc = template.replace("\\n", "\n").replace("{title}", title)
    if v["kind"] == "short" and "#shorts" not in desc.lower():
        desc += " #Shorts"
    tags = [t.strip() for t in env(
        "VIDEO_TAGS",
        "hindi cartoon,hindi stories,funny cartoon,storytelling,cartoon stories,hindi kahani,ai storytelling videos",
    ).split(",") if t.strip()]
    status = {
        "privacyStatus": "private",  # required for scheduled publishing
        "publishAt": dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "selfDeclaredMadeForKids": env("MADE_FOR_KIDS").lower() in ("1", "true", "yes"),
    }
    if env("CONTAINS_SYNTHETIC").lower() in ("1", "true", "yes"):
        status["containsSyntheticMedia"] = True  # "altered or synthetic content" label
    body = {
        "snippet": {"title": title, "description": desc, "tags": tags,
                    "categoryId": env("CATEGORY_ID", "1")},  # 1 = Film & Animation
        "status": status,
    }
    lang = env("VIDEO_LANGUAGE")  # e.g. "hi" for Hindi
    if lang:
        body["snippet"]["defaultLanguage"] = lang
        body["snippet"]["defaultAudioLanguage"] = lang
    media = MediaFileUpload(path, chunksize=8 * 1024 * 1024, resumable=True, mimetype="video/*")
    req = yt.videos().insert(part="snippet,status", body=body, media_body=media)
    resp = None
    while resp is None:
        _, resp = req.next_chunk()
    return resp["id"], title


def main():
    dry = env("DRY_RUN").lower() in ("1", "true", "yes")
    plan = parse_plan(env("SLOT_PLAN", "09:00=any,16:00=any,18:00=any"))
    horizon = int(env("HORIZON_DAYS", "2"))
    lead = int(env("MIN_LEAD_MINUTES", "20"))
    max_uploads = int(env("MAX_UPLOADS_PER_RUN", "3"))
    root_ids = [x.strip() for x in os.environ["DRIVE_FOLDER_IDS"].split(",") if x.strip()]

    yt_creds = Credentials(
        None,
        refresh_token=os.environ["YT_REFRESH_TOKEN"],
        token_uri="https://oauth2.googleapis.com/token",
        client_id=os.environ["YT_CLIENT_ID"],
        client_secret=os.environ["YT_CLIENT_SECRET"],
        scopes=SCOPES,
    )
    # The Drive folder can be shared with a different Google account than the one that
    # owns the YouTube channel. If so, set the DRIVE_REFRESH_TOKEN secret (optional).
    if env("DRIVE_REFRESH_TOKEN"):
        drive_creds = Credentials(
            None,
            refresh_token=os.environ["DRIVE_REFRESH_TOKEN"],
            token_uri="https://oauth2.googleapis.com/token",
            client_id=env("DRIVE_CLIENT_ID", os.environ["YT_CLIENT_ID"]),
            client_secret=env("DRIVE_CLIENT_SECRET", os.environ["YT_CLIENT_SECRET"]),
            scopes=SCOPES,
        )
    else:
        drive_creds = yt_creds
    drive = build("drive", "v3", credentials=drive_creds, cache_discovery=False)

    state = load_state()
    used = set(state["used"])
    videos = [v for v in scan(drive, root_ids) if v["id"] not in used]
    pending = {k: [v for v in videos if v["kind"] == k] for k in ("video", "short")}
    print(f"Unused in Drive: {len(pending['video'])} long videos, {len(pending['short'])} Shorts")

    now = datetime.now(timezone.utc)
    slots = [(dt, k) for dt, k in upcoming_slots(now, plan, horizon, lead)
             if dt.isoformat() not in state["slots"]]
    if not slots:
        print("All upcoming slots are already filled. Nothing to do.")
        return

    yt = None if dry else build("youtube", "v3", credentials=yt_creds, cache_discovery=False)
    done = 0
    for dt, kind in slots:
        if done >= max_uploads:
            print(f"Reached MAX_UPLOADS_PER_RUN ({max_uploads}); the next run continues.")
            break
        v = pick(pending, kind)
        if v is None:
            print("No unused videos left in Drive. Add more videos.")
            break
        when = dt.strftime("%a %d %b %H:%M IST")
        if dry:
            print(f"[dry run] {when}: would upload '{v['name']}' ({v['kind']}) from {v['folder']}")
            done += 1
            continue
        print(f"{when}: uploading '{v['name']}' ({v['kind']})")
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "video.bin")
            download(drive, v["id"], path)
            yt_id, title = upload(yt, path, v, dt)
        state["used"].append(v["id"])
        state["slots"][dt.isoformat()] = {"file": v["name"], "youtube_id": yt_id}
        save_state(state)  # saved after every upload so a later failure loses nothing
        print(f"  scheduled '{title}' -> https://youtu.be/{yt_id}")
        done += 1


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
