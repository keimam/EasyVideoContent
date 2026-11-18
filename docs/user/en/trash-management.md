# Trash & Safe Project Deletion

Deleting a project moves its folder into **Trash** (`storage/longform_projects/_trash/`) so accidental deletions can always be recovered before permanent removal.

---

## 1. Moving a Project to Trash

1. From the **Long Video** home card (`⋯` menu) or the project page top bar, click **Delete project**.
2. The confirmation modal displays the project **folder size (MB)** and the count of **installed CapCut drafts**.
3. *(Optional)* Check **Also delete installed CapCut drafts** if you also want to remove matching CapCut drafts (default **off**).
4. Type the **project title** or **project ID**, then click **Move to Trash**.

---

## 2. Deleting While a Job Is Running

- All projects — even those with active background tasks — can be deleted.
- When a job is active, the dialog displays a non-blocking warning, signals the worker to stop cleanly, waits for exit, releases `.lock`, and then moves the folder to Trash.
- If a file is still locked by another Windows process (such as a media player), Studio rolls back safely and displays a friendly error listing the locked path(s) under **Details**.

---

## 3. Restoring or Emptying Trash

- Select the **Trash** filter on the Long Video home page.
- Click **Restore** to return a project to the active list (interrupted jobs restore cleanly in *stopped* state so they can be resumed).
- Click **Delete permanently** for a single project or **Empty trash** to reclaim disk space.

> **Note:** Moving a project to Trash does not delete global footage cache (`storage/longform_cache/`) or custom presets.
