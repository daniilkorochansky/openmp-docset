# openmp-docset

Automatically compiled offline documentation for **[openmultiplayer](https://github.com/openmultiplayer/open.mp)** in **docset** format.

## Features
* **Full Autonomy:** The entire documentation database and HTML pages are stored locally and work without the internet.
* **Rolling Updates:** Updates are checked in the official repository every 12 hours. If the documentation has been updated, the Docset is automatically updated and the rolling update of the `latest` release is released.
* **Adaptive Design:** HTML pages are optimized for WebView and support both light and dark system themes.

## Data source
The source texts of references, functions, and articles are taken directly from the official repository of the openmultiplayer project: **[openmultiplayer/web](https://github.com/openmultiplayer/web)**

## How to use

### Option 1. Automatic updates in Zeal (Recommended)
You can add this repository as a data source directly in the Zeal interface to receive one-button updates:
1. Open Zeal and go to **Docset Library** -> **Add feed**.
2. Insert the link to the XML feed:
   ```text
   https://github.com/daniilkorochansky/openmp-docset/releases/download/latest/open.mp.xml
   ```
3. Click **Add** and wait for the download to finish.

### Option 2. Browsing through a regular browser
Unpack the archive `openmp.docset.tgz` with any archiver and open the file `openmp.docset/Contents/Resources/Documents/index.html ` in any convenient browser.
