# Photo → Reel Automation for @rolento_pro_photography

Turns curated still photos into 12-second, 1080x1920 branded Instagram Reels,
matching the exact edit style of the three sample clips you sent (branded
rest frame → push in → ease back out to a seamless loop), then auto-posts
them.

Pipeline: `Drive: 01_chosen_by_me` → GitHub Actions renders reels → `Drive:
02_Reels_made` → Make.com posts → `Drive: 03_Reels_Published`.

## 1. What's in this package

- `photo_to_reel.py` — the core renderer. Works standalone too:
  ```
  python3 photo_to_reel.py my_photo.jpg my_reel.mp4
  python3 photo_to_reel.py --batch photos_folder/ reels_folder/
  ```
- `assets/` — the brand overlay images (logo/url/city lockup), cropped
  directly from your three sample videos so the wordmark matches exactly.
  **These must stay in an `assets/` folder next to the script.** See
  section 3 for a caveat on their quality.
- `drive_pipeline.py` — watches a Drive folder for new photos, renders each
  with `photo_to_reel.py`, uploads the result to a second Drive folder.
  Skips photos it's already rendered, so it's safe to re-run on a schedule.
- `.github/workflows/reel_pipeline.yml` — runs `drive_pipeline.py` every 6
  hours (and on-demand from the Actions tab).
- `requirements.txt` — Python dependencies.

## 2. The edit, as built from your samples

I extracted frames every ~1s from all three of your sample clips and
matched the pacing exactly. Every reel now works like this:

1. **Opens on a branded rest frame.** The photo sits inset below (and for
   one category, between) white bars carrying your logo/url/cities text —
   never a bare full-bleed photo at rest.
2. **Pushes in**, cropping the white bars away as it scales up, eventually
   landing on a tight, full-bleed crop.
3. **Eases back out**, landing on the *exact* starting frame — so the clip
   loops with no visible seam when it repeats.

Which of three brand treatments a photo gets, and how the push/pull is
timed, depends on its aspect ratio (width ÷ height):

- **Portrait / near-square** (ratio ≤ 1.15): full 4-line logo block at top
  (wordmark + url + cities together), photo fills everything below it
  edge-to-edge (cropped to cover, never squeezed). Push-in is slow and
  continues to ~85% through the clip — ending on a tight crop around the
  subject — then snaps back out fast in the last ~10% for the loop reset.
- **Landscape** (1.15 < ratio ≤ 1.8): short 2-line wordmark at top, photo
  fit to the full width with nothing cropped, and a 2-line url/cities
  footer filling the leftover space at the bottom. Push-in peaks earlier
  (~40% through the clip) and eases back out more gradually.
- **Panorama** (ratio > 1.8): short 2-line wordmark at top only, photo
  cover-cropped (sides trimmed) to fill everything below it edge-to-edge.
  Same quicker-in/slower-out pacing as landscape.

I validated all three end-to-end — rendered a test reel per category,
confirmed 1080x1920/30fps/12s output, and checked frames at several points
through each clip to confirm the push-in reaches the expected peak and
the last frame matches the first.

## 3. One caveat: the logo asset quality

The images in `assets/` were cropped directly from the first frame of your
three sample *videos* — I don't have your original logo file. That means
the wordmark is only as sharp as a 1080p H.264 video frame allows: legible
and usable, but a little softer than a true vector/high-res source would
give you. If you have the original logo/watermark file (PNG with
transparency, or an Illustrator/vector export), send it over and I'll swap
it in for a crisper result — everything else in the pipeline stays the
same.

## 4. Performance note

Each 12-second reel currently takes **about a minute to render** (360
frames, each individually cropped and resized at 2x resolution for a sharp
push-in, then encoded). That's fine for a few photos per GitHub Actions
run, but if you're batching a lot of photos at once it adds up — let me
know if you want this sped up (there's room to optimize).

## 5. One-time setup

### A. Put this in a repo
Push this folder to a new GitHub repo (or a folder inside your existing
VideoAutoGen repo, if you'd rather keep one repo). Make sure `assets/`
comes along — the renderer will fail without it.

### B. Google Drive access for GitHub Actions
GitHub Actions needs its own way into Drive — a service account is the
simplest (no OAuth token refresh to babysit):

1. In Google Cloud Console (same project as VideoAutoGen, or a new one),
   go to **IAM & Admin → Service Accounts → Create Service Account**.
2. Give it any name (e.g. `reel-pipeline`), no roles needed at the project
   level.
3. Open the new service account → **Keys → Add Key → Create new key → JSON**.
   Download it — this is a one-time copy, same as your HeyGen/Google API
   key rule.
4. Enable the **Google Drive API** for that project if it isn't already.
5. In Google Drive, right-click both `01_chosen_by_me` and `02_Reels_made`
   → **Share** → paste the service account's email (ends in
   `...gserviceaccount.com`, found in the JSON key or the Cloud Console) →
   give it **Editor** access.
6. Get each folder's ID from its Drive URL:
   `drive.google.com/drive/folders/<THIS_IS_THE_ID>`

### C. GitHub repo secrets
In the repo → **Settings → Secrets and variables → Actions**, add:

| Secret name | Value |
|---|---|
| `GOOGLE_SERVICE_ACCOUNT_JSON` | paste the entire JSON key file contents |
| `DRIVE_FOLDER_SOURCE_ID` | folder ID of `01_chosen_by_me` |
| `DRIVE_FOLDER_OUTPUT_ID` | folder ID of `02_Reels_made` |

That's it — the workflow will start picking up new photos on its next
6-hourly run, or trigger it immediately from the **Actions** tab →
"Photo to Reel Pipeline" → **Run workflow**.

## 6. Posting to Instagram — Make.com scenario

**Heads-up:** your Make.com plan is Free tier, which caps you at **2 total
scenarios** — you're already using 1 for the VideoAutoGen posting scenario,
so you have exactly **1 slot left**. Build this as one new scenario (it can
still post to Instagram, Facebook, and YouTube in one go, same as your
existing scenario does — that doesn't cost extra slots).

Steps (in Make.com):

1. **Create scenario** → search and add these modules in order, matching
   the shape of your existing "Integration Google Drive, YouTube, Facebook
   Pages, Instagram for Business" scenario:
   - **Google Drive → Watch Files in a Folder**, folder = `02_Reels_made`.
     - Connection: you can reuse your existing farkbrotv Google Drive
       connection, or connect your new dedicated photography Google
       account instead — either works, just pick whichever Drive account
       actually holds the `Instagram_automation` folder structure.
   - **Google Drive → Get a File** (to fetch the file content/URL for the
     next steps), same pattern as your existing scenario.
   - **Instagram for Business → Create a Reel Post** — pick the Page/IG
     account for `@rolento_pro_photography`. You can reuse your existing
     Facebook connection ("Rolento Ong") as long as that Facebook login
     has admin access to the Page linked to `@rolento_pro_photography`'s
     professional account.
   - *(Optional, to match your original plan of posting everywhere)*:
     **Facebook Pages → Upload Video** and **YouTube → Upload Video**,
     same as the existing scenario — just point them at the photography
     Page/channel instead.
   - **Google Drive → Move a File** — move the just-posted file from
     `02_Reels_made` to `03_Reels_Published` so it isn't posted twice.
2. **Scheduling**: your existing scenario runs Mon/Wed/Fri at 9:00 AM
   Singapore time — set the same here, or pick your own cadence.
3. Turn the scenario **ON**.

Once both pieces are running: drop curated photos into `01_chosen_by_me` →
GitHub Actions renders them into branded reels (portrait/landscape/panorama
each with matching push-in/pull-out pacing) → Make.com posts them and
archives them.

## 7. Notes

- Reels are silent (a silent audio track is added so Instagram doesn't
  reject a video with no audio at all) — add music afterward in Make (an
  audio-overlay module) or directly in Instagram if you want a soundtrack.
- One photo → one reel by default.
- The aspect-ratio thresholds (1.15 and 1.8) that decide portrait vs.
  landscape vs. panorama treatment are easy to tune in `photo_to_reel.py`
  (`PORTRAIT_MAX_RATIO` / `PANORAMA_MIN_RATIO`) if a particular photo gets
  bucketed differently than you'd like.
