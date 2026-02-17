#!/usr/bin/env python3
"""TTS Tester — Text-to-speech engine comparison tool."""

import sys
import os
import csv
import json
import tempfile
import subprocess
import gettext
import time
import threading

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gi.repository import Gtk, Adw, Gio, GLib, Pango, Gdk

from tts_tester import __version__
from tts_tester.engines import detect_engines, get_engine, ENGINE_CLASSES
from tts_tester.settings import (
    load_settings, save_settings, load_favorites, save_favorites
)

# Set up gettext
TEXTDOMAIN = 'tts-tester'
gettext.textdomain(TEXTDOMAIN)
gettext.bindtextdomain(TEXTDOMAIN, '/usr/share/locale')
_ = gettext.gettext

# Sample texts for different languages
SAMPLE_TEXTS = {
    "English": "The quick brown fox jumps over the lazy dog. "
               "This is a sample text for testing text-to-speech engines.",
    "Svenska": "Den snabba bruna räven hoppar över den lata hunden. "
               "Detta är en exempeltext för att testa text-till-tal-motorer.",
    "Deutsch": "Der schnelle braune Fuchs springt über den faulen Hund. "
               "Dies ist ein Beispieltext zum Testen von Text-zu-Sprache-Engines.",
    "Français": "Le rapide renard brun saute par-dessus le chien paresseux. "
                "Ceci est un texte d'exemple pour tester les moteurs de synthèse vocale.",
    "Español": "El rápido zorro marrón salta sobre el perro perezoso. "
               "Este es un texto de ejemplo para probar motores de texto a voz.",
}

SSML_TEMPLATE = """<speak>
  Hello, this is a <emphasis level="strong">test</emphasis> of SSML.
  <break time="500ms"/>
  <prosody rate="slow" pitch="low">This part is slower and lower.</prosody>
  <break time="300ms"/>
  <prosody rate="fast" pitch="high">And this part is faster and higher!</prosody>
</speak>"""


def _timestamp():
    return time.strftime("%H:%M:%S")


class VoiceSettingsPanel(Gtk.Box):
    """Panel with voice/speed/pitch/volume controls for one engine."""

    def __init__(self, on_change=None):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.set_margin_start(12)
        self.set_margin_end(12)
        self.set_margin_top(6)
        self.set_margin_bottom(6)
        self._on_change = on_change

        # Voice selector
        voice_label = Gtk.Label(label=_("Voice"), xalign=0)
        voice_label.add_css_class("heading")
        self.append(voice_label)

        self.voice_dropdown = Gtk.DropDown()
        self.voice_dropdown.set_model(Gtk.StringList.new([]))
        self.voice_dropdown.connect("notify::selected", self._on_voice_changed)
        self.append(self.voice_dropdown)

        self._voice_ids = []

        # Speed slider
        speed_label = Gtk.Label(label=_("Speed"), xalign=0)
        speed_label.add_css_class("heading")
        self.append(speed_label)

        self.speed_scale = Gtk.Scale.new_with_range(
            Gtk.Orientation.HORIZONTAL, 0.25, 4.0, 0.25
        )
        self.speed_scale.set_value(1.0)
        self.speed_scale.add_mark(1.0, Gtk.PositionType.BOTTOM, None)
        self.speed_scale.connect("value-changed", self._on_setting_changed)
        self.append(self.speed_scale)

        # Pitch slider
        pitch_label = Gtk.Label(label=_("Pitch"), xalign=0)
        pitch_label.add_css_class("heading")
        self.append(pitch_label)

        self.pitch_scale = Gtk.Scale.new_with_range(
            Gtk.Orientation.HORIZONTAL, 0.25, 2.0, 0.25
        )
        self.pitch_scale.set_value(1.0)
        self.pitch_scale.add_mark(1.0, Gtk.PositionType.BOTTOM, None)
        self.pitch_scale.connect("value-changed", self._on_setting_changed)
        self.append(self.pitch_scale)

        # Volume slider
        volume_label = Gtk.Label(label=_("Volume"), xalign=0)
        volume_label.add_css_class("heading")
        self.append(volume_label)

        self.volume_scale = Gtk.Scale.new_with_range(
            Gtk.Orientation.HORIZONTAL, 0.0, 2.0, 0.1
        )
        self.volume_scale.set_value(1.0)
        self.volume_scale.add_mark(1.0, Gtk.PositionType.BOTTOM, None)
        self.volume_scale.connect("value-changed", self._on_setting_changed)
        self.append(self.volume_scale)

    def populate_voices(self, voices):
        """Set voice list. voices is list of (id, display_name)."""
        self._voice_ids = [v[0] for v in voices]
        names = [v[1] for v in voices]
        self.voice_dropdown.set_model(Gtk.StringList.new(names if names else [_("(none)")]))
        if not voices:
            self._voice_ids = [""]

    def get_selected_voice(self):
        idx = self.voice_dropdown.get_selected()
        if 0 <= idx < len(self._voice_ids):
            return self._voice_ids[idx]
        return None

    def set_selected_voice(self, voice_id):
        if voice_id in self._voice_ids:
            self.voice_dropdown.set_selected(self._voice_ids.index(voice_id))

    def get_settings(self):
        return {
            "voice": self.get_selected_voice(),
            "speed": self.speed_scale.get_value(),
            "pitch": self.pitch_scale.get_value(),
            "volume": self.volume_scale.get_value(),
        }

    def apply_settings(self, settings):
        if "speed" in settings:
            self.speed_scale.set_value(settings["speed"])
        if "pitch" in settings:
            self.pitch_scale.set_value(settings["pitch"])
        if "volume" in settings:
            self.volume_scale.set_value(settings["volume"])
        if "voice" in settings and settings["voice"]:
            self.set_selected_voice(settings["voice"])

    def _on_voice_changed(self, dropdown, pspec):
        if self._on_change:
            self._on_change()

    def _on_setting_changed(self, scale):
        if self._on_change:
            self._on_change()


class MainWindow(Adw.ApplicationWindow):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.set_default_size(1000, 700)
        self.set_title(_("TTS Tester"))

        self._engines = {}
        self._current_engine = None
        self._current_engine_name = None
        self._playback_process = None
        self._ab_mode = False
        self._ssml_mode = False
        self._available_engine_classes = detect_engines()
        self._all_settings = load_settings()
        self._favorites = load_favorites()
        self._ab_ratings = {}  # track A/B ratings

        # Build UI
        self._build_ui()
        self._populate_engines()
        self._update_status(_("Ready"))

    def _build_ui(self):
        # Main layout
        self._main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.set_content(self._main_box)

        # Header bar
        header = Adw.HeaderBar()
        header.set_title_widget(Gtk.Label(label=_("TTS Tester")))

        # Engine selector
        self._engine_dropdown = Gtk.DropDown()
        self._engine_dropdown.connect("notify::selected", self._on_engine_changed)
        header.pack_start(self._engine_dropdown)

        # Play button
        play_btn = Gtk.Button(icon_name="media-playback-start-symbolic")
        play_btn.set_tooltip_text(_("Play (Ctrl+Enter)"))
        play_btn.connect("clicked", self._on_play)
        play_btn.add_css_class("suggested-action")
        header.pack_start(play_btn)

        # Stop button
        stop_btn = Gtk.Button(icon_name="media-playback-stop-symbolic")
        stop_btn.set_tooltip_text(_("Stop"))
        stop_btn.connect("clicked", self._on_stop)
        header.pack_start(stop_btn)

        # A/B toggle
        self._ab_toggle = Gtk.ToggleButton(label=_("A/B"))
        self._ab_toggle.set_tooltip_text(_("A/B Comparison Mode"))
        self._ab_toggle.connect("toggled", self._on_ab_toggled)
        header.pack_start(self._ab_toggle)

        # SSML toggle
        self._ssml_toggle = Gtk.ToggleButton(label=_("SSML"))
        self._ssml_toggle.set_tooltip_text(_("SSML Editing Mode"))
        self._ssml_toggle.connect("toggled", self._on_ssml_toggled)
        header.pack_start(self._ssml_toggle)

        # Save audio button
        save_btn = Gtk.Button(icon_name="document-save-symbolic")
        save_btn.set_tooltip_text(_("Save Audio"))
        save_btn.connect("clicked", self._on_save_audio)
        header.pack_end(save_btn)

        # Favorites dropdown
        self._favorites_button = Gtk.MenuButton(icon_name="starred-symbolic")
        self._favorites_button.set_tooltip_text(_("Favorites"))
        self._build_favorites_menu()
        header.pack_end(self._favorites_button)

        # Menu button
        menu_btn = Gtk.MenuButton(icon_name="open-menu-symbolic")
        menu_model = Gio.Menu()
        menu_model.append(_("Load Text from File…"), "win.load-text")
        menu_model.append(_("Save Favorite…"), "win.save-favorite")

        sample_section = Gio.Menu()
        for lang in SAMPLE_TEXTS:
            sample_section.append(
                _("Sample: %s") % lang,
                f"win.sample-{lang.lower()}"
            )
        menu_model.append_section(_("Sample Texts"), sample_section)

        menu_model.append(_("Export…"), "app.export")
        menu_model.append(_("Keyboard Shortcuts"), "app.shortcuts")
        menu_model.append(_("About TTS Tester"), "app.about")
        menu_btn.set_menu_model(menu_model)
        header.pack_end(menu_btn)

        self._main_box.append(header)

        # Content area — stack for normal vs A/B mode
        self._view_stack = Gtk.Stack()
        self._view_stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self._main_box.append(self._view_stack)

        # Normal view
        self._normal_view = self._build_normal_view()
        self._view_stack.add_named(self._normal_view, "normal")

        # A/B view
        self._ab_view = self._build_ab_view()
        self._view_stack.add_named(self._ab_view, "ab")

        self._view_stack.set_visible_child_name("normal")

        # Progress bar
        self._progress = Gtk.ProgressBar()
        self._progress.set_visible(False)
        self._main_box.append(self._progress)

        # Status bar
        self._status_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        self._status_box.set_margin_start(12)
        self._status_box.set_margin_end(12)
        self._status_box.set_margin_top(4)
        self._status_box.set_margin_bottom(4)

        self._status_label = Gtk.Label(label=_("Ready"), xalign=0)
        self._status_label.add_css_class("dim-label")
        self._status_label.set_hexpand(True)
        self._status_box.append(self._status_label)

        self._char_count_label = Gtk.Label(label="", xalign=1)
        self._char_count_label.add_css_class("dim-label")
        self._status_box.append(self._char_count_label)

        self._engine_info_label = Gtk.Label(label="", xalign=1)
        self._engine_info_label.add_css_class("dim-label")
        self._status_box.append(self._engine_info_label)

        self._main_box.append(self._status_box)

        # Window actions
        self._setup_window_actions()

    def _build_normal_view(self):
        """Build the normal (non-A/B) view."""
        paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        paned.set_vexpand(True)
        paned.set_shrink_start_child(False)
        paned.set_shrink_end_child(False)

        # Left: text editor
        text_frame = Gtk.Frame()
        text_frame.set_margin_start(6)
        text_frame.set_margin_top(6)
        text_frame.set_margin_bottom(6)

        scroll = Gtk.ScrolledWindow()
        scroll.set_vexpand(True)
        scroll.set_hexpand(True)
        scroll.set_min_content_width(400)

        self._text_view = Gtk.TextView()
        self._text_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self._text_view.set_left_margin(12)
        self._text_view.set_right_margin(12)
        self._text_view.set_top_margin(12)
        self._text_view.set_bottom_margin(12)
        self._text_view.get_buffer().connect("changed", self._on_text_changed)

        scroll.set_child(self._text_view)
        text_frame.set_child(scroll)
        paned.set_start_child(text_frame)

        # Right: voice settings
        settings_scroll = Gtk.ScrolledWindow()
        settings_scroll.set_min_content_width(250)
        settings_scroll.set_margin_end(6)
        settings_scroll.set_margin_top(6)
        settings_scroll.set_margin_bottom(6)

        self._voice_panel = VoiceSettingsPanel(on_change=self._on_voice_settings_changed)
        settings_scroll.set_child(self._voice_panel)
        paned.set_end_child(settings_scroll)

        paned.set_position(600)
        return paned

    def _build_ab_view(self):
        """Build the A/B comparison view."""
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.set_vexpand(True)

        # Shared text editor
        text_frame = Gtk.Frame()
        text_frame.set_margin_start(6)
        text_frame.set_margin_end(6)
        text_frame.set_margin_top(6)

        scroll = Gtk.ScrolledWindow()
        scroll.set_vexpand(True)
        scroll.set_min_content_height(150)

        self._ab_text_view = Gtk.TextView()
        self._ab_text_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self._ab_text_view.set_left_margin(12)
        self._ab_text_view.set_right_margin(12)
        self._ab_text_view.set_top_margin(12)
        self._ab_text_view.set_bottom_margin(12)

        scroll.set_child(self._ab_text_view)
        text_frame.set_child(scroll)
        box.append(text_frame)

        # Split pane: Engine A | Engine B
        paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        paned.set_vexpand(True)
        paned.set_shrink_start_child(False)
        paned.set_shrink_end_child(False)

        # Engine A
        a_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        a_label = Gtk.Label(label=_("Engine A"))
        a_label.add_css_class("title-3")
        a_box.append(a_label)

        self._ab_engine_a = Gtk.DropDown()
        a_box.append(self._ab_engine_a)

        self._ab_panel_a = VoiceSettingsPanel()
        a_box.append(self._ab_panel_a)

        a_buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        a_buttons.set_halign(Gtk.Align.CENTER)
        play_a = Gtk.Button(label=_("Play A"))
        play_a.add_css_class("suggested-action")
        play_a.connect("clicked", self._on_play_a)
        a_buttons.append(play_a)

        self._ab_rate_a_up = Gtk.Button(icon_name="thumb-up-symbolic")
        self._ab_rate_a_up.set_tooltip_text(_("Thumbs Up"))
        self._ab_rate_a_up.connect("clicked", lambda b: self._rate_ab("A", "up"))
        a_buttons.append(self._ab_rate_a_up)

        self._ab_rate_a_down = Gtk.Button(icon_name="thumb-down-symbolic")
        self._ab_rate_a_down.set_tooltip_text(_("Thumbs Down"))
        self._ab_rate_a_down.connect("clicked", lambda b: self._rate_ab("A", "down"))
        a_buttons.append(self._ab_rate_a_down)

        a_box.append(a_buttons)
        paned.set_start_child(a_box)

        # Engine B
        b_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        b_label = Gtk.Label(label=_("Engine B"))
        b_label.add_css_class("title-3")
        b_box.append(b_label)

        self._ab_engine_b = Gtk.DropDown()
        b_box.append(self._ab_engine_b)

        self._ab_panel_b = VoiceSettingsPanel()
        b_box.append(self._ab_panel_b)

        b_buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        b_buttons.set_halign(Gtk.Align.CENTER)
        play_b = Gtk.Button(label=_("Play B"))
        play_b.add_css_class("suggested-action")
        play_b.connect("clicked", self._on_play_b)
        b_buttons.append(play_b)

        self._ab_rate_b_up = Gtk.Button(icon_name="thumb-up-symbolic")
        self._ab_rate_b_up.set_tooltip_text(_("Thumbs Up"))
        self._ab_rate_b_up.connect("clicked", lambda b: self._rate_ab("B", "up"))
        b_buttons.append(self._ab_rate_b_up)

        self._ab_rate_b_down = Gtk.Button(icon_name="thumb-down-symbolic")
        self._ab_rate_b_down.set_tooltip_text(_("Thumbs Down"))
        self._ab_rate_b_down.connect("clicked", lambda b: self._rate_ab("B", "down"))
        b_buttons.append(self._ab_rate_b_down)

        b_box.append(b_buttons)
        paned.set_end_child(b_box)

        box.append(paned)

        # Play Both button
        both_btn = Gtk.Button(label=_("Play Both"))
        both_btn.set_halign(Gtk.Align.CENTER)
        both_btn.set_margin_bottom(6)
        both_btn.add_css_class("suggested-action")
        both_btn.connect("clicked", self._on_play_both)
        box.append(both_btn)

        return box

    def _setup_window_actions(self):
        """Create window-level actions."""
        load_action = Gio.SimpleAction.new("load-text", None)
        load_action.connect("activate", self._on_load_text)
        self.add_action(load_action)

        save_fav_action = Gio.SimpleAction.new("save-favorite", None)
        save_fav_action.connect("activate", self._on_save_favorite)
        self.add_action(save_fav_action)

        # Sample text actions
        for lang in SAMPLE_TEXTS:
            action = Gio.SimpleAction.new(f"sample-{lang.lower()}", None)
            action.connect("activate", self._make_sample_handler(lang))
            self.add_action(action)

    def _make_sample_handler(self, lang):
        def handler(action, param):
            buf = self._get_active_text_buffer()
            buf.set_text(SAMPLE_TEXTS[lang])
            self._update_status(_("Loaded sample text: %s") % lang)
        return handler

    def _get_active_text_buffer(self):
        if self._ab_mode:
            return self._ab_text_view.get_buffer()
        return self._text_view.get_buffer()

    def _get_text(self):
        buf = self._get_active_text_buffer()
        return buf.get_text(buf.get_start_iter(), buf.get_end_iter(), True)

    # --- Engine management ---

    def _populate_engines(self):
        names = []
        self._engine_list = []

        if self._available_engine_classes:
            for cls in self._available_engine_classes:
                names.append(cls.display_name)
                self._engine_list.append(cls)
        else:
            # Show all engines even if unavailable, for UI completeness
            for cls in ENGINE_CLASSES:
                names.append(cls.display_name + _(" (not found)"))
                self._engine_list.append(cls)

        model = Gtk.StringList.new(names)
        self._engine_dropdown.set_model(model)
        self._ab_engine_a.set_model(Gtk.StringList.new(names))
        self._ab_engine_b.set_model(Gtk.StringList.new(names))

        if self._engine_list:
            self._engine_dropdown.set_selected(0)
            self._switch_engine(0)

    def _switch_engine(self, idx):
        if idx < 0 or idx >= len(self._engine_list):
            return
        cls = self._engine_list[idx]
        engine_name = cls.name

        if engine_name not in self._engines:
            self._engines[engine_name] = cls()

        self._current_engine = self._engines[engine_name]
        self._current_engine_name = engine_name

        # Load voices
        voices = self._current_engine.get_voices()
        self._voice_panel.populate_voices(voices)

        # Restore saved settings
        if engine_name in self._all_settings:
            self._voice_panel.apply_settings(self._all_settings[engine_name])
            self._current_engine.apply_settings(self._all_settings[engine_name])

        self._engine_info_label.set_text(cls.display_name)
        self._update_status(_("Engine: %s") % cls.display_name)

    def _get_ab_engine(self, which):
        """Get or create engine for A/B panel."""
        dropdown = self._ab_engine_a if which == "A" else self._ab_engine_b
        panel = self._ab_panel_a if which == "A" else self._ab_panel_b
        idx = dropdown.get_selected()
        if idx < 0 or idx >= len(self._engine_list):
            return None
        cls = self._engine_list[idx]
        engine = cls()
        settings = panel.get_settings()
        engine.apply_settings(settings)
        return engine

    # --- Callbacks ---

    def _on_engine_changed(self, dropdown, pspec):
        self._switch_engine(dropdown.get_selected())

    def _on_text_changed(self, buf):
        text = buf.get_text(buf.get_start_iter(), buf.get_end_iter(), True)
        chars = len(text)
        words = len(text.split()) if text.strip() else 0
        self._char_count_label.set_text(
            _("%(chars)d characters, %(words)d words") % {"chars": chars, "words": words}
        )

    def _on_voice_settings_changed(self):
        if self._current_engine and self._current_engine_name:
            settings = self._voice_panel.get_settings()
            self._current_engine.apply_settings(settings)
            self._all_settings[self._current_engine_name] = settings
            save_settings(self._all_settings)

    def _on_play(self, *args):
        text = self._get_text().strip()
        if not text:
            self._update_status(_("No text to speak"))
            return
        if not self._current_engine:
            self._update_status(_("No engine selected"))
            return

        self._update_status(_("Speaking…"))
        self._progress.set_visible(True)
        self._progress.pulse()

        def do_speak():
            try:
                self._current_engine.speak(text, ssml=self._ssml_mode)
                GLib.idle_add(self._on_playback_done)
            except Exception as e:
                GLib.idle_add(self._update_status, _("Error: %s") % str(e))

        t = threading.Thread(target=do_speak, daemon=True)
        t.start()

    def _on_playback_done(self):
        self._progress.set_visible(False)
        self._update_status(_("Playback finished"))

    def _on_stop(self, *args):
        if self._current_engine:
            self._current_engine.stop()
        self._progress.set_visible(False)
        self._update_status(_("Stopped"))

    def _on_ab_toggled(self, btn):
        self._ab_mode = btn.get_active()
        if self._ab_mode:
            # Sync text
            text = self._get_text()
            self._ab_text_view.get_buffer().set_text(text)
            self._view_stack.set_visible_child_name("ab")
            # Populate A/B engine voice lists
            for dropdown, panel in [
                (self._ab_engine_a, self._ab_panel_a),
                (self._ab_engine_b, self._ab_panel_b),
            ]:
                idx = dropdown.get_selected()
                if 0 <= idx < len(self._engine_list):
                    cls = self._engine_list[idx]
                    eng = cls()
                    panel.populate_voices(eng.get_voices())
            self._update_status(_("A/B Comparison mode"))
        else:
            self._view_stack.set_visible_child_name("normal")
            self._update_status(_("Normal mode"))

    def _on_ssml_toggled(self, btn):
        self._ssml_mode = btn.get_active()
        if self._ssml_mode:
            buf = self._get_active_text_buffer()
            text = buf.get_text(buf.get_start_iter(), buf.get_end_iter(), True)
            if not text.strip():
                buf.set_text(SSML_TEMPLATE)
            self._update_status(_("SSML mode enabled"))
        else:
            self._update_status(_("Plain text mode"))

    def _on_play_a(self, btn):
        self._play_ab("A")

    def _on_play_b(self, btn):
        self._play_ab("B")

    def _on_play_both(self, btn):
        self._play_ab("A")
        # Slight delay then play B (sequential for simplicity)
        GLib.timeout_add(100, lambda: self._play_ab("B") or False)

    def _play_ab(self, which):
        text = self._ab_text_view.get_buffer()
        text_str = text.get_text(text.get_start_iter(), text.get_end_iter(), True).strip()
        if not text_str:
            self._update_status(_("No text to speak"))
            return

        engine = self._get_ab_engine(which)
        if not engine:
            self._update_status(_("No engine for %s") % which)
            return

        self._update_status(_("Playing %s…") % which)

        def do_speak():
            try:
                engine.speak(text_str, ssml=self._ssml_mode)
                GLib.idle_add(
                    self._update_status,
                    _("Finished %s") % which
                )
            except Exception as e:
                GLib.idle_add(
                    self._update_status,
                    _("Error (%(engine)s): %(error)s") % {"engine": which, "error": str(e)}
                )

        t = threading.Thread(target=do_speak, daemon=True)
        t.start()

    def _rate_ab(self, which, rating):
        self._ab_ratings[which] = rating
        self._update_status(
            _("Rated %(engine)s: %(rating)s") % {"engine": which, "rating": "👍" if rating == "up" else "👎"}
        )

    def _on_save_audio(self, btn):
        text = self._get_text().strip()
        if not text:
            self._update_status(_("No text to save"))
            return
        if not self._current_engine:
            self._update_status(_("No engine selected"))
            return

        dialog = Gtk.FileDialog()
        dialog.set_title(_("Save Audio"))
        dialog.set_initial_name("output.wav")

        filter_wav = Gtk.FileFilter()
        filter_wav.set_name(_("WAV files"))
        filter_wav.add_pattern("*.wav")
        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(filter_wav)
        dialog.set_filters(filters)

        dialog.save(self, None, self._on_save_audio_response)

    def _on_save_audio_response(self, dialog, result):
        try:
            gfile = dialog.save_finish(result)
            path = gfile.get_path()

            text = self._get_text().strip()
            self._update_status(_("Saving audio…"))

            def do_save():
                try:
                    self._current_engine.speak(
                        text, output_file=path, ssml=self._ssml_mode
                    )
                    GLib.idle_add(
                        self._update_status,
                        _("Audio saved to %s") % os.path.basename(path)
                    )
                except Exception as e:
                    GLib.idle_add(
                        self._update_status,
                        _("Save error: %s") % str(e)
                    )

            t = threading.Thread(target=do_save, daemon=True)
            t.start()
        except GLib.Error:
            pass  # User cancelled

    def _on_load_text(self, action, param):
        dialog = Gtk.FileDialog()
        dialog.set_title(_("Load Text File"))

        filter_text = Gtk.FileFilter()
        filter_text.set_name(_("Text files"))
        filter_text.add_mime_type("text/plain")
        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(filter_text)
        dialog.set_filters(filters)

        dialog.open(self, None, self._on_load_text_response)

    def _on_load_text_response(self, dialog, result):
        try:
            gfile = dialog.open_finish(result)
            path = gfile.get_path()
            with open(path, "r") as f:
                text = f.read()
            buf = self._get_active_text_buffer()
            buf.set_text(text)
            self._update_status(_("Loaded: %s") % os.path.basename(path))
        except (GLib.Error, OSError):
            pass

    def _on_save_favorite(self, action, param):
        dialog = Adw.AlertDialog()
        dialog.set_heading(_("Save Favorite"))
        dialog.set_body(_("Enter a name for this preset:"))
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("save", _("Save"))
        dialog.set_response_appearance("save", Adw.ResponseAppearance.SUGGESTED)

        entry = Gtk.Entry()
        entry.set_placeholder_text(_("Preset name"))
        dialog.set_extra_child(entry)

        dialog.choose(self, None, self._on_save_favorite_response, entry)

    def _on_save_favorite_response(self, dialog, result, entry):
        try:
            response = dialog.choose_finish(result)
        except GLib.Error:
            return
        if response != "save":
            return

        name = entry.get_text().strip()
        if not name:
            return

        fav = {
            "name": name,
            "engine": self._current_engine_name,
            "settings": self._voice_panel.get_settings(),
        }

        self._favorites.append(fav)
        save_favorites(self._favorites)
        self._build_favorites_menu()
        self._update_status(_("Saved favorite: %s") % name)

    def _build_favorites_menu(self):
        menu = Gio.Menu()
        if self._favorites:
            for i, fav in enumerate(self._favorites):
                action_name = f"win.load-fav-{i}"
                menu.append(fav.get("name", _("Unnamed")), action_name)

                action = Gio.SimpleAction.new(f"load-fav-{i}", None)
                action.connect("activate", self._make_fav_handler(i))
                # Remove old action if exists
                self.remove_action(f"load-fav-{i}")
                self.add_action(action)
        else:
            menu.append(_("No favorites yet"), "win.noop")
            noop = Gio.SimpleAction.new("noop", None)
            noop.set_enabled(False)
            self.remove_action("noop")
            self.add_action(noop)

        self._favorites_button.set_menu_model(menu)

    def _make_fav_handler(self, idx):
        def handler(action, param):
            if idx < len(self._favorites):
                fav = self._favorites[idx]
                # Switch engine
                engine_name = fav.get("engine")
                for i, cls in enumerate(self._engine_list):
                    if cls.name == engine_name:
                        self._engine_dropdown.set_selected(i)
                        break
                # Apply settings
                if "settings" in fav:
                    self._voice_panel.apply_settings(fav["settings"])
                    if self._current_engine:
                        self._current_engine.apply_settings(fav["settings"])
                self._update_status(
                    _("Loaded favorite: %s") % fav.get("name", "")
                )
        return handler

    def _update_status(self, msg):
        self._status_label.set_text(f"[{_timestamp()}] {msg}")
        return False

    # --- Public for app-level actions ---

    def show_about(self, action, param):
        about = Adw.AboutDialog()
        about.set_application_name(_("TTS Tester"))
        about.set_application_icon("se.danielnylander.tts-tester")
        about.set_developer_name("Daniel Nylander")
        about.set_version(__version__)
        about.set_website("https://github.com/yeager/tts-tester")
        about.set_issue_url("https://github.com/yeager/tts-tester/issues")
        about.set_copyright("© 2026 Daniel Nylander")
        about.set_license_type(Gtk.License.GPL_3_0)
        about.set_translator_credits(_("translator-credits"))
        about.present(self)

    def show_shortcuts(self, action, param):
        builder = Gtk.Builder()
        builder.add_from_string('''
        <interface>
          <object class="GtkShortcutsWindow" id="shortcuts">
            <property name="modal">True</property>
            <child>
              <object class="GtkShortcutsSection">
                <property name="section-name">shortcuts</property>
                <child>
                  <object class="GtkShortcutsGroup">
                    <property name="title" translatable="yes">Playback</property>
                    <child>
                      <object class="GtkShortcutsShortcut">
                        <property name="title" translatable="yes">Play</property>
                        <property name="accelerator">&lt;Primary&gt;Return</property>
                      </object>
                    </child>
                  </object>
                </child>
                <child>
                  <object class="GtkShortcutsGroup">
                    <property name="title" translatable="yes">General</property>
                    <child>
                      <object class="GtkShortcutsShortcut">
                        <property name="title" translatable="yes">Export</property>
                        <property name="accelerator">&lt;Primary&gt;e</property>
                      </object>
                    </child>
                    <child>
                      <object class="GtkShortcutsShortcut">
                        <property name="title" translatable="yes">Refresh Engines</property>
                        <property name="accelerator">F5</property>
                      </object>
                    </child>
                    <child>
                      <object class="GtkShortcutsShortcut">
                        <property name="title" translatable="yes">Show Shortcuts</property>
                        <property name="accelerator">&lt;Primary&gt;question</property>
                      </object>
                    </child>
                    <child>
                      <object class="GtkShortcutsShortcut">
                        <property name="title" translatable="yes">Quit</property>
                        <property name="accelerator">&lt;Primary&gt;q</property>
                      </object>
                    </child>
                  </object>
                </child>
              </object>
            </child>
          </object>
        </interface>
        ''')
        shortcuts = builder.get_object("shortcuts")
        shortcuts.set_transient_for(self)
        shortcuts.present()

    def do_export(self, action, param):
        """Export settings/comparison data."""
        dialog = Gtk.FileDialog()
        dialog.set_title(_("Export Data"))
        dialog.set_initial_name("tts-tester-export.json")

        filter_json = Gtk.FileFilter()
        filter_json.set_name(_("JSON files"))
        filter_json.add_pattern("*.json")
        filter_csv = Gtk.FileFilter()
        filter_csv.set_name(_("CSV files"))
        filter_csv.add_pattern("*.csv")
        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(filter_json)
        filters.append(filter_csv)
        dialog.set_filters(filters)

        dialog.save(self, None, self._on_export_response)

    def _on_export_response(self, dialog, result):
        try:
            gfile = dialog.save_finish(result)
            path = gfile.get_path()
        except GLib.Error:
            return

        data = {
            "version": __version__,
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            "engine": self._current_engine_name,
            "settings": self._voice_panel.get_settings() if self._current_engine else {},
            "text": self._get_text(),
            "ab_ratings": self._ab_ratings,
            "favorites": self._favorites,
        }

        if path.endswith(".csv"):
            with open(path, "w", newline="") as f:
                writer = csv.writer(f)
                writer.writerow(["key", "value"])
                for k, v in data.items():
                    writer.writerow([k, json.dumps(v) if isinstance(v, (dict, list)) else v])
        else:
            with open(path, "w") as f:
                json.dump(data, f, indent=2)

        self._update_status(_("Exported to %s") % os.path.basename(path))


class Application(Adw.Application):
    def __init__(self):
        super().__init__(application_id="se.danielnylander.tts-tester")

    def do_activate(self):
        window = self.props.active_window
        if not window:
            window = MainWindow(application=self)
        window.present()

    def do_startup(self):
        Adw.Application.do_startup(self)

        # App actions
        quit_action = Gio.SimpleAction.new("quit", None)
        quit_action.connect("activate", self.quit_app)
        self.add_action(quit_action)

        about_action = Gio.SimpleAction.new("about", None)
        about_action.connect("activate", self.show_about)
        self.add_action(about_action)

        shortcuts_action = Gio.SimpleAction.new("shortcuts", None)
        shortcuts_action.connect("activate", self.show_shortcuts)
        self.add_action(shortcuts_action)

        refresh_action = Gio.SimpleAction.new("refresh", None)
        refresh_action.connect("activate", self.refresh_data)
        self.add_action(refresh_action)

        export_action = Gio.SimpleAction.new("export", None)
        export_action.connect("activate", self.do_export)
        self.add_action(export_action)

        play_action = Gio.SimpleAction.new("play", None)
        play_action.connect("activate", self.play)
        self.add_action(play_action)

        # Keyboard shortcuts
        self.set_accels_for_action("app.quit", ["<Primary>q"])
        self.set_accels_for_action("app.shortcuts", ["<Primary>question"])
        self.set_accels_for_action("app.refresh", ["F5"])
        self.set_accels_for_action("app.export", ["<Primary>e"])
        self.set_accels_for_action("app.play", ["<Primary>Return"])

    def quit_app(self, action, param):
        self.quit()

    def show_about(self, action, param):
        window = self.props.active_window
        if window:
            window.show_about(action, param)

    def show_shortcuts(self, action, param):
        window = self.props.active_window
        if window:
            window.show_shortcuts(action, param)

    def refresh_data(self, action, param):
        window = self.props.active_window
        if window:
            window._available_engine_classes = detect_engines()
            window._populate_engines()
            window._update_status(_("Engines refreshed"))

    def do_export(self, action, param):
        window = self.props.active_window
        if window:
            window.do_export(action, param)

    def play(self, action, param):
        window = self.props.active_window
        if window:
            window._on_play()


def main():
    app = Application()
    return app.run(sys.argv)


if __name__ == '__main__':
    main()
