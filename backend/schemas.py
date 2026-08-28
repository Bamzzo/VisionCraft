from typing import Literal

from pydantic import BaseModel, Field


class ProjectCreate(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    source_text: str = Field(min_length=5)
    style: str = Field(default="cinematic clean realism")
    aspect_ratio: str = Field(default="16:9")
    duration_seconds: int = Field(default=5, ge=5, le=10)
    shot_count_mode: str = Field(default="auto")
    requested_shot_count: int | None = Field(default=None, ge=1, le=12)
    review_mode: bool = Field(default=False)


class FeedbackCreate(BaseModel):
    user_text: str = Field(min_length=1, max_length=1000)


class VideoGenerateRequest(BaseModel):
    video_mode: Literal["t2v", "i2v", "keyframes"] = "t2v"
    provider: str | None = Field(default=None, max_length=40)
    model: str | None = Field(default=None, max_length=120)
    duration_seconds: int | None = Field(default=None, ge=1, le=30)
    version_id: str | None = Field(default=None, max_length=40)
    description: str | None = Field(default=None, max_length=2000)
    camera_motion: str | None = Field(default=None, max_length=240)
    visual_prompt: str | None = Field(default=None, max_length=4000)
    first_frame_path: str | None = None
    last_frame_path: str | None = None
    reference_frame_path: str | None = None


class ShotDraftUpdate(BaseModel):
    description: str | None = Field(default=None, max_length=2000)
    camera_motion: str | None = Field(default=None, max_length=240)
    visual_prompt: str | None = Field(default=None, max_length=4000)
    negative_prompt: str | None = Field(default=None, max_length=2000)
    audio_prompt: str | None = Field(default=None, max_length=2000)
    video_mode: Literal["t2v", "i2v", "keyframes"] | None = None
    provider: str | None = Field(default=None, max_length=40)
    model: str | None = Field(default=None, max_length=120)
    duration_seconds: int | None = Field(default=None, ge=1, le=30)
    first_frame_path: str | None = None
    last_frame_path: str | None = None
    reference_frame_path: str | None = None


class KeyframeSelectRequest(BaseModel):
    first_frame_path: str | None = None
    last_frame_path: str | None = None


class KeyframeRedrawRequest(BaseModel):
    target: Literal["first", "last", "both"] = "both"


class AdaptationSelectRequest(BaseModel):
    option_id: str = Field(min_length=3, max_length=40)


class StoryBibleUpdate(BaseModel):
    logline: str | None = Field(default=None, max_length=400)
    adaptation_summary: str | None = Field(default=None, max_length=4000)
    summary: str | None = Field(default=None, max_length=4000)
    worldview: str | None = Field(default=None, max_length=4000)
    emotion_curve: str | None = Field(default=None, max_length=400)
    protagonist: str | None = Field(default=None, max_length=80)
    protagonist_goal: str | None = Field(default=None, max_length=400)
    obstacle: str | None = Field(default=None, max_length=400)
    visual_style: str | None = Field(default=None, max_length=240)
    consistency_constraints: str | None = Field(default=None, max_length=2000)
    themes: list[str] | None = None
    style_tags: list[str] | None = None
    character_cards: list[dict] | None = None
    scene_cards: list[dict] | None = None


class StoryboardSaveRequest(BaseModel):
    shots: list[dict] = Field(default_factory=list)


class AdaptationRegenerateRequest(BaseModel):
    stage: Literal["scope", "bible", "storyboard"]


class DemoCleanupRequest(BaseModel):
    keep_project_id: str | None = None
    archive_failed: bool = True
    remove_invalid_videos: bool = True


class ProjectSummary(BaseModel):
    id: str
    title: str
    status: str
    style: str
    aspect_ratio: str
    updated_at: str
