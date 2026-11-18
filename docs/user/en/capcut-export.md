# CapCut Desktop Export

EasyFaceless Studio builds portable **CapCut Desktop** drafts containing shot video tracks, chapter narration audio, background music, and synchronized subtitles ready for timeline editing.

---

## 1. Tested Version & Important Requirement

- **Tested CapCut Desktop version:** **8.9.0** (Windows).
- **Always close CapCut before exporting (`--install` / *Export & Install to CapCut*):**
  CapCut maintains its project index in `root_meta_info.json`. Installing a draft while CapCut Desktop is open can corrupt that index or hide new drafts. Studio checks for active `CapCut.exe` processes and blocks installation until CapCut is closed.

---

## 2. Export Steps

1. Complete the **Render** step first.
2. Open the **CapCut** tab in your project.
3. Close **CapCut Desktop** completely.
4. Click **Export & Install to CapCut**. Studio writes the draft into `%LOCALAPPDATA%\CapCut\User Data\Projects\com.lveditor.draft\EFS_<project_id>_...`.
5. Reopen CapCut Desktop — your exported draft appears at the top of the CapCut home list.

---

## 3. Subtitle Text Size Calibration

Different aspect ratios (`16:9` vs `9:16`) and fonts affect subtitle scaling inside CapCut:
1. Under **Text Size Calibration** in the CapCut tab, click **Create Calibration Draft**.
2. Open that calibration draft in CapCut Desktop — it displays test segments sizes **5 through 10**.
3. Pick the clearest size without horizontal clipping, then save it in Studio via **Save Text Size** for that preset.
