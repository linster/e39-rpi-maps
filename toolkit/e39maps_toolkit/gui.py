"""The map toolkit window (Tk): build the map database, push it to QEMU or a
device (live swap), or put it into the image."""

import queue
import threading
import tkinter as tk
import traceback
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from mapdata import schema

from .config import MACHINES, QEMU_TARGET, REPO
from . import versions
from mapdata import release
from .push import PushError, copy_to_usb, live_swap, start_render, update_image

PAD = {"padx": 6, "pady": 3}


class ToolkitWindow:

    def __init__(self, root, settings):
        self.root, self.settings = root, settings
        self.events = queue.Queue()
        self.busy = False
        self.cancel_requested = threading.Event()
        root.title("e39-rpi map toolkit")
        root.minsize(1000, 820)
        root.protocol("WM_DELETE_WINDOW", self.close)

        main = ttk.Frame(root, padding=8)
        main.pack(fill="both", expand=True)
        main.columnconfigure(0, weight=1)
        main.rowconfigure(0, weight=1)

        # Two tabs: what the map covers, and building/delivering it. The log
        # stays visible under both.
        tabs = ttk.Notebook(main)
        tabs.grid(row=0, column=0, sticky="nsew")
        region_tab = ttk.Frame(tabs, padding=4)
        build_tab = ttk.Frame(tabs, padding=4)
        tabs.add(region_tab, text="1. Region and POIs")
        tabs.add(build_tab, text="2. Build and deliver")

        from .regionpicker import RegionPicker

        region_tab.columnconfigure(0, weight=1)
        region_tab.rowconfigure(0, weight=1)
        self.picker = RegionPicker(region_tab, self.settings.region, self.settings.cache_dir, self.log)
        self.picker.grid(row=0, column=0, sticky="nsew", **PAD)
        self._categories_frame(region_tab).grid(row=1, column=0, sticky="ew", **PAD)

        from .rasterpanel import RasterPanel

        def current_region():
            try:
                return self.picker.get()
            except ValueError:
                return None

        def current_settings():
            s = self.settings
            region = current_region()
            if region is not None:
                s.region = region
            return s

        self.raster = RasterPanel(region_tab, self.settings, current_region, current_settings,
                                  self.start_render, self.log)
        self.raster.grid(row=2, column=0, sticky="ew", **PAD)

        build_tab.columnconfigure(0, weight=1)
        build_tab.columnconfigure(1, weight=1)
        self._build_frame(build_tab).grid(row=0, column=0, columnspan=2, sticky="nsew", **PAD)
        self._push_frame(build_tab).grid(row=1, column=0, sticky="nsew", **PAD)
        self._image_frame(build_tab).grid(row=1, column=1, sticky="nsew", **PAD)
        self._release_frame(build_tab).grid(row=2, column=0, columnspan=2, sticky="nsew", **PAD)

        logf = ttk.LabelFrame(main, text="Log")
        logf.grid(row=1, column=0, sticky="nsew", **PAD)
        self.log_text = tk.Text(logf, height=8, wrap="word", state="disabled", font="TkFixedFont")
        scroll = ttk.Scrollbar(logf, command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=scroll.set)
        self.log_text.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

        self.refresh_db_info()
        root.after(100, self.pump)
        root.after(300, self.check_versions)

    # --- layout ------------------------------------------------------------

    def _entry(self, parent, row, label, value, width=14, column=0):
        ttk.Label(parent, text=label).grid(row=row, column=column, sticky="w", **PAD)
        var = tk.StringVar(value=str(value))
        ttk.Entry(parent, textvariable=var, width=width).grid(row=row, column=column + 1, sticky="ew", **PAD)
        return var

    def _categories_frame(self, parent):
        f = ttk.LabelFrame(parent, text="POI categories")
        self.category_vars = {}
        for i, (key, label) in enumerate(schema.CATEGORIES.items()):
            var = tk.BooleanVar(value=key in self.settings.categories)
            ttk.Checkbutton(f, text=label, variable=var).grid(row=i // 5, column=i % 5, sticky="w", **PAD)
            self.category_vars[key] = var
        return f

    def _build_frame(self, parent):
        f = ttk.LabelFrame(parent, text="Map database")
        f.columnconfigure(1, weight=1)
        self.cache_dir = self._entry(f, 0, "Extract cache", self.settings.cache_dir, 50)
        self.output = self._entry(f, 1, "Database file", self.settings.output, 50)
        ttk.Button(f, text="...", width=3, command=self.choose_output).grid(row=1, column=2, **PAD)
        self.routing = tk.BooleanVar(value=self.settings.routing)
        ttk.Checkbutton(f, text="Routing tiles (Valhalla, car; cached, rebuilt when the extracts change)",
                        variable=self.routing).grid(row=2, column=0, columnspan=3, sticky="w", **PAD)
        self.db_info = ttk.Label(f, text="", foreground="#333")
        self.db_info.grid(row=4, column=0, columnspan=3, sticky="w", **PAD)
        row = ttk.Frame(f)
        row.grid(row=5, column=0, columnspan=3, sticky="ew", **PAD)
        row.columnconfigure(2, weight=1)
        self.build_button = ttk.Button(row, text="Build map database", command=self.start_build)
        self.build_button.grid(row=0, column=0, **PAD)
        self.cancel_button = ttk.Button(row, text="Cancel", command=self.cancel_requested.set, state="disabled")
        self.cancel_button.grid(row=0, column=1, **PAD)
        self.local_usb_button = ttk.Button(row, text="Copy to USB stick...", command=lambda: self.start_usb(None))
        self.local_usb_button.grid(row=0, column=4, **PAD)
        self.progress = ttk.Progressbar(row, mode="determinate", maximum=1.0)
        self.progress.grid(row=0, column=2, sticky="ew", **PAD)
        self.status = ttk.Label(row, text="", width=34)
        self.status.grid(row=0, column=3, sticky="e", **PAD)
        return f

    def _push_frame(self, parent):
        f = ttk.LabelFrame(parent, text="Live swap on a running system")
        f.columnconfigure(1, weight=1)
        is_qemu = self.settings.target == QEMU_TARGET
        self.target_kind = tk.StringVar(value="qemu" if is_qemu else "device")
        ttk.Radiobutton(f, text=f"QEMU ({QEMU_TARGET})", value="qemu",
                        variable=self.target_kind).grid(row=0, column=0, columnspan=2, sticky="w", **PAD)
        ttk.Radiobutton(f, text="Device", value="device",
                        variable=self.target_kind).grid(row=1, column=0, sticky="w", **PAD)
        self.device_host = tk.StringVar(value=self.settings.device_host or ("" if is_qemu else self.settings.target))
        ttk.Entry(f, textvariable=self.device_host, width=22).grid(row=1, column=1, sticky="ew", **PAD)
        ttk.Label(f, text="host or IP[:port]; ssh as root", foreground="#666").grid(
            row=2, column=1, sticky="w", padx=6)
        self.push_button = ttk.Button(f, text="Live swap", command=self.start_push)
        self.push_button.grid(row=3, column=0, columnspan=2, sticky="w", **PAD)
        ttk.Label(f, text="Stops the map services, replaces the map\ndatabase, starts them again. "
                          "User POIs stay.", foreground="#666").grid(row=4, column=0, columnspan=2,
                                                                      sticky="w", **PAD)
        return f

    def _image_frame(self, parent):
        f = ttk.LabelFrame(parent, text="Image")
        f.columnconfigure(1, weight=1)
        self.yocto_dir = self._entry(f, 0, "e39-rpi-yocto", self.settings.yocto_dir, 26)
        ttk.Label(f, text="Machine").grid(row=1, column=0, sticky="w", **PAD)
        self.machine = tk.StringVar(value=self.settings.machine)
        ttk.Combobox(f, textvariable=self.machine, values=MACHINES, state="readonly",
                     width=18).grid(row=1, column=1, sticky="w", **PAD)
        self.build_image = tk.BooleanVar(value=True)
        ttk.Checkbutton(f, text="Build the image afterwards", variable=self.build_image).grid(
            row=2, column=0, columnspan=2, sticky="w", **PAD)
        self.image_button = ttk.Button(f, text="Update image", command=self.start_update_image)
        self.image_button.grid(row=3, column=0, columnspan=2, sticky="w", **PAD)
        ttk.Label(f, text="Puts the database into the image build's map\ndata, so run-qemu.sh and the SD "
                          "image tools use it.", foreground="#666").grid(row=4, column=0, columnspan=2,
                                                                         sticky="w", **PAD)
        return f

    def check_versions(self, before=None):
        """Warn about a Valhalla mismatch (log; a dialog before building or
        updating the image). Returns False if the user cancels."""
        found = versions.problems(self.yocto_dir.get().strip())
        if before is None:
            import os

            container = os.environ.get("E39MAPS_TOOLKIT_CONTAINER")
            if container:
                self.log(f"Running in the toolkit container ({container}).")
            self.log(versions.summary(self.yocto_dir.get().strip()))
            for p in found:
                self.log(f"WARNING: {p}")
            return True
        if not found:
            return True
        return messagebox.askokcancel(
            "Valhalla version mismatch",
            "\n\n".join(found) + f"\n\n{before} anyway?", icon="warning")

    def _release_frame(self, parent):
        f = ttk.LabelFrame(parent, text="Published maps (GitHub releases)")
        f.columnconfigure(1, weight=1)
        ttk.Label(f, text="Release").grid(row=0, column=0, sticky="w", **PAD)
        self.release_tag = tk.StringVar()
        self.release_box = ttk.Combobox(f, textvariable=self.release_tag, state="readonly", width=60)
        self.release_box.grid(row=0, column=1, sticky="ew", **PAD)
        ttk.Button(f, text="Refresh", command=self.start_list_releases).grid(row=0, column=2, **PAD)
        row = ttk.Frame(f)
        row.grid(row=1, column=0, columnspan=3, sticky="w")
        self.release_buttons = [
            ttk.Button(row, text="Publish local map", command=self.start_publish),
            ttk.Button(row, text="Download", command=lambda: self.start_release_action("download")),
            ttk.Button(row, text="Download + live swap", command=lambda: self.start_release_action("swap")),
            ttk.Button(row, text="Download + copy to USB stick...",
                       command=lambda: self.start_release_action("usb")),
        ]
        for i, b in enumerate(self.release_buttons):
            b.grid(row=0, column=i, **PAD)
        ttk.Label(f, text="Needs a GitHub login (gh auth login). Downloads are kept in the toolkit's cache. "
                          "Live swap uses the target on the left.", foreground="#666").grid(
            row=2, column=0, columnspan=3, sticky="w", **PAD)
        return f

    def selected_tag(self):
        return self.release_tag.get().split(" ", 1)[0] or None

    def start_list_releases(self):
        def work(settings):
            pinned = (release.read_manifest(REPO) or {}).get("tag")
            found = release.GitHub().list_releases()
            items = [f"{r['tag_name']}  ({sum(a['size'] for a in r.get('assets', [])) / 1e6:.0f} MB"
                     + (", pinned)" if r["tag_name"] == pinned else ")") for r in found]
            self.events.put(("releases", items))
            self.log(f"{len(items)} published map(s)" + (f"; pinned: {pinned}" if pinned else ""))

        self.run_task("List published maps", work)

    def start_publish(self):
        if not messagebox.askokcancel(
                "Publish map", "Upload the local map (database + routing tiles, ~640 MB) as a GitHub "
                               "release of e39-rpi-maps, and pin it in map-release.json?"):
            return

        def work(settings):
            def bytes_progress(name, done, total):
                self.events.put(("progress", (done / total if total else 0,
                                              f"{name}: {done / 1e6:.0f} / {total / 1e6:.0f} MB")))

            manifest = release.publish(settings.output, REPO, log=self.log, progress=bytes_progress)
            found = release.GitHub().list_releases()
            self.events.put(("releases", [f"{r['tag_name']}" + (" (pinned)" if r["tag_name"] == manifest["tag"] else "")
                                          for r in found]))

        self.run_task("Publish map", work)

    def start_release_action(self, action):
        from .__main__ import fetch_published

        tag = self.selected_tag()
        if tag is None:
            messagebox.showinfo("Map toolkit", "Choose a release first (Refresh lists them).")
            return
        stick = None
        if action == "usb":
            stick = filedialog.askdirectory(title="The USB stick (its mount point)")
            if not stick:
                return

        def progress(fraction, text):
            self.events.put(("progress", (fraction, text)))

        def work(settings):
            db = fetch_published(settings, tag, log=self.log, progress=progress)
            if action == "swap":
                if not settings.target:
                    raise PushError("enter the device's host or IP")
                live_swap(db, settings.target, settings.ssh_user, settings.ssh_password, log=self.log)
            elif action == "usb":
                copy_to_usb(db, stick, log=self.log, progress=progress)

        self.run_task({"download": f"Download {tag}", "swap": f"Download + live swap {tag}",
                       "usb": f"Download {tag} to a USB stick"}[action], work)

    def start_usb(self, db):
        stick = filedialog.askdirectory(title="The USB stick (its mount point)")
        if not stick:
            return

        def work(settings):
            copy_to_usb(db or settings.output, stick, log=self.log,
                        progress=lambda f, t: self.events.put(("progress", (f, t))))

        self.run_task("Copy the local map to a USB stick", work)

    # --- settings ----------------------------------------------------------

    def collect(self):
        s = self.settings
        s.region = self.picker.get()
        s.raster = self.raster.settings_dict()
        s.categories = [k for k, v in self.category_vars.items() if v.get()]
        s.routing = self.routing.get()
        s.cache_dir = self.cache_dir.get().strip()
        s.output = self.output.get().strip()
        s.device_host = self.device_host.get().strip()
        s.target = QEMU_TARGET if self.target_kind.get() == "qemu" else s.device_host
        s.yocto_dir = self.yocto_dir.get().strip()
        s.machine = self.machine.get()
        s.save()
        return s

    def choose_output(self):
        path = filedialog.asksaveasfilename(initialfile=Path(self.output.get()).name,
                                            initialdir=str(Path(self.output.get()).parent),
                                            defaultextension=".sqlite")
        if path:
            self.output.set(path)
            self.refresh_db_info()

    def refresh_db_info(self):
        path = Path(self.output.get())
        if not path.exists():
            self.db_info.configure(text="No database yet: build one.")
            return
        try:
            meta = schema.check(path, quick_check=False)
            routing = schema.routing_info(meta)
            tiles = (f"routing tiles {int(routing['routing_size']) / 1e6:.0f} MB (Valhalla {routing['valhalla_version']})"
                     if routing else "no routing tiles")
            self.db_info.configure(text=f"Built {meta['built_at']}: {meta['poi_count']} POIs, "
                                        f"{path.stat().st_size / 1e6:.1f} MB, {tiles}")
        except schema.InvalidMapDatabase as exc:
            self.db_info.configure(text=f"Not a map database from this toolkit: {exc}")

    # --- background work ---------------------------------------------------

    def log(self, text):
        self.events.put(("log", text))

    def run_task(self, title, func):
        if self.busy:
            return
        try:
            settings = self.collect()
        except ValueError as exc:
            messagebox.showerror("Map toolkit", str(exc))
            return
        self.busy = True
        self.cancel_requested.clear()
        for b in self.task_buttons():
            b.configure(state="disabled")
        self.log(f"--- {title}")

        def work():
            try:
                func(settings)
                self.events.put(("done", f"{title}: done"))
            except InterruptedError:
                self.events.put(("done", f"{title}: cancelled"))
            except (PushError, schema.InvalidMapDatabase, release.ReleaseError, OSError, SystemExit) as exc:
                self.events.put(("failed", f"{title} failed: {exc}"))
            except Exception:
                self.log(traceback.format_exc())
                self.events.put(("failed", f"{title} failed (see the log)"))

        threading.Thread(target=work, daemon=True).start()

    def start_build(self):
        from .__main__ import run_build

        if not any(v.get() for v in self.category_vars.values()):
            messagebox.showerror("Map toolkit", "Choose at least one POI category.")
            return
        if self.routing.get() and not self.check_versions("Build"):
            return
        self.cancel_button.configure(state="normal")

        def download_progress(done, total):
            self.events.put(("progress", (done / total if total else 0, f"{done / 1e6:.0f} / {total / 1e6:.0f} MB")))

        def work(settings):
            self.events.put(("progress", (0, "")))
            def build_status(name, kept):
                self.events.put(("progress", (0, f"{name.split('-latest')[0]}: {kept} POIs")))

            run_build(settings, log=self.log, progress=download_progress, status=build_status,
                      cancelled=self.cancel_requested.is_set)

        self.run_task("Build map database", work)

    def start_push(self):
        def work(settings):
            if not settings.target:
                raise PushError("enter the device's host or IP")
            live_swap(settings.output, settings.target, settings.ssh_user, settings.ssh_password, log=self.log)

        self.run_task(f"Live swap on {self.collect().target or 'device'}", work)

    def start_render(self):
        """Render the offline tiles: toolkit/render-tiles.sh on the host
        (podman), in the background with its own progress window."""
        if not messagebox.askokcancel(
                "Render offline tiles",
                "Render the offline map tiles for this region now?\n\n" + self.raster.estimate.cget("text")
                + "\n\nThe first time, importing the region into the renderer's database takes 1-2 hours "
                  "and tens of GB of disk. Rendering is resumable: run it again after an interruption. "
                  "Rebuild the map afterwards to include the tiles."):
            return

        def work(settings):
            log = start_render(settings.yocto_dir, log=self.log)
            if log:
                self.log(f"Render log: {log}")

        self.run_task("Render offline tiles", work)

    def start_update_image(self):
        if not self.check_versions("Update the image"):
            return
        build = self.build_image.get()

        def work(settings):
            log = update_image(settings.output, settings.yocto_dir, settings.machine, build=build, log=self.log)
            if log:
                self.log(f"Build log: {log}")

        self.run_task("Update image", work)

    def task_buttons(self):
        return [self.build_button, self.push_button, self.image_button, self.local_usb_button,
                *self.release_buttons]

    def pump(self):
        try:
            while True:
                kind, data = self.events.get_nowait()
                if kind == "log":
                    self.log_text.configure(state="normal")
                    self.log_text.insert("end", data + "\n")
                    self.log_text.see("end")
                    self.log_text.configure(state="disabled")
                elif kind == "releases":
                    self.release_box.configure(values=data)
                    if data and self.release_tag.get() not in data:
                        self.release_tag.set(data[0])
                elif kind == "progress":
                    fraction, text = data
                    self.progress.configure(value=fraction)
                    self.status.configure(text=text)
                elif kind in ("done", "failed"):
                    self.busy = False
                    for b in self.task_buttons():
                        b.configure(state="normal")
                    self.cancel_button.configure(state="disabled")
                    self.status.configure(text="")
                    self.progress.configure(value=0)
                    self.refresh_db_info()
                    self.log_text.configure(state="normal")
                    self.log_text.insert("end", data + "\n", ("bold",))
                    self.log_text.see("end")
                    self.log_text.configure(state="disabled")
                    if kind == "failed":
                        messagebox.showerror("Map toolkit", data)
        except queue.Empty:
            pass
        self.root.after(100, self.pump)

    def close(self):
        if self.busy and not messagebox.askyesno("Map toolkit", "A task is running. Quit anyway?"):
            return
        try:
            self.collect()
        except ValueError:
            pass
        self.root.destroy()


def run(settings):
    root = tk.Tk()
    ToolkitWindow(root, settings)
    root.mainloop()
