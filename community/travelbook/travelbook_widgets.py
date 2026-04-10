import math
from typing import List, Optional, Tuple

import gi

from travelbook_core import (
    CLUSTER_COLOR_FILL,
    CLUSTER_COLOR_STROKE,
    MIN_CANVAS_SIZE,
    Poi,
)


gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, Gtk, cairo  # noqa: E402


class RadarArea(Gtk.DrawingArea):
    def __init__(self, app: "TravelbookApp") -> None:
        super().__init__()
        self.app = app
        self.canvas_size = MIN_CANVAS_SIZE
        self.center = self.canvas_size // 2
        self._pinch_base_zoom = 1.0
        self._projected_points: List[Tuple[Poi, float, float]] = []

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

    def on_draw(self, _widget, cr: cairo.Context):
        center, max_r, scale, visible_radius_m = self._metrics()
        radar_heading = self.app.get_effective_heading() or 0.0

        cr.set_source_rgb(0.04, 0.08, 0.10)
        cr.paint()

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
        cr.set_font_size(12)
        for ratio in ring_ratios:
            dist = int(visible_radius_m * ratio)
            cr.move_to(center + 6, center - (max_r * ratio) - 4)
            cr.show_text(f"{dist} m")

        cr.set_source_rgb(0.15, 1.0, 0.25)
        cr.arc(center, center, 8, 0, 2 * math.pi)
        cr.fill()

        self._projected_points = []

        if not self.app.current_location:
            cr.set_source_rgb(0.8, 0.8, 0.8)
            cr.select_font_face("Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL)
            cr.set_font_size(18)
            cr.move_to(center - 180, center + 40)
            cr.show_text("Keine GPS-Position verfuegbar")
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
            cr.set_font_size(12)
            label = poi.name[:20]
            cr.move_to(x + 8, y - 6)
            cr.show_text(label)
            self._projected_points.append((poi, x, y))


class NavigationArea(Gtk.DrawingArea):
    def __init__(self, app: "TravelbookApp") -> None:
        super().__init__()
        self.app = app
        self.connect("draw", self.on_draw)

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
            cr.set_font_size(15)
            cr.move_to(18, center_y)
            cr.show_text("POI im Radar antippen fuer Navigation")
            return

        poi, _distance, _bearing, _heading, turn = nav

        cr.set_source_rgb(0.30, 0.75, 0.98)
        cr.set_font_size(13)
        top_label = poi.name[:10] or "Ziel"
        cr.move_to(center_x - 18, center_y - radius - 8)
        cr.show_text(top_label)

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
