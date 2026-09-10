from pydantic import BaseModel, Field


class SandboxPolicy(BaseModel):
    allowed_operations: set[str] = Field(
        default_factory=lambda: {
            "filesystem.read",
            "filesystem.write",
            "filesystem.edit",
            "filesystem.glob",
            "filesystem.grep",
        }
    )
    allow_shell: bool = False
    allowed_executables: set[str] = Field(default_factory=set)
    max_read_bytes: int = 2 * 1024 * 1024
    max_write_bytes: int = 2 * 1024 * 1024
    max_output_bytes: int = 2 * 1024 * 1024
    max_glob_results: int = 1000
    max_grep_results: int = 1000
    max_shell_timeout_seconds: int = 60
