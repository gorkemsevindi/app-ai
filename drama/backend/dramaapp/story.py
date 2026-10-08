"""Story bible / script schema shared by every LLM adapter (local template writer, Anthropic, ...)."""

from typing import Literal

from pydantic import BaseModel, Field

Emotion = Literal["neutral", "happy", "sad", "angry", "fear", "surprise", "tender"]
Genre = Literal["drama", "romance", "thriller", "revenge", "comedy", "mystery"]


class Look(BaseModel):
    skin: str = Field(description="hex colour, e.g. #c99a7a")
    hair: str = Field(description="hex colour")
    hair_style: Literal["short", "long", "bun", "curly", "bald", "bob"] = "short"
    eyes: str = "#3b2a1a"
    outfit: str = "#2c3e50"
    accent: str = "#c0392b"
    face_width: float = Field(1.0, ge=0.85, le=1.15)
    glasses: bool = False
    beard: bool = False
    age_band: Literal["young", "adult", "senior"] = "adult"


class VoiceHint(BaseModel):
    gender: Literal["female", "male", "neutral"] = "neutral"
    pitch: int = Field(50, ge=0, le=99, description="relative pitch 0-99")
    rate: int = Field(165, ge=110, le=220, description="words per minute")
    timbre: str = Field("warm", description="free text casting note for premium TTS")


class CharacterCard(BaseModel):
    key: str = Field(description="stable id used in scripts, e.g. 'elif'")
    name: str
    role: str = Field(description="protagonist, antagonist, love interest, ally ...")
    personality: str
    want: str
    secret: str
    look: Look
    voice: VoiceHint
    relationships: dict[str, str] = Field(default_factory=dict)


class Location(BaseModel):
    key: str
    name: str
    ambience: Literal["room", "city", "rain", "nature", "cafe", "office", "night"] = "room"
    lighting: Literal["warm", "cool", "noir", "night", "daylight", "neon"] = "warm"
    palette: list[str] = Field(default_factory=lambda: ["#2b2d42", "#8d99ae"])


class Line(BaseModel):
    speaker: str = Field(description="character key")
    text: str
    emotion: Emotion = "neutral"
    intensity: float = Field(0.7, ge=0, le=1)
    action: str = ""
    look_at: str | None = Field(None, description="character key the speaker looks at")


class Scene(BaseModel):
    location: str = Field(description="location key")
    time_of_day: Literal["day", "night", "dusk", "dawn"] = "day"
    mood: Literal["tense", "warm", "sad", "mysterious", "romantic", "playful", "dramatic"] = "tense"
    characters: list[str]
    camera: Literal["static", "push_in", "pull_out", "pan_left", "pan_right", "handheld"] = "push_in"
    lines: list[Line]


class EpisodeScript(BaseModel):
    number: int
    title: str
    synopsis: str
    cliffhanger: str
    scenes: list[Scene]


class StoryBible(BaseModel):
    title: str
    logline: str
    genre: Genre
    language: Literal["tr", "en"]
    tone: str
    setting: str
    season_arc: str
    characters: list[CharacterCard]
    locations: list[Location]
    episodes: list[EpisodeScript]


class WizardInput(BaseModel):
    title: str | None = None
    genre: Genre = "drama"
    logline: str = ""
    language: Literal["tr", "en"] = "tr"
    episode_count: int = Field(3, ge=1, le=60)
    episode_duration_s: int = Field(60, ge=20, le=180)
    character_count: int = Field(3, ge=2, le=4)
    audience_rating: Literal["7+", "13+", "16+", "18+"] = "13+"
    visual_style: Literal["cinematic", "anime", "noir", "pastel"] = "cinematic"
    seed: int | None = None
