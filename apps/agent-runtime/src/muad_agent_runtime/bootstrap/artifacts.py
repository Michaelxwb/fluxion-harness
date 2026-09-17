import os

from muad_artifact_store import NfsArtifactStore, SkillArtifactCache

artifact_store = NfsArtifactStore(os.getenv("ARTIFACT_ROOT", "/mnt/muad-artifacts"))
skill_artifact_cache = SkillArtifactCache(
    artifact_store,
    os.getenv("SKILL_CACHE_ROOT", "/var/cache/muad/skills"),
)
