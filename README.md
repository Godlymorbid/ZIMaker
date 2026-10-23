# ZIMaker
Turns websites, archive.org books, ebooks, documents and pictures into ZIM files for Kiwix. It runs on **Windows** and **Linux**.

## Install it (or share it)

### Windows

1. Unzip `ZIM Creator.zip` anywhere.
2. Double-click **`Install ZIM Creator.cmd`**.

The installer:

- checks for WSL (Windows Subsystem for Linux), and offers to install it with Ubuntu if it's missing;
- checks for Python 3.9 or newer inside WSL;
- installs ZIM Creator for the current Windows user in `%LOCALAPPDATA%\Programs\ZIM Creator` (no administrator rights needed);
- downloads the libraries it needs, about 150 MB;
- adds a **ZIM Creator** icon to the desktop and to the Start menu.

Installing WSL needs administrator approval and a restart. After the restart, open **Ubuntu** from the Start menu once to finish its setup, then run the installer again.

To update, run a newer copy's installer over the old one. To remove ZIM Creator, run `Uninstall ZIM Creator.cmd` in the install folder. Uninstalling never touches the ZIM files you made.

Windows may warn about running scripts from a downloaded zip ("Windows protected your PC"). Click **More info → Run anyway**.

Requirements: Windows 10 or 11 with WSL 2. It works on Intel/AMD and ARM PCs. Microsoft Edge is needed for the app window; without it, the app opens in your default browser.

### Linux

1. Unzip `ZIM Creator.zip`.
2. In a terminal, in the unzipped folder, run: `sh install.sh`

It installs into your home folder, so it doesn't need root. It downloads the libraries (about 150 MB) and adds **ZIM Creator** to your applications menu, a desktop icon where your desktop supports one, and a `zim-creator` command.

To remove it, run `sh ~/.local/share/zim-creator/uninstall.sh`.

Requirements: Python 3.9 or newer, which most distributions include, on a 64-bit Intel/AMD or ARM PC. If Chrome, Chromium, Edge or Brave is installed, the app opens in its own window; otherwise it opens in your default browser.

## Start it

- **Windows:** double-click the **ZIM Creator** icon on the desktop or in the Start menu.
  - To recreate the desktop icon, run `Create desktop shortcut.cmd`.
  - You can also double-click `ZIM Creator.vbs` in the install folder.
- **Linux:** open **ZIM Creator** from your applications menu, or run `zim-creator`.

**Stopping:** close the window, and ZIM Creator stops by itself about 2–3 minutes later. If a conversion is still running, it finishes the conversion first and then stops. The **Quit** button stops it straight away; it asks first if a conversion is running.

**Troubleshooting:**

- **Windows:** `Start ZIM Creator (with console).cmd` runs the same app with a visible console that shows any errors.
- **Linux:** errors are logged to `~/.zim-creator/server.log`.

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

Jobs run one at a time. The Jobs list shows progress, a log, and an **Open folder** button when a job is done. Every file is checked with openZIM's `zimcheck` before it's saved.

## What it accepts

| Kind | Formats | What you get |
|---|---|---|
| Websites | any http/https address | the site's pages with their styles, scripts, fonts and images; a list of all saved pages |
| archive.org books | item links or IDs | OCR text of every page, with printed page numbers and running heads; scans of illustrated pages |
| Ebooks | EPUB, MOBI, AZW, AZW3, FB2 | the book's own chapters, styles and images, plus a contents page and Previous/Next links |
| Documents | DOCX, ODT, RTF, PPTX, XLSX, CSV, TSV, Markdown, HTML, Jupyter notebooks, LaTeX, reStructuredText, Org, AsciiDoc, Typst, MediaWiki | converted with Pandoc: split into chapters at headings, with a contents page and the document's pictures |
| Plain text | TXT | split into chapters (or parts of about 4,000 words) |
| Page-based | PDF, XPS, CBZ comics | the text of each page; page images for illustrated pages (comics and scans without text keep every page as an image) |
| Pictures | JPG, PNG, GIF, WEBP, TIFF, BMP, SVG | one gallery for all the pictures in a job, a picture per page |
| ZIP files | — | a saved website folder becomes a website; a folder of pictures becomes a gallery; anything else is converted file by file |

**Not supported:**

- Old Office files (`.doc`, `.xls`, `.ppt`): save them in the newer format first.
- DjVu: convert to PDF first.
- CBR/RAR and 7z: repack as ZIP or CBZ.
- Apple iWork files: export them first.
- Audio and video.

The app says why when you add one of these.

## Notes

- **Websites:** the crawler follows the site's `robots.txt` and never saves audio or video. Links to pages that weren't saved (past the page limit, or outside the section) still point at the live site. Sites that build their pages with JavaScript (web apps, infinite scroll) may not come out well.
- **archive.org books:**
  - An item that holds several books (a multi-volume set, for example) gives one volume per book.
  - Borrow-only (lending library) items can't be downloaded; the app says so.
  - If archive.org is down or busy (it happens), the app says it couldn't reach archive.org. Try again later.
- **Picture detection** looks for ink on the page that isn't under any word. It catches illustrations, maps, plates and most line drawings. Covers and endpapers usually count as pictures. A very faint or tiny diagram can be missed.
- **Big scan sets take a while.** A 1,000-page volume downloads about 1–3.5 GB of scans, which are deleted once the ZIM is built.
- **Broken ebooks:** if an ebook refers to files it doesn't contain (common in real-world ebooks), those references are repaired or removed, so the ZIM has no broken links.
- **Where its files live:** libraries, job logs and temporary files are in `~/.zim-creator` (inside WSL on Windows). To clear old job logs, use **Remove from list**, or delete `~/.zim-creator/jobs`.

## Files

- **Windows:**
  - `Install ZIM Creator.cmd` / `Uninstall ZIM Creator.cmd`: the installer and uninstaller (scripts in `app/install.ps1` and `app/uninstall.ps1`).
  - `ZIM Creator.vbs`: the launcher.
  - `Create desktop shortcut.cmd`: (re)creates the desktop icon.
  - `Start ZIM Creator (with console).cmd`: the launcher with a visible console, for troubleshooting.
- **Linux:** `install.sh`, `uninstall.sh`, and `zim-creator.sh` (the launcher).
- **Both:**
  - `app/setup.py`: first-run setup.
  - `app/server.py`: the local web app.
  - `app/ui.html`: the page you see.
  - `app/icon.ico` and `app/icon.png`: the app icon.
  - `app/zc/`: the converter.
    - `ia.py`: archive.org.
    - `web.py`: websites.
    - `epub.py`, `pdf.py`, `formats.py`: files.
    - `images.py`: picture detection.
    - `builder.py`: ZIM writing and validation.
    - `runner.py`: runs one job.
    - `deps.py`: installs the libraries, Pandoc and zimcheck.

