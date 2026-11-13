"""The "Offline map tiles" panel: which tiles to render (zooms for the
circle, around towns, extra areas), a live estimate, and the Render button.

The towns come from the extracts (OSM place=city/town in the circle),
found in the background once and kept in the region's render folder.
"""

import json
import queue
import threading
import tkinter as tk
from tkinter import ttk

from . import rastertiles

PAD = {"padx": 4, "pady": 2}


def _spin(parent, var, lo, hi, width=4, command=None):
    box = ttk.Spinbox(parent, from_=lo, to=hi, width=width, textvariable=var, command=command)
    if command:
        box.bind("<KeyRelease>", lambda _e: command())
    return box


class RasterPanel(ttk.LabelFrame):

    def __init__(self, parent, settings, get_region, get_settings, on_render, log):
        super().__init__(parent, text="Offline map tiles (OSM style, rendered here with toolkit/render-tiles.sh)")
        self.get_region, self.get_settings, self.log = get_region, get_settings, log
        plan = rastertiles.TilePlan.from_dict(settings.raster)
        self.events = queue.Queue()
        self.towns, self.towns_key = None, None
        self._job = None

        self.include = tk.BooleanVar(value=settings.raster.get("include", True))
        self.circle_min = tk.IntVar(value=plan.circle_zooms[0])
        self.circle_max = tk.IntVar(value=plan.circle_zooms[1])
        self.use_towns = tk.BooleanVar(value=plan.towns)
        self.town_min = tk.IntVar(value=plan.town_zooms[0])
        self.town_max = tk.IntVar(value=plan.town_zooms[1])
        self.city_r = tk.DoubleVar(value=plan.city_radius_km)
        self.town_r = tk.DoubleVar(value=plan.town_radius_km)
        self.areas = [rastertiles.Area(**a) for a in plan.extra_areas]

        row = ttk.Frame(self)
        row.grid(row=0, column=0, columnspan=3, sticky="w")
        ttk.Checkbutton(row, text="Include in the map", variable=self.include).pack(side="left", **PAD)
        ttk.Label(row, text="  Circle zooms").pack(side="left")
        _spin(row, self.circle_min, 0, 19, command=self.changed).pack(side="left")
        ttk.Label(row, text="to").pack(side="left")
        _spin(row, self.circle_max, 0, 19, command=self.changed).pack(side="left")
        ttk.Checkbutton(row, text="  Towns: zooms", variable=self.use_towns, command=self.changed).pack(side="left")
        _spin(row, self.town_min, 0, 19, command=self.changed).pack(side="left")
        ttk.Label(row, text="to").pack(side="left")
        _spin(row, self.town_max, 0, 19, command=self.changed).pack(side="left")
        ttk.Label(row, text="  within km of a city").pack(side="left")
        _spin(row, self.city_r, 0.5, 50, width=4, command=self.changed).pack(side="left")
        ttk.Label(row, text="/ town").pack(side="left")
        _spin(row, self.town_r, 0.5, 50, width=4, command=self.changed).pack(side="left")

        areas = ttk.Frame(self)
        areas.grid(row=1, column=0, columnspan=3, sticky="ew")
        areas.columnconfigure(1, weight=1)
        ttk.Label(areas, text="Extra areas").grid(row=0, column=0, sticky="nw", **PAD)
        self.area_list = tk.Listbox(areas, height=3)
        self.area_list.grid(row=0, column=1, sticky="ew", **PAD)
        buttons = ttk.Frame(areas)
        buttons.grid(row=0, column=2, sticky="n")
        self.new_r = tk.DoubleVar(value=5.0)
        self.new_min = tk.IntVar(value=14)
        self.new_max = tk.IntVar(value=17)
        line = ttk.Frame(buttons)
        line.pack(anchor="w")
        _spin(line, self.new_r, 0.5, 100, width=4).pack(side="left")
        ttk.Label(line, text="km, zooms").pack(side="left")
        _spin(line, self.new_min, 0, 19).pack(side="left")
        ttk.Label(line, text="to").pack(side="left")
        _spin(line, self.new_max, 0, 19).pack(side="left")
        ttk.Button(buttons, text="Add at the map's centre", command=self.add_area).pack(anchor="w", **PAD)
        ttk.Button(buttons, text="Remove", command=self.remove_area).pack(anchor="w", **PAD)

        bottom = ttk.Frame(self)
        bottom.grid(row=2, column=0, columnspan=3, sticky="ew")
        bottom.columnconfigure(0, weight=1)
        self.estimate = ttk.Label(bottom, text="", foreground="#333", wraplength=760, justify="left")
        self.estimate.grid(row=0, column=0, sticky="w", **PAD)
        self.render_button = ttk.Button(bottom, text="Render offline tiles", command=on_render)
        self.render_button.grid(row=0, column=1, sticky="e", **PAD)
        self.columnconfigure(0, weight=1)

        self.refresh_areas()
        self.after(150, self.pump)
        self.after(1000, self.changed)

    # --- areas ----------------------------------------------------------------

    def add_area(self):
        region = self.get_region()
        if region is None:
            return
        self.areas.append(rastertiles.Area(region.center_lat, region.center_lon, float(self.new_r.get()),
                                           int(self.new_min.get()), int(self.new_max.get()), region.name))
        self.refresh_areas()
        self.changed()

    def remove_area(self):
        sel = self.area_list.curselection()
        if sel:
            del self.areas[sel[0]]
            self.refresh_areas()
            self.changed()

    def refresh_areas(self):
        self.area_list.delete(0, "end")
        for a in self.areas:
            self.area_list.insert("end", f"{a.lat:.4f}, {a.lon:.4f}  {a.radius_km:g} km  zooms {a.min_zoom}-{a.max_zoom}"
                                         + (f"  ({a.name})" if a.name else ""))

    # --- the plan and its estimate ------------------------------------------------

    def plan(self):
        try:
            return rastertiles.TilePlan(
                circle_zooms=(int(self.circle_min.get()), int(self.circle_max.get())),
                towns=self.use_towns.get(),
                town_zooms=(int(self.town_min.get()), int(self.town_max.get())),
                city_radius_km=float(self.city_r.get()), town_radius_km=float(self.town_r.get()),
                extra_areas=[a.__dict__ for a in self.areas])
        except (tk.TclError, ValueError):
            return None

    def settings_dict(self):
        plan = self.plan() or rastertiles.TilePlan()
        return {**plan.to_dict(), "include": self.include.get()}

    def changed(self):
        if self._job:
            self.after_cancel(self._job)
        self._job = self.after(400, self.update_estimate)

    def update_estimate(self):
        self._job = None
        plan, region = self.plan(), self.get_region()
        if plan is None or region is None:
            return
        threading.Thread(target=self._estimate, args=(plan, region), daemon=True).start()

    def _estimate(self, plan, region):
        try:
            from .__main__ import render_work

            settings = self.get_settings()
            work, paths = render_work(settings)
            towns_file = work / "towns.json"
            if plan.towns and not towns_file.exists():
                self.events.put(("text", "Finding the towns in the circle..."))
                towns = rastertiles.find_towns(paths, region.center_lat, region.center_lon, region.radius_km)
                work.mkdir(parents=True, exist_ok=True)
                towns_file.write_text(json.dumps(towns, ensure_ascii=False))
            towns = json.loads(towns_file.read_text()) if towns_file.exists() else []
            tiles = rastertiles.plan_tiles(plan, region, towns if plan.towns else [])
            counts, est = rastertiles.estimate(tiles)
            files = rastertiles.rendered_files(work)
            done = rastertiles.summarize(files)["tiles_count"] if files else 0
            per_zoom = ", ".join(f"z{z} {n:,}" for z, n in counts.items())
            text = (f"{len(tiles):,} tiles, about {est / 1e9:.1f} GB (~{est / rastertiles.MAX_FILE_BYTES + 0.5:.0f} "
                    f"file(s) under 2 GiB)" + (f", {len(towns)} towns" if plan.towns else "") + f".  {per_zoom}."
                    + (f"  Rendered for this region so far: {done:,} tiles in {len(files)} file(s)." if files
                       else "  Nothing rendered for this region yet."))
        except SystemExit as exc:
            text = f"Estimate after the extracts are downloaded ({exc})"
        except Exception as exc:          # noqa: BLE001 - shown in the panel
            text = f"Can't estimate: {exc}"
        self.events.put(("text", text))

    def pump(self):
        try:
            while True:
                kind, data = self.events.get_nowait()
                if kind == "text":
                    self.estimate.configure(text=data)
        except queue.Empty:
            pass
        self.after(150, self.pump)
