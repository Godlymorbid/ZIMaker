# ZIMaker

Turns websites, archive.org books, ebooks, documents, emails, pictures, audio and video into ZIM files for Kiwix, and opens ZIM files so you can read and search them. It runs on **Windows** and **Linux**.

## Full Transparency
Yes, AI was used in the creation of this app. No, I do not care if you hate that. This was created for my own use at home, and for my wife to have a drag and drop tool to gather tools for home education. If you dislike it, please make your own version, and send it my way. I am in no way emotionally attached to this project.

## Install it (or share it)

**Download:** on the [GitHub page](https://github.com/Godlymorbid/ZIMaker), click the green **Code** button, then **Download ZIP**. You get `ZIMaker-main.zip`.

### Windows

1. Unzip the downloaded zip: right-click it and choose **Extract All…**. Don't run the installer from inside the zip; it needs the whole folder.
2. In the extracted folder, double-click **`Install ZIMaker.cmd`**.

The installer:

- checks for WSL (Windows Subsystem for Linux), and offers to turn it on if it's off;
- picks the Linux distribution ZIMaker runs in. If you already have some, it lists the ones that can run it (Ubuntu or Debian work best; Docker's internal one and Alpine can't) and asks which to use, or offers to install a separate Ubuntu next to them. Your default distribution stays the same. If there's none, it offers to install Ubuntu for your account. The choice is remembered (in `distro.txt` in the install folder), so updates don't ask again;
- installs ZIMaker for the current Windows user in `%LOCALAPPDATA%\Programs\ZIMaker` (no administrator rights needed);
- downloads the libraries it needs, about 150 MB;
- adds a **ZIMaker** icon to the desktop and to the Start menu.

Turning on WSL needs administrator approval and a restart. After the restart, run the installer again: it installs Ubuntu (no administrator needed) and asks you to pick a Linux username and password. When you see the Linux prompt, type `exit` and the installer carries on.

ZIMaker updates itself (see **Updates** below). You can also update by running a newer copy's installer over the old one; that also replaces an older install called "ZIM Creator", keeping its job history. To remove ZIMaker, run `Uninstall ZIMaker.cmd` in the install folder. Uninstalling never touches the ZIM files you made. It asks before each of these:

- removing its libraries and job logs inside WSL (Yes by default);
- removing the Linux distribution ZIMaker ran in (No by default). If ZIMaker installed it, answer y; if you had it before ZIMaker, you have to type its name, since this deletes everything in it;
- removing WSL itself from Windows (No by default). This is offered only when no Linux distributions are left (so Docker Desktop's, for example, keeps it), and needs administrator approval and a restart.

Windows may warn about running scripts from a downloaded zip ("Windows protected your PC"). Click **More info → Run anyway**.

Requirements: Windows 10 or 11 with WSL 2. It works on Intel/AMD and ARM PCs. Microsoft Edge is needed for the app window; without it, the app opens in your default browser.

### Linux

1. Unzip the downloaded zip, or clone the repository: `git clone https://github.com/Godlymorbid/ZIMaker`
2. In a terminal, in that folder, run: `sh install.sh`

It installs into your home folder, so it doesn't need root. It downloads the libraries (about 150 MB) and adds **ZIMaker** to your applications menu, a desktop icon where your desktop supports one, and a `zimaker` command.

To remove it, run `sh ~/.local/share/zimaker/uninstall.sh`.

Requirements: Python 3.9 or newer, which most distributions include, on a 64-bit Intel/AMD or ARM PC. If Chrome, Chromium, Edge or Brave is installed, the app opens in its own window; otherwise it opens in your default browser.

## Start it

- **Windows:** double-click the **ZIMaker** icon on the desktop or in the Start menu.
  - To recreate the desktop icon, run `Create desktop shortcut.cmd`.
  - You can also double-click `ZIMaker.vbs` in the install folder.
- **Linux:** open **ZIMaker** from your applications menu, or run `zimaker`.

**Stopping:** close the window, and ZIMaker stops by itself about 2–3 minutes later. If a conversion is still running, it finishes the conversion first and then stops. The **Quit** button stops it straight away; it asks first if a conversion is running.

## Settings

Click **⚙ Settings** in the top corner.

- **Appearance:** System, Light or Dark. System follows your computer's setting. **Accent color:** pick a swatch or any color with the color picker; button text switches between white and black so it stays readable.
- **Optional tools:** shows whether FFmpeg, 7-Zip and LibreOffice are installed, and the one command that installs the missing ones (see **Optional tools** below).
- **Updates:** turn the automatic update check on or off, or click **Check for updates now**.
- **Profiles:** named sets of defaults. Examples: "Websites" (just the linked page, text only) and "Scanned books" (only pages with pictures, sharper images). Create, duplicate, rename and delete them. Each profile sets:
  - page images
  - one file or combined
  - save folder
  - website page limit and scope
  - **website audio and video:** off by default; when on, playable audio and video on the site (up to 500 MB each) are saved too
  - **image quality:** smaller files (1400 px), balanced (1800 px) or sharper (2400 px). It also sets video quality: 480p, 720p or 1080p
  - **picture detection:** catch more pictures, balanced, or fewer false alarms
- **Connection and privacy:** a proxy for all downloads, private mode, neutral file names and **Clear history now** (see **Privacy and censorship** below).

On the main screen, pick a profile from **Profile** to fill in all the options at once. You can still change options for a single job, or click **Save options to profile** to keep them. Settings are stored in `~/.zimaker/settings.json`, so they survive updates and reinstalls.

## Updates

When it opens, ZIMaker checks this GitHub repository for a newer version, at most every 6 hours. If there is one, a banner offers **Update now**: ZIMaker downloads the latest version, reinstalls it in the background, and the window refreshes by itself in about a minute. It won't update while a conversion is running. To check yourself, go to **⚙ Settings → Updates → Check for updates now**.

The check compares each installed file with the repository, so any change pushed to GitHub counts as an update. No version numbers to bump. The repository it checks is set in `app/update.json`; if you fork ZIMaker, change it there.

If an update doesn't finish, close the window and open ZIMaker again. The log is in `%TEMP%\ZIMaker-update.log` on Windows and `~/.zimaker/update.log` on Linux.

## Troubleshooting

- **Windows:** `Start ZIMaker (with console).cmd` runs the same app with a visible console that shows any errors.
- **Linux:** errors are logged to `~/.zimaker/server.log`.

## Use it

1. **What to convert**
   - **Drag and drop** files, or links dragged from your browser's address bar or a page, anywhere onto the window.
   - Or paste **web addresses**, one per line: any website, or archive.org books. Click **Check links** to see what each one is.
   - Or click **Choose files…**. For a very large file, paste its path under "Use its path instead" so it isn't copied.
   - You can mix websites, books and files in one job.
2. **Page images**
   - **Only pages with pictures** (the default): for scanned books, every page is text, and the original scan is shown only on pages with an illustration, map, diagram or plate.
   - **Every page as an image**: the scan of every page, with its text underneath.
   - **Text only**: no images anywhere.
3. **Websites**: how many pages to save at most (300 by default), and whether to save the whole site or only the section you linked to.
4. **Output**: one ZIM per book, document or site, or **combine** everything into one ZIM with a home page and search across all of it.
5. Optionally set a **Title**, choose the **Save to folder** (a Proton Drive folder works), and click **Build ZIM**.

Jobs run one at a time. The Jobs list shows progress, a log, and **Preview** and **Open folder** buttons when a job is done. Every file is checked with openZIM's `zimcheck` before it's saved.

## Open and preview ZIM files

**Your ZIM files** lists the ZIMs you made and the ones you opened before. To open any other ZIM, drop it onto the window, or paste its path and click **Open**. Each one opens in a viewer window with:

- Back, Forward and Home buttons;
- title suggestions as you type in the search box;
- full-text search (press Enter or click **Search**);
- playable audio and video, with seeking.

The viewer shows the ZIM's pages without running their scripts, so a ZIM from anywhere can't reach ZIMaker or your files. Pages that need JavaScript to work look better in Kiwix.

## What it accepts

| Kind | Formats | What you get |
|---|---|---|
| Websites | any http/https address | the site's pages with their styles, scripts, fonts and images; a list of all saved pages |
| archive.org books | item links or IDs | OCR text of every page, with printed page numbers and running heads; scans of illustrated pages |
| Ebooks | EPUB, MOBI, AZW, AZW3, FB2 | the book's own chapters, styles and images, plus a contents page and Previous/Next links |
| Documents | DOCX, ODT, RTF, PPTX, XLSX, CSV, TSV, Markdown, HTML, Jupyter notebooks, LaTeX, reStructuredText, Org, AsciiDoc, Typst, MediaWiki, Textile, txt2tags, Muse, POD, Creole, Djot | converted with Pandoc: split into chapters at headings, with a contents page and the document's pictures |
| Older and other office files ¹ | DOC, XLS, PPT, PPS, WPD, WPS, ABW, HWP, ODS, ODP, ODG, Visio, Publisher, StarOffice and macro/template variants | converted with LibreOffice first, then as above (slides and drawings become pages) |
| Saved web pages | MHT, MHTML | the page with its pictures and styles |
| Email | EML, MBOX | one page per message (newest first), with its pictures; other attachments are listed. Scripts and remote images are removed |
| Plain text | TXT, LOG, NFO | split into chapters (or parts of about 4,000 words) |
| Page-based | PDF, XPS, CBZ comics | the text of each page; page images for illustrated pages (comics and scans without text keep every page as an image) |
| Pictures | JPG, PNG, GIF, WEBP, TIFF, BMP, SVG, HEIC/HEIF, AVIF, ICO, TGA, PSD, JPEG 2000, PCX, PPM | one gallery for all the pictures in a job, a picture per page |
| Video ¹ | MP4, MKV, MOV, AVI, WEBM, WMV, FLV, MPG, TS, 3GP, OGV, VOB and more | WebM (VP9 + Opus) with a poster image, a page per video; `.srt`/`.vtt` subtitles with the same name are added |
| Audio ¹ | MP3, M4A, AAC, WAV, FLAC, OGG, OPUS, WMA, AIFF, M4B and more | MP3 (MP3s are kept as they are), with cover art when the file has it |
| Archives | ZIP, TAR, TAR.GZ, TAR.XZ, TAR.BZ2; 7Z, RAR, CBR, CB7 ¹ | a saved website folder becomes a website; a CHM-style help folder becomes a site; a folder of pictures becomes a gallery; anything else is converted file by file, including archives inside archives |
| Help files ¹ | CHM | the help pages as a website |

¹ Needs an optional tool (below).

**Not supported:**

- DjVu: convert to PDF first.
- Apple iWork files (Pages, Keynote, Numbers): export them first.
- WARC web archives.
- ZIM files: these open in the viewer instead.

The app says why when you add one of these.

## Optional tools

Video, audio, 7Z/RAR/CBR and CHM, and old Office files use free tools from your Linux distribution, which ZIMaker doesn't download itself:

| Tool | For |
|---|---|
| FFmpeg | audio and video |
| 7-Zip (and unar) | 7Z, RAR, CBR, CB7, CHM |
| LibreOffice (no-GUI parts) | DOC, XLS, PPT and other office formats |

Install them once (about 400 MB in all):

- **Windows**, in PowerShell: `wsl -u root -- sh -c "apt-get update && apt-get install -y ffmpeg 7zip unar libreoffice-writer-nogui libreoffice-calc-nogui libreoffice-impress-nogui libreoffice-draw-nogui"`
- **Linux** (Debian/Ubuntu): `sudo apt install ffmpeg 7zip unar libreoffice-writer-nogui libreoffice-calc-nogui libreoffice-impress-nogui libreoffice-draw-nogui`

**⚙ Settings → Optional tools** shows which are installed and the exact command for your system. If you add a file that needs a missing tool, the job says which one and how to install it.

## Privacy and censorship

ZIMaker can help where websites are blocked or reading them is risky: download once, then read offline, with no repeated visits that could be noticed. But no tool makes this safe on its own. In many places the bigger danger is someone finding the files on a phone or computer, not the download. If you're at real risk, get advice for your own country first. Access Now's free [Digital Security Helpline](https://www.accessnow.org/help/) helps activists, journalists and others at risk.

### Getting past blocks: a proxy

**⚙ Settings → Connection and privacy → Proxy** sends *all* of ZIMaker's downloads through a proxy: websites, archive.org, its own libraries and updates. Pictures that documents link to (via Pandoc or LibreOffice) go through it too.

- **Tor Browser:** start Tor Browser and leave it open, then click **Tor Browser** (SOCKS5, 127.0.0.1:9150).
- **Tor as a service:** click **Tor service** (127.0.0.1:9050).
- **Psiphon:** choose SOCKS5 and enter the SOCKS port that Psiphon's settings show. Set a fixed port there, so it doesn't change.
- **Any other SOCKS5 or HTTP proxy:** enter its address, its port, and a login if it needs one. The password is stored in ZIMaker's settings file, which only your account can read.
- **A VPN** running on your computer covers ZIMaker already, so leave the setting on **No proxy**.

Click **Test connection** to see the address websites will see. Through Tor, it also says it's a Tor exit.

Two rules, on purpose:

- **Fail closed:** with a proxy set, ZIMaker never connects directly. If the proxy stops, downloads fail with a message instead of quietly going around it.
- **No DNS leaks:** with SOCKS5, the proxy looks up website names, so your local network never sees which sites you asked for.

**Windows: Tor Browser or Psiphon can't be reached?** They listen on Windows' own address (127.0.0.1), and ZIMaker runs in WSL, which can't reach that address in its default network mode. To fix it once (Windows 11 22H2 or newer):

1. In your Windows user folder (`C:\Users\you`), create a text file named `.wslconfig` containing:
   ```
   [wsl2]
   networkingMode=mirrored
   ```
2. Run `wsl --shutdown` in PowerShell. This stops all Linux programs in WSL.
3. Open ZIMaker again.

Or run Tor inside WSL instead (`sudo apt install tor`), and use **Tor service**. Where Tor itself is blocked, Tor Browser's built-in bridges (Snowflake, obfs4) or Psiphon usually still get through.

Using Tor or a VPN is itself illegal or watched in some countries, and a well-equipped state can often tell that you're using one, even when it can't see what you're reading.

### Leaving fewer traces

- **Private mode** (Settings): ZIMaker deletes the Jobs list, with its logs and the web addresses you saved, when it stops. It deletes uploaded copies too, and doesn't remember which ZIMs you opened. **Clear history now** does the same at any time. Jobs that are still running are kept.
- **Neutral file names** (Settings): new ZIMs are saved as `zim-20261029-1.zim` instead of after their title.
- **The viewer:**
  - It never puts a ZIM's title in its address. In private mode, the window title stays "ZIM viewer" too, so your browser's history doesn't list what you read.
  - ZIM pages can't load anything from the internet. A remote picture would tell the network what you're reading.
  - Nothing ZIMaker shows is kept in the browser's cache.

What these settings **don't** hide:

- **Inside a ZIM file:** its title, description and every page still say what it is, whatever the file is called.
- **Outside ZIMaker:** Windows' "Recent files", Kiwix's own history and bookmarks, backups and cloud sync (OneDrive, Google Drive) can all record or copy your files.

### Keeping files safe: encrypt them

The strongest protection is to keep your ZIMs in an encrypted place that only opens with your password. Then set ZIMaker's **Save to folder** to that place.

- **[VeraCrypt](https://veracrypt.io)** (free; Windows, Linux, macOS): creates an encrypted "container" file that opens as a drive. Close it (Dismount) when you're done.
- **BitLocker** (Windows Pro): can encrypt a USB stick or a whole drive.
- **Linux:** encrypt the disk when installing (LUKS), or use VeraCrypt.
- **Phones:** a phone's built-in encryption protects it only while it's locked. Use a strong passcode, not a pattern or 4-digit PIN.

An encrypted container doesn't help while it's open, and some places force people to unlock their devices. For sharing, ZIMs work well on SD cards and USB sticks, which are easy to hide or wipe, and Kiwix reads them offline on phones and computers.

## Notes

- **Websites:** you choose what to save, so a site's `robots.txt` doesn't block it; ZIMaker still waits between pages when the site's `robots.txt` asks it to (Crawl-delay), and slows down when a site says it's busy. It saves audio and video only when the profile's **website audio and video** option is on. Links to pages that weren't saved (past the page limit, or outside the section) still point at the live site. Sites that build their pages with JavaScript (web apps, infinite scroll) may not come out well.
- **archive.org books:**
  - An item that holds several books (a multi-volume set, for example) gives one volume per book.
  - Borrow-only (lending library) items can't be downloaded; the app says so.
  - If archive.org is down or busy (it happens), the app says it couldn't reach archive.org. Try again later.
- **Picture detection** looks for ink on the page that isn't under any word. It catches illustrations, maps, plates and most line drawings. Covers and endpapers usually count as pictures. A very faint or tiny diagram can be missed.
- **Big scan sets take a while.** A 1,000-page volume downloads about 1–3.5 GB of scans, which are deleted once the ZIM is built.
- **Broken ebooks:** if an ebook refers to files it doesn't contain (common in real-world ebooks), those references are repaired or removed, so the ZIM has no broken links.
- **Where its files live:** libraries, job logs and temporary files are in `~/.zimaker` (inside WSL on Windows). To clear old job logs, use **Remove from list**, or delete `~/.zimaker/jobs`.

## Files

- **Windows:**
  - `Install ZIMaker.cmd` / `Uninstall ZIMaker.cmd`: the installer and uninstaller (scripts in `app/install.ps1` and `app/uninstall.ps1`).
  - `ZIMaker.vbs`: the launcher.
  - `Create desktop shortcut.cmd`: (re)creates the desktop icon.
  - `Start ZIMaker (with console).cmd`: the launcher with a visible console, for troubleshooting.
- **Linux:** `install.sh`, `uninstall.sh`, and `zimaker.sh` (the launcher).
- **Both:**
  - `app/setup.py`: first-run setup.
  - `app/server.py`: the local web app.
  - `app/ui.html`: the page you see.
  - `app/viewer.html`: the ZIM viewer.
  - `app/icon.ico` and `app/icon.png`: the app icon.
  - `app/zc/`: the converter.
    - `ia.py`: archive.org.
    - `web.py`: websites.
    - `epub.py`, `pdf.py`, `formats.py`: files.
    - `media.py`: audio and video.
    - `emails.py`: EML and MBOX.
    - `tools.py`: finds the optional tools.
    - `settings.py`: settings and profiles.
    - `images.py`: picture detection.
    - `builder.py`: ZIM writing and validation.
    - `runner.py`: runs one job.
    - `deps.py`: installs the libraries, Pandoc and zimcheck.
