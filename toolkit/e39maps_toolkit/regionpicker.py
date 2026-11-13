"""The region picker: a map to place the circle on, and a checklist of the
Geofabrik extracts it overlaps.

- Map (tkintermapview, OpenStreetMap tiles): click to set the centre; the
  radius slider (or its box) sets the radius; "Find" looks a place up
  (Nominatim). The circle and the ticked extracts' outlines are drawn.
- Extracts: those the circle overlaps, with download sizes; "suggested" marks
  the smallest set that covers the circle (regions.Index.overlapping).
  Ticks are kept when the circle moves; "Tick suggested" applies the
  suggestion. The search box finds any other extract.
"""

import queue
import threading
import tkinter as tk
from tkinter import messagebox, ttk

from .build import Region
from . import regions

PAD = {"padx": 4, "pady": 2}
RADIUS_MIN, RADIUS_MAX = 5, 1500
CIRCLE_COLOR = "#d03030"
OUTLINE_COLOR = "#3060c0"


def _size(n):
    if n is None:
        return ""
    return f"{n / 1e9:.1f} GB" if n >= 1e9 else f"{n / 1e6:.0f} MB"


class RegionPicker(ttk.LabelFrame):

    def __init__(self, parent, region, cache_dir, log):
        super().__init__(parent, text="Region: a circle on the map, and the OSM extracts to build it from")
        self.cache_dir, self.log = cache_dir, log
        self.index = None
        self.overlap, self.suggested = [], set()
        self.search_hits = []
        self.sizes = {}
        self.checked = {}                     # path -> BooleanVar
        self.events = queue.Queue()
        self.center = (region.center_lat, region.center_lon)
        self._redraw_job = self._overlap_job = None
        self._circle_shape, self._outline_shapes, self._marker = None, [], None
        for path in region.extracts:
            self.checked[path] = tk.BooleanVar(value=True)

        self.columnconfigure(0, weight=3)
        self.columnconfigure(1, weight=2)
        self.rowconfigure(1, weight=1)

        # --- left: the map and its controls ---------------------------------
        top = ttk.Frame(self)
        top.grid(row=0, column=0, sticky="ew", **PAD)
        top.columnconfigure(1, weight=1)
        ttk.Label(top, text="Find place").grid(row=0, column=0, sticky="w")
        self.place = tk.StringVar()
        entry = ttk.Entry(top, textvariable=self.place)
        entry.grid(row=0, column=1, sticky="ew", **PAD)
        entry.bind("<Return>", lambda _e: self.find_place())
        ttk.Button(top, text="Find", command=self.find_place).grid(row=0, column=2, **PAD)

        import tkintermapview

        self.map = tkintermapview.TkinterMapView(self, width=560, height=320, corner_radius=0,
                                                 user_agent=regions.USER_AGENT)
        self.map.grid(row=1, column=0, sticky="nsew", **PAD)
        self.map.add_left_click_map_command(self.on_map_click)

        controls = ttk.Frame(self)
        controls.grid(row=2, column=0, sticky="ew", **PAD)
        controls.columnconfigure(1, weight=1)
        ttk.Label(controls, text="Radius (km)").grid(row=0, column=0, sticky="w")
        self.radius = tk.DoubleVar(value=region.radius_km)
        ttk.Scale(controls, from_=RADIUS_MIN, to=RADIUS_MAX, variable=self.radius,
                  command=lambda _v: self.circle_changed()).grid(row=0, column=1, sticky="ew", **PAD)
        self.radius_text = tk.StringVar(value=f"{region.radius_km:g}")
        box = ttk.Spinbox(controls, from_=RADIUS_MIN, to=RADIUS_MAX, increment=10, width=7,
                          textvariable=self.radius_text, command=self.radius_typed)
        box.grid(row=0, column=2, **PAD)
        box.bind("<Return>", lambda _e: self.radius_typed())
        box.bind("<FocusOut>", lambda _e: self.radius_typed())
        ttk.Label(controls, text="Name").grid(row=1, column=0, sticky="w")
        self.name = tk.StringVar(value=region.name)
        # The name follows the circle until it's edited by hand.
        self._auto_name, self._place_label = region.name, None
        ttk.Entry(controls, textvariable=self.name).grid(row=1, column=1, columnspan=2, sticky="ew", **PAD)
        self.center_label = ttk.Label(controls, foreground="#555")
        self.center_label.grid(row=2, column=0, columnspan=3, sticky="w", **PAD)
        ttk.Label(controls, text="Click the map to move the centre.", foreground="#666").grid(
            row=3, column=0, columnspan=3, sticky="w", **PAD)

        # --- right: the extracts checklist ----------------------------------
        right = ttk.Frame(self)
        right.grid(row=0, column=1, rowspan=3, sticky="nsew", **PAD)
        right.columnconfigure(0, weight=1)
        right.rowconfigure(2, weight=1)
        bar = ttk.Frame(right)
        bar.grid(row=0, column=0, sticky="ew")
        bar.columnconfigure(1, weight=1)
        ttk.Button(bar, text="Tick suggested", command=self.tick_suggested).grid(row=0, column=0, **PAD)
        self.search = tk.StringVar()
        self.search.trace_add("write", lambda *_: self.after_idle(self.search_changed))
        ttk.Entry(bar, textvariable=self.search).grid(row=0, column=1, sticky="ew", **PAD)
        ttk.Label(bar, text="search", foreground="#666").grid(row=0, column=2)
        self.status = ttk.Label(right, text="Loading Geofabrik's extract index...", foreground="#555")
        self.status.grid(row=1, column=0, sticky="w", **PAD)

        holder = ttk.Frame(right)
        holder.grid(row=2, column=0, sticky="nsew")
        holder.rowconfigure(0, weight=1)
        holder.columnconfigure(0, weight=1)
        self.list_canvas = tk.Canvas(holder, highlightthickness=0, width=360)
        scroll = ttk.Scrollbar(holder, orient="vertical", command=self.list_canvas.yview)
        self.list_frame = ttk.Frame(self.list_canvas)
        self.list_frame.bind("<Configure>", lambda _e: self.list_canvas.configure(
            scrollregion=self.list_canvas.bbox("all")))
        self.list_canvas.create_window((0, 0), window=self.list_frame, anchor="nw")
        self.list_canvas.configure(yscrollcommand=scroll.set)
        self.list_canvas.grid(row=0, column=0, sticky="nsew")
        scroll.grid(row=0, column=1, sticky="ns")
        self.total = ttk.Label(right, text="", foreground="#333")
        self.total.grid(row=3, column=0, sticky="w", **PAD)

        self.after(100, self.pump)
        self.after(200, self.initial_view)
        self.background(self._load_index)

    # --- background work (results come back through self.events) -------------

    def background(self, func, *args):
        def run():
            try:
                func(*args)
            except Exception as exc:          # noqa: BLE001 - shown to the user
                self.events.put(("error", str(exc)))
        threading.Thread(target=run, daemon=True).start()

    def _load_index(self):
        self.events.put(("index", regions.Index.load(self.cache_dir, log=lambda m: self.events.put(("log", m)))))

    def _compute_overlap(self, lat, lon, radius):
        touched, suggested = self.index.overlapping(lat, lon, radius)
        self.events.put(("overlap", (touched, {e.path for e in suggested})))

    def _fetch_sizes(self, paths):
        for path in paths:
            self.events.put(("size", (path, regions.download_size(path))))

    def pump(self):
        try:
            while True:
                kind, data = self.events.get_nowait()
                if kind == "index":
                    self.index = data
                    self.compute_overlap()
                elif kind == "overlap":
                    self.overlap, self.suggested = data
                    self.refresh_list()
                    self.redraw()
                elif kind == "size":
                    self.sizes[data[0]] = data[1]
                    self.refresh_list(rebuild=False)
                elif kind == "geocoded":
                    lat, lon, label = data
                    self.center = (round(lat, 4), round(lon, 4))
                    self._place_label = label.split(",")[0]
                    self.log(f"Found: {label}")
                    self.circle_changed(fit=True)
                elif kind == "log":
                    self.log(data)
                elif kind == "error":
                    self.status.configure(text=f"Error: {data}")
                    self.log(f"Region picker: {data}")
        except queue.Empty:
            pass
        self.after(100, self.pump)

    # --- the circle -------------------------------------------------------------

    def initial_view(self):
        self.fit()
        self.redraw()

    def fit(self):
        pts = regions.circle(*self.center, self.radius.get(), 36)
        lats, lons = [p[0] for p in pts], [p[1] for p in pts]
        self.map.fit_bounding_box((max(lats), min(lons)), (min(lats), max(lons)))

    def on_map_click(self, coords):
        self.center = (round(coords[0], 4), round(coords[1], 4))
        self._place_label = None
        self.circle_changed()

    def radius_typed(self):
        try:
            value = float(self.radius_text.get())
        except ValueError:
            return
        self.radius.set(min(max(value, RADIUS_MIN), RADIUS_MAX))
        self.circle_changed(fit=True)

    def circle_changed(self, fit=False):
        self.radius_text.set(f"{round(self.radius.get()):g}")
        if self.name.get() == self._auto_name:
            lat, lon = self.center
            where = self._place_label or f"{lat:.3f}, {lon:.3f}"
            self._auto_name = f"{where} +{round(self.radius.get())} km"
            self.name.set(self._auto_name)
        if fit:
            self.fit()
        # Redraw soon; recompute the overlap once the slider rests.
        if self._redraw_job:
            self.after_cancel(self._redraw_job)
        self._redraw_job = self.after(80, self.redraw)
        if self._overlap_job:
            self.after_cancel(self._overlap_job)
        self._overlap_job = self.after(500, self.compute_overlap)

    def compute_overlap(self):
        if self.index is None:
            return
        self.status.configure(text="Finding the extracts the circle overlaps...")
        self.background(self._compute_overlap, *self.center, round(self.radius.get()))

    def redraw(self):
        self._redraw_job = None
        lat, lon = self.center
        self.center_label.configure(text=f"Centre {lat:.4f}, {lon:.4f}")
        for shape in [self._circle_shape, self._marker, *self._outline_shapes]:
            if shape is not None:
                shape.delete()
        self._outline_shapes = []
        if self.index is not None:
            for path, var in self.checked.items():
                extract = self.index.extracts.get(path)
                if var.get() and extract:
                    for ring in extract.outline():
                        self._outline_shapes.append(self.map.set_polygon(
                            ring, outline_color=OUTLINE_COLOR, fill_color=None, border_width=1))
        self._circle_shape = self.map.set_polygon(regions.circle(lat, lon, self.radius.get()),
                                                  outline_color=CIRCLE_COLOR, fill_color=None, border_width=2)
        self._marker = self.map.set_marker(lat, lon)

    def find_place(self):
        query = self.place.get().strip()
        if not query:
            return

        def look_up():
            hit = regions.geocode(query)
            if hit is None:
                self.events.put(("error", f"no place found for '{query}'"))
            else:
                self.events.put(("geocoded", hit))

        self.background(look_up)

    # --- the checklist ------------------------------------------------------------

    def search_changed(self):
        if self.index is not None:
            self.search_hits = self.index.search(self.search.get())
            self.refresh_list()

    def tick_suggested(self):
        for path, var in self.checked.items():
            var.set(path in self.suggested)
        for path in self.suggested:
            self.checked.setdefault(path, tk.BooleanVar(value=True)).set(True)
        self.refresh_list()
        self.redraw()

    def refresh_list(self, rebuild=True):
        if self.index is None:
            return
        overlap_paths = {e.path for e in self.overlap}
        shown = {e.path: e for e in self.overlap}
        for path, var in self.checked.items():
            if var.get() and path in self.index.extracts:
                shown.setdefault(path, self.index.extracts[path])
        for e in self.search_hits:
            shown.setdefault(e.path, e)
        missing = [p for p in shown if p not in self.sizes]
        if missing:
            for p in missing:
                self.sizes[p] = None
            self.background(self._fetch_sizes, missing)

        if rebuild:
            for child in self.list_frame.winfo_children():
                child.destroy()
            ordered = sorted(shown.values(), key=lambda e: (e.path not in self.suggested,
                                                            e.path not in overlap_paths, e.label))
            self._rows = {}
            for row, e in enumerate(ordered):
                var = self.checked.setdefault(e.path, tk.BooleanVar(value=False))
                cb = ttk.Checkbutton(self.list_frame, variable=var, command=self.ticks_changed)
                cb.grid(row=row, column=0, sticky="w")
                self._rows[e.path] = cb
            self.status.configure(text=f"{len(self.overlap)} extracts overlap the circle; "
                                       f"{len(self.suggested)} suggested (smallest covering set)")
        for path, cb in getattr(self, "_rows", {}).items():
            e = shown.get(path) or self.index.extracts[path]
            tags = []
            if path in self.suggested:
                tags.append("suggested")
            elif path not in overlap_paths and not any(x.path == path for x in self.search_hits):
                tags.append("outside the circle")
            text = f"{e.label}  {_size(self.sizes.get(path))}" + (f"  ({', '.join(tags)})" if tags else "")
            cb.configure(text=text)
        self.update_total()

    def ticks_changed(self):
        self.update_total()
        self.redraw()

    def update_total(self):
        ticked = [p for p, v in self.checked.items() if v.get()]
        size = sum(self.sizes.get(p) or 0 for p in ticked)
        unknown = any(self.sizes.get(p) is None for p in ticked)
        self.total.configure(text=f"Ticked: {len(ticked)} extract(s), {_size(size) or '0 MB'}"
                                  f"{' +' if unknown else ''} to download (once; cached)")

    # --- the result -----------------------------------------------------------------

    def get(self):
        extracts = sorted(p for p, v in self.checked.items() if v.get())
        if not extracts:
            raise ValueError("Tick at least one OSM extract.")
        lat, lon = self.center
        return Region(name=self.name.get().strip() or f"{lat:.3f},{lon:.3f} +{round(self.radius.get())} km",
                      center_lat=lat, center_lon=lon, radius_km=float(round(self.radius.get())),
                      extracts=extracts)
