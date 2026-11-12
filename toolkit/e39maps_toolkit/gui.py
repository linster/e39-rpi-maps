"""The map toolkit window (Tk): build the map database, push it to QEMU or a
device (live swap), or put it into the image."""

import queue
import threading
import tkinter as tk
import traceback
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from mapdata import schema

from .config import MACHINES, QEMU_TARGET
from .push import PushError, live_swap, update_image

PAD = {"padx": 6, "pady": 3}


class ToolkitWindow:

    def __init__(self, root, settings):
        self.root, self.settings = root, settings
        self.events = queue.Queue()
        self.busy = False
        self.cancel_requested = threading.Event()
        root.title("e39-rpi map toolkit")
        root.minsize(760, 640)
        root.protocol("WM_DELETE_WINDOW", self.close)

        main = ttk.Frame(root, padding=8)
        main.pack(fill="both", expand=True)
        main.columnconfigure(0, weight=1)
        main.columnconfigure(1, weight=1)

        self._region_frame(main).grid(row=0, column=0, sticky="nsew", **PAD)
        self._categories_frame(main).grid(row=0, column=1, sticky="nsew", **PAD)
        self._build_frame(main).grid(row=1, column=0, columnspan=2, sticky="nsew", **PAD)
        self._push_frame(main).grid(row=2, column=0, sticky="nsew", **PAD)
        self._image_frame(main).grid(row=2, column=1, sticky="nsew", **PAD)

        logf = ttk.LabelFrame(main, text="Log")
        logf.grid(row=3, column=0, columnspan=2, sticky="nsew", **PAD)
        main.rowconfigure(3, weight=1)
        self.log_text = tk.Text(logf, height=12, wrap="word", state="disabled", font="TkFixedFont")
        scroll = ttk.Scrollbar(logf, command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=scroll.set)
        self.log_text.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

        self.refresh_db_info()
        root.after(100, self.pump)

    # --- layout ------------------------------------------------------------

    def _entry(self, parent, row, label, value, width=14, column=0):
        ttk.Label(parent, text=label).grid(row=row, column=column, sticky="w", **PAD)
        var = tk.StringVar(value=str(value))
        ttk.Entry(parent, textvariable=var, width=width).grid(row=row, column=column + 1, sticky="ew", **PAD)
        return var

    def _region_frame(self, parent):
        r = self.settings.region
        f = ttk.LabelFrame(parent, text="Region (a circle)")
        f.columnconfigure(1, weight=1)
        self.region_name = self._entry(f, 0, "Name", r.name, 30)
        self.center_lat = self._entry(f, 1, "Centre latitude", r.center_lat)
        self.center_lon = self._entry(f, 2, "Centre longitude", r.center_lon)
        self.radius = self._entry(f, 3, "Radius (km)", f"{r.radius_km:g}")
        self.extracts = self._entry(f, 4, "Geofabrik extracts", ", ".join(r.extracts), 30)
        ttk.Label(f, text="Default: Ottawa, reaching London ON and Quebec City; Canada only.",
                  foreground="#666").grid(row=5, column=0, columnspan=2, sticky="w", **PAD)
        return f

    def _categories_frame(self, parent):
        f = ttk.LabelFrame(parent, text="POI categories")
        self.category_vars = {}
        for i, (key, label) in enumerate(schema.CATEGORIES.items()):
            var = tk.BooleanVar(value=key in self.settings.categories)
            ttk.Checkbutton(f, text=label, variable=var).grid(row=i % 5, column=i // 5, sticky="w", **PAD)
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

    # --- settings ----------------------------------------------------------

    def collect(self):
        s = self.settings
        try:
            s.region.name = self.region_name.get().strip()
            s.region.center_lat = float(self.center_lat.get())
            s.region.center_lon = float(self.center_lon.get())
            s.region.radius_km = float(self.radius.get())
        except ValueError:
            raise ValueError("Centre and radius must be numbers.")
        s.region.extracts = [e.strip() for e in self.extracts.get().split(",") if e.strip()]
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
        for b in (self.build_button, self.push_button, self.image_button):
            b.configure(state="disabled")
        self.log(f"--- {title}")

        def work():
            try:
                func(settings)
                self.events.put(("done", f"{title}: done"))
            except InterruptedError:
                self.events.put(("done", f"{title}: cancelled"))
            except (PushError, schema.InvalidMapDatabase, OSError, SystemExit) as exc:
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

    def start_update_image(self):
        build = self.build_image.get()

        def work(settings):
            log = update_image(settings.output, settings.yocto_dir, settings.machine, build=build, log=self.log)
            if log:
                self.log(f"Build log: {log}")

        self.run_task("Update image", work)

    def pump(self):
        try:
            while True:
                kind, data = self.events.get_nowait()
                if kind == "log":
                    self.log_text.configure(state="normal")
                    self.log_text.insert("end", data + "\n")
                    self.log_text.see("end")
                    self.log_text.configure(state="disabled")
                elif kind == "progress":
                    fraction, text = data
                    self.progress.configure(value=fraction)
                    self.status.configure(text=text)
                elif kind in ("done", "failed"):
                    self.busy = False
                    for b in (self.build_button, self.push_button, self.image_button):
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
