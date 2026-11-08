"""产物版本历史规范化：`metadata_json.versions` 从「纯 key 字符串」统一成 `{key, checksum, size}`。

交付要能按**发布那一刻的 checksum** 找回对应版本的字节；旧格式只有键、没有校验和与长度。
一次性规范化成同一种形状（旧条目的 checksum/size 记为 null：它们从未被发布过，不需要可选取），
读取侧因此只有一种形状，不留双格式分支。
"""

import sqlalchemy as sa
from alembic import op

revision = "0041"
down_revision = "0040"
branch_labels = None
depends_on = None

_NORMALIZE = sa.text(
    """
    UPDATE runtime.artifact
    SET metadata_json = jsonb_set(metadata_json, '{versions}', normalized.versions)
    FROM (
        SELECT artifact.id AS artifact_id,
               jsonb_agg(
                   CASE
                       WHEN jsonb_typeof(entry.value) = 'string'
                       THEN jsonb_build_object(
                           'key', entry.value #>> '{}', 'checksum', NULL, 'size', NULL
                       )
                       ELSE entry.value
                   END
                   ORDER BY entry.ordinality
               ) AS versions
        FROM runtime.artifact AS artifact
        CROSS JOIN LATERAL jsonb_array_elements(
            CASE WHEN jsonb_typeof(artifact.metadata_json->'versions') = 'array'
                 THEN artifact.metadata_json->'versions' ELSE '[]'::jsonb END
        ) WITH ORDINALITY AS entry(value, ordinality)
        WHERE jsonb_typeof(artifact.metadata_json->'versions') = 'array'
        GROUP BY artifact.id
        HAVING bool_or(jsonb_typeof(entry.value) = 'string')
    ) AS normalized
    WHERE runtime.artifact.id = normalized.artifact_id
    """
)

_DENORMALIZE = sa.text(
    """
    UPDATE runtime.artifact
    SET metadata_json = jsonb_set(metadata_json, '{versions}', normalized.versions)
    FROM (
        SELECT artifact.id AS artifact_id,
               jsonb_agg(
                   CASE
                       WHEN jsonb_typeof(entry.value) = 'object'
                       THEN to_jsonb(entry.value->>'key')
                       ELSE entry.value
                   END
                   ORDER BY entry.ordinality
               ) AS versions
        FROM runtime.artifact AS artifact
        CROSS JOIN LATERAL jsonb_array_elements(
            CASE WHEN jsonb_typeof(artifact.metadata_json->'versions') = 'array'
                 THEN artifact.metadata_json->'versions' ELSE '[]'::jsonb END
        ) WITH ORDINALITY AS entry(value, ordinality)
        WHERE jsonb_typeof(artifact.metadata_json->'versions') = 'array'
        GROUP BY artifact.id
        HAVING bool_or(jsonb_typeof(entry.value) = 'object')
    ) AS normalized
    WHERE runtime.artifact.id = normalized.artifact_id
    """
)


def upgrade() -> None:
    op.get_bind().execute(_NORMALIZE)


def downgrade() -> None:
    op.get_bind().execute(_DENORMALIZE)
