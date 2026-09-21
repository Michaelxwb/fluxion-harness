from muad_artifact_store import NfsArtifactStore, SkillArtifactCache
from muad_common import SharedSettings

_settings = SharedSettings()

artifact_store = NfsArtifactStore(_settings.artifact_root)
skill_artifact_cache = SkillArtifactCache(artifact_store, _settings.skill_cache_root)
