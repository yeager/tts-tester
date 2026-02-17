"""TTS engine backends for tts-tester."""

import subprocess
import shutil
import os
import json
import tempfile
import gettext

TEXTDOMAIN = 'tts-tester'
_ = gettext.gettext


class TTSEngine:
    """Base class for TTS engines."""

    name = "base"
    display_name = "Base"

    def __init__(self):
        self.speed = 1.0
        self.pitch = 1.0
        self.volume = 1.0
        self.voice = None

    @classmethod
    def is_available(cls):
        """Check if this engine is installed."""
        return False

    def get_voices(self):
        """Return list of (voice_id, display_name) tuples."""
        return []

    def speak(self, text, output_file=None, ssml=False):
        """Speak text or save to file. Returns output file path or None."""
        raise NotImplementedError

    def stop(self):
        """Stop current playback."""
        pass

    def get_settings(self):
        """Return current settings as dict."""
        return {
            "speed": self.speed,
            "pitch": self.pitch,
            "volume": self.volume,
            "voice": self.voice,
        }

    def apply_settings(self, settings):
        """Apply settings from dict."""
        self.speed = settings.get("speed", 1.0)
        self.pitch = settings.get("pitch", 1.0)
        self.volume = settings.get("volume", 1.0)
        self.voice = settings.get("voice", None)


class EspeakEngine(TTSEngine):
    """espeak-ng TTS engine."""

    name = "espeak-ng"
    display_name = "eSpeak NG"

    def __init__(self):
        super().__init__()
        self._process = None

    @classmethod
    def is_available(cls):
        return shutil.which("espeak-ng") is not None

    def get_voices(self):
        voices = []
        try:
            result = subprocess.run(
                ["espeak-ng", "--voices"],
                capture_output=True, text=True, timeout=10
            )
            for line in result.stdout.strip().split("\n")[1:]:
                parts = line.split()
                if len(parts) >= 4:
                    lang = parts[1]
                    name = parts[3]
                    voices.append((name, f"{name} ({lang})"))
        except (subprocess.SubprocessError, OSError):
            pass
        return voices

    def speak(self, text, output_file=None, ssml=False):
        cmd = ["espeak-ng"]

        if self.voice:
            cmd += ["-v", self.voice]

        # Speed: espeak uses words-per-minute, default 175
        wpm = int(175 * self.speed)
        cmd += ["-s", str(wpm)]

        # Pitch: espeak uses 0-99, default 50
        pitch_val = int(50 * self.pitch)
        pitch_val = max(0, min(99, pitch_val))
        cmd += ["-p", str(pitch_val)]

        # Volume: espeak uses 0-200, default 100
        vol = int(100 * self.volume)
        vol = max(0, min(200, vol))
        cmd += ["-a", str(vol)]

        if ssml:
            cmd += ["-m"]

        if output_file:
            cmd += ["-w", output_file]
            cmd.append(text)
            self._process = subprocess.Popen(cmd)
            self._process.wait()
            return output_file
        else:
            cmd.append(text)
            self._process = subprocess.Popen(cmd)
            return None

    def stop(self):
        if self._process and self._process.poll() is None:
            self._process.terminate()
            self._process = None


class PiperEngine(TTSEngine):
    """Piper neural TTS engine."""

    name = "piper"
    display_name = "Piper"

    def __init__(self):
        super().__init__()
        self._process = None

    @classmethod
    def is_available(cls):
        return shutil.which("piper") is not None

    def get_voices(self):
        voices = []
        # Piper models are in ~/.local/share/piper-voices/ or similar
        model_dirs = [
            os.path.expanduser("~/.local/share/piper-voices"),
            "/usr/share/piper-voices",
            os.path.expanduser("~/.local/share/piper/voices"),
        ]
        for model_dir in model_dirs:
            if os.path.isdir(model_dir):
                for f in sorted(os.listdir(model_dir)):
                    if f.endswith(".onnx"):
                        name = f.replace(".onnx", "")
                        voices.append((os.path.join(model_dir, f), name))
        if not voices:
            voices.append(("", _("(default)")))
        return voices

    def speak(self, text, output_file=None, ssml=False):
        if not output_file:
            output_file = tempfile.mktemp(suffix=".wav")

        cmd = ["piper", "--output_file", output_file]

        if self.voice and self.voice != "":
            cmd += ["--model", self.voice]

        # Piper supports --length_scale for speed (inverse: higher = slower)
        if self.speed != 1.0:
            length_scale = 1.0 / max(0.1, self.speed)
            cmd += ["--length_scale", str(length_scale)]

        self._process = subprocess.Popen(
            cmd, stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        self._process.communicate(input=text.encode())

        # Play the output file
        if os.path.exists(output_file):
            return output_file
        return None

    def stop(self):
        if self._process and self._process.poll() is None:
            self._process.terminate()
            self._process = None


class FestivalEngine(TTSEngine):
    """Festival TTS engine."""

    name = "festival"
    display_name = "Festival"

    def __init__(self):
        super().__init__()
        self._process = None

    @classmethod
    def is_available(cls):
        return shutil.which("festival") is not None

    def get_voices(self):
        voices = []
        try:
            result = subprocess.run(
                ["festival", "-b", "(voice.list)"],
                capture_output=True, text=True, timeout=10
            )
            # Parse Scheme output like (kal_diphone cmu_us_slt_arctic_hts ...)
            output = result.stdout.strip()
            if output.startswith("(") and output.endswith(")"):
                names = output[1:-1].split()
                for name in names:
                    voices.append((name, name))
        except (subprocess.SubprocessError, OSError):
            pass
        return voices

    def speak(self, text, output_file=None, ssml=False):
        if output_file:
            # Generate WAV file
            scheme_cmd = ""
            if self.voice:
                scheme_cmd += f'({self.voice})\n'
            if self.speed != 1.0:
                rate = self.speed
                scheme_cmd += f'(Parameter.set \'Duration_Stretch {1.0/rate})\n'
            scheme_cmd += f'(set! utt1 (SynthText "{text}"))\n'
            scheme_cmd += f'(utt.save.wave utt1 "{output_file}")\n'

            self._process = subprocess.Popen(
                ["festival"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE
            )
            self._process.communicate(input=scheme_cmd.encode())
            return output_file
        else:
            # Direct playback
            scheme_cmd = ""
            if self.voice:
                scheme_cmd += f'({self.voice})\n'
            if self.speed != 1.0:
                rate = self.speed
                scheme_cmd += f'(Parameter.set \'Duration_Stretch {1.0/rate})\n'
            scheme_cmd += f'(SayText "{text}")\n'

            self._process = subprocess.Popen(
                ["festival"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE
            )
            self._process.communicate(input=scheme_cmd.encode())
            return None

    def stop(self):
        if self._process and self._process.poll() is None:
            self._process.terminate()
            self._process = None


# Registry of all engine classes
ENGINE_CLASSES = [EspeakEngine, PiperEngine, FestivalEngine]


def detect_engines():
    """Return list of available engine classes."""
    return [cls for cls in ENGINE_CLASSES if cls.is_available()]


def get_engine(name):
    """Create engine instance by name."""
    for cls in ENGINE_CLASSES:
        if cls.name == name:
            return cls()
    return None
