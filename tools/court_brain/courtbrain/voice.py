"""Optional speech boundary. Text play never depends on a speech service."""


class DisabledVoice:
    can_listen = False
    can_speak = False
    description = "Voice is disabled. Type your ruler's words; no speech provider is installed."

    def listen_start(self, timeout_s=30.0):
        return False

    def listen_stop(self):
        return ""

    def speak(self, text, voice_id=""):
        return None

    def close(self):
        return None


def make_voice(cfg):
    # A later backend can implement these capabilities without changing AI clients.
    return DisabledVoice()
