#!/usr/bin/env python3
import math
import threading
import uuid
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import gi

from travelbook_core import (
    APP_ID,
    CANVAS_PADDING,
    CATEGORY_COLORS,
    CITY_POI_FILTERS,
    CITY_POI_RADIUS_M,
    DEFAULT_RADIUS_M,
    DRIVE_MODE_AVG_WINDOW_SECS,
    DIARY_APP_VERSION,
    INDICATOR_COLORS,
    MAX_RADIUS_M,
    MAX_ZOOM,
    MIN_CANVAS_SIZE,
    MIN_ZOOM,
    OLLAMA_BASE_URL_DEFAULT,
    OLLAMA_DIARY_SYSTEM_PROMPT_DEFAULT,
    POI_OPTIONS,
    RADAR_HEADING_REDRAW_THRESHOLD_DEG,
    REGION_REFRESH_MOVE_M,
    REGION_REFRESH_SECS,
    UNKNOWN_COLOR,
    Cluster,
    Poi,
    build_overpass_query,
    compute_runtime_indicators,
    format_fix_age,
    parse_filter,
)
from travelbook_providers import CompassProvider, GeoClueProvider
from travelbook_services import (
    DiaryImproveError,
    PoiFetchError,
    average_speed_mps,
    assign_clusters,
    bearing_deg,
    calculate_speed_mps,
    calculate_navigation_info,
    detect_travel_mode,
    diary_file_path,
    distance_m,
    derive_travel_heading,
    effective_query_radius,
    fetch_pois,
    infer_category,
    is_city_poi,
    improve_diary_entry,
    load_diary_entries,
    load_app_settings,
    poi_refresh_distance,
    poi_refresh_interval,
    resolve_region,
    save_diary_entries,
    save_app_settings,
    should_refresh_pois,
    trim_location_samples,
)
from travelbook_widgets import NavigationArea, RadarArea


gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GdkPixbuf, GLib, Gtk, Pango, cairo  # noqa: E402

WEBKIT_AVAILABLE = False
WebKit2 = None
for _webkit_ver in ("4.1", "4.0"):
    try:
        gi.require_version("WebKit2", _webkit_ver)
        from gi.repository import WebKit2 as _WebKit2  # type: ignore

        WebKit2 = _WebKit2
        WEBKIT_AVAILABLE = True
        break
    except Exception:
        continue


class TravelbookApp(Gtk.Window):
    def __init__(self) -> None:
        super().__init__(title="travelbook")
        self.screen_width, self.screen_height = self._detect_screen_resolution()
        self.set_default_size(self.screen_width, self.screen_height)
        self.connect("destroy", self._on_destroy)

        self.radius_m = DEFAULT_RADIUS_M
        self.current_query_radius_m = DEFAULT_RADIUS_M
        self.zoom_factor = 0.7
        self.heading_deg: Optional[float] = None
        self.travel_heading_deg: Optional[float] = None
        self.compass_available = False
        self.current_location: Optional[Tuple[float, float]] = None
        self.current_location_ts: Optional[float] = None
        self.current_speed_mps: Optional[float] = None
        self.avg_speed_mps: Optional[float] = None
        self.travel_mode = "pedestrian"
        self.location_samples: List[Tuple[float, Tuple[float, float]]] = []
        self.last_real_gps_location: Optional[Tuple[float, float]] = None
        self.last_real_gps_fix_ts: Optional[float] = None
        self.previous_location: Optional[Tuple[float, float]] = None
        self.previous_location_ts: Optional[float] = None
        self.last_query_location: Optional[Tuple[float, float]] = None
        self.last_poi_query_ts: Optional[float] = None
        self.pois: List[Poi] = []
        self.clusters: List[Cluster] = []
        self.selected_poi: Optional[Poi] = None
        self.fetch_in_progress = False
        self.reload_requested = False
        self.active_poi_query_location: Optional[Tuple[float, float]] = None
        self.city_fetch_in_progress = False
        self.active_city_query: Optional[Tuple[str, float, float]] = None
        self.region_fetch_in_progress = False
        self.location_source = "unknown"
        self.network_state = "unknown"
        self.last_region_query_location: Optional[Tuple[float, float]] = None
        self.last_region_query_ts = 0.0
        self.region_city = "-"
        self.region_name = "-"
        self.region_wiki_url = "https://en.wikipedia.org"
        self.diary_date = date.today()
        self.diary_entries: List[Dict] = []
        self.diary_edit_id: Optional[str] = None
        self.tabs_collapsed: Optional[bool] = None
        self.radar_page_idx = -1
        self.profile_page_idx = -1
        self.nav_page_idx = -1
        self.diary_page_idx = -1
        self.research_page_idx = -1
        self.app_data_dir = Path.home() / ".local" / "share" / "travelbook"
        self.app_data_dir.mkdir(parents=True, exist_ok=True)
        self.diary_dir = self.app_data_dir / "diary"
        self.diary_dir.mkdir(parents=True, exist_ok=True)
        self.app_settings = load_app_settings(self.app_data_dir)
        self.ollama_base_url = self.app_settings.get("ollama_base_url", OLLAMA_BASE_URL_DEFAULT)
        self.ollama_diary_system_prompt = self.app_settings.get(
            "ollama_diary_system_prompt",
            OLLAMA_DIARY_SYSTEM_PROMPT_DEFAULT,
        )

        self.categories: Dict[str, bool] = {osm_filter: enabled for _label, osm_filter, enabled in POI_OPTIONS}
        self.category_labels: Dict[str, str] = {osm_filter: label for label, osm_filter, _ in POI_OPTIONS}
        self.category_colors: Dict[str, Tuple[float, float, float]] = {
            osm_filter: CATEGORY_COLORS[idx % len(CATEGORY_COLORS)] for idx, (_label, osm_filter, _enabled) in enumerate(POI_OPTIONS)
        }
        for city_label, city_filter in CITY_POI_FILTERS:
            self.category_labels[city_filter] = city_label
            self.category_colors[city_filter] = (0.35, 0.78, 0.98)

        self.filter_lookup: Dict[Tuple[str, str], str] = {}
        for _label, osm_filter, _enabled in POI_OPTIONS:
            parsed = parse_filter(osm_filter)
            if parsed:
                self.filter_lookup[parsed] = osm_filter

        self.geo_provider = GeoClueProvider()
        self.compass_provider = CompassProvider()

        self._build_ui()
        self.connect("size-allocate", self._on_window_size_allocate)
        self._center_radar_view()
        self._load_diary_day(self.diary_date)
        self._fit_window_to_screen()

        GLib.timeout_add(250, self._tick_heading)
        GLib.timeout_add_seconds(2, self._tick_location)
        self._tick_heading()
        self._tick_location()

    @staticmethod
    def _set_scroller_height(scroller: Gtk.ScrolledWindow, min_height: int, max_height: Optional[int] = None):
        target_max = min_height if max_height is None else max_height
        if scroller.get_min_content_height() != int(min_height):
            scroller.set_min_content_height(int(min_height))
        if scroller.get_max_content_height() != int(target_max):
            scroller.set_max_content_height(int(target_max))

    def _mode_icon_path(self, mode: str) -> Path:
        filename = "mode-drive.svg" if mode == "drive" else "mode-pedestrian.svg"
        return Path(__file__).resolve().with_name(filename)

    def _load_mode_icon(self, mode: str):
        try:
            return GdkPixbuf.Pixbuf.new_from_file_at_scale(str(self._mode_icon_path(mode)), 18, 18, True)
        except Exception:
            return None

    def _on_destroy(self, *_):
        self.compass_provider.close()
        Gtk.main_quit()

    def _detect_screen_resolution(self) -> Tuple[int, int]:
        try:
            screen = Gdk.Screen.get_default()
            if screen is None:
                return 360, 640
            monitor = screen.get_primary_monitor()
            if monitor < 0:
                monitor = 0
            geom = screen.get_monitor_geometry(monitor)
            return max(320, int(geom.width)), max(480, int(geom.height))
        except Exception:
            return 360, 640

    def _fit_window_to_screen(self):
        target_w = max(320, self.screen_width - 4)
        target_h = max(480, self.screen_height - 4)
        self.resize(target_w, target_h)
        if self.screen_width <= 900:
            try:
                self.maximize()
            except Exception:
                pass

    def _build_ui(self) -> None:
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.add(root)

        self.top_nav_row = Gtk.Box(spacing=6)
        self.top_nav_row.set_border_width(4)
        root.pack_start(self.top_nav_row, False, False, 0)

        self.menu_button = Gtk.MenuButton()
        self.menu_button.set_tooltip_text("Menu")
        menu_image = Gtk.Image.new_from_icon_name("open-menu-symbolic", Gtk.IconSize.BUTTON)
        self.menu_button.add(menu_image)
        self.menu_button.set_no_show_all(True)
        self.top_nav_row.pack_start(self.menu_button, False, False, 0)

        self.gps_indicator_label = Gtk.Label()
        self.gps_indicator_label.set_xalign(0.0)
        self.gps_indicator_label.set_use_markup(True)
        self.gps_indicator_label.set_ellipsize(Pango.EllipsizeMode.END)
        self.gps_indicator_label.set_width_chars(12)
        self.gps_indicator_label.set_max_width_chars(16)
        self.top_nav_row.pack_start(self.gps_indicator_label, False, False, 0)

        self.network_indicator_label = Gtk.Label()
        self.network_indicator_label.set_xalign(0.0)
        self.network_indicator_label.set_use_markup(True)
        self.network_indicator_label.set_ellipsize(Pango.EllipsizeMode.END)
        self.network_indicator_label.set_width_chars(12)
        self.network_indicator_label.set_max_width_chars(16)
        self.top_nav_row.pack_start(self.network_indicator_label, False, False, 0)

        self.loading_indicator_label = Gtk.Label()
        self.loading_indicator_label.set_xalign(0.0)
        self.loading_indicator_label.set_use_markup(True)
        self.loading_indicator_label.set_ellipsize(Pango.EllipsizeMode.END)
        self.loading_indicator_label.set_width_chars(14)
        self.loading_indicator_label.set_max_width_chars(18)
        self.top_nav_row.pack_start(self.loading_indicator_label, False, False, 0)

        self.notebook = Gtk.Notebook()
        self.notebook.set_scrollable(True)
        self.notebook.connect("switch-page", self._on_notebook_switch_page)
        root.pack_start(self.notebook, True, True, 0)

        radar_page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        radar_page.set_border_width(8)

        controls = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)

        action_row = Gtk.Box(spacing=8)
        refresh_btn = Gtk.Button(label="POIs neu laden")
        refresh_btn.connect("clicked", lambda *_: self._refresh_pois(force=True))
        self.zoom_label = Gtk.Label(label="Zoom: 1.00x")
        self.zoom_label.set_xalign(0.0)
        self.mode_box = Gtk.Box(spacing=4)
        self.mode_icon = Gtk.Image()
        self.mode_box.pack_start(self.mode_icon, False, False, 0)
        action_row.pack_start(refresh_btn, False, False, 0)
        action_row.pack_start(self.zoom_label, True, True, 0)
        action_row.pack_start(self.mode_box, False, False, 0)
        controls.pack_start(action_row, False, False, 0)

        self.status_label = Gtk.Label(label="Starte GPS...")
        self.status_label.set_xalign(0.0)
        self.status_label.set_line_wrap(True)
        controls.pack_start(self.status_label, False, False, 0)

        self.poi_error_label = Gtk.Label(label="")
        self.poi_error_label.set_xalign(0.0)
        self.poi_error_label.set_line_wrap(True)
        self.poi_error_label.set_markup("")
        self.poi_error_label.hide()
        controls.pack_start(self.poi_error_label, False, False, 0)
        radar_page.pack_start(controls, False, False, 0)

        self.radar_area = RadarArea(self)
        self.scroller = Gtk.ScrolledWindow()
        self.scroller.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        self.scroller.connect("size-allocate", self._on_scroller_size_allocate)
        self.scroller.add(self.radar_area)
        radar_page.pack_start(self.scroller, True, True, 0)

        self.poi_header_label = Gtk.Label(label="POIs im Auto-Radius")
        self.poi_header_label.set_xalign(0.0)
        radar_page.pack_start(self.poi_header_label, False, False, 0)

        self.poi_listbox = Gtk.ListBox()
        self.poi_listbox.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.poi_listbox.connect("row-activated", self._on_poi_row_activated)
        self.poi_list_scroller = Gtk.ScrolledWindow()
        self.poi_list_scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.poi_list_scroller.set_min_content_height(120)
        self.poi_list_scroller.set_max_content_height(200)
        self.poi_list_scroller.add(self.poi_listbox)
        radar_page.pack_start(self.poi_list_scroller, False, True, 0)

        self.radar_page_idx = self.notebook.append_page(radar_page, Gtk.Label(label="Radar"))

        profile_page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        profile_page.set_border_width(12)
        profile_page.pack_start(Gtk.Label(label="Profil"), False, False, 0)

        self.manual_lat = Gtk.Entry()
        self.manual_lon = Gtk.Entry()
        self.manual_lat.set_placeholder_text("Fallback Latitude")
        self.manual_lon.set_placeholder_text("Fallback Longitude")

        row = Gtk.Box(spacing=8)
        row.pack_start(self.manual_lat, True, True, 0)
        row.pack_start(self.manual_lon, True, True, 0)
        profile_page.pack_start(Gtk.Label(label="Manuelle Position (nur wenn GPS fehlt):"), False, False, 0)
        profile_page.pack_start(row, False, False, 0)

        profile_page.pack_start(Gtk.Label(label="Ollama Basis-URL"), False, False, 0)
        self.ollama_base_url_entry = Gtk.Entry()
        self.ollama_base_url_entry.set_text(self.ollama_base_url)
        profile_page.pack_start(self.ollama_base_url_entry, False, False, 0)

        profile_page.pack_start(Gtk.Label(label="Ollama System-Prompt für Tagebuch"), False, False, 0)
        self.ollama_prompt_view = Gtk.TextView()
        self.ollama_prompt_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self.ollama_prompt_view.set_vexpand(False)
        self.ollama_prompt_view.set_hexpand(True)
        self.ollama_prompt_view.get_buffer().set_text(self.ollama_diary_system_prompt)
        self.ollama_prompt_scroller = Gtk.ScrolledWindow()
        self.ollama_prompt_scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.ollama_prompt_scroller.set_min_content_height(110)
        self.ollama_prompt_scroller.set_max_content_height(170)
        self.ollama_prompt_scroller.add(self.ollama_prompt_view)
        profile_page.pack_start(self.ollama_prompt_scroller, False, True, 0)

        ollama_save_btn = Gtk.Button(label="Ollama Einstellungen speichern")
        ollama_save_btn.connect("clicked", self._on_ollama_settings_save)
        profile_page.pack_start(ollama_save_btn, False, False, 0)

        profile_page.pack_start(Gtk.Label(label="POI-Kategorien"), False, False, 0)
        primary_category_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        extra_category_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        primary_groups: Dict[str, List[str]] = {}
        for label, key, enabled in POI_OPTIONS:
            if enabled:
                primary_groups.setdefault(label, []).append(key)
                continue

            cb_row = Gtk.Box(spacing=8)
            color = self.get_color_for_filter(key)
            color_dot = Gtk.DrawingArea()
            color_dot.set_size_request(12, 12)
            color_dot.connect("draw", self._draw_color_dot, color)
            cb_row.pack_start(color_dot, False, False, 0)

            cb = Gtk.CheckButton(label=self.category_labels[key])
            cb.set_active(enabled)
            cb.connect("toggled", self._on_category_toggled, key)
            cb_row.pack_start(cb, True, True, 0)
            extra_category_box.pack_start(cb_row, False, False, 0)

        for label, keys in primary_groups.items():
            cb_row = Gtk.Box(spacing=8)
            color = self.get_color_for_filter(keys[0])
            color_dot = Gtk.DrawingArea()
            color_dot.set_size_request(12, 12)
            color_dot.connect("draw", self._draw_color_dot, color)
            cb_row.pack_start(color_dot, False, False, 0)

            cb = Gtk.CheckButton(label=label)
            cb.set_active(all(self.categories[key] for key in keys))
            cb.connect("toggled", self._on_category_toggled, tuple(keys))
            cb_row.pack_start(cb, True, True, 0)
            primary_category_box.pack_start(cb_row, False, False, 0)

        profile_page.pack_start(primary_category_box, False, False, 0)

        if any(not enabled for _label, _key, enabled in POI_OPTIONS):
            extra_expander = Gtk.Expander(label="Weitere POI-Kategorien")
            extra_expander.set_expanded(False)
            extra_content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
            extra_content.set_border_width(6)
            extra_content.pack_start(extra_category_box, False, False, 0)
            extra_expander.add(extra_content)
            profile_page.pack_start(extra_expander, False, False, 0)

        profile_scroller = Gtk.ScrolledWindow()
        profile_scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        profile_scroller.add(profile_page)
        self.profile_page_idx = self.notebook.append_page(profile_scroller, Gtk.Label(label="Profil"))

        nav_page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        nav_page.set_border_width(12)
        nav_page.pack_start(Gtk.Label(label="Navigation zum ausgewaehlten POI"), False, False, 0)

        self.nav_target_label = Gtk.Label(label="Ziel: -")
        self.nav_target_label.set_xalign(0.0)
        nav_page.pack_start(self.nav_target_label, False, False, 0)

        self.nav_info_label = Gtk.Label(label="Distanz: - | Ziel oben | Turn: - | Kompass: -")
        self.nav_info_label.set_xalign(0.0)
        self.nav_info_label.set_line_wrap(True)
        nav_page.pack_start(self.nav_info_label, False, False, 0)

        self.nav_detail_label = Gtk.Label(label="POI: -")
        self.nav_detail_label.set_xalign(0.0)
        self.nav_detail_label.set_line_wrap(True)
        self.nav_detail_label.set_selectable(True)
        nav_page.pack_start(self.nav_detail_label, False, False, 0)

        self.city_table_status_label = Gtk.Label(label="Stadt-POIs: waehle eine Stadt im Radar.")
        self.city_table_status_label.set_xalign(0.0)
        self.city_table_status_label.set_line_wrap(True)
        nav_page.pack_start(self.city_table_status_label, False, False, 0)

        self.city_poi_store = Gtk.ListStore(str, str, str)
        self.city_poi_tree = Gtk.TreeView(model=self.city_poi_store)
        for idx, title in enumerate(("Name", "Kategorie", "Distanz")):
            renderer = Gtk.CellRendererText()
            column = Gtk.TreeViewColumn(title, renderer, text=idx)
            column.set_resizable(True)
            self.city_poi_tree.append_column(column)
        self.city_poi_scroller = Gtk.ScrolledWindow()
        self.city_poi_scroller.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        self.city_poi_scroller.set_min_content_height(140)
        self.city_poi_scroller.set_max_content_height(220)
        self.city_poi_scroller.add(self.city_poi_tree)
        nav_page.pack_start(self.city_poi_scroller, False, True, 0)

        self.nav_link_button = Gtk.LinkButton.new_with_label("https://example.com", "POI im Browser öffnen")
        self.nav_link_button.set_no_show_all(True)
        self.nav_link_button.hide()
        nav_page.pack_start(self.nav_link_button, False, False, 0)

        self.navigation_area = NavigationArea(self)
        self.navigation_area.set_hexpand(True)
        self.navigation_area.set_vexpand(True)
        nav_frame = Gtk.AspectFrame(xalign=0.5, yalign=0.5, ratio=1.0, obey_child=False)
        nav_frame.set_hexpand(True)
        nav_frame.set_vexpand(True)
        nav_frame.add(self.navigation_area)
        nav_page.pack_start(nav_frame, True, True, 0)

        self.nav_page_idx = self.notebook.append_page(nav_page, Gtk.Label(label="Navigation"))

        diary_page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        diary_page.set_border_width(10)

        day_controls = Gtk.Box(spacing=6)
        prev_btn = Gtk.Button(label="< Tag")
        prev_btn.connect("clicked", self._on_diary_prev_day)
        next_btn = Gtk.Button(label="Tag >")
        next_btn.connect("clicked", self._on_diary_next_day)
        today_btn = Gtk.Button(label="Heute")
        today_btn.connect("clicked", self._on_diary_today)
        diary_save_btn = Gtk.Button(label="Speichern")
        diary_save_btn.connect("clicked", self._on_diary_save_entry)
        self.diary_date_label = Gtk.Label(label="-")
        self.diary_date_label.set_xalign(0.0)
        day_controls.pack_start(prev_btn, False, False, 0)
        day_controls.pack_start(next_btn, False, False, 0)
        day_controls.pack_start(today_btn, False, False, 0)
        self.diary_save_btn = diary_save_btn
        day_controls.pack_start(self.diary_save_btn, False, False, 0)
        diary_page.pack_start(day_controls, False, False, 0)
        diary_page.pack_start(self.diary_date_label, False, False, 0)

        diary_view_controls = Gtk.Box(spacing=6)
        self.diary_editor_view_btn = Gtk.Button(label="Eintrag")
        self.diary_editor_view_btn.connect("clicked", lambda *_: self._show_diary_editor())
        self.diary_history_view_btn = Gtk.Button(label="Verlauf")
        self.diary_history_view_btn.connect("clicked", lambda *_: self._show_diary_history())
        diary_view_controls.pack_start(self.diary_editor_view_btn, False, False, 0)
        diary_view_controls.pack_start(self.diary_history_view_btn, False, False, 0)
        diary_page.pack_start(diary_view_controls, False, False, 0)

        self.diary_stack = Gtk.Stack()
        self.diary_stack.set_transition_type(Gtk.StackTransitionType.NONE)
        self.diary_stack.set_transition_duration(0)
        self.diary_stack.set_vexpand(True)

        diary_editor_page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        diary_editor_page.pack_start(Gtk.Label(label="Neuer Eintrag"), False, False, 0)
        self.diary_textview = Gtk.TextView()
        self.diary_textview.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self.diary_textview.set_vexpand(True)
        self.diary_textview.set_hexpand(True)
        self.diary_editor_scroller = Gtk.ScrolledWindow()
        self.diary_editor_scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.diary_editor_scroller.set_vexpand(True)
        self.diary_editor_scroller.set_hexpand(True)
        self.diary_editor_scroller.add(self.diary_textview)
        diary_editor_page.pack_start(self.diary_editor_scroller, True, True, 0)

        diary_btn_row = Gtk.Box(spacing=8)
        diary_clear_btn = Gtk.Button(label="Eingabe leeren")
        diary_clear_btn.connect("clicked", self._on_diary_clear_edit)
        self.diary_clear_btn = diary_clear_btn
        diary_btn_row.pack_start(self.diary_clear_btn, False, False, 0)
        diary_editor_page.pack_start(diary_btn_row, False, False, 0)

        diary_history_page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        diary_history_page.pack_start(Gtk.Label(label="Einträge dieses Tages"), False, False, 0)
        self.diary_listbox = Gtk.ListBox()
        self.diary_listbox.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.diary_listbox_row_selected_handler = self.diary_listbox.connect("row-selected", self._on_diary_row_selected)
        self.diary_list_scroller = Gtk.ScrolledWindow()
        self.diary_list_scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.diary_list_scroller.set_min_content_height(120)
        self.diary_list_scroller.set_max_content_height(220)
        self.diary_list_scroller.set_vexpand(True)
        self.diary_list_scroller.set_hexpand(True)
        self.diary_list_scroller.add(self.diary_listbox)
        diary_history_page.pack_start(self.diary_list_scroller, True, True, 0)

        self.diary_stack.add_named(diary_editor_page, "editor")
        self.diary_stack.add_named(diary_history_page, "history")
        diary_page.pack_start(self.diary_stack, True, True, 0)

        self.diary_page_idx = self.notebook.append_page(diary_page, Gtk.Label(label="Tagebuch"))

        research_page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        research_page.set_border_width(10)

        self.research_region_label = Gtk.Label(label="Region: -")
        self.research_region_label.set_xalign(0.0)
        research_page.pack_start(self.research_region_label, False, False, 0)

        self.research_city_label = Gtk.Label(label="Stadt: -")
        self.research_city_label.set_xalign(0.0)
        research_page.pack_start(self.research_city_label, False, False, 0)

        if WEBKIT_AVAILABLE and WebKit2 is not None:
            self.research_webview = WebKit2.WebView()
            self.research_webview.load_uri(self.region_wiki_url)
            research_scroller = Gtk.ScrolledWindow()
            research_scroller.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
            research_scroller.add(self.research_webview)
            research_page.pack_start(research_scroller, True, True, 0)
            self.research_link_btn = None
        else:
            self.research_link_btn = Gtk.LinkButton.new_with_label(self.region_wiki_url, "Wikipedia Seite öffnen")
            research_page.pack_start(self.research_link_btn, False, False, 0)
            research_page.pack_start(
                Gtk.Label(label="WebKit2 nicht verfügbar, öffne Wikipedia über Link."),
                False,
                False,
                0,
            )
            self.research_webview = None

        research_refresh_btn = Gtk.Button(label="Region neu laden")
        research_refresh_btn.connect("clicked", lambda *_: self._refresh_region_info(force=True))
        research_page.pack_start(research_refresh_btn, False, False, 0)

        self.research_page_idx = self.notebook.append_page(research_page, Gtk.Label(label="Research"))
        self._init_tab_menu()
        self._update_runtime_indicators()
        self._update_mode_ui()

    def _on_notebook_switch_page(self, _notebook, _page, page_num: int):
        if hasattr(self, "tab_titles"):
            title = self.tab_titles.get(page_num)
            if title:
                self.menu_button.set_tooltip_text(f"Menu ({title})")
        if not hasattr(self, "diary_textview"):
            return
        if page_num == self.diary_page_idx:
            self.diary_textview.set_editable(True)
            self.diary_textview.set_cursor_visible(True)
            return

        self.diary_textview.set_editable(False)
        self.diary_textview.set_cursor_visible(False)
        self.set_focus(None)
        if page_num == self.radar_page_idx:
            self.radar_area.grab_focus()

    def _init_tab_menu(self):
        self.tab_titles = {
            self.radar_page_idx: "Radar",
            self.profile_page_idx: "Profil",
            self.nav_page_idx: "Navigation",
            self.diary_page_idx: "Tagebuch",
            self.research_page_idx: "Research",
        }
        popover = Gtk.Popover.new(self.menu_button)
        menu_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        menu_box.set_border_width(8)
        for idx in sorted(self.tab_titles.keys()):
            btn = Gtk.Button(label=self.tab_titles[idx])
            btn.connect("clicked", self._on_tab_menu_clicked, idx, popover)
            menu_box.pack_start(btn, False, False, 0)
        popover.add(menu_box)
        popover.show_all()
        self.menu_button.set_popover(popover)

    def _on_tab_menu_clicked(self, _button, idx: int, popover: Gtk.Popover):
        self.notebook.set_current_page(idx)
        popover.popdown()

    def _draw_color_dot(self, _widget, cr: cairo.Context, color: Tuple[float, float, float]):
        cr.set_source_rgb(*color)
        cr.arc(6, 6, 5, 0, 2 * math.pi)
        cr.fill()

    def set_zoom(self, zoom: float):
        self.zoom_factor = max(MIN_ZOOM, min(MAX_ZOOM, zoom))
        self.zoom_label.set_text(f"Zoom: {self.zoom_factor:.2f}x")
        self.radar_area.schedule_draw()

    def _update_mode_ui(self):
        mode_text = "drive" if self.travel_mode == "drive" else "pedestrian"
        radius = self.get_effective_query_radius()
        pixbuf = self._load_mode_icon(self.travel_mode)
        if pixbuf is not None:
            self.mode_icon.set_from_pixbuf(pixbuf)
        self.mode_icon.set_tooltip_text(f"Modus: {mode_text} | Radius {radius} m")
        if hasattr(self, "poi_header_label"):
            suffix = " nur Staedte" if self.travel_mode == "drive" else ""
            self.poi_header_label.set_text(f"POIs im Auto-Radius ({mode_text}{suffix})")

    def get_color_for_filter(self, filter_key: str) -> Tuple[float, float, float]:
        return self.category_colors.get(filter_key, UNKNOWN_COLOR)

    def _clear_city_poi_table(self, message: str):
        if not hasattr(self, "city_poi_store"):
            return
        self.city_poi_store.clear()
        self.city_table_status_label.set_text(message)

    def _center_radar_view(self):
        def _center():
            hadj = self.scroller.get_hadjustment()
            vadj = self.scroller.get_vadjustment()
            hadj.set_value((hadj.get_upper() - hadj.get_page_size()) / 2.0)
            vadj.set_value((vadj.get_upper() - vadj.get_page_size()) / 2.0)
            return False

        GLib.idle_add(_center)

    def _on_scroller_size_allocate(self, _widget, allocation):
        target = max(MIN_CANVAS_SIZE, max(allocation.width, allocation.height) + CANVAS_PADDING * 2)
        if target != self.radar_area.canvas_size:
            self.radar_area.canvas_size = target
            self.radar_area.set_size_request(target, target)
            self._center_radar_view()

    def _on_window_size_allocate(self, _widget, allocation):
        self._apply_responsive_layout(allocation.width, allocation.height)
        self._update_tab_collapse(allocation.width)

    def _apply_responsive_layout(self, width: int, height: int):
        h = max(480, int(height))
        w = max(320, int(width))
        compact = w < 420

        poi_min = max(90, int(h * (0.14 if compact else 0.17)))
        poi_max = max(poi_min + 20, int(h * (0.24 if compact else 0.30)))
        self._set_scroller_height(self.poi_list_scroller, poi_min, poi_max)

        diary_list_min_default = max(90, int(h * 0.14))
        diary_list_max_default = max(diary_list_min_default + 20, int(h * 0.22))
        self._set_scroller_height(self.diary_list_scroller, diary_list_min_default, diary_list_max_default)

        editor_min = max(180, int(h * 0.34))
        self._set_scroller_height(self.diary_editor_scroller, editor_min)
        self._set_scroller_height(self.city_poi_scroller, max(120, int(h * 0.16)), max(180, int(h * 0.24)))

    def _estimate_tab_bar_width(self) -> int:
        if not hasattr(self, "tab_titles"):
            return 0
        total = 0
        for title in self.tab_titles.values():
            layout = self.create_pango_layout(title)
            tw, _ = layout.get_pixel_size()
            total += tw + 44
        return total

    def _update_tab_collapse(self, window_width: int):
        threshold = int(max(240, window_width * 0.8))
        estimated = self._estimate_tab_bar_width()
        margin = 36
        if self.tabs_collapsed is None:
            collapse = estimated > threshold
        elif self.tabs_collapsed:
            collapse = estimated > (threshold - margin)
        else:
            collapse = estimated > (threshold + margin)
        if collapse == self.tabs_collapsed:
            return
        self.tabs_collapsed = collapse
        self.notebook.set_show_tabs(not collapse)
        if collapse:
            self.menu_button.show()
        else:
            self.menu_button.hide()

    def _set_status(self, text: str):
        self.status_label.set_text(f"{text}")

    def _set_poi_error(self, message: str):
        if not message:
            self.poi_error_label.hide()
            return
        safe_message = GLib.markup_escape_text(message)
        self.poi_error_label.set_markup(f"<span foreground='red'>{safe_message}</span>")
        self.poi_error_label.show()

    def _set_diary_view(self, name: str):
        if not hasattr(self, "diary_stack"):
            return
        self.diary_stack.set_visible_child_name(name)
        editor_active = name == "editor"
        if hasattr(self, "diary_editor_view_btn"):
            self.diary_editor_view_btn.set_sensitive(not editor_active)
        if hasattr(self, "diary_history_view_btn"):
            self.diary_history_view_btn.set_sensitive(editor_active)

    def _show_diary_editor(self):
        self._set_diary_view("editor")

    def _show_diary_history(self):
        self._set_diary_view("history")

    def _focus_diary_editor(self):
        if not hasattr(self, "diary_textview"):
            return

        def _focus():
            if not self.diary_textview.get_visible():
                return False
            try:
                self.diary_textview.grab_focus()
            except Exception:
                pass
            return False

        GLib.idle_add(_focus)

    def _set_indicator_markup(self, label: Gtk.Label, title: str, value: str, level: str):
        color = INDICATOR_COLORS.get(level, INDICATOR_COLORS["muted"])
        safe_title = GLib.markup_escape_text(title)
        safe_value = GLib.markup_escape_text(value)
        label.set_markup(f"<span foreground='{color}'><b>{safe_title}</b>: {safe_value}</span>")

    def _update_runtime_indicators(self):
        indicators = compute_runtime_indicators(
            self.location_source,
            self.network_state,
            self.fetch_in_progress,
            self.region_fetch_in_progress,
            self.reload_requested,
        )
        self._set_indicator_markup(self.gps_indicator_label, "GPS", *indicators["GPS"])
        self._set_indicator_markup(self.network_indicator_label, "NET", *indicators["Network"])
        self._set_indicator_markup(self.loading_indicator_label, "DATA", *indicators["Data"])

    def _on_category_toggled(self, widget: Gtk.CheckButton, key: object):
        keys = (key,) if isinstance(key, str) else tuple(key)
        for category_key in keys:
            self.categories[category_key] = widget.get_active()
        self.last_query_location = None
        if is_city_poi(self.selected_poi):
            self._refresh_city_pois_for_selection()
        self._refresh_pois(force=True)

    def _tick_heading(self):
        try:
            previous_effective_heading = self.get_effective_heading()
            previous_compass_available = self.compass_available
            self.compass_available = self.compass_provider.is_available()
            new_heading = self.compass_provider.get_heading()
            if new_heading is None:
                if previous_effective_heading is not None or previous_compass_available != self.compass_available:
                    self._refresh_navigation_view()
                    self.radar_area.schedule_draw()
                return True

            if self.heading_deg is None:
                self.heading_deg = new_heading
                heading_changed = True
            else:
                diff = (new_heading - self.heading_deg + 540.0) % 360.0 - 180.0
                self.heading_deg = (self.heading_deg + (0.45 * diff) + 360.0) % 360.0
                heading_changed = abs(diff) >= RADAR_HEADING_REDRAW_THRESHOLD_DEG
            if previous_compass_available != self.compass_available:
                heading_changed = True
            if heading_changed:
                self._refresh_navigation_view()
                self.radar_area.schedule_draw()
        except Exception:
            pass
        return True

    def _tick_location(self):
        try:
            previous_location = self.current_location
            previous_location_ts = self.current_location_ts
            loc = self.geo_provider.get_location()
            if loc is None:
                if self.last_real_gps_location is not None:
                    self.location_source = "gps_cached"
                    self.current_location = self.last_real_gps_location
                    self.current_location_ts = self.last_real_gps_fix_ts
                    self.current_speed_mps = None
                    provider_error = self.geo_provider.last_error or "Kein neuer GPS-Fix."
                    fix_age = format_fix_age(
                        None if self.last_real_gps_fix_ts is None else time.time() - self.last_real_gps_fix_ts
                    )
                    self._set_status(f"{provider_error} Nutze letzten echten GPS-Fix ({fix_age} alt).")
                else:
                    fallback = self._get_manual_location()
                    if fallback is None:
                        self.location_source = "none"
                        provider_error = self.geo_provider.last_error or "Keine GPS-Position."
                        self._set_status(f"{provider_error} Fallback im Profil setzen.")
                        self.previous_location = self.current_location
                        self.current_location = None
                        self.current_speed_mps = None
                        self.avg_speed_mps = None
                        self.travel_mode = "pedestrian"
                        self.location_samples = []
                        self.pois = []
                        self._update_mode_ui()
                        self._update_runtime_indicators()
                        self.radar_area.schedule_draw()
                        self._refresh_poi_list()
                        self._refresh_navigation_view()
                        self._update_research_view()
                        return True
                    self.location_source = "manual"
                    self._set_status("GPS nicht verfuegbar, nutze manuelle Position.")
                    self.current_location = fallback
                    self.current_location_ts = time.time()
                    self.current_speed_mps = None
            else:
                now = time.time()
                self.location_source = "gps"
                self.current_location = loc
                self.current_location_ts = now
                self.last_real_gps_location = loc
                self.last_real_gps_fix_ts = now
                self.current_speed_mps = calculate_speed_mps(
                    previous_location,
                    previous_location_ts,
                    self.current_location,
                    self.current_location_ts,
                    distance_fn=self._distance_m,
                )
                self._set_status(f"GPS: {loc[0]:.5f}, {loc[1]:.5f}")

            self.previous_location = previous_location
            self.previous_location_ts = previous_location_ts
            self._update_travel_metrics()
            travel_heading = derive_travel_heading(
                self.previous_location,
                self.current_location,
                distance_fn=self._distance_m,
                bearing_fn=self._bearing_deg,
            )
            if travel_heading is not None:
                self.travel_heading_deg = travel_heading

            self._update_mode_ui()
            self._update_runtime_indicators()
            self._refresh_pois(force=False)
            self._refresh_region_info(force=False)
            self._refresh_navigation_view()
            self.radar_area.schedule_draw()
        except Exception:
            self.location_source = "none"
            self._set_status("Interner Fehler im Update-Timer")
            self._update_runtime_indicators()
        return True

    def _update_travel_metrics(self):
        if self.current_location is None or self.current_location_ts is None:
            self.avg_speed_mps = None
            self.travel_mode = "pedestrian"
            self.location_samples = []
            return
        if self.location_source == "gps":
            self.location_samples.append((self.current_location_ts, self.current_location))
            self.location_samples = trim_location_samples(self.location_samples, DRIVE_MODE_AVG_WINDOW_SECS)
        self.avg_speed_mps = average_speed_mps(self.location_samples, DRIVE_MODE_AVG_WINDOW_SECS, self._distance_m)
        previous_mode = self.travel_mode
        self.travel_mode = detect_travel_mode(self.current_speed_mps, self.avg_speed_mps)
        if previous_mode != self.travel_mode:
            self.last_query_location = None

    def _refresh_region_info(self, force: bool):
        if self.current_location is None or self.region_fetch_in_progress:
            return
        now = time.time()
        if not force and self.last_region_query_location is not None:
            moved = self._distance_m(
                self.current_location[0],
                self.current_location[1],
                self.last_region_query_location[0],
                self.last_region_query_location[1],
            )
            if moved < REGION_REFRESH_MOVE_M and (now - self.last_region_query_ts) < REGION_REFRESH_SECS:
                return

        self.region_fetch_in_progress = True
        self._update_runtime_indicators()
        lat, lon = self.current_location
        thread = threading.Thread(target=self._fetch_region_thread, args=(lat, lon), daemon=True)
        thread.start()

    def _fetch_region_thread(self, lat: float, lon: float):
        try:
            info = resolve_region(lat, lon)
            GLib.idle_add(self._apply_region_info, info, (lat, lon))
        except Exception:
            GLib.idle_add(self._region_fetch_failed)

    def _region_fetch_failed(self):
        self.region_fetch_in_progress = False
        self.network_state = "error"
        self._update_runtime_indicators()
        return False

    def _apply_region_info(self, info: Dict[str, str], query_location: Tuple[float, float]):
        self.region_city = info.get("city", "-")
        self.region_name = info.get("region", "-")
        self.region_wiki_url = info.get("wiki_url", "https://en.wikipedia.org")
        self.last_region_query_location = query_location
        self.last_region_query_ts = time.time()
        self.region_fetch_in_progress = False
        self.network_state = "online"
        self._update_research_view()
        self._update_runtime_indicators()
        return False

    def _update_research_view(self):
        if hasattr(self, "research_region_label"):
            self.research_region_label.set_text(f"Region: {self.region_name}")
        if hasattr(self, "research_city_label"):
            self.research_city_label.set_text(f"Stadt: {self.region_city}")
        if getattr(self, "research_webview", None) is not None:
            try:
                self.research_webview.load_uri(self.region_wiki_url)
            except Exception:
                pass
        if getattr(self, "research_link_btn", None) is not None:
            self.research_link_btn.set_uri(self.region_wiki_url)

    def _on_poi_row_activated(self, _listbox: Gtk.ListBox, row: Gtk.ListBoxRow):
        poi = getattr(row, "poi", None)
        if poi is not None:
            self.select_poi(poi)

    def _refresh_poi_list(self):
        for child in self.poi_listbox.get_children():
            self.poi_listbox.remove(child)

        if not self.pois:
            row = Gtk.ListBoxRow()
            row.add(Gtk.Label(label="Keine POIs verfuegbar"))
            self.poi_listbox.add(row)
            self.poi_listbox.show_all()
            return

        max_distance = max(float(self.current_query_radius_m), 1.0)
        for poi in self.pois[:25]:
            row = Gtk.ListBoxRow()
            row.poi = poi

            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
            box.set_border_width(6)

            top = Gtk.Box(spacing=8)
            cluster_badge = f" [C{poi.cluster_id}]" if poi.cluster_id >= 0 else ""
            name = Gtk.Label(label=f"{poi.name} ({poi.category_label}){cluster_badge}")
            name.set_xalign(0.0)
            name.set_line_wrap(True)
            dist = Gtk.Label(label=f"{int(poi.distance_m)} m")
            dist.set_xalign(1.0)
            top.pack_start(name, True, True, 0)
            top.pack_start(dist, False, False, 0)

            bar = Gtk.LevelBar()
            bar.set_min_value(0.0)
            bar.set_max_value(max_distance)
            bar.set_value(min(poi.distance_m, max_distance))
            bar.set_hexpand(True)

            box.pack_start(top, False, False, 0)
            box.pack_start(bar, False, True, 0)
            row.add(box)
            self.poi_listbox.add(row)

        self.poi_listbox.show_all()

    def _diary_file_path(self, d: date) -> Path:
        return diary_file_path(self.diary_dir, d)

    def _load_diary_day(self, d: date):
        self.diary_date = d
        self.diary_entries = load_diary_entries(self.diary_dir, d)
        self.diary_edit_id = None
        if hasattr(self, "diary_textview"):
            self.diary_textview.get_buffer().set_text("")
        self._show_diary_editor()
        self._refresh_diary_list()

    def _save_diary_day(self):
        save_diary_entries(self.diary_dir, self.diary_date, self.diary_entries, version=DIARY_APP_VERSION)

    def _refresh_diary_list(self):
        if not hasattr(self, "diary_listbox"):
            return
        self.diary_date_label.set_text(f"Tagebuch: {self.diary_date.isoformat()}")
        handler_id = getattr(self, "diary_listbox_row_selected_handler", None)
        if handler_id is not None:
            self.diary_listbox.handler_block(handler_id)
        self.diary_listbox.unselect_all()

        for child in self.diary_listbox.get_children():
            self.diary_listbox.remove(child)

        if not self.diary_entries:
            row = Gtk.ListBoxRow()
            label = Gtk.Label()
            label.set_markup("<span size='small' alpha='70%'>Keine Einträge für diesen Tag</span>")
            label.set_xalign(0.0)
            label.set_margin_top(2)
            label.set_margin_bottom(2)
            row.add(label)
            self.diary_listbox.add(row)
            self.diary_listbox.show_all()
            if handler_id is not None:
                self.diary_listbox.handler_unblock(handler_id)
            return
        for entry in self.diary_entries:
            row = Gtk.ListBoxRow()
            row.entry_id = entry.get("id")
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
            box.set_border_width(6)

            created = entry.get("created_at", "-")
            header = Gtk.Label(label=f"{created}")
            header.set_xalign(0.0)
            body = Gtk.Label(label=entry.get("text", ""))
            body.set_xalign(0.0)
            body.set_line_wrap(True)
            body.set_selectable(True)

            meta_loc = entry.get("location")
            meta_poi = entry.get("selected_poi")
            footer_text = ""
            if isinstance(meta_loc, dict):
                footer_text += f"GPS: {meta_loc.get('lat', '-')}, {meta_loc.get('lon', '-')}"
            if isinstance(meta_poi, dict):
                if footer_text:
                    footer_text += " | "
                footer_text += f"POI: {meta_poi.get('name', '-')}"
            ai_status = entry.get("ai_status")
            if isinstance(ai_status, str) and ai_status:
                if footer_text:
                    footer_text += " | "
                ai_labels = {
                    "queued": "KI: Warteschlange",
                    "done": "KI: überarbeitet",
                    "error": "KI: Fehler",
                }
                footer_text += ai_labels.get(ai_status, f"KI: {ai_status}")
            if footer_text:
                footer = Gtk.Label(label=footer_text)
                footer.set_xalign(0.0)
                footer.set_line_wrap(True)
            box.pack_start(header, False, False, 0)
            box.pack_start(body, False, False, 0)
            if footer_text:
                box.pack_start(footer, False, False, 0)
            row.add(box)
            self.diary_listbox.add(row)

        self.diary_listbox.show_all()
        if handler_id is not None:
            self.diary_listbox.handler_unblock(handler_id)

    def _on_diary_row_selected(self, _listbox: Gtk.ListBox, row: Optional[Gtk.ListBoxRow]):
        if row is None:
            return
        entry_id = getattr(row, "entry_id", None)
        if entry_id is None:
            return
        for entry in self.diary_entries:
            if entry.get("id") == entry_id:
                self.diary_edit_id = entry_id
                self.diary_textview.get_buffer().set_text(entry.get("text", ""))
                self._show_diary_editor()
                self._focus_diary_editor()
                break

    def _on_diary_prev_day(self, *_):
        self._load_diary_day(self.diary_date - timedelta(days=1))

    def _on_diary_next_day(self, *_):
        self._load_diary_day(self.diary_date + timedelta(days=1))

    def _on_diary_today(self, *_):
        self._load_diary_day(date.today())

    def _on_diary_clear_edit(self, *_):
        self.diary_edit_id = None
        self.diary_textview.get_buffer().set_text("")
        self._show_diary_editor()
        self._focus_diary_editor()

    def _set_diary_busy(self, busy: bool):
        if hasattr(self, "diary_save_btn"):
            self.diary_save_btn.set_label("..." if busy else "Speichern")
            self.diary_save_btn.set_sensitive(not busy)
        if hasattr(self, "diary_clear_btn"):
            self.diary_clear_btn.set_sensitive(not busy)
        if hasattr(self, "diary_textview"):
            self.diary_textview.set_editable(not busy)
            self.diary_textview.set_cursor_visible(not busy)

    def _on_diary_save_entry(self, *_):
        buf = self.diary_textview.get_buffer()
        text = buf.get_text(buf.get_start_iter(), buf.get_end_iter(), True).strip()
        if not text:
            self._set_status("Tagebuch: Bitte Text eingeben")
            return

        now = datetime.utcnow().isoformat() + "Z"
        location_meta = None
        if self.current_location is not None:
            location_meta = {"lat": round(self.current_location[0], 6), "lon": round(self.current_location[1], 6)}
        poi_meta = None
        if self.selected_poi is not None:
            poi_meta = {
                "name": self.selected_poi.name,
                "category": self.selected_poi.category_label,
                "distance_m": int(self.selected_poi.distance_m),
            }

        entry_id = self.diary_edit_id or str(uuid.uuid4())
        updated = False
        for entry in self.diary_entries:
            if entry.get("id") == entry_id:
                entry["text"] = text
                entry["updated_at"] = now
                entry["location"] = location_meta
                entry["selected_poi"] = poi_meta
                entry["ai_status"] = "queued"
                updated = True
                break
        if not updated:
            self.diary_entries.append(
                {
                    "id": entry_id,
                    "created_at": now,
                    "updated_at": now,
                    "text": text,
                    "location": location_meta,
                    "selected_poi": poi_meta,
                    "ai_status": "queued",
                }
            )
        self._save_diary_day()
        self._refresh_diary_list()
        self.diary_edit_id = None
        self.diary_textview.get_buffer().set_text("")
        self._show_diary_editor()
        self._set_status("Tagebuch gespeichert, Ollama überarbeitet den Eintrag im Hintergrund")
        thread = threading.Thread(
            target=self._diary_save_thread,
            args=(self.diary_date, entry_id, text, now, location_meta, poi_meta),
            daemon=True,
        )
        thread.start()

    def _on_ollama_settings_save(self, *_):
        prompt_buffer = self.ollama_prompt_view.get_buffer()
        prompt = prompt_buffer.get_text(prompt_buffer.get_start_iter(), prompt_buffer.get_end_iter(), True).strip()
        base_url = self.ollama_base_url_entry.get_text().strip() or OLLAMA_BASE_URL_DEFAULT
        self.ollama_base_url = base_url
        self.ollama_diary_system_prompt = prompt or OLLAMA_DIARY_SYSTEM_PROMPT_DEFAULT
        self.app_settings = {
            "ollama_base_url": self.ollama_base_url,
            "ollama_diary_system_prompt": self.ollama_diary_system_prompt,
        }
        save_app_settings(self.app_data_dir, self.app_settings)
        self._set_status("Ollama-Einstellungen gespeichert")

    def _diary_save_thread(
        self,
        day: date,
        entry_id: str,
        text: str,
        updated_at: str,
        location_meta: Optional[Dict[str, float]],
        poi_meta: Optional[Dict[str, object]],
    ):
        last_error = "Ollama konnte den Eintrag nicht überarbeiten."
        for _attempt in range(3):
            try:
                improved = improve_diary_entry(
                    text,
                    base_url=self.ollama_base_url,
                    system_prompt=self.ollama_diary_system_prompt,
                )
                GLib.idle_add(self._apply_diary_save_result, day, entry_id, updated_at, improved, location_meta, poi_meta)
                return
            except DiaryImproveError as exc:
                last_error = exc.user_message
            except Exception:
                last_error = "Ollama konnte den Eintrag nicht überarbeiten."
        GLib.idle_add(self._apply_diary_save_failure, day, entry_id, last_error)

    def _apply_diary_save_result(
        self,
        day: date,
        entry_id: str,
        updated_at: str,
        improved_text: str,
        location_meta: Optional[Dict[str, float]],
        poi_meta: Optional[Dict[str, object]],
    ):
        entries = self.diary_entries if self.diary_date == day else load_diary_entries(self.diary_dir, day)
        updated = False
        for entry in entries:
            if entry.get("id") == entry_id:
                entry["text"] = improved_text
                entry["updated_at"] = updated_at
                entry["location"] = location_meta
                entry["selected_poi"] = poi_meta
                entry["ai_status"] = "done"
                updated = True
                break
        if not updated:
            entries.append(
                {
                    "id": entry_id,
                    "created_at": updated_at,
                    "updated_at": updated_at,
                    "text": improved_text,
                    "location": location_meta,
                    "selected_poi": poi_meta,
                    "ai_status": "done",
                }
            )

        if self.diary_date == day:
            self.diary_entries = entries
            self._save_diary_day()
            self._refresh_diary_list()
            self._set_status("Tagebuch-Eintrag durch Ollama überarbeitet")
        else:
            save_diary_entries(self.diary_dir, day, entries, version=DIARY_APP_VERSION)
        return False

    def _apply_diary_save_failure(self, day: date, entry_id: str, message: str):
        entries = self.diary_entries if self.diary_date == day else load_diary_entries(self.diary_dir, day)
        for entry in entries:
            if entry.get("id") == entry_id:
                entry["ai_status"] = "error"
                break
        if self.diary_date == day:
            self.diary_entries = entries
            self._save_diary_day()
            self._refresh_diary_list()
        else:
            save_diary_entries(self.diary_dir, day, entries, version=DIARY_APP_VERSION)
        self._set_status(message)
        return False

    def _get_manual_location(self) -> Optional[Tuple[float, float]]:
        try:
            lat = float(self.manual_lat.get_text().strip())
            lon = float(self.manual_lon.get_text().strip())
            return lat, lon
        except Exception:
            return None

    def _refresh_pois(self, force: bool):
        if self.current_location is None:
            self.radar_area.schedule_draw()
            return
        now = time.time()
        seconds_since_refresh = None if self.last_poi_query_ts is None else now - self.last_poi_query_ts
        query_radius = self.get_effective_query_radius()
        if self.fetch_in_progress:
            if force or should_refresh_pois(
                self.current_location,
                self.active_poi_query_location,
                query_radius,
                self.current_speed_mps,
                seconds_since_refresh,
                self._distance_m,
            ):
                self.reload_requested = True
            self._update_runtime_indicators()
            self.radar_area.schedule_draw()
            return

        if not force and not should_refresh_pois(
            self.current_location,
            self.last_query_location,
            query_radius,
            self.current_speed_mps,
            seconds_since_refresh,
            self._distance_m,
        ):
            self.radar_area.schedule_draw()
            return

        self.fetch_in_progress = True
        self.active_poi_query_location = self.current_location
        self.current_query_radius_m = query_radius
        self._update_runtime_indicators()
        lat, lon = self.current_location
        radius = query_radius
        include_cities = False
        city_only = self.travel_mode == "drive"
        thread = threading.Thread(
            target=self._fetch_pois_thread,
            args=(lat, lon, radius, include_cities, city_only),
            daemon=True,
        )
        thread.start()

    def _fetch_pois_thread(self, lat: float, lon: float, radius: int, include_cities: bool, city_only: bool):
        try:
            pois = fetch_pois(
                lat,
                lon,
                radius,
                self.categories,
                self.filter_lookup,
                self.category_labels,
                include_cities=include_cities,
                city_only=city_only,
            )
            GLib.idle_add(self._apply_pois, pois, (lat, lon))
        except PoiFetchError as exc:
            GLib.idle_add(self._fetch_failed, exc.user_message)
        except Exception:
            GLib.idle_add(self._fetch_failed)

    def _fetch_failed(self, message: str = "POIs konnten nicht geladen werden. Bitte Verbindung/Standort prüfen."):
        self.fetch_in_progress = False
        self.active_poi_query_location = None
        self.network_state = "error"
        self._set_status("POI-Abfrage fehlgeschlagen")
        self._set_poi_error(message)
        self._update_runtime_indicators()
        if self.reload_requested:
            self.reload_requested = False
            self._refresh_pois(force=True)
        return False

    def _apply_pois(self, pois: List[Poi], query_location: Tuple[float, float]):
        self.clusters = assign_clusters(pois, self.current_query_radius_m, self._distance_m)
        self.pois = pois
        self.last_query_location = query_location
        self.last_poi_query_ts = time.time()
        self.fetch_in_progress = False
        self.active_poi_query_location = None
        self.network_state = "online"
        threshold = int(poi_refresh_distance(self.current_query_radius_m))
        interval_s = int(round(poi_refresh_interval(self.current_query_radius_m, self.current_speed_mps)))
        speed_text = "-"
        if self.current_speed_mps is not None:
            speed_text = f"{(self.current_speed_mps * 3.6):.1f} km/h"
        avg_speed_text = "-"
        if self.avg_speed_mps is not None:
            avg_speed_text = f"{(self.avg_speed_mps * 3.6):.1f} km/h"
        self._set_status(
            f"{len(pois)} POIs im Auto-Radius {self.current_query_radius_m} m, "
            f"Modus {self.travel_mode}, Cluster: {len(self.clusters)}, "
            f"Refresh ~{interval_s}s / {threshold}m, Speed {speed_text}, Avg5m {avg_speed_text}"
        )
        self._set_poi_error("")
        self._update_mode_ui()
        self._update_runtime_indicators()
        self.radar_area.schedule_draw()
        self._refresh_poi_list()
        self._refresh_navigation_view()
        if self.current_location is not None and should_refresh_pois(
            self.current_location,
            query_location,
            self.current_query_radius_m,
            self.current_speed_mps,
            0.0,
            self._distance_m,
        ):
            self.reload_requested = False
            self._refresh_pois(force=True)
            return False
        if self.reload_requested:
            self.reload_requested = False
            self._refresh_pois(force=True)
        return False

    def select_poi(self, poi: Poi):
        self.selected_poi = poi
        self._refresh_city_pois_for_selection()
        self._refresh_navigation_view()
        self.notebook.set_current_page(2)

    def get_navigation_info(self) -> Optional[Tuple[Poi, float, float, Optional[float], float]]:
        return calculate_navigation_info(
            self.selected_poi,
            self.current_location,
            self.get_effective_heading(),
            self._distance_m,
            self._bearing_deg,
        )

    def get_effective_heading(self) -> Optional[float]:
        if self.compass_available and self.heading_deg is not None:
            return self.heading_deg
        return self.travel_heading_deg

    def get_effective_query_radius(self) -> int:
        return effective_query_radius(self.radius_m, MAX_RADIUS_M, self.current_speed_mps, self.travel_mode)

    def _refresh_city_pois_for_selection(self):
        if not is_city_poi(self.selected_poi):
            self.city_fetch_in_progress = False
            self.active_city_query = None
            self._clear_city_poi_table("Stadt-POIs: waehle eine Stadt im Radar.")
            return
        city = self.selected_poi
        if city is None:
            return
        self.city_fetch_in_progress = True
        self.active_city_query = (city.name, city.lat, city.lon)
        self._clear_city_poi_table(f"Stadt-POIs fuer {city.name} werden geladen...")
        thread = threading.Thread(
            target=self._fetch_city_pois_thread,
            args=(city.name, city.lat, city.lon),
            daemon=True,
        )
        thread.start()

    def _fetch_city_pois_thread(self, city_name: str, lat: float, lon: float):
        try:
            pois = fetch_pois(
                lat,
                lon,
                CITY_POI_RADIUS_M,
                self.categories,
                self.filter_lookup,
                self.category_labels,
                include_cities=False,
            )
            GLib.idle_add(self._apply_city_pois, city_name, pois, lat, lon)
        except PoiFetchError as exc:
            GLib.idle_add(self._city_poi_fetch_failed, city_name, exc.user_message)
        except Exception:
            GLib.idle_add(self._city_poi_fetch_failed, city_name, "Stadt-POIs konnten nicht geladen werden.")

    def _apply_city_pois(self, city_name: str, pois: List[Poi], lat: float, lon: float):
        if self.active_city_query != (city_name, lat, lon):
            return False
        self.city_fetch_in_progress = False
        self.city_poi_store.clear()
        for poi in pois[:40]:
            self.city_poi_store.append([poi.name, poi.category_label, f"{int(poi.distance_m)} m"])
        if pois:
            self.city_table_status_label.set_text(
                f"Stadt-POIs in {city_name} ({min(len(pois), 40)} von {len(pois)} im Radius {CITY_POI_RADIUS_M} m)"
            )
        else:
            self.city_table_status_label.set_text(f"Keine Stadt-POIs in {city_name} gefunden.")
        return False

    def _city_poi_fetch_failed(self, city_name: str, message: str):
        if self.active_city_query is None or self.active_city_query[0] != city_name:
            return False
        self.city_fetch_in_progress = False
        self.city_poi_store.clear()
        self.city_table_status_label.set_text(message)
        return False

    def _refresh_navigation_view(self):
        nav = self.get_navigation_info()
        if nav is None:
            self.nav_target_label.set_text("Ziel: -")
            if self.compass_available:
                heading_text = "Kompass"
            elif self.travel_heading_deg is not None:
                heading_text = "Bewegung"
            else:
                heading_text = "nicht verfuegbar"
            self.nav_info_label.set_text(f"Distanz: - | Ziel oben | Turn: - | Richtung: {heading_text}")
            self.nav_detail_label.set_text("POI: -")
            self.nav_link_button.hide()
            self._clear_city_poi_table("Stadt-POIs: waehle eine Stadt im Radar.")
        else:
            poi, distance, bearing, heading, turn = nav
            self.nav_target_label.set_text(f"Ziel: {poi.name} ({poi.category_label})")
            if heading is None:
                heading_text = "wartet" if self.compass_available else "nicht verfuegbar"
            elif self.compass_available:
                heading_text = f"Kompass {int(heading)} deg"
            else:
                heading_text = f"Bewegung {int(heading)} deg"
            self.nav_info_label.set_text(
                f"Distanz: {int(distance)} m | Ziel oben | Turn: {int(turn)} deg | Richtung: {heading_text}"
            )
            cluster_text = "-" if poi.cluster_id < 0 else f"C{poi.cluster_id}"
            self.nav_detail_label.set_text(
                f"POI: {poi.name} | Kategorie: {poi.category_label} | Bearing: {int(bearing)} deg | "
                f"Koordinaten: {poi.lat:.5f}, {poi.lon:.5f} | Cluster: {cluster_text}"
            )
            if poi.url:
                self.nav_link_button.set_uri(poi.url)
                self.nav_link_button.show()
            else:
                self.nav_link_button.hide()
            if not is_city_poi(poi):
                self._clear_city_poi_table("Stadt-POIs: nur verfuegbar, wenn das Ziel eine Stadt ist.")
        self.navigation_area.schedule_draw()

    @staticmethod
    def _infer_category(tags: Dict[str, str]) -> str:
        return infer_category(tags)

    @staticmethod
    def _distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
        return distance_m(lat1, lon1, lat2, lon2)

    @staticmethod
    def _bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
        return bearing_deg(lat1, lon1, lat2, lon2)


def main() -> None:
    win = TravelbookApp()
    win.show_all()
    Gtk.main()


if __name__ == "__main__":
    main()
