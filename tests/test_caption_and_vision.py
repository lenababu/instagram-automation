from types import SimpleNamespace

from app.audio_transcription import Transcript
from app.caption_generator import Caption, build_prompt, generate_caption
from app.config import Settings
from app.visual_analysis import VisualAnalysis, parse_json_block


class FakeGroq:
    def __init__(self, reply):
        self.reply = reply
        self.kwargs = None
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.kwargs = kwargs
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=self.reply))])


def test_parse_json_block_tolerates_fences():
    assert parse_json_block('Sure!\n```json\n{"a": 1}\n```')["a"] == 1


def test_prompt_contains_video_facts_and_previous_hooks():
    t = Transcript(text="നമസ്കാരം", language="ml", english="Hello everyone")
    v = VisualAnalysis(summary="A man cooks biryani", purpose="promote a restaurant")
    p = build_prompt(t, v, "Brand X in Thrissur", ["Old hook 1"])
    for needle in ("Hello everyone", "A man cooks biryani", "promote a restaurant", "Brand X", "Old hook 1"):
        assert needle in p
    assert "{{" not in p


def test_generate_caption_builds_structured_caption():
    reply = '{"hook":"The biryani that took 6 hours","caption":"Line one.\\nLine two.","description":"A chef cooks biryani.","cta":"Save this for the weekend.","hashtags":["Biryani","#KeralaFood","thrissur","food","cooking","viral"]}'
    client = FakeGroq(reply)
    cap = generate_caption(None, VisualAnalysis(summary="chef cooking"), Settings(groq_api_key="k"), [], client=client)
    text = cap.render()
    assert text.startswith("The biryani that took 6 hours")
    assert "#KeralaFood" in text and "#Biryani" in text and "#viral" in text
    assert client.kwargs["model"] == "openai/gpt-oss-120b"
    assert len(text) <= 2200


def test_render_caps_hashtags_at_eight():
    cap = Caption("h", "b", "", "", [f"#t{i}" for i in range(12)])
    assert cap.render().count("#") == 12  # render doesn't trim; _clean_hashtags does


def test_hashtag_cleaning_limits_to_eight_and_dedupes():
    from app.caption_generator import _clean_hashtags
    tags = _clean_hashtags(["#A", "a", "b c", "#d!", "e", "f", "g", "h", "i", "j"])
    assert tags[0] == "#A" and "#a" not in tags and len(tags) == 8 and "#bc" in tags
