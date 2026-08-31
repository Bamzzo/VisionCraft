CREATE TABLE IF NOT EXISTS projects (
  id TEXT PRIMARY KEY,
  title TEXT NOT NULL,
  source_text TEXT NOT NULL,
  style TEXT NOT NULL,
  aspect_ratio TEXT NOT NULL,
  duration_seconds INTEGER NOT NULL,
  output_resolution TEXT NOT NULL DEFAULT '1280x720',
  shot_count_mode TEXT NOT NULL,
  requested_shot_count INTEGER,
  review_mode INTEGER NOT NULL DEFAULT 0,
  archived INTEGER NOT NULL DEFAULT 0,
  assembly_stale INTEGER NOT NULL DEFAULT 0,
  selected_option_id TEXT,
  status TEXT NOT NULL,
  routing_mode TEXT NOT NULL DEFAULT 'direct',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS story_bibles (
  project_id TEXT PRIMARY KEY REFERENCES projects(id) ON DELETE CASCADE,
  summary TEXT NOT NULL,
  worldview TEXT NOT NULL,
  style_tags TEXT NOT NULL,
  themes TEXT NOT NULL,
  logline TEXT,
  adaptation_summary TEXT,
  emotion_curve TEXT,
  protagonist TEXT,
  protagonist_goal TEXT,
  obstacle TEXT,
  character_cards_json TEXT NOT NULL DEFAULT '[]',
  scene_cards_json TEXT NOT NULL DEFAULT '[]',
  visual_style TEXT,
  consistency_constraints TEXT,
  option_id TEXT,
  source TEXT NOT NULL DEFAULT 'mock_planner',
  review_status TEXT NOT NULL DEFAULT 'draft',
  scope_id TEXT,
  created_at TEXT,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS characters (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  name TEXT NOT NULL,
  role TEXT NOT NULL,
  description TEXT NOT NULL,
  visual_prompt TEXT NOT NULL,
  asset_id TEXT,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS scenes (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  name TEXT NOT NULL,
  description TEXT NOT NULL,
  visual_prompt TEXT NOT NULL,
  asset_id TEXT,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS global_constraints (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  target TEXT NOT NULL,
  positive_prompt TEXT NOT NULL,
  negative_prompt TEXT NOT NULL,
  source TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS shots (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  shot_index INTEGER NOT NULL,
  title TEXT NOT NULL,
  description TEXT NOT NULL,
  characters TEXT NOT NULL,
  scene TEXT NOT NULL,
  camera_motion TEXT NOT NULL,
  visual_prompt TEXT NOT NULL,
  negative_prompt TEXT NOT NULL,
  audio_prompt TEXT NOT NULL,
  rag_evidence TEXT NOT NULL DEFAULT '[]',
  narrative_purpose TEXT,
  action_text TEXT,
  duration_seconds INTEGER,
  bible_character TEXT,
  bible_scene TEXT,
  source_excerpt TEXT,
  source_start INTEGER,
  source_end INTEGER,
  source_type TEXT,
  review_status TEXT,
  status TEXT NOT NULL,
  retry_count INTEGER NOT NULL DEFAULT 0,
  current_version_id TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS shot_versions (
  id TEXT PRIMARY KEY,
  shot_id TEXT NOT NULL REFERENCES shots(id) ON DELETE CASCADE,
  version_number INTEGER NOT NULL,
  description TEXT NOT NULL,
  visual_prompt TEXT NOT NULL,
  negative_prompt TEXT NOT NULL,
  audio_prompt TEXT NOT NULL,
  first_frame_path TEXT,
  last_frame_path TEXT,
  video_path TEXT,
  video_mode TEXT NOT NULL DEFAULT 't2v',
  provider TEXT,
  model TEXT,
  camera_motion TEXT,
  duration_seconds INTEGER,
  reference_frame_path TEXT,
  change_summary TEXT,
  created_by TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS shot_drafts (
  shot_id TEXT PRIMARY KEY REFERENCES shots(id) ON DELETE CASCADE,
  project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  description TEXT,
  camera_motion TEXT,
  visual_prompt TEXT,
  negative_prompt TEXT,
  audio_prompt TEXT,
  video_mode TEXT,
  provider TEXT,
  model TEXT,
  duration_seconds INTEGER,
  first_frame_path TEXT,
  last_frame_path TEXT,
  reference_frame_path TEXT,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS assets (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  type TEXT NOT NULL,
  name TEXT NOT NULL,
  description TEXT NOT NULL,
  prompt TEXT NOT NULL,
  file_path TEXT NOT NULL,
  embedding_ref TEXT,
  source_task_id TEXT,
  source_remote_task_id TEXT,
  asset_role TEXT,
  mime_type TEXT,
  byte_size INTEGER,
  sha256 TEXT,
  width INTEGER,
  height INTEGER,
  duration_seconds REAL,
  source TEXT,
  source_provider TEXT,
  source_model TEXT,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS media_transfers (
  id TEXT PRIMARY KEY,
  asset_id TEXT NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
  target_provider TEXT NOT NULL,
  target_model TEXT NOT NULL,
  transfer_mode TEXT NOT NULL,
  role TEXT NOT NULL,
  request_reference TEXT NOT NULL,
  metadata_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_media_transfers_asset_target
ON media_transfers(asset_id, target_provider, target_model);

CREATE TABLE IF NOT EXISTS feedback_records (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  shot_id TEXT REFERENCES shots(id) ON DELETE CASCADE,
  user_text TEXT NOT NULL,
  scope TEXT NOT NULL,
  target TEXT NOT NULL,
  positive_prompt TEXT NOT NULL,
  negative_prompt TEXT NOT NULL,
  reason TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS jobs (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  type TEXT NOT NULL,
  status TEXT NOT NULL,
  progress INTEGER NOT NULL DEFAULT 0,
  message TEXT NOT NULL DEFAULT '',
  retry_count INTEGER NOT NULL DEFAULT 0,
  error_message TEXT,
  stage TEXT,
  shot_id TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS job_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
  project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  shot_id TEXT,
  event_type TEXT NOT NULL,
  stage TEXT NOT NULL,
  status TEXT NOT NULL,
  progress INTEGER NOT NULL DEFAULT 0,
  message TEXT NOT NULL DEFAULT '',
  detail_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_job_events_job_id ON job_events(job_id);
CREATE INDEX IF NOT EXISTS idx_job_events_project_id ON job_events(project_id);
CREATE INDEX IF NOT EXISTS idx_job_events_created_at ON job_events(created_at);
CREATE INDEX IF NOT EXISTS idx_job_events_project_id_id ON job_events(project_id, id);

CREATE TABLE IF NOT EXISTS video_tasks (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  shot_id TEXT NOT NULL REFERENCES shots(id) ON DELETE CASCADE,
  version_id TEXT NOT NULL REFERENCES shot_versions(id) ON DELETE CASCADE,
  job_id TEXT REFERENCES jobs(id) ON DELETE SET NULL,
  provider TEXT NOT NULL,
  model TEXT NOT NULL,
  remote_task_id TEXT NOT NULL,
  status TEXT NOT NULL,
  cloud_status TEXT NOT NULL DEFAULT '',
  prompt TEXT NOT NULL DEFAULT '',
  submit_payload TEXT NOT NULL DEFAULT '{}',
  status_payload TEXT NOT NULL DEFAULT '{}',
  video_url TEXT,
  result_path TEXT,
  error_code TEXT,
  error_message TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(provider, remote_task_id)
);

CREATE TABLE IF NOT EXISTS workflow_checkpoints (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
  node TEXT NOT NULL,
  status TEXT NOT NULL,
  state_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS adaptation_options (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  option_index INTEGER NOT NULL,
  title TEXT NOT NULL,
  rationale TEXT NOT NULL,
  protagonist_goal TEXT NOT NULL,
  conflict TEXT NOT NULL,
  ending_orientation TEXT NOT NULL,
  suggested_duration_seconds INTEGER NOT NULL,
  suggested_shot_count INTEGER NOT NULL,
  source_excerpt TEXT NOT NULL,
  source_start INTEGER,
  source_end INTEGER,
  selected INTEGER NOT NULL DEFAULT 0,
  source TEXT NOT NULL DEFAULT 'mock_planner',
  scope_id TEXT,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS storyboard_drafts (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  shot_index INTEGER NOT NULL,
  title TEXT NOT NULL,
  narrative_purpose TEXT NOT NULL DEFAULT '',
  characters TEXT NOT NULL DEFAULT '[]',
  scene TEXT NOT NULL DEFAULT '',
  action_text TEXT NOT NULL DEFAULT '',
  camera_motion TEXT NOT NULL DEFAULT '',
  duration_seconds INTEGER NOT NULL DEFAULT 5,
  visual_prompt TEXT NOT NULL DEFAULT '',
  bible_character TEXT,
  bible_scene TEXT,
  source_excerpt TEXT NOT NULL DEFAULT '',
  source_start INTEGER,
  source_end INTEGER,
  source_type TEXT NOT NULL DEFAULT 'auto_draft',
  review_status TEXT NOT NULL DEFAULT 'draft',
  scope_id TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS review_records (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  stage TEXT NOT NULL,
  action TEXT NOT NULL,
  summary TEXT NOT NULL DEFAULT '',
  target_id TEXT,
  target_version TEXT,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS source_chunks (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  chunk_index INTEGER NOT NULL,
  text TEXT NOT NULL,
  start_offset INTEGER NOT NULL,
  end_offset INTEGER NOT NULL,
  char_count INTEGER NOT NULL,
  summary TEXT NOT NULL DEFAULT '',
  characters_json TEXT NOT NULL DEFAULT '[]',
  places_json TEXT NOT NULL DEFAULT '[]',
  conflict_terms_json TEXT NOT NULL DEFAULT '[]',
  source TEXT NOT NULL DEFAULT 'mock_segmenter',
  created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_source_chunks_project ON source_chunks(project_id);
CREATE INDEX IF NOT EXISTS idx_source_chunks_project_index ON source_chunks(project_id, chunk_index);

CREATE TABLE IF NOT EXISTS story_events (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  event_index INTEGER NOT NULL,
  title TEXT NOT NULL,
  summary TEXT NOT NULL DEFAULT '',
  characters_json TEXT NOT NULL DEFAULT '[]',
  places_json TEXT NOT NULL DEFAULT '[]',
  goal TEXT NOT NULL DEFAULT '',
  conflict TEXT NOT NULL DEFAULT '',
  outcome TEXT NOT NULL DEFAULT '',
  chunk_ids_json TEXT NOT NULL DEFAULT '[]',
  source_excerpt TEXT NOT NULL DEFAULT '',
  source_start INTEGER,
  source_end INTEGER,
  importance REAL NOT NULL DEFAULT 0,
  source TEXT NOT NULL DEFAULT 'mock_event_extractor',
  created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_story_events_project ON story_events(project_id);
CREATE INDEX IF NOT EXISTS idx_story_events_project_index ON story_events(project_id, event_index);

CREATE TABLE IF NOT EXISTS storylines (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  storyline_index INTEGER NOT NULL,
  title TEXT NOT NULL,
  rationale TEXT NOT NULL DEFAULT '',
  protagonist TEXT NOT NULL DEFAULT '',
  protagonist_goal TEXT NOT NULL DEFAULT '',
  conflict TEXT NOT NULL DEFAULT '',
  turning_point TEXT NOT NULL DEFAULT '',
  ending_orientation TEXT NOT NULL DEFAULT '',
  event_ids_json TEXT NOT NULL DEFAULT '[]',
  chunk_ids_json TEXT NOT NULL DEFAULT '[]',
  source_excerpt TEXT NOT NULL DEFAULT '',
  suggested_duration_seconds INTEGER NOT NULL DEFAULT 45,
  suggested_shot_count INTEGER NOT NULL DEFAULT 5,
  selected INTEGER NOT NULL DEFAULT 0,
  source TEXT NOT NULL DEFAULT 'mock_storyline_planner',
  created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_storylines_project ON storylines(project_id);

CREATE TABLE IF NOT EXISTS adaptation_scopes (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  storyline_id TEXT,
  event_ids_json TEXT NOT NULL DEFAULT '[]',
  chunk_ids_json TEXT NOT NULL DEFAULT '[]',
  scoped_text TEXT NOT NULL DEFAULT '',
  start_offset INTEGER,
  end_offset INTEGER,
  review_status TEXT NOT NULL DEFAULT 'draft',
  user_note TEXT NOT NULL DEFAULT '',
  source TEXT NOT NULL DEFAULT 'user_scope',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_adaptation_scopes_project ON adaptation_scopes(project_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_adaptation_scopes_project_unique ON adaptation_scopes(project_id);

CREATE TABLE IF NOT EXISTS provider_capabilities (
  id TEXT PRIMARY KEY,
  provider TEXT NOT NULL,
  kind TEXT NOT NULL,
  label TEXT NOT NULL,
  supported_ratios TEXT NOT NULL,
  supported_durations TEXT NOT NULL,
  supported_resolutions TEXT NOT NULL,
  mode TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS assembly_settings (
  project_id TEXT PRIMARY KEY REFERENCES projects(id) ON DELETE CASCADE,
  subtitle_enabled INTEGER NOT NULL DEFAULT 0,
  subtitle_text TEXT NOT NULL DEFAULT '',
  subtitle_srt_path TEXT NOT NULL DEFAULT '',
  audio_enabled INTEGER NOT NULL DEFAULT 0,
  audio_asset_path TEXT NOT NULL DEFAULT '',
  audio_volume REAL NOT NULL DEFAULT 0.4,
  keep_source_audio INTEGER NOT NULL DEFAULT 0,
  subtitle_font_size INTEGER NOT NULL DEFAULT 28,
  subtitle_position TEXT NOT NULL DEFAULT 'bottom',
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS workflow_model_configs (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  workflow_run_id TEXT,
  stage TEXT NOT NULL,
  provider TEXT NOT NULL,
  model TEXT NOT NULL,
  parameters TEXT NOT NULL DEFAULT '{}',
  selected_by_user INTEGER NOT NULL DEFAULT 0,
  is_default INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(project_id, stage)
);

CREATE INDEX IF NOT EXISTS idx_workflow_model_configs_project ON workflow_model_configs(project_id);

CREATE TABLE IF NOT EXISTS vision_reviews (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  asset_id TEXT NOT NULL,
  asset_role TEXT,
  provider TEXT NOT NULL,
  model TEXT NOT NULL,
  transport_mode TEXT,
  mime_type TEXT,
  width INTEGER,
  height INTEGER,
  byte_size INTEGER,
  request_id TEXT,
  result_json TEXT NOT NULL DEFAULT '{}',
  used_local_fallback INTEGER NOT NULL DEFAULT 0,
  generation_mode TEXT,
  source TEXT,
  config_source TEXT,
  created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_vision_reviews_project ON vision_reviews(project_id);
