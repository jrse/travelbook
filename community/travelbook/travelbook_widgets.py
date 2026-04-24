import math
import threading
from typing import Dict, List, Optional, Set, Tuple

import gi
import requests

from travelbook_core import (
    CLUSTER_COLOR_FILL,
    CLUSTER_COLOR_STROKE,
    APP_ID,
    MIN_CANVAS_SIZE,
    Poi,
    RADAR_LABEL_LIMIT,
    RADAR_TILE_FETCH_TIMEOUT_SECS,
    RADAR_TILE_REQUESTS_PER_DRAW,
    RADAR_TILE_OPACITY,
    RADAR_TILE_SIZE,
    RADAR_TILE_URL_TEMPLATE,
    RADAR_TILE_ZOOM_MAX,
    RADAR_TILE_ZOOM_MIN,
)


gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
gi.require_version("Pango", "1.0")
gi.require_version("PangoCairo", "1.0")
from gi.repository import Gdk, GdkPixbuf, GLib, Gtk, Pango, PangoCairo, cairo  # noqa: E402


def _layout_cache_key(widget: Gtk.DrawingArea, text: str, size: float) -> Tuple[str, int, str]:
    settings = Gtk.Settings.get_default()
    font_name = ""
    if settings is not None:
        font_name = str(settings.get_property("gtk-font-name") or "")
    return text, int(size * Pango.SCALE), font_name


def _draw_text(
    widget: Gtk.DrawingArea,
    cr: cairo.Context,
    text: str,
    x: float,
    y: float,
    *,
    size: float,
) -> None:
    cache = getattr(widget, "_text_layout_cache", None)
    cache_key = _layout_cache_key(widget, text, size)
    layout = cache.get(cache_key) if cache is not None else None
    if layout is None:
        layout = widget.create_pango_layout(text)
        description = Pango.FontDescription()
        settings = Gtk.Settings.get_default()
        if settings is not None:
            font_name = settings.get_property("gtk-font-name")
            if font_name:
                description = Pango.FontDescription(font_name)
        description.set_absolute_size(int(size * Pango.SCALE))
        layout.set_font_description(description)
        if cache is not None:
            cache[cache_key] = layout
    cr.move_to(x, y)
    PangoCairo.show_layout(cr, layout)


class RadarArea(Gtk.DrawingArea):
    def __init__(self, app: "TravelbookApp") -> None:
        super().__init__()
        self.app = app
        self.canvas_size = MIN_CANVAS_SIZE
        self.center = self.canvas_size // 2
        self._pinch_base_zoom = 1.0
        self._projected_points: List[Tuple[Poi, float, float]] = []
        self._tile_cache: Dict[Tuple[int, int, int], GdkPixbuf.Pixbuf] = {}
        self._pending_tiles: Set[Tuple[int, int, int]] = set()
        self._failed_tiles: Set[Tuple[int, int, int]] = set()
        self._text_layout_cache: Dict[Tuple[str, int, str], Pango.Layout] = {}
        self._draw_queued = False

        self.set_size_request(self.canvas_size, self.canvas_size)
        self.set_can_focus(True)
        self.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)
        self.connect("draw", self.on_draw)
        self.connect("button-press-event", self.on_button_press)

        self.zoom_gesture = None
        try:
            self.zoom_gesture = Gtk.GestureZoom.new(self)
            self.zoom_gesture.connect("begin", self.on_zoom_begin)
            self.zoom_gesture.connect("scale-changed", self.on_zoom_scale_changed)
        except Exception:
            self.zoom_gesture = None

    def _metrics(self) -> Tuple[float, float, float, float]:
        center = float(self.center)
        max_r = float(max(200, min(self.center - 50, 900)))
        radius_m = float(self.app.get_effective_query_radius())
        scale = (max_r / radius_m) * self.app.zoom_factor
        return center, max_r, scale, radius_m / self.app.zoom_factor

    def on_zoom_begin(self, _gesture, _sequence):
        self._pinch_base_zoom = self.app.zoom_factor

    def on_zoom_scale_changed(self, _gesture, scale_delta: float):
        if not math.isfinite(scale_delta) or scale_delta <= 0:
            return
        self.app.set_zoom(self._pinch_base_zoom * scale_delta)

    def on_button_press(self, _widget, event: Gdk.EventButton):
        if event.button != 1 or not self._projected_points:
            return False

        try:
            nearest: Optional[Poi] = None
            nearest_dist = 20.0
            for poi, x, y in self._projected_points:
                distance = math.hypot(event.x - x, event.y - y)
                if distance < nearest_dist:
                    nearest = poi
                    nearest_dist = distance

            if nearest is not None:
                self.app.select_poi(nearest)
                return True
        except Exception:
            return False

        return False

    def schedule_draw(self) -> None:
        if self._draw_queued:
            return
        self._draw_queued = True

        def _flush():
            self._draw_queued = False
            self.queue_draw()
            return False

        GLib.idle_add(_flush)

    def _visible_radius_to_zoom(self, lat: float, max_r: float, visible_radius_m: float) -> int:
        if visible_radius_m <= 0 or max_r <= 0:
            return RADAR_TILE_ZOOM_MIN
        meters_per_screen_px = visible_radius_m / max_r
        cos_lat = max(0.2, math.cos(math.radians(lat)))
        zoom = math.log2((156543.03392 * cos_lat) / meters_per_screen_px)
        return max(RADAR_TILE_ZOOM_MIN, min(RADAR_TILE_ZOOM_MAX, int(round(zoom))))

    def _latlon_to_world_px(self, lat: float, lon: float, zoom: int) -> Tuple[float, float]:
        scale = float(1 << zoom) * RADAR_TILE_SIZE
        lat_rad = math.radians(max(-85.05112878, min(85.05112878, lat)))
        x = (lon + 180.0) / 360.0 * scale
        y = (1.0 - math.asinh(math.tan(lat_rad)) / math.pi) / 2.0 * scale
        return x, y

    def _request_tile(self, key: Tuple[int, int, int]) -> None:
        if key in self._tile_cache or key in self._pending_tiles or key in self._failed_tiles:
            return
        self._pending_tiles.add(key)
        thread = threading.Thread(target=self._fetch_tile, args=(key,), daemon=True)
        thread.start()

    def _fetch_tile(self, key: Tuple[int, int, int]) -> None:
        z, x, y = key
        pixbuf = None
        try:
            response = requests.get(
                RADAR_TILE_URL_TEMPLATE.format(z=z, x=x, y=y),
                headers={"User-Agent": f"{APP_ID}/radar-map"},
                timeout=RADAR_TILE_FETCH_TIMEOUT_SECS,
            )
            response.raise_for_status()
            loader = GdkPixbuf.PixbufLoader.new_with_type("png")
            loader.write(response.content)
            loader.close()
            pixbuf = loader.get_pixbuf()
        except Exception:
            self._failed_tiles.add(key)
        finally:
            self._pending_tiles.discard(key)

        if pixbuf is not None:
            self._tile_cache[key] = pixbuf
            self.schedule_draw()

    def _draw_map_background(
        self,
        cr: cairo.Context,
        center: float,
        max_r: float,
        visible_radius_m: float,
        radar_heading: float,
    ) -> None:
        if not self.app.current_location:
            return

        lat, lon = self.app.current_location
        zoom = self._visible_radius_to_zoom(lat, max_r, visible_radius_m)
        center_world_x, center_world_y = self._latlon_to_world_px(lat, lon, zoom)
        cos_lat = max(0.2, math.cos(math.radians(lat)))
        meters_per_tile_px = 156543.03392 * cos_lat / float(1 << zoom)
        meters_per_screen_px = visible_radius_m / max_r
        scale = meters_per_tile_px / meters_per_screen_px
        if not math.isfinite(scale) or scale <= 0:
            return

        half_span_world_px = (max_r * math.sqrt(2.0)) / scale
        world_tile_count = 1 << zoom
        tile_x_min = max(0, int(math.floor((center_world_x - half_span_world_px) / RADAR_TILE_SIZE)) - 1)
        tile_x_max = min(world_tile_count - 1, int(math.floor((center_world_x + half_span_world_px) / RADAR_TILE_SIZE)) + 1)
        tile_y_min = max(0, int(math.floor((center_world_y - half_span_world_px) / RADAR_TILE_SIZE)) - 1)
        tile_y_max = min(world_tile_count - 1, int(math.floor((center_world_y + half_span_world_px) / RADAR_TILE_SIZE)) + 1)

        cr.save()
        cr.arc(center, center, max_r, 0, 2 * math.pi)
        cr.clip()
        cr.translate(center, center)
        cr.rotate(math.radians(-radar_heading))
        requests_started = 0

        for tile_x in range(tile_x_min, tile_x_max + 1):
            for tile_y in range(tile_y_min, tile_y_max + 1):
                key = (zoom, tile_x, tile_y)
                pixbuf = self._tile_cache.get(key)
                if pixbuf is None:
                    if requests_started < RADAR_TILE_REQUESTS_PER_DRAW:
                        self._request_tile(key)
                        requests_started += 1
                    continue
                tile_world_x = tile_x * RADAR_TILE_SIZE
                tile_world_y = tile_y * RADAR_TILE_SIZE
                draw_x = (tile_world_x - center_world_x) * scale
                draw_y = (tile_world_y - center_world_y) * scale
                cr.save()
                cr.translate(draw_x, draw_y)
                cr.scale(scale, scale)
                Gdk.cairo_set_source_pixbuf(cr, pixbuf, 0, 0)
                cr.paint_with_alpha(RADAR_TILE_OPACITY)
                cr.restore()

        cr.restore()

    def on_draw(self, _widget, cr: cairo.Context):
        center, max_r, scale, visible_radius_m = self._metrics()
        radar_heading = self.app.get_effective_heading() or 0.0

        cr.set_source_rgb(0.04, 0.08, 0.10)
        cr.paint()

        self._draw_map_background(cr, center, max_r, visible_radius_m, radar_heading)

        cr.set_line_width(1)
        cr.set_source_rgb(0.15, 0.45, 0.35)
        ring_ratios = [0.25, 0.5, 0.75, 1.0]
        for ratio in ring_ratios:
            cr.arc(center, center, max_r * ratio, 0, 2 * math.pi)
            cr.stroke()

        cr.move_to(center - max_r, center)
        cr.line_to(center + max_r, center)
        cr.move_to(center, center - max_r)
        cr.line_to(center, center + max_r)
        cr.stroke()

        cr.set_source_rgb(0.60, 0.85, 0.72)
        for ratio in ring_ratios:
            dist = int(visible_radius_m * ratio)
            _draw_text(self, cr, f"{dist} m", center + 6, center - (max_r * ratio) - 12, size=12)

        cr.set_source_rgb(0.15, 1.0, 0.25)
        cr.arc(center, center, 8, 0, 2 * math.pi)
        cr.fill()

        self._projected_points = []

        if not self.app.current_location:
            cr.set_source_rgb(0.8, 0.8, 0.8)
            _draw_text(self, cr, "Keine GPS-Position verfuegbar", center - 180, center + 22, size=18)
            return

        for cluster in self.app.clusters:
            cluster_distance = self.app._distance_m(
                self.app.current_location[0],
                self.app.current_location[1],
                cluster.center_lat,
                cluster.center_lon,
            )
            cluster_bearing = self.app._bearing_deg(
                self.app.current_location[0],
                self.app.current_location[1],
                cluster.center_lat,
                cluster.center_lon,
            )
            cluster_dist_px = cluster_distance * scale
            if cluster_dist_px > max_r:
                continue
            cluster_angle = math.radians(cluster_bearing - radar_heading - 90)
            cluster_x = center + math.cos(cluster_angle) * cluster_dist_px
            cluster_y = center + math.sin(cluster_angle) * cluster_dist_px
            cluster_radius = max(14.0, cluster.radius_m * scale)

            cr.set_source_rgba(*CLUSTER_COLOR_FILL)
            cr.arc(cluster_x, cluster_y, cluster_radius, 0, 2 * math.pi)
            cr.fill_preserve()
            cr.set_source_rgba(*CLUSTER_COLOR_STROKE)
            cr.set_line_width(2)
            cr.stroke()

        visible_label_count = 0
        for poi in self.app.pois:
            dist_px = poi.distance_m * scale
            if dist_px > max_r:
                continue
            angle = math.radians(poi.bearing_deg - radar_heading - 90)
            x = center + math.cos(angle) * dist_px
            y = center + math.sin(angle) * dist_px

            red, green, blue = self.app.get_color_for_filter(poi.category_filter)
            cr.set_source_rgb(red, green, blue)
            cr.arc(x, y, 6, 0, 2 * math.pi)
            cr.fill()
            if poi.cluster_id >= 0:
                cr.set_source_rgb(1.0, 1.0, 1.0)
                cr.set_line_width(1.5)
                cr.arc(x, y, 7.5, 0, 2 * math.pi)
                cr.stroke()

            cr.set_source_rgb(1, 1, 1)
            if visible_label_count < RADAR_LABEL_LIMIT:
                label = poi.name[:20]
                _draw_text(self, cr, label, x + 8, y - 14, size=12)
                visible_label_count += 1
            self._projected_points.append((poi, x, y))


class NavigationArea(Gtk.DrawingArea):
    def __init__(self, app: "TravelbookApp") -> None:
        super().__init__()
        self.app = app
        self._text_layout_cache: Dict[Tuple[str, int, str], Pango.Layout] = {}
        self._draw_queued = False
        self.connect("draw", self.on_draw)

    def schedule_draw(self) -> None:
        if self._draw_queued:
            return
        self._draw_queued = True

        def _flush():
            self._draw_queued = False
            self.queue_draw()
            return False

        GLib.idle_add(_flush)

    def on_draw(self, _widget, cr: cairo.Context):
        allocation = self.get_allocation()
        width, height = float(allocation.width), float(allocation.height)
        center_x, center_y = width / 2.0, height / 2.0
        radius = max(40.0, min(width, height) * 0.34)

        cr.set_source_rgb(0.05, 0.09, 0.12)
        cr.paint()

        cr.set_line_width(2)
        cr.set_source_rgb(0.22, 0.55, 0.42)
        cr.arc(center_x, center_y, radius, 0, 2 * math.pi)
        cr.stroke()

        nav = self.app.get_navigation_info()
        if nav is None:
            cr.set_source_rgb(0.85, 0.85, 0.85)
            _draw_text(self, cr, "POI im Radar antippen fuer Navigation", 18, center_y - 10, size=15)
            return

        poi, _distance, _bearing, _heading, turn = nav

        cr.set_source_rgb(0.30, 0.75, 0.98)
        top_label = poi.name[:10] or "Ziel"
        _draw_text(self, cr, top_label, center_x - 18, center_y - radius - 18, size=13)

        angle = math.radians(turn - 90)
        tip_x = center_x + math.cos(angle) * (radius - 8)
        tip_y = center_y + math.sin(angle) * (radius - 8)

        cr.set_source_rgb(0.96, 0.26, 0.22)
        cr.set_line_width(5)
        cr.move_to(center_x, center_y)
        cr.line_to(tip_x, tip_y)
        cr.stroke()

        left = angle + math.radians(150)
        right = angle - math.radians(150)
        wing = 14
        cr.move_to(tip_x, tip_y)
        cr.line_to(tip_x + math.cos(left) * wing, tip_y + math.sin(left) * wing)
        cr.line_to(tip_x + math.cos(right) * wing, tip_y + math.sin(right) * wing)
        cr.close_path()
        cr.fill()
